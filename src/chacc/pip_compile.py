"""
One pip-compile run over a set of modules.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

from .errors import (
    FILE_ERROR_MARKERS, NETWORK_ERROR_MARKERS, ResolutionError, not_a_conflict, summarize_pip_error,
)
from .requirements import attribute_packages, included_files, parse_lock_file, pip_path

Pins = Dict[str, str]


def run_pip_compile(
    requirements: List[Tuple[str, str]],
    *,
    work_root: str,
    logger: logging.Logger,
    timeout: Optional[int],
    constraint_files: List[str],
    upgrade: bool = False,
    seed_pins: Optional[Pins] = None,
    upgrade_packages: Optional[List[str]] = None,
) -> Tuple[Pins, Dict[str, Pins]]:
    """
    Resolve all given modules together.

    Each module becomes its own ``.in`` file, so pip-compile's ``# via -r`` notes tell
    which module pulled in which package. ``seed_pins`` are written to the output
    file first, so pip-compile keeps those versions where it can. ``upgrade_packages``
    upgrades only those packages.

    Returns ``(all_pins, per_module_pins)``. Raises :class:`ResolutionError` on failure.
    """
    action = "Upgrading" if upgrade else "Resolving"
    non_empty = [(name, content) for name, content in requirements if content.strip()]
    if not non_empty:
        return {}, {name: {} for name, _ in requirements}

    missing = [p for p in constraint_files if not os.path.isfile(p)]
    if missing:
        raise not_a_conflict(f"Constraint file(s) not found: {', '.join(missing)}")

    names = ', '.join(name for name, _ in non_empty)
    logger.info(f"{action} dependencies for {len(non_empty)} module(s) together: {names}")
    if constraint_files:
        logger.info(f"Applying constraints from: {', '.join(constraint_files)}")

    work_dir = tempfile.mkdtemp(prefix="resolve-", dir=work_root)
    try:
        module_files, input_paths = _write_inputs(work_dir, non_empty, constraint_files)
        output_path = os.path.join(work_dir, "resolved.txt")
        if seed_pins and not upgrade:
            with open(output_path, 'w') as f:
                for package, spec in sorted(seed_pins.items()):
                    if spec.startswith('=='):
                        f.write(f"{package}{spec}\n")

        cmd = [
            sys.executable, '-m', 'piptools', 'compile',
            '--quiet', '--no-header', '--allow-unsafe', '--strip-extras',
            '--output-file', output_path,
        ]
        if upgrade:
            cmd.append('--upgrade')
        elif upgrade_packages:
            for package in sorted(set(upgrade_packages)):
                cmd.extend(['--upgrade-package', package])
        cmd.extend(input_paths)

        started = time.monotonic()
        _run(cmd, timeout, action, names, upgrade)

        with open(output_path, 'r') as f:
            pins, via = parse_lock_file(f.read())
        included: Dict[str, List[str]] = {}
        for name, content in non_empty:
            for path in included_files(content):
                included.setdefault(path, []).append(name)
        per_module = attribute_packages(pins, via, module_files, included)
        if len(non_empty) == 1:
            per_module[non_empty[0][0]] = dict(pins)
        for name, _ in requirements:
            per_module.setdefault(name, {})

        logger.info(f"Resolved {len(pins)} packages in {time.monotonic() - started:.1f}s")
        return pins, per_module
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_inputs(
    work_dir: str, modules: List[Tuple[str, str]], constraint_files: List[str],
) -> Tuple[Dict[str, str], List[str]]:
    """Write one ``.in`` file per module (plus the constraints). Returns ``({module: basename}, paths)``."""
    module_files: Dict[str, str] = {}
    input_paths = []
    for index, (name, content) in enumerate(modules):
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:60]
        basename = f"m{index:03d}-{safe}.in"
        path = os.path.join(work_dir, basename)
        with open(path, 'w') as f:
            f.write(content.rstrip() + "\n")
        module_files[name] = basename
        input_paths.append(path)

    if constraint_files:
        constraints_path = os.path.join(work_dir, "constraints.in")
        with open(constraints_path, 'w') as f:
            for constraint in constraint_files:
                f.write(f"-c {pip_path(constraint)}\n")
        input_paths.append(constraints_path)
    return module_files, input_paths


def _run(cmd: List[str], timeout: Optional[int], action: str, names: str, upgrade: bool):
    """Run pip-compile, turning every failure into a ResolutionError with pip's message."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise not_a_conflict(
            f"pip-compile timed out after {timeout}s while {action.lower()} "
            f"[{names}]; raise --resolve-timeout / resolve_timeout if this is expected"
        )
    except OSError as e:
        raise not_a_conflict(f"Could not run pip-compile: {e}")

    if result.returncode == 0:
        return
    raw = result.stderr or result.stdout or "unknown error"
    details = summarize_pip_error(raw)
    verb = "upgrade" if upgrade else "resolve"
    if any(marker in raw for marker in FILE_ERROR_MARKERS):
        raise not_a_conflict(
            f"Failed to {verb} dependencies for [{names}]: a requirements or constraints file "
            f"could not be opened (not a dependency conflict). Check that the path exists.\n{details}"
        )
    if any(marker in raw for marker in NETWORK_ERROR_MARKERS):
        raise not_a_conflict(
            f"Failed to {verb} dependencies for [{names}]: the package index could not be "
            f"reached (network or index problem, not a dependency conflict). "
            f"Nothing was changed; try again later.\n{details}"
        )
    raise ResolutionError(f"Failed to {verb} dependencies for [{names}]:\n{details}")
