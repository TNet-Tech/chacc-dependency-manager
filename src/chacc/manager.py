"""
Dependency Manager - the public entry point for resolving, caching and installing dependencies.

:class:`DependencyManager` holds the settings and runs each operation. The work is
done by these modules:

* :mod:`chacc.registry` - which modules are registered, discovery, module hashes
* :mod:`chacc.resolver` - pip-compile runs and the conflict policy
* :mod:`chacc.installer` - pip installs, retries and checks
* :mod:`chacc.cache` - the cache file
* :mod:`chacc.locking` - one operation at a time per cache directory
"""

import asyncio
import functools
import logging
import os
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from . import cache as cache_file
from . import registry
from .errors import ResolutionError
from .installer import InstalledPackages, Installer, packages_needing_install
from .locking import lock_for
from .requirements import absolutize_requirement_paths, requirement_names
from .resolver import CORE_CONSTRAINTS, ON_CONFLICT_FAIL, ON_CONFLICT_RESOLVE, Resolver
from .utils import default_logger
from .versions import format_conflict


__all__ = [
    "DependencyManager", "ResolutionError", "InstalledPackages",
    "ON_CONFLICT_RESOLVE", "ON_CONFLICT_FAIL", "CORE_CONSTRAINTS",
]


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
    packages keep their versions. If modules turn out to be incompatible, each module
    is resolved on its own and the results are merged:

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
        on_conflict: ``"resolve"`` (default) applies the version policy above;
            ``"fail"`` raises :class:`ResolutionError` describing the conflicts.
        log_level: Logging level for ChaCC's messages, as a number (``logging.INFO``) or a
            name (``"INFO"``, ``"warning"``). Applied to ``logger`` (or ChaCC's own
            ``dependency_manager`` logger when none is passed, which affects every manager
            using it). Default: None, which leaves the level unchanged (DEBUG for the
            built-in logger, as in earlier versions).

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

        self._lock = lock_for(self.cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    @property
    def resolver(self) -> Resolver:
        return Resolver(
            logger=self.logger,
            work_root=self.cache_dir,
            timeout=self.resolve_timeout,
            constraint_files=self.constraint_files,
            on_conflict=self.on_conflict,
            pre_resolve_hook=self.pre_resolve_hook,
            post_resolve_hook=self.post_resolve_hook,
        )

    @property
    def installer(self) -> Installer:
        return Installer(
            logger=self.logger,
            timeout=self.install_timeout,
            max_retries=self.max_retries,
            install_hook=self.install_hook,
        )

    def load_cache(self) -> Dict:
        """Load dependency cache from file."""
        return cache_file.load_cache(self.cache_file, self.logger)

    def save_cache(self, cache_data: Dict):
        """Save dependency cache to file (atomically, so a crash can't leave half a file)."""
        cache_file.save_cache(self.cache_file, cache_data, self.logger)

    def invalidate_cache(self):
        """Invalidate the entire dependency cache (this also unregisters every module)."""
        try:
            self.save_cache(cache_file.empty_cache())
            self.logger.info("Dependency cache invalidated")
        except Exception as e:
            self.logger.warning(f"Failed to invalidate dependency cache: {e}")
            if os.path.exists(self.cache_file):
                try:
                    os.remove(self.cache_file)
                    self.logger.info("Dependency cache file removed")
                except IOError as e2:
                    self.logger.error(f"Failed to remove dependency cache file: {e2}")

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
                cache = cache_file.reset_for_new_environment(self.load_cache(), self.logger)
                req_caches = cache.get('requirements_caches', {})
                requirements, meta = registry.effective_requirements(
                    req_caches, modules_requirements, requirements_file_pattern, search_dirs,
                    replace, self.logger,
                )
                if not requirements:
                    return {}

                cached_pins = cache.get('resolved_packages', {}) or {}
                module_hashes, combined_hash = registry.requirement_hashes(requirements, self.constraint_files)
                changed = [n for n, h in module_hashes.items() if req_caches.get(n, {}).get('hash') != h]
                removed = [n for n in req_caches if n not in module_hashes]

                if changed or removed or not cached_pins or cache.get('combined_hash') != combined_hash:
                    self._log_why_resolving(cached_pins, req_caches, changed, removed, len(requirements))
                    pins, per_module, conflicts = self.resolver.resolve_set(
                        requirements, upgrade=False, seed_pins=cached_pins,
                        req_caches=req_caches, changed=changed,
                    )
                    if dry_run:
                        self.logger.info("Dry run: the cache was not changed")
                    else:
                        self.save_cache(cache_file.build_cache(
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
                    self.installer.sync(pins, no_deps=bool(conflicts))
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
                cache = cache_file.reset_for_new_environment(self.load_cache(), self.logger)
                req_caches = cache.get('requirements_caches', {})
                requirements, meta = registry.effective_requirements(
                    req_caches, modules_requirements, requirements_file_pattern, search_dirs,
                    replace=False, logger=self.logger, prune=False,
                )
                if not requirements:
                    return {}

                module_hashes, combined_hash = registry.requirement_hashes(requirements, self.constraint_files)
                if modules_requirements:
                    targets = self._upgrade_targets(modules_requirements, req_caches)
                    pins, per_module, conflicts = self.resolver.resolve_set(
                        requirements, upgrade=False, seed_pins=cache.get('resolved_packages') or {},
                        req_caches=req_caches, changed=list(modules_requirements),
                        upgrade_packages=targets,
                    )
                else:
                    pins, per_module, conflicts = self.resolver.resolve_set(
                        requirements, upgrade=True, seed_pins=None,
                        req_caches=req_caches, changed=list(module_hashes),
                    )
                self.save_cache(cache_file.build_cache(
                    requirements, meta, module_hashes, combined_hash, pins, per_module, conflicts
                ))
                self.logger.info("💾 Cache updated with upgraded dependencies")

                self.last_conflicts = conflicts
                if pins:
                    self.installer.sync(pins, no_deps=bool(conflicts))

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
        resolver = self.resolver
        resolver.run_pre_hooks([(module_name, requirements_content)])
        try:
            pins, _ = resolver.compile([(module_name, requirements_content)], upgrade=upgrade)
        except ResolutionError as e:
            if raise_on_error:
                raise
            self.logger.error(str(e))
            return {}
        resolver.run_post_hooks({module_name: pins})
        return pins

    def merge_resolved_packages(self, *package_dicts: Dict[str, str]) -> Dict[str, str]:
        """
        Merge multiple resolved package dictionaries ("last wins" on conflict).

        Kept for backward compatibility. The resolver no longer uses this; see
        :func:`chacc.utils.merge_with_version_policy` for the policy it applies.
        """
        merged: Dict[str, str] = {}
        for package_dict in package_dicts:
            for package_name, version_spec in package_dict.items():
                existing_version = merged.get(package_name)
                if existing_version is not None and version_spec != existing_version:
                    self.logger.warning(
                        f"Version conflict for {package_name}: {existing_version} vs {version_spec}, "
                        f"using {version_spec}"
                    )
                merged[package_name] = version_spec
        return merged

    @staticmethod
    def packages_needing_install(
        resolved_packages: Dict[str, str],
        installed_packages: InstalledPackages,
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Compare resolved pins with what is installed: ``(missing, wrong_version, satisfied)``.

        See :func:`chacc.installer.packages_needing_install`.
        """
        return packages_needing_install(resolved_packages, installed_packages)

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
        self.installer.install_missing(resolved_packages, installed_packages, no_deps=no_deps)

    def _log_why_resolving(self, cached_pins, req_caches, changed, removed, module_count):
        if not cached_pins and not req_caches:
            self.logger.info("📦 Cache miss: No cached data found, performing full dependency resolution")
            return
        if changed:
            self.logger.info(f"🔄 Requirements added or changed: {', '.join(changed)}")
        if removed:
            self.logger.info(f"🧹 Requirements removed, dropping their pins: {', '.join(removed)}")
        if not changed and not removed:
            self.logger.info("🔄 Constraints or cache state changed")
        self.logger.info(f"Re-resolving all {module_count} module(s) together")

    def _upgrade_targets(self, modules_requirements: Dict[str, str], req_caches: Dict[str, Dict]) -> List[str]:
        """Packages a selective upgrade may move: the modules' direct requirements and everything they pull in."""
        targets: List[str] = []
        for name, content in modules_requirements.items():
            targets.extend(requirement_names(content))
            targets.extend(req_caches.get(name, {}).get('packages', {}))
        self.logger.info(f"Upgrading {len(set(targets))} package(s) from: {', '.join(modules_requirements)}")
        return targets
