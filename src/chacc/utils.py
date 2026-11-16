"""
Utility functions for the ChaCC Dependency Manager.

Provides logging setup, hash calculations, and package management utilities.
"""

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    from packaging.utils import canonicalize_name
except ImportError:
    def canonicalize_name(name: str) -> str:
        return name.replace('_', '-').replace('.', '-').lower()

try:
    from packaging.version import Version, InvalidVersion
except ImportError:  # pragma: no cover - packaging ships with pip-tools
    Version = None
    InvalidVersion = Exception


default_logger = logging.getLogger('dependency_manager')
default_logger.setLevel(logging.DEBUG)
if not default_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(name)s - %(message)s')
    handler.setFormatter(formatter)
    default_logger.addHandler(handler)


def get_environment_hash() -> str:
    """
    Calculate hash of the current environment.

    Covers what actually changes which packages get resolved/installed:
    Python implementation and version, OS family and CPU architecture.

    The OS release (``platform.release()``) is deliberately excluded: on Linux it is
    the *host kernel* version, which changes on every host update (and inside Docker
    it is the host's kernel, not the image's), needlessly wiping the cache.
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    implementation = platform.python_implementation()
    os_info = f"{platform.system()}-{platform.machine()}"
    env_content = f"python:{implementation}:{python_version}:os:{os_info}"
    return hashlib.sha256(env_content.encode()).hexdigest()


def calculate_module_hash(module_name: str, requirements_content: str) -> str:
    """Calculate hash of a specific module's requirements including environment."""
    env_hash = get_environment_hash()
    content = f"{module_name}:{requirements_content}:env:{env_hash}"
    return hashlib.sha256(content.encode()).hexdigest()


def calculate_combined_requirements_hash(module_hashes: Dict[str, str]) -> str:
    """Calculate hash of all module requirement hashes combined."""
    sorted_hashes = sorted(module_hashes.items())
    combined = "|".join(f"{name}:{hash}" for name, hash in sorted_hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


def calculate_files_hash(paths: Iterable[str]) -> str:
    """Hash the paths and contents of a set of files (missing files hash as empty)."""
    digest = hashlib.sha256()
    for path in sorted(os.path.abspath(p) for p in paths):
        digest.update(path.encode())
        try:
            with open(path, 'rb') as f:
                digest.update(f.read())
        except OSError:
            digest.update(b'<missing>')
    return digest.hexdigest()


def base_package_name(name: str) -> str:
    """Strip extras from a requirement name: ``passlib[bcrypt]`` -> ``passlib``."""
    return name.split('[', 1)[0].strip()


def get_installed_versions() -> Dict[str, str]:
    """Get currently installed packages as ``{canonical_name: version}``."""
    try:
        result = subprocess.run([
            sys.executable, '-m', 'pip', 'list', '--format=json', '--disable-pip-version-check'
        ], capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            default_logger.warning(f"Failed to get installed packages: {result.stderr}")
            return {}
        return {
            canonicalize_name(entry['name']): entry['version']
            for entry in json.loads(result.stdout or '[]')
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError,
            ValueError, KeyError, TypeError) as e:
        default_logger.warning(f"Error getting installed packages: {e}")
        return {}


def get_installed_packages() -> Set[str]:
    """Get set of currently installed packages (canonicalized names)."""
    return set(get_installed_versions())


def version_satisfies(installed_version: Optional[str], version_spec: str) -> bool:
    """
    Check whether an installed version satisfies a resolved pin.

    Only exact pins (``==X``) are compared; anything else (direct URLs, missing
    spec) is treated as satisfied when the package is present.
    """
    if installed_version is None:
        return False
    spec = (version_spec or '').strip()
    if not spec.startswith('=='):
        return True
    wanted = spec[2:].strip()
    if Version is not None:
        try:
            return Version(installed_version) == Version(wanted)
        except InvalidVersion:
            pass
    return installed_version == wanted


def version_sort_key(version: str):
    """Sort key for versions (PEP 440 when possible, otherwise numeric-ish string parts)."""
    if Version is not None:
        try:
            return (1, Version(version))
        except InvalidVersion:
            pass
    return (0, tuple(int(p) if p.isdigit() else -1 for p in re.split(r'[.\-+]', version)))


def major_version_key(version: str) -> Tuple:
    """
    The part of a version that signals breaking changes.

    ``2.31.0`` -> ``(2,)``. For ``0.x`` releases the minor number is breaking by
    semver convention, so ``0.9.1`` -> ``(0, 9)``.
    """
    if Version is not None:
        try:
            parsed = Version(version)
            return (parsed.major,) if parsed.major else (0, parsed.minor)
        except InvalidVersion:
            pass
    parts = version.split('.')
    return tuple(parts[:1]) if parts[0] != '0' else tuple(parts[:2])


def merge_with_version_policy(
    module_pins: List[Tuple[str, Dict[str, str]]],
) -> Tuple[Dict[str, str], List[Dict]]:
    """
    Merge per-module resolutions whose pins disagree.

    ``module_pins`` is ordered by precedence (existing modules first). For every
    package pinned differently by several modules:

    * same major version -> the latest requested version is used by everyone
    * different major    -> the major used by the highest-precedence module wins (the
      latest version within that major); modules wanting another major are skipped
      for that package and reported in ``skipped_modules``

    Returns ``(merged_pins, conflicts)``; each conflict is a dict with ``package``,
    ``chosen``, ``chosen_from``, ``requested`` ({module: version}), ``kind``
    ("minor" or "major") and ``skipped_modules``.
    """
    by_package: Dict[str, List[Tuple[str, str, str]]] = {}
    for module, pins in module_pins:
        for name, spec in pins.items():
            canonical = canonicalize_name(base_package_name(name))
            by_package.setdefault(canonical, []).append((module, name, spec))

    merged: Dict[str, str] = {}
    conflicts: List[Dict] = []
    for canonical, entries in by_package.items():
        first_module, first_name, first_spec = entries[0]
        specs = {spec for _, _, spec in entries}
        if len(specs) == 1:
            merged[first_name] = first_spec
            continue

        requested = {module: spec[2:] if spec.startswith('==') else spec.strip() for module, _, spec in entries}
        if not all(spec.startswith('==') for _, _, spec in entries):
            # Direct URLs can't be compared: the highest-precedence module wins.
            merged[first_name] = first_spec
            conflicts.append({
                'package': first_name, 'chosen': requested[first_module], 'chosen_from': first_module,
                'requested': requested, 'kind': 'major',
                'skipped_modules': [m for m, _, s in entries if s != first_spec],
            })
            continue

        winning_major = major_version_key(requested[first_module])
        same_major = [(m, v) for m, v in requested.items() if major_version_key(v) == winning_major]
        chosen_from, chosen = max(same_major, key=lambda item: version_sort_key(item[1]))
        skipped = [m for m, v in requested.items() if major_version_key(v) != winning_major]
        merged[first_name] = f"=={chosen}"
        conflicts.append({
            'package': first_name, 'chosen': chosen, 'chosen_from': chosen_from,
            'requested': requested, 'kind': 'major' if skipped else 'minor',
            'skipped_modules': skipped,
        })
    return merged, conflicts


def format_conflict(conflict: Dict) -> str:
    """Human-readable one-line description of a conflict from :func:`merge_with_version_policy`."""
    wants = ", ".join(f"{m} wants {v}" for m, v in conflict['requested'].items())
    if conflict['kind'] == 'minor':
        return (f"'{conflict['package']}': {wants}. Same major version, so the latest "
                f"({conflict['chosen']}) is used for all modules")
    skipped = ", ".join(conflict['skipped_modules'])
    return (f"'{conflict['package']}': {wants}. Major versions differ, so {conflict['chosen']} "
            f"(from {conflict['chosen_from']}) is kept and the requirement from {skipped} is skipped. "
            f"{skipped} may not work correctly")


def requirement_names(requirements_content: str) -> List[str]:
    """Package names declared in requirements text (options, comments and URLs-only lines skipped)."""
    names = []
    for line in requirements_content.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)', line)
        if match:
            names.append(match.group(1))
    return names


def parse_lock_file(text: str) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """
    Parse pip-compile output.

    Returns:
        pins: ``{package_name: version_spec}`` e.g. ``{"requests": "==2.31.0"}``
        via:  ``{canonical_name: {annotation, ...}}`` taken from ``# via`` comments,
              e.g. ``{"idna": {"requests", "-r /path/module.in"}}``
    """
    pins: Dict[str, str] = {}
    via: Dict[str, Set[str]] = {}
    current: Optional[str] = None

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        if stripped.startswith('#'):
            if current is None:
                continue
            body = stripped.lstrip('#').strip()
            if body.startswith('via'):
                body = body[3:].strip()
            if body:
                via[current].add(body)
            continue

        if raw[:1].isspace():
            # Continuation lines (e.g. --hash=...) belong to the previous requirement.
            continue

        current = None
        if stripped.startswith('-'):
            # Options such as --index-url / -e are not pins.
            continue

        requirement = stripped.split(';', 1)[0].rstrip('\\').strip()
        if ' @ ' in requirement:
            name, url = requirement.split(' @ ', 1)
            name = name.strip()
            pins[name] = f" @ {url.strip()}"
        elif '==' in requirement:
            name, version = requirement.split('==', 1)
            name = name.strip()
            version = version.strip().split()[0] if version.strip() else ''
            if not name or not version:
                continue
            pins[name] = f"=={version}"
        else:
            continue

        current = canonicalize_name(base_package_name(name))
        via.setdefault(current, set())

    return pins, via


def attribute_packages(
    pins: Dict[str, str],
    via: Dict[str, Set[str]],
    module_files: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    """
    Work out which resolved packages each module pulls in (directly or transitively).

    Args:
        pins: resolved pins from :func:`parse_lock_file`
        via: reverse-dependency annotations from :func:`parse_lock_file`
        module_files: ``{module_name: input_file_basename}`` passed to pip-compile
    """
    file_to_module = {basename: module for module, basename in module_files.items()}
    roots: Dict[str, Set[str]] = {module: set() for module in module_files}
    children: Dict[str, Set[str]] = {}

    for package, sources in via.items():
        for source in sources:
            if source.startswith('-r '):
                module = file_to_module.get(os.path.basename(source[3:].strip()))
                if module is not None:
                    roots[module].add(package)
            elif not source.startswith('-'):
                parent = canonicalize_name(base_package_name(source))
                children.setdefault(parent, set()).add(package)

    canonical_pins = {canonicalize_name(base_package_name(n)): n for n in pins}
    result: Dict[str, Dict[str, str]] = {}
    for module, direct in roots.items():
        seen: Set[str] = set()
        queue = deque(direct)
        while queue:
            package = queue.popleft()
            if package in seen:
                continue
            seen.add(package)
            queue.extend(children.get(package, ()))
        result[module] = {
            canonical_pins[p]: pins[canonical_pins[p]]
            for p in sorted(seen) if p in canonical_pins
        }
    return result
