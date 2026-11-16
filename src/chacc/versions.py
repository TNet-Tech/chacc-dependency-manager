"""
Version comparison and the conflict policy used when modules disagree on a package.

* :func:`version_satisfies` - does an installed version match a resolved pin?
* :func:`merge_with_version_policy` - merge per-module pins: same major -> latest,
  different major -> the earlier module wins and the newcomer is skipped and flagged
* :func:`format_conflict` - one-line, human-readable description of a conflict
"""

import re
from typing import Dict, List, Optional, Tuple

from .names import base_package_name, canonicalize_name

try:
    from packaging.version import InvalidVersion, Version
except ImportError:
    Version = None
    InvalidVersion = Exception


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
