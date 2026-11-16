"""
Dependency Manager - Core functionality for managing Python package dependencies.

Provides intelligent, incremental dependency resolution and caching for modular applications.
"""

import asyncio
import errno
import functools
import glob
import json
import logging
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from typing import Callable, Dict, List, Mapping, Optional, Set, Tuple, Union

from .utils import (
    absolutize_requirement_paths,
    attribute_packages,
    base_package_name,
    calculate_combined_requirements_hash,
    calculate_files_hash,
    calculate_module_hash,
    canonicalize_name,
    default_logger,
    format_conflict,
    get_environment_hash,
    get_installed_versions,
    included_files,
    major_version_key,
    merge_with_version_policy,
    module_name_for_path,
    parse_lock_file,
    requirement_names,
    version_satisfies,
)


class ResolutionError(RuntimeError):
    """Raised when dependencies cannot be resolved. Nothing is written to the cache."""

    def __init__(self, message: str, conflicts: Optional[List[Dict]] = None, module: Optional[str] = None):
        super().__init__(message)
        #: Conflicts found between modules (see :func:`chacc.utils.merge_with_version_policy`)
        self.conflicts = conflicts or []
        #: Module that failed to resolve on its own, if any
        self.module = module


InstalledPackages = Union[Set[str], Mapping[str, str]]


def _not_a_conflict(message: str) -> ResolutionError:
    """A ResolutionError that resolving modules one by one can't help with (setup/network)."""
    error = ResolutionError(message)
    error._not_a_conflict = True
    return error


# pip messages that mean the package index couldn't be reached, not that versions clash.
_NETWORK_ERROR_MARKERS = (
    "NewConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ProxyError", "SSLError",
    "Max retries exceeded", "Temporary failure in name resolution", "Name or service not known",
    "Network is unreachable", "Connection refused", "Connection reset",
)

ON_CONFLICT_RESOLVE = "resolve"
ON_CONFLICT_FAIL = "fail"

_CONSTRAINTS_KEY = "__constraints__"

#: Name used for the core constraint files in conflict reports
CORE_CONSTRAINTS = "core constraints"

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None
try:  # Windows
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


class _CacheLock:
    """
    Serialises work on one cache directory across threads *and* processes.

    Threads in a process share a re-entrant lock. The outermost acquisition also takes
    an OS file lock on ``<cache_dir>/.lock`` (``flock`` on POSIX, ``msvcrt.locking`` on
    Windows), so several server workers or parallel CI jobs sharing a cache directory
    take turns instead of overwriting each other's cache. The file is opened per
    acquisition, so forked workers never share a lock by accident. If the file system
    doesn't support locking, ChaCC logs a warning and carries on with the thread lock.
    """

    def __init__(self, cache_dir: str):
        self._thread_lock = threading.RLock()
        self._path = os.path.join(cache_dir, ".lock")
        self._depth = 0
        self._fd: Optional[int] = None

    def __enter__(self) -> "_CacheLock":
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self._fd = self._lock_file()
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    if _fcntl is not None:
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                    elif _msvcrt is not None:
                        os.lseek(fd, 0, os.SEEK_SET)
                        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                os.close(fd)
        finally:
            self._thread_lock.release()

    def _lock_file(self) -> Optional[int]:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            default_logger.warning(f"Can't create the cache lock file ({e}); other processes are not coordinated")
            return None
        try:
            if self._try_lock(fd, blocking=False):
                return fd
            default_logger.info(f"Waiting for another process using the cache in {os.path.dirname(self._path)}...")
            self._try_lock(fd, blocking=True)
            return fd
        except OSError as e:
            default_logger.warning(f"File locking isn't supported here ({e}); other processes are not coordinated")
            os.close(fd)
            return None

    @staticmethod
    def _try_lock(fd: int, blocking: bool) -> bool:
        """Take the file lock. Returns False if it's held elsewhere (non-blocking only)."""
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | (0 if blocking else _fcntl.LOCK_NB))
                return True
            except OSError as e:
                if not blocking and e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    return False
                raise
        if _msvcrt is not None:
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    if not blocking:
                        return False
                    time.sleep(0.1)
        return True


# One lock per cache directory, shared by every DependencyManager pointing at it.
_CACHE_LOCKS: Dict[str, _CacheLock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


def _lock_for(cache_dir: str) -> _CacheLock:
    with _CACHE_LOCKS_GUARD:
        if cache_dir not in _CACHE_LOCKS:
            _CACHE_LOCKS[cache_dir] = _CacheLock(cache_dir)
        return _CACHE_LOCKS[cache_dir]


_PYTHON_EXCEPTION_LINE = re.compile(r'^[A-Za-z_]\w*(\.\w+)+: ')


def _summarize_pip_error(output: str, max_lines: int = 25) -> str:
    """Drop Python traceback blocks from pip/pip-compile output, keeping the actual messages."""
    lines = []
    in_traceback = False
    for line in output.strip().splitlines():
        if line.startswith("Traceback (most recent call last):"):
            in_traceback = True
            continue
        if in_traceback:
            if line[:1].isspace():
                continue
            in_traceback = False  # first unindented line is the exception message
        if _PYTHON_EXCEPTION_LINE.match(line):
            # e.g. "pip._internal.exceptions.DistributionNotFound: ...": repeats pip's own
            # ERROR line in Python terms.
            continue
        if line.startswith(("During handling of the above exception", "The above exception was")):
            continue
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines[-max_lines:]) or output.strip()[-2000:]


def _parse_log_level(level: Union[int, str]) -> int:
    """``logging.INFO`` / ``20`` / ``"info"`` -> ``20``."""
    if isinstance(level, bool) or not isinstance(level, (int, str)):
        raise ValueError(f"log_level must be a logging level number or name, not {level!r}")
    if isinstance(level, int):
        return level
    value = logging.getLevelName(level.strip().upper())
    if not isinstance(value, int):
        raise ValueError(f"Unknown log_level {level!r}; use DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return value


class DependencyManager:
    """
    Manages Python package dependencies for modular applications.

    Each resolve checks the passed modules together with every module registered by
    earlier calls, in one pip-compile run seeded with the previous pins so unchanged
    packages keep their versions. If modules turn out to be
    incompatible, each module is resolved on its own and the results are merged:

    * same major version  -> the latest requested version is used by everyone
    * different major     -> the existing module's version is kept; the newcomer's
      requirement for that package is skipped and reported

    Passing ``modules_requirements`` is additive: modules cached by earlier calls stay
    part of the set until :meth:`remove_module` is called. Auto-discovered requirement
    files are the full set: a deleted file is treated as a removed module.

    Args:
        cache_dir: Directory for dependency cache files (default: ".dependency_cache")
        logger: Logger instance to use (default: built-in logging)
        pre_resolve_hook: Optional callback called before dependency resolution
        post_resolve_hook: Optional callback called after dependency resolution
        install_hook: Optional callback for custom package installation logic. Receives
            the resolved pins (``{name: "==version"}``) and the set of installed package
            names (canonicalized), as in earlier versions. Return True if it installed
            everything, False to fall back to pip.
        install_timeout: Timeout in seconds for each pip install attempt (default: 300,
            None disables)
        max_retries: Maximum number of retry attempts for failed package batches (default: 5)
        resolve_timeout: Timeout in seconds for a pip-compile run (default: 600,
            None disables)
        constraint_files: Constraint files (pip ``-c`` format) applied to every
            resolution, e.g. your application's core/pinned packages. Modules can
            add packages but cannot move these versions.
        log_level: Logging level for ChaCC's messages, as a number (``logging.INFO``) or a
            name (``"INFO"``, ``"warning"``). Applied to ``logger`` (or ChaCC's own
            ``dependency_manager`` logger when none is passed, which affects every manager
            using it). Default: None, which leaves the level unchanged (DEBUG for the
            built-in logger, as in earlier versions).
        on_conflict: ``"resolve"`` (default) applies the version policy above;
            ``"fail"`` raises :class:`ResolutionError` describing the conflicts.

    Hooks run in a worker thread when the async methods are used.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        pre_resolve_hook: Optional[Callable[[str, str], None]] = None,
        post_resolve_hook: Optional[Callable[[str, Dict[str, str]], None]] = None,
        install_hook: Optional[Callable[[Dict[str, str], Set[str]], bool]] = None,
        install_timeout: Optional[int] = 300,
        max_retries: int = 5,
        resolve_timeout: Optional[int] = 600,
        constraint_files: Optional[List[str]] = None,
        on_conflict: str = ON_CONFLICT_RESOLVE,
        log_level: Optional[Union[int, str]] = None,
    ):
        """Initialize the dependency manager with configurable paths and hooks."""
        if on_conflict not in (ON_CONFLICT_RESOLVE, ON_CONFLICT_FAIL):
            raise ValueError(f"on_conflict must be '{ON_CONFLICT_RESOLVE}' or '{ON_CONFLICT_FAIL}'")

        self.cache_dir = os.path.abspath(cache_dir or ".dependency_cache")
        self.cache_file = os.path.join(self.cache_dir, "dependency_cache.json")

        self.logger = logger or default_logger
        if log_level is not None:
            self.logger.setLevel(_parse_log_level(log_level))

        self.pre_resolve_hook = pre_resolve_hook
        self.post_resolve_hook = post_resolve_hook
        self.install_hook = install_hook
        self.install_timeout = install_timeout or None
        self.max_retries = max(1, max_retries)
        self.resolve_timeout = resolve_timeout or None
        self.constraint_files = [os.path.abspath(p) for p in (constraint_files or [])]
        self.on_conflict = on_conflict

        self.last_conflicts: List[Dict] = []

        self._lock = _lock_for(self.cache_dir)

        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _empty_cache() -> Dict:
        return {
            'requirements_caches': {},
            'backbone_hash': None,
            'combined_hash': None,
            'environment_hash': None,
            'resolved_packages': {},
            'conflicts': [],
            'last_updated': None
        }

    def load_cache(self) -> Dict:
        """Load dependency cache from file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                for key, value in self._empty_cache().items():
                    cache.setdefault(key, value)
                return cache
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Failed to load dependency cache: {e}")
        return self._empty_cache()

    def save_cache(self, cache_data: Dict):
        """Save dependency cache to file (atomically, so a crash can't leave half a file)."""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp_file = f"{self.cache_file}.tmp"
            with open(tmp_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            os.replace(tmp_file, self.cache_file)
        except IOError as e:
            self.logger.error(f"Failed to save dependency cache: {e}")

    def invalidate_cache(self):
        """Invalidate the entire dependency cache."""
        try:
            self.save_cache(self._empty_cache())
            self.logger.info("Dependency cache invalidated")
        except Exception as e:
            self.logger.warning(f"Failed to invalidate dependency cache: {e}")
            if os.path.exists(self.cache_file):
                try:
                    os.remove(self.cache_file)
                    self.logger.info("Dependency cache file removed")
                except IOError as e2:
                    self.logger.error(f"Failed to remove dependency cache file: {e2}")

    def _reset_for_new_environment(self, cache: Dict) -> Dict:
        """
        Drop everything resolved for the old environment (Python version, OS, CPU) but
        keep the registered modules, so modules added one call at a time aren't lost.
        """
        if cache.get('environment_hash') in (None, get_environment_hash()):
            return cache
        self.logger.info("Environment changed (Python version/OS/architecture), re-resolving every module")
        fresh = self._empty_cache()
        for name, entry in cache.get('requirements_caches', {}).items():
            if 'requirements' in entry:
                fresh['requirements_caches'][name] = {
                    'hash': None,
                    'requirements': entry['requirements'],
                    'packages': {},
                    'complete': False,
                    'order': entry.get('order', 0),
                    'source': entry.get('source', 'explicit'),
                    'skipped_packages': [],
                    'last_updated': entry.get('last_updated'),
                }
        return fresh

    def invalidate_module_cache(self, module_name: str):
        """Force a module to be re-resolved on the next run (it stays registered)."""
        with self._lock:
            try:
                cache = self.load_cache()
                entry = cache.get('requirements_caches', {}).get(module_name)
                if entry is not None:
                    entry['hash'] = None
                    entry['complete'] = False
                    cache['combined_hash'] = None
                    self.save_cache(cache)
                    self.logger.info(f"Cache invalidated for module: {module_name}")
            except Exception as e:
                self.logger.warning(f"Failed to invalidate cache for module {module_name}: {e}")

    def remove_module(self, module_name: str) -> bool:
        """
        Unregister a module. Its pins are dropped on the next resolve.

        Packages are not uninstalled. Returns False if the module wasn't cached.
        """
        with self._lock:
            cache = self.load_cache()
            if module_name not in cache.get('requirements_caches', {}):
                return False
            del cache['requirements_caches'][module_name]
            cache['combined_hash'] = None
            self.save_cache(cache)
            self.logger.info(f"Module removed: {module_name} (its pins are dropped on the next resolve)")
            return True

    def _discover_requirements(
        self,
        requirements_file_pattern: str,
        search_dirs: Optional[List[str]],
    ) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        seen_paths: Set[str] = set()
        search_dirs = search_dirs or ["."]
        for base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue
            for req_file in sorted(glob.glob(os.path.join(base_dir, requirements_file_pattern))):
                if os.path.abspath(req_file) in seen_paths:
                    continue
                seen_paths.add(os.path.abspath(req_file))
                try:
                    with open(req_file, 'r') as f:
                        req_content = f.read().strip()
                    if req_content:
                        # Paths inside the file are relative to the file, as pip reads them.
                        req_content = absolutize_requirement_paths(req_content, os.path.dirname(req_file))
                        found.append((req_file, req_content))
                        self.logger.info(f"Discovered requirements file: {req_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to read requirements file {req_file}: {e}")

        if not found:
            self.logger.info(
                f"No requirements files found matching pattern: {requirements_file_pattern} in {search_dirs}"
            )
        # Modules are named after the file, as in earlier versions. Files that share a
        # name (e.g. several requirements.txt in different folders) get their relative
        # path instead, so none of them is dropped.
        short_names = [module_name_for_path(path, short=True) for path, _ in found]
        return [
            (short if short_names.count(short) == 1 else module_name_for_path(path), content)
            for short, (path, content) in zip(short_names, found)
        ]

    def _effective_requirements(
        self,
        req_caches: Dict[str, Dict],
        modules_requirements: Optional[Dict[str, str]],
        requirements_file_pattern: str,
        search_dirs: Optional[List[str]],
        replace: bool,
        prune: bool = True,
    ) -> Tuple[List[Tuple[str, str]], Dict[str, Dict]]:
        """
        Work out the full set of modules to resolve, in precedence order.

        * explicit modules are added to the registered ones (``replace=True``: they are
          the complete set)
        * discovered files are the complete set of discovered modules, so a deleted
          file counts as removed. Explicit modules are kept. With ``prune=False``
          (upgrades), or when no file is found at all, no registered module is dropped.

        Returns ``(requirements, meta)`` where ``meta[name]`` holds ``order`` and ``source``.
        """
        registered = {n: e for n, e in req_caches.items() if 'requirements' in e}
        if modules_requirements:
            # Relative paths in passed-in text (-r/-c/-e/./local) are relative to the
            # current folder; they're made absolute because pip-compile runs elsewhere.
            incoming = [(n, absolutize_requirement_paths(c, os.getcwd())) for n, c in modules_requirements.items()]
            source = 'explicit'
            keep = {} if replace else {n: e for n, e in registered.items() if n not in modules_requirements}
        else:
            incoming, source = self._discover_requirements(requirements_file_pattern, search_dirs), 'discovered'
            names = {n for n, _ in incoming}
            if not incoming and registered:
                # Most likely run from the wrong folder: don't drop what's registered.
                self.logger.info(f"Using the {len(registered)} registered module(s)")
            keep = {
                n: e for n, e in registered.items()
                if n not in names and (not prune or not incoming or e.get('source') == 'explicit')
            }

        next_order = max([e.get('order', 0) for e in req_caches.values()] + [0]) + 1
        meta: Dict[str, Dict] = {}
        contents: Dict[str, str] = {}
        for name, entry in keep.items():
            contents[name] = entry['requirements']
            meta[name] = {'order': entry.get('order', 0), 'source': entry.get('source', 'explicit')}
        for name, content in incoming:
            existing = req_caches.get(name, {})
            order = existing.get('order')
            if order is None:
                order, next_order = next_order, next_order + 1
            contents[name] = content
            meta[name] = {'order': order, 'source': source}

        ordered = sorted(contents, key=lambda n: (meta[n]['order'], n))
        return [(name, contents[name]) for name in ordered], meta

    @staticmethod
    def _module_hash(name: str, content: str) -> str:
        # Files included with -r/-c count as part of the module: editing a shared
        # base.txt must re-resolve every module that includes it.
        files = included_files(content)
        if files:
            content = f"{content}\n#included:{calculate_files_hash(files)}"
        return calculate_module_hash(name, content)

    def _requirement_hashes(self, requirements: List[Tuple[str, str]]) -> Tuple[Dict[str, str], str]:
        module_hashes = {name: self._module_hash(name, content) for name, content in requirements}
        hashes_for_combined = dict(module_hashes)
        if self.constraint_files:
            hashes_for_combined[_CONSTRAINTS_KEY] = calculate_files_hash(self.constraint_files)
        return module_hashes, calculate_combined_requirements_hash(hashes_for_combined)

    def _run_pre_hooks(self, requirements: List[Tuple[str, str]]):
        if not self.pre_resolve_hook:
            return
        for name, content in requirements:
            if content.strip():
                try:
                    self.pre_resolve_hook(name, content)
                except Exception as e:
                    self.logger.warning(f"Pre-resolve hook failed: {e}")

    def _run_post_hooks(self, per_module: Dict[str, Dict[str, str]]):
        if not self.post_resolve_hook:
            return
        for name, packages in per_module.items():
            try:
                self.post_resolve_hook(name, packages)
            except Exception as e:
                self.logger.warning(f"Post-resolve hook failed: {e}")

    def _compile(
        self,
        requirements: List[Tuple[str, str]],
        upgrade: bool = False,
        seed_pins: Optional[Dict[str, str]] = None,
        use_constraints: bool = True,
        upgrade_packages: Optional[List[str]] = None,
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        """
        Run pip-compile once over all given modules.

        ``upgrade_packages`` upgrades only those packages, keeping every other seeded pin.

        Returns ``(all_pins, per_module_pins)``. Raises :class:`ResolutionError` on failure.
        """
        action = "Upgrading" if upgrade else "Resolving"
        non_empty = [(name, content) for name, content in requirements if content.strip()]
        if not non_empty:
            return {}, {name: {} for name, _ in requirements}

        constraint_files = self.constraint_files if use_constraints else []
        missing = [p for p in constraint_files if not os.path.isfile(p)]
        if missing:
            raise _not_a_conflict(f"Constraint file(s) not found: {', '.join(missing)}")

        names = ', '.join(name for name, _ in non_empty)
        self.logger.info(f"{action} dependencies for {len(non_empty)} module(s) together: {names}")
        if constraint_files:
            self.logger.info(f"Applying constraints from: {', '.join(self.constraint_files)}")

        work_dir = tempfile.mkdtemp(prefix="resolve-", dir=self.cache_dir)
        try:
            module_files: Dict[str, str] = {}
            input_paths = []
            for index, (name, content) in enumerate(non_empty):
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
                        f.write(f"-c {constraint}\n")
                input_paths.append(constraints_path)

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
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.resolve_timeout)
            except subprocess.TimeoutExpired:
                raise _not_a_conflict(
                    f"pip-compile timed out after {self.resolve_timeout}s while {action.lower()} "
                    f"[{names}]; raise --resolve-timeout / resolve_timeout if this is expected"
                )
            except OSError as e:
                raise _not_a_conflict(f"Could not run pip-compile: {e}")

            if result.returncode != 0:
                raw = result.stderr or result.stdout or "unknown error"
                details = _summarize_pip_error(raw)
                verb = "upgrade" if upgrade else "resolve"
                if any(marker in raw for marker in _NETWORK_ERROR_MARKERS):
                    raise _not_a_conflict(
                        f"Failed to {verb} dependencies for [{names}]: the package index could not be "
                        f"reached (network or index problem, not a dependency conflict). "
                        f"Nothing was changed; try again later.\n{details}"
                    )
                raise ResolutionError(f"Failed to {verb} dependencies for [{names}]:\n{details}")

            with open(output_path, 'r') as f:
                pins, via = parse_lock_file(f.read())
            per_module = attribute_packages(pins, via, module_files)
            if len(non_empty) == 1:
                per_module[non_empty[0][0]] = dict(pins)
            for name, _ in requirements:
                per_module.setdefault(name, {})

            self.logger.info(f"Resolved {len(pins)} packages in {time.monotonic() - started:.1f}s")
            return pins, per_module
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _resolve_set(
        self,
        requirements: List[Tuple[str, str]],
        upgrade: bool,
        seed_pins: Optional[Dict[str, str]],
        req_caches: Dict[str, Dict],
        changed: List[str],
        upgrade_packages: Optional[List[str]] = None,
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]], List[Dict]]:
        """
        Resolve the module set, falling back to per-module resolution plus the version
        policy when the modules can't all be satisfied together.

        Returns ``(pins, per_module, conflicts)``.
        """
        self._run_pre_hooks(requirements)
        try:
            pins, per_module = self._compile(
                requirements, upgrade=upgrade, seed_pins=seed_pins, upgrade_packages=upgrade_packages
            )
            self._run_post_hooks({n: per_module[n] for n, c in requirements if c.strip()})
            return pins, per_module, []
        except ResolutionError as combined_error:
            non_empty = [(n, c) for n, c in requirements if c.strip()]
            message = str(combined_error)
            if getattr(combined_error, '_not_a_conflict', False):
                raise
            if len(non_empty) < 2 and not self.constraint_files:
                raise
            self.logger.warning(
                "Modules cannot all be satisfied together; resolving each module on its own "
                "to find the conflict..."
            )
            self.logger.debug(message)

        per_module: Dict[str, Dict[str, str]] = {n: {} for n, _ in requirements}
        module_pins: List[Tuple[str, Dict[str, str]]] = []
        for name, content in non_empty:
            entry = req_caches.get(name, {})
            reusable = (not upgrade and name not in changed and entry.get('complete')
                        and entry.get('packages'))
            if reusable:
                pins_for_module = dict(entry['packages'])
            else:
                pins_for_module = self._resolve_alone(
                    name, content, upgrade, entry.get('packages'), upgrade_packages
                )
            per_module[name] = pins_for_module
            module_pins.append((name, pins_for_module))

        core_pins = self._constraint_pins()
        if core_pins:
            used = {canonicalize_name(base_package_name(p)) for _, pins in module_pins for p in pins}
            core_pins = {p: s for p, s in core_pins.items()
                         if canonicalize_name(base_package_name(p)) in used}
            # Core constraints take precedence over every module.
            module_pins.insert(0, (CORE_CONSTRAINTS, core_pins))

        merged, conflicts = merge_with_version_policy(module_pins)
        self._enforce_core_pins(merged, conflicts)
        if not conflicts:
            raise ResolutionError(
                "Modules resolve individually but not together, and no differing versions were "
                "found to reconcile. Check the constraint files."
            )

        for conflict in conflicts:
            log = self.logger.warning if conflict['kind'] == 'major' else self.logger.info
            log(f"{'⚠️ ' if conflict['kind'] == 'major' else '↗ '} Dependency conflict on {format_conflict(conflict)}")

        if self.on_conflict == ON_CONFLICT_FAIL:
            raise ResolutionError(
                "Conflicting module dependencies:\n" + "\n".join(f"  - {format_conflict(c)}" for c in conflicts),
                conflicts=conflicts,
            )

        self._run_post_hooks({n: per_module[n] for n, _ in non_empty})
        return merged, per_module, conflicts

    def _resolve_alone(
        self, name: str, content: str, upgrade: bool, seed: Optional[Dict[str, str]],
        upgrade_packages: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Resolve one module by itself, retrying without core constraints if they are the problem."""
        try:
            pins, _ = self._compile([(name, content)], upgrade=upgrade, seed_pins=seed,
                                    upgrade_packages=upgrade_packages)
            return pins
        except ResolutionError as with_constraints:
            if getattr(with_constraints, '_not_a_conflict', False):
                raise
            if not self.constraint_files:
                raise ResolutionError(
                    f"Module '{name}' cannot be resolved even on its own, so this is not a "
                    f"conflict between modules. Fix its requirements:\n{with_constraints}",
                    module=name,
                ) from with_constraints
        try:
            pins, _ = self._compile([(name, content)], upgrade=upgrade, seed_pins=seed,
                                    use_constraints=False, upgrade_packages=upgrade_packages)
        except ResolutionError as e:
            if getattr(e, '_not_a_conflict', False):
                raise
            raise ResolutionError(
                f"Module '{name}' cannot be resolved even on its own, so this is not a "
                f"conflict between modules. Fix its requirements:\n{e}",
                module=name,
            ) from e
        self.logger.warning(f"Module '{name}' only resolves without the core constraints")
        return pins

    def _constraint_pins(self) -> Dict[str, str]:
        """Exact (``==``) pins from the constraint files."""
        pins: Dict[str, str] = {}
        for path in self.constraint_files:
            try:
                with open(path, 'r') as f:
                    file_pins, _ = parse_lock_file(f.read())
            except OSError:
                continue
            pins.update({n: s for n, s in file_pins.items() if s.startswith('==')})
        return pins

    @staticmethod
    def _enforce_core_pins(merged: Dict[str, str], conflicts: List[Dict]):
        """Core constraints are hard pins: their version wins even within the same major."""
        for conflict in conflicts:
            core_version = conflict['requested'].get(CORE_CONSTRAINTS)
            if core_version is None:
                continue
            core_major = major_version_key(core_version)
            conflict['chosen'] = core_version
            conflict['chosen_from'] = CORE_CONSTRAINTS
            conflict['skipped_modules'] = [
                m for m, v in conflict['requested'].items()
                if m != CORE_CONSTRAINTS and major_version_key(v) != core_major
            ]
            conflict['kind'] = 'major' if conflict['skipped_modules'] else 'minor'
            for name in list(merged):
                if canonicalize_name(base_package_name(name)) == canonicalize_name(conflict['package']):
                    merged[name] = f"=={core_version}"

    def resolve_module_dependencies(
        self,
        module_name: str,
        requirements_content: str,
        upgrade: bool = False,
        raise_on_error: bool = False,
    ) -> Dict[str, str]:
        """
        Resolve dependencies for a single module in isolation (nothing is cached).

        As in earlier versions, a failure is logged and ``{}`` is returned. Pass
        ``raise_on_error=True`` to get a :class:`ResolutionError` instead, which lets you
        tell a failure apart from "no dependencies".
        """
        requirements_content = absolutize_requirement_paths(requirements_content, os.getcwd())
        self._run_pre_hooks([(module_name, requirements_content)])
        try:
            pins, _ = self._compile([(module_name, requirements_content)], upgrade=upgrade)
        except ResolutionError as e:
            if raise_on_error:
                raise
            self.logger.error(str(e))
            return {}
        self._run_post_hooks({module_name: pins})
        return pins

    def merge_resolved_packages(self, *package_dicts: Dict[str, str]) -> Dict[str, str]:
        """
        Merge multiple resolved package dictionaries ("last wins" on conflict).

        Kept for backward compatibility. The resolver no longer uses this; see
        :func:`chacc.utils.merge_with_version_policy` for the policy it applies.
        """
        merged = {}

        for package_dict in package_dicts:
            for package_name, version_spec in package_dict.items():
                if package_name in merged:
                    existing_version = merged[package_name]
                    if version_spec != existing_version:
                        self.logger.warning(
                            f"Version conflict for {package_name}: {existing_version} vs {version_spec}, "
                            f"using {version_spec}"
                        )
                merged[package_name] = version_spec

        return merged

    # ---------------------------------------------------------- installation

    @staticmethod
    def packages_needing_install(
        resolved_packages: Dict[str, str],
        installed_packages: InstalledPackages,
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Compare resolved pins with what is installed.

        ``installed_packages`` may be a ``{canonical_name: version}`` mapping (version
        aware) or, for backward compatibility, a set of canonical names (presence only).

        Returns ``(missing, wrong_version, satisfied)`` as lists of ``name<spec>`` /
        ``name`` strings.
        """
        version_aware = isinstance(installed_packages, Mapping)
        missing, wrong_version, satisfied = [], [], []
        for package_name, version_spec in resolved_packages.items():
            canonical = canonicalize_name(base_package_name(package_name))
            if canonical not in installed_packages:
                missing.append(f"{package_name}{version_spec}")
            elif version_aware and not version_satisfies(installed_packages[canonical], version_spec):
                wrong_version.append(f"{package_name}{version_spec}")
            else:
                satisfied.append(package_name)
        return missing, wrong_version, satisfied

    def _log_install_target(self):
        """Log where pip will install, and warn if it will silently fall back to --user."""
        purelib = sysconfig.get_paths().get('purelib', '')
        in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
        self.logger.info(
            f"Install target: {purelib} (interpreter: {sys.executable}"
            f"{', virtualenv' if in_venv else ''})"
        )
        check_dir = purelib if os.path.isdir(purelib) else os.path.dirname(purelib)
        if not in_venv and check_dir and not os.access(check_dir, os.W_OK):
            try:
                user_site = site.getusersitepackages()
            except Exception:
                user_site = "the user site-packages directory"
            self.logger.warning(
                f"{purelib} is not writable: pip will fall back to a user install in {user_site}. "
                "Those packages are only visible to this user and may be hidden from other "
                "processes/services. Use a virtualenv or a writable environment to avoid this."
            )

    def _run_pip_install(self, batch: List[str], no_deps: bool = False):
        last_error = None
        base_cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check']
        if no_deps:
            base_cmd.append('--no-deps')
        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    base_cmd + batch, capture_output=True, text=True, timeout=self.install_timeout
                )
                output = f"{result.stdout or ''}\n{result.stderr or ''}"
                for line in (result.stdout or '').splitlines():
                    if line.startswith('Successfully installed'):
                        self.logger.debug(line.strip())
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(
                        result.returncode, result.args, result.stdout, result.stderr
                    )
                if "Defaulting to user installation" in output:
                    self.logger.warning(
                        "pip installed into the user site-packages "
                        f"({site.getusersitepackages()}) because the environment is not writable"
                    )
                return
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {self.install_timeout}s"
                self.logger.warning(
                    f"pip install attempt {attempt}/{self.max_retries} timed out for {len(batch)} packages"
                )
            except subprocess.CalledProcessError as e:
                last_error = e.stderr or "unknown error"
                self.logger.warning(
                    f"pip install attempt {attempt}/{self.max_retries} failed for {len(batch)} packages"
                )

            if attempt < self.max_retries:
                backoff = min(2 ** (attempt - 1), 30)
                self.logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        self.logger.error(f"Failed to install package batch after {self.max_retries} attempts: {last_error}")
        raise subprocess.CalledProcessError(1, [], None, last_error)

    def install_missing_packages(
        self,
        resolved_packages: Dict[str, str],
        installed_packages: InstalledPackages,
        no_deps: bool = False,
    ):
        """
        Install packages that are missing or installed at a different version.

        Pass ``installed_packages`` as ``{canonical_name: version}`` (see
        :func:`chacc.utils.get_installed_versions`) for version-aware installs. A plain
        set of names is still accepted but only checks presence.

        ``no_deps`` installs the pins exactly as given (used after a conflict merge,
        where the pins intentionally break some declared requirements).
        """
        if self.install_hook:
            try:
                # Hooks receive a set of canonical names, as in earlier versions.
                success = self.install_hook(resolved_packages, set(installed_packages))
                if success:
                    self.logger.info("Custom installation hook completed successfully")
                    return
                else:
                    self.logger.warning("Custom installation hook failed, falling back to default")
            except Exception as e:
                self.logger.warning(f"Custom installation hook failed: {e}, falling back to default")

        missing, wrong_version, satisfied = self.packages_needing_install(resolved_packages, installed_packages)

        if satisfied:
            self.logger.info("Skipped Packages:")
            for package_name in satisfied:
                self.logger.info(f"     ✓ {package_name}")

        packages_to_install = missing + wrong_version
        if not packages_to_install:
            self.logger.info("All required packages are already installed")
            return

        self.logger.info(
            f"Installing {len(packages_to_install)} packages "
            f"({len(missing)} missing, {len(wrong_version)} version changes)..."
        )
        for package_name in missing:
            self.logger.info(f"     + {package_name}")
        for package_name in wrong_version:
            self.logger.info(f"     ↻ {package_name}")

        self._log_install_target()
        batch_size = 50
        for i in range(0, len(packages_to_install), batch_size):
            self._run_pip_install(packages_to_install[i:i + batch_size], no_deps=no_deps)

        self.logger.info("Package installation completed successfully")

    def _report_broken_requirements(self, pins: Dict[str, str]):
        """After a conflict merge, log which declared requirements are now unmet (pip check)."""
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'check', '--disable-pip-version-check'],
                capture_output=True, text=True, timeout=120
            )
        except (subprocess.TimeoutExpired, OSError):
            return
        ours = {canonicalize_name(base_package_name(n)) for n in pins}
        for line in (result.stdout or '').splitlines():
            words = line.split()
            if words and canonicalize_name(words[0]) in ours:
                self.logger.warning(f"Unmet requirement after conflict resolution: {line.strip()}")

    # --------------------------------------------------------- orchestration

    def _build_cache(
        self,
        requirements: List[Tuple[str, str]],
        meta: Dict[str, Dict],
        module_hashes: Dict[str, str],
        combined_hash: str,
        pins: Dict[str, str],
        per_module: Dict[str, Dict[str, str]],
        conflicts: List[Dict],
        backbone_hash: Optional[str] = None,
    ) -> Dict:
        now = str(time.time())
        skipped: Dict[str, List[str]] = {}
        for conflict in conflicts:
            for module in conflict['skipped_modules']:
                skipped.setdefault(module, []).append(conflict['package'])
        caches = {}
        for name, content in requirements:
            packages = per_module.get(name, {})
            caches[name] = {
                'hash': module_hashes[name],
                'requirements': content,
                'packages': packages,
                'complete': bool(packages) or not content.strip(),
                'order': meta[name]['order'],
                'source': meta[name]['source'],
                'skipped_packages': skipped.get(name, []),
                'last_updated': now,
            }
        return {
            'requirements_caches': caches,
            'backbone_hash': backbone_hash,
            'combined_hash': combined_hash,
            'environment_hash': get_environment_hash(),
            'resolved_packages': pins,
            'conflicts': conflicts,
            'last_updated': now,
        }

    def _install(self, pins: Dict[str, str], no_deps: bool):
        installed = get_installed_versions()
        self.logger.debug(f"Found {len(installed)} installed packages")
        missing, wrong, _ = self.packages_needing_install(pins, installed)
        if not (missing or wrong):
            self.logger.info("🎉 All dependencies are installed at the resolved versions")
            return
        self.install_missing_packages(pins, installed, no_deps=no_deps)
        if no_deps:
            self._report_broken_requirements(pins)
        self.logger.info("🚀 Dependencies resolved and installed successfully")

    def resolve_dependencies_sync(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None,
        install: bool = True,
        replace: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, str]:
        """
        Blocking version of :meth:`resolve_dependencies`. Returns the resolved pins.

        1. Builds the module set: ``modules_requirements`` is added to the modules already
           cached (``replace=True`` makes it the full set); auto-discovered files are the
           full set of discovered modules
        2. If anything was added, changed or removed (or constraints changed), re-resolves
           all modules together, seeded with the previous pins
        3. If they conflict, applies the version policy (see the class docstring)
        4. Saves the cache only after a successful resolution
        5. Installs packages that are missing or at the wrong version (unless install=False)

        ``dry_run=True`` resolves and returns the pins without saving anything to the
        cache (so the passed modules are not registered) and without installing.
        """
        with self._lock:
            self.logger.info("Starting incremental dependency resolution...")
            try:
                cache = self._reset_for_new_environment(self.load_cache())

                req_caches = cache.get('requirements_caches', {})
                requirements, meta = self._effective_requirements(
                    req_caches, modules_requirements, requirements_file_pattern, search_dirs, replace
                )
                if not requirements:
                    return {}

                cached_pins = cache.get('resolved_packages', {}) or {}
                module_hashes, combined_hash = self._requirement_hashes(requirements)
                changed = [n for n, h in module_hashes.items() if req_caches.get(n, {}).get('hash') != h]
                removed = [n for n in req_caches if n not in module_hashes]
                needs_resolution = bool(
                    changed or removed or not cached_pins or cache.get('combined_hash') != combined_hash
                )

                if needs_resolution:
                    if not cached_pins and not req_caches:
                        self.logger.info("📦 Cache miss: No cached data found, performing full dependency resolution")
                    else:
                        if changed:
                            self.logger.info(f"🔄 Requirements added or changed: {', '.join(changed)}")
                        if removed:
                            self.logger.info(f"🧹 Requirements removed, dropping their pins: {', '.join(removed)}")
                        if not changed and not removed:
                            self.logger.info("🔄 Constraints or cache state changed")
                        self.logger.info(f"Re-resolving all {len(requirements)} module(s) together")

                    pins, per_module, conflicts = self._resolve_set(
                        requirements, upgrade=False, seed_pins=cached_pins,
                        req_caches=req_caches, changed=changed,
                    )
                    if dry_run:
                        self.logger.info("Dry run: the cache was not changed")
                    else:
                        self.save_cache(self._build_cache(
                            requirements, meta, module_hashes, combined_hash, pins, per_module,
                            conflicts, backbone_hash=cache.get('backbone_hash'),
                        ))
                        self.logger.info("💾 Cache updated: New dependency resolution results saved")
                else:
                    pins = cached_pins
                    conflicts = cache.get('conflicts', [])
                    self.logger.info("✅ Cache hit: requirements unchanged, using cached resolution")
                    for conflict in conflicts:
                        if conflict['kind'] == 'major':
                            self.logger.warning(f"⚠️  Unresolved major-version conflict on {format_conflict(conflict)}")

                self.last_conflicts = conflicts
                if install and not dry_run:
                    self._install(pins, no_deps=bool(conflicts))
                return pins

            except Exception as e:
                self.logger.error(f"Error during dependency resolution: {e}",
                                  exc_info=not isinstance(e, ResolutionError))
                raise

    async def resolve_dependencies(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None,
        install: bool = True,
        replace: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, str]:
        """
        Perform incremental dependency resolution for requirements.

        The blocking pip/pip-compile work runs in a worker thread, so awaiting this
        does not freeze the event loop (e.g. an async web server). Concurrent calls for
        the same cache directory are serialised.

        Args:
            modules_requirements: Dict of name -> requirements_content, added to the
                                  modules already cached. If None, auto-discovers from filesystem
            requirements_file_pattern: Glob pattern for requirements files (default: "requirements.txt")
            search_dirs: List of directories to search for requirements files (default: current dir)
            install: Install missing/outdated packages after resolving (default: True)
            replace: Treat ``modules_requirements`` as the complete module set, dropping
                     other cached modules (default: False)
            dry_run: Resolve and return the pins without saving the cache or installing
                     (default: False)

        Raises:
            ResolutionError: resolution failed (cache left untouched)
            subprocess.CalledProcessError: pip install failed after all retries
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(
            self.resolve_dependencies_sync,
            modules_requirements, requirements_file_pattern, search_dirs, install, replace, dry_run
        ))

    def upgrade_dependencies_sync(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Blocking version of :meth:`upgrade_dependencies`. Returns the upgraded pins."""
        with self._lock:
            self.logger.info("Starting dependency upgrade...")
            try:
                cache = self._reset_for_new_environment(self.load_cache())
                req_caches = cache.get('requirements_caches', {})
                # Upgrading never unregisters modules, even ones not discovered this time.
                requirements, meta = self._effective_requirements(
                    req_caches, modules_requirements, requirements_file_pattern, search_dirs,
                    replace=False, prune=False,
                )
                if not requirements:
                    return {}

                module_hashes, combined_hash = self._requirement_hashes(requirements)
                # The existing cache is only replaced once the upgrade resolves successfully.
                if modules_requirements:
                    # Selective upgrade (as in earlier versions): only the given modules'
                    # packages move; every other module keeps its pins.
                    targets = []
                    for name, content in modules_requirements.items():
                        targets.extend(requirement_names(content))
                        targets.extend(req_caches.get(name, {}).get('packages', {}))
                    self.logger.info(
                        f"Upgrading {len(set(targets))} package(s) from: {', '.join(modules_requirements)}"
                    )
                    pins, per_module, conflicts = self._resolve_set(
                        requirements, upgrade=False, seed_pins=cache.get('resolved_packages') or {},
                        req_caches=req_caches, changed=list(modules_requirements),
                        upgrade_packages=targets,
                    )
                else:
                    pins, per_module, conflicts = self._resolve_set(
                        requirements, upgrade=True, seed_pins=None,
                        req_caches=req_caches, changed=list(module_hashes),
                    )
                self.save_cache(self._build_cache(
                    requirements, meta, module_hashes, combined_hash, pins, per_module, conflicts
                ))
                self.logger.info("💾 Cache updated with upgraded dependencies")

                self.last_conflicts = conflicts
                if pins:
                    self._install(pins, no_deps=bool(conflicts))

                self.logger.info("🚀 Dependencies upgraded successfully")
                return pins

            except Exception as e:
                self.logger.error(f"Error during dependency upgrade: {e}", exc_info=not isinstance(e, ResolutionError))
                raise

    async def upgrade_dependencies(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """
        Upgrade dependencies to their latest versions.

        * With ``modules_requirements``: those modules are added/updated and only their
          packages are upgraded; every other registered module keeps its pins.
        * Without (auto-discovery): every registered module is upgraded.

        Everything is resolved together with the same conflict policy, and any package
        whose installed version differs from the new pin is installed. Runs in a worker
        thread so the event loop stays responsive.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(
            self.upgrade_dependencies_sync,
            modules_requirements, requirements_file_pattern, search_dirs
        ))
