"""
Reading requirements text and pip-compile output.

* naming modules after requirements files (:func:`module_name_for_path`)
* making ``-r``/``-c``/``-e``/local paths absolute, and finding included files
* parsing lock files and working out which module pulled in which package
"""

import os
import re
import shlex
from pathlib import PurePath
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .names import base_package_name, canonicalize_name


def module_name_for_path(path: str, short: bool = False) -> str:
    """
    Module name for a requirements file.

    ``short=True`` gives the filename without its extension (``requirements``), as in
    earlier versions. Otherwise files in the current directory get the short name and
    other files get their path relative to it, without the extension
    (``services/api/requirements``), so same-named files in different folders don't
    collide.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    if short:
        return stem
    try:
        relative = os.path.relpath(path)
    except ValueError:
        relative = os.path.abspath(path)
    directory = os.path.dirname(relative)
    if not directory or directory == os.curdir:
        return stem
    return os.path.join(directory, stem).replace(os.sep, '/')


_PATH_OPTIONS = ('-r', '--requirement', '-c', '--constraint', '-e', '--editable')


_INCLUDE_OPTIONS = ('-r', '--requirement', '-c', '--constraint')


def pip_path(path: str) -> str:
    """
    A local path written the way pip reads it on an option line (``-r``/``-c``/``-e``).

    pip splits option lines with ``shlex``, which treats ``\\`` as an escape character and
    splits on spaces. So a Windows path like ``C:\\Users\\me\\core.txt`` would turn into
    ``C:Usersmecore.txt``. Forward slashes (which Windows accepts) and shell quoting
    keep the path intact on every platform.
    """
    return shlex.quote(PurePath(path).as_posix())


def _is_local_path(target: str) -> bool:
    return bool(target) and '://' not in target and \
        not target.startswith(('git+', 'hg+', 'svn+', 'bzr+', 'file:'))


def _option_target(rest: str) -> Tuple[str, str]:
    """``"'/a b/x.txt'  # note"`` -> ``("/a b/x.txt", "  # note")``: the path, and what follows it."""
    if rest[:1] in ('"', "'"):
        end = rest.find(rest[0], 1)
        if end != -1:
            return rest[1:end], rest[end + 1:]
    target = rest.split()[0] if rest.split() else ''
    return target, rest[len(target):]


def _split_option(line: str) -> Optional[Tuple[str, str, str]]:
    """``-r base.txt  # x`` -> ``("-r", " ", "base.txt  # x")``, or None if not a path option."""
    for option in _PATH_OPTIONS:
        if line.startswith(option + '='):
            return option, '=', line[len(option) + 1:]
        if line.startswith(option) and line[len(option):len(option) + 1].isspace():
            rest = line[len(option):]
            return option, rest[:len(rest) - len(rest.lstrip())], rest.lstrip()
    return None


def absolutize_requirement_paths(requirements_content: str, base_dir: str) -> str:
    """
    Make relative paths in requirements text absolute, relative to ``base_dir``.

    ChaCC hands the text to pip-compile from a temporary folder, so ``-r base.txt``,
    ``-c constraints.txt``, ``-e ./libs/x`` or a bare ``./libs/x`` would otherwise point
    at the wrong place. Paths are resolved the way pip resolves them inside a
    requirements file: relative to the file's own folder. URLs, VCS links and absolute
    paths are left alone. Paths on option lines are written with :func:`pip_path`, so
    they also work on Windows and with spaces.
    """
    base_dir = os.path.abspath(base_dir)
    lines = []
    for raw in requirements_content.splitlines():
        stripped = raw.strip()
        option = _split_option(stripped)
        if option is not None:
            name, sep, rest = option
            target, tail = _option_target(rest)
            if _is_local_path(target):
                absolute = os.path.normpath(os.path.join(base_dir, target))
                raw = f"{name}{sep}{pip_path(absolute)}{tail}"
        elif stripped.startswith(('./', '../', '.\\', '..\\')) or stripped in ('.', '..'):
            target, _, tail = stripped.partition(' ')
            raw = os.path.normpath(os.path.join(base_dir, target)) + (f" {tail}" if tail else "")
        lines.append(raw)
    return "\n".join(lines)


def included_files(requirements_content: str, base_dir: Optional[str] = None) -> List[str]:
    """
    Local files pulled in with ``-r`` / ``-c``, followed recursively.

    Used so that editing an included file (e.g. a shared ``base.txt``) re-resolves the
    module. Relative paths are resolved against ``base_dir`` (default: current folder).
    """
    found: List[str] = []
    pending = [(requirements_content, os.path.abspath(base_dir or os.curdir))]
    while pending:
        content, folder = pending.pop()
        for raw in content.splitlines():
            option = _split_option(raw.strip())
            if option is None or option[0] not in _INCLUDE_OPTIONS:
                continue
            target, _ = _option_target(option[2])
            if not target or '://' in target:
                continue
            path = os.path.normpath(os.path.join(folder, target))
            if path in found:
                continue
            found.append(path)
            try:
                with open(path, 'r') as f:
                    pending.append((f.read(), os.path.dirname(path)))
            except OSError:
                pass
    return found


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
            continue

        current = None
        if stripped.startswith('-'):
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
    included: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Dict[str, str]]:
    """
    Work out which resolved packages each module pulls in (directly or transitively).

    Args:
        pins: resolved pins from :func:`parse_lock_file`
        via: reverse-dependency annotations from :func:`parse_lock_file`
        module_files: ``{module_name: input_file_basename}`` passed to pip-compile
        included: ``{absolute_path: [module_name, ...]}`` for files the modules include
            with ``-r`` (see :func:`included_files`). pip-compile credits packages to the
            included file (``# via -r base.txt``, relative to the current folder), so
            they are credited to every module that includes it.
    """
    file_to_module = {basename: module for module, basename in module_files.items()}
    included_by = {os.path.normpath(path): modules for path, modules in (included or {}).items()}
    roots: Dict[str, Set[str]] = {module: set() for module in module_files}
    children: Dict[str, Set[str]] = {}

    for package, sources in via.items():
        for source in sources:
            if source.startswith('-r '):
                path = source[3:].strip()
                module = file_to_module.get(os.path.basename(path))
                owners = [module] if module is not None else included_by.get(os.path.abspath(path), [])
                for owner in owners:
                    if owner in roots:
                        roots[owner].add(package)
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
