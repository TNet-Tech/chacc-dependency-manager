"""
Resolving a set of modules, with the fallback used when they can't all be satisfied together.

1. All modules are resolved in one pip-compile run.
2. If that fails because of a conflict, each new or changed module is resolved on its
   own (unchanged modules reuse their cached packages) and the results are merged with
   :func:`chacc.versions.merge_with_version_policy`. Core constraints always win.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from .errors import ResolutionError, is_not_a_conflict
from .names import base_package_name, canonicalize_name
from .pip_compile import Pins, run_pip_compile
from .requirements import parse_lock_file
from .versions import format_conflict, major_version_key, merge_with_version_policy

ON_CONFLICT_RESOLVE = "resolve"
ON_CONFLICT_FAIL = "fail"

CORE_CONSTRAINTS = "core constraints"


class Resolver:
    """
    Resolves modules with pip-compile and applies the conflict policy.

    Built by :class:`chacc.manager.DependencyManager` from its current settings.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        work_root: str,
        timeout: Optional[int],
        constraint_files: List[str],
        on_conflict: str,
        pre_resolve_hook: Optional[Callable[[str, str], None]] = None,
        post_resolve_hook: Optional[Callable[[str, Dict[str, str]], None]] = None,
    ):
        self.logger = logger
        self.work_root = work_root
        self.timeout = timeout
        self.constraint_files = constraint_files
        self.on_conflict = on_conflict
        self.pre_resolve_hook = pre_resolve_hook
        self.post_resolve_hook = post_resolve_hook

    def compile(
        self,
        requirements: List[Tuple[str, str]],
        upgrade: bool = False,
        seed_pins: Optional[Pins] = None,
        use_constraints: bool = True,
        upgrade_packages: Optional[List[str]] = None,
    ) -> Tuple[Pins, Dict[str, Pins]]:
        """One pip-compile run over ``requirements``. See :func:`chacc.pip_compile.run_pip_compile`."""
        return run_pip_compile(
            requirements,
            work_root=self.work_root,
            logger=self.logger,
            timeout=self.timeout,
            constraint_files=self.constraint_files if use_constraints else [],
            upgrade=upgrade,
            seed_pins=seed_pins,
            upgrade_packages=upgrade_packages,
        )

    def resolve_set(
        self,
        requirements: List[Tuple[str, str]],
        upgrade: bool,
        seed_pins: Optional[Pins],
        req_caches: Dict[str, Dict],
        changed: List[str],
        upgrade_packages: Optional[List[str]] = None,
    ) -> Tuple[Pins, Dict[str, Pins], List[Dict]]:
        """
        Resolve the module set, falling back to per-module resolution plus the version
        policy when the modules can't all be satisfied together.

        Returns ``(pins, per_module, conflicts)``.
        """
        self.run_pre_hooks(requirements)
        non_empty = [(n, c) for n, c in requirements if c.strip()]
        try:
            pins, per_module = self.compile(
                requirements, upgrade=upgrade, seed_pins=seed_pins, upgrade_packages=upgrade_packages
            )
            self.run_post_hooks({n: per_module[n] for n, _ in non_empty})
            return pins, per_module, []
        except ResolutionError as combined_error:
            if is_not_a_conflict(combined_error):
                raise
            if len(non_empty) < 2 and not self.constraint_files:
                raise
            self.logger.warning(
                "Modules cannot all be satisfied together; resolving each module on its own "
                "to find the conflict..."
            )
            self.logger.debug(str(combined_error))

        per_module: Dict[str, Pins] = {n: {} for n, _ in requirements}
        module_pins: List[Tuple[str, Pins]] = []
        for name, content in non_empty:
            entry = req_caches.get(name, {})
            reusable = not upgrade and name not in changed and entry.get('complete') and entry.get('packages')
            if reusable:
                pins_for_module = dict(entry['packages'])
            else:
                pins_for_module = self.resolve_alone(name, content, upgrade, entry.get('packages'), upgrade_packages)
            per_module[name] = pins_for_module
            module_pins.append((name, pins_for_module))

        merged, conflicts = self._merge(module_pins)
        if self.on_conflict == ON_CONFLICT_FAIL:
            raise ResolutionError(
                "Conflicting module dependencies:\n" + "\n".join(f"  - {format_conflict(c)}" for c in conflicts),
                conflicts=conflicts,
            )

        self.run_post_hooks({n: per_module[n] for n, _ in non_empty})
        return merged, per_module, conflicts

    def resolve_alone(
        self,
        name: str,
        content: str,
        upgrade: bool,
        seed: Optional[Pins],
        upgrade_packages: Optional[List[str]] = None,
    ) -> Pins:
        """Resolve one module by itself, retrying without core constraints if they are the problem."""
        try:
            pins, _ = self.compile([(name, content)], upgrade=upgrade, seed_pins=seed,
                                   upgrade_packages=upgrade_packages)
            return pins
        except ResolutionError as with_constraints:
            if is_not_a_conflict(with_constraints):
                raise
            if not self.constraint_files:
                raise self._broken_module(name, with_constraints) from with_constraints
        try:
            pins, _ = self.compile([(name, content)], upgrade=upgrade, seed_pins=seed,
                                   use_constraints=False, upgrade_packages=upgrade_packages)
        except ResolutionError as e:
            if is_not_a_conflict(e):
                raise
            raise self._broken_module(name, e) from e
        self.logger.warning(f"Module '{name}' only resolves without the core constraints")
        return pins

    def run_pre_hooks(self, requirements: List[Tuple[str, str]]):
        if not self.pre_resolve_hook:
            return
        for name, content in requirements:
            if content.strip():
                try:
                    self.pre_resolve_hook(name, content)
                except Exception as e:
                    self.logger.warning(f"Pre-resolve hook failed: {e}")

    def run_post_hooks(self, per_module: Dict[str, Pins]):
        if not self.post_resolve_hook:
            return
        for name, packages in per_module.items():
            try:
                self.post_resolve_hook(name, packages)
            except Exception as e:
                self.logger.warning(f"Post-resolve hook failed: {e}")

    
    def _merge(self, module_pins: List[Tuple[str, Pins]]) -> Tuple[Pins, List[Dict]]:
        """Apply the version policy (core constraints first, so they win) and log each conflict."""
        core_pins = self._constraint_pins()
        if core_pins:
            used = {canonicalize_name(base_package_name(p)) for _, pins in module_pins for p in pins}
            core_pins = {p: s for p, s in core_pins.items() if canonicalize_name(base_package_name(p)) in used}
            module_pins = [(CORE_CONSTRAINTS, core_pins)] + module_pins

        merged, conflicts = merge_with_version_policy(module_pins)
        _enforce_core_pins(merged, conflicts)
        if not conflicts:
            raise ResolutionError(
                "Modules resolve individually but not together, and no differing versions were "
                "found to reconcile. Check the constraint files."
            )
        for conflict in conflicts:
            major = conflict['kind'] == 'major'
            log = self.logger.warning if major else self.logger.info
            log(f"{'⚠️ ' if major else '↗ '} Dependency conflict on {format_conflict(conflict)}")
        return merged, conflicts

    def _constraint_pins(self) -> Pins:
        """Exact (``==``) pins from the constraint files."""
        pins: Pins = {}
        for path in self.constraint_files:
            try:
                with open(path, 'r') as f:
                    file_pins, _ = parse_lock_file(f.read())
            except OSError:
                continue
            pins.update({n: s for n, s in file_pins.items() if s.startswith('==')})
        return pins

    @staticmethod
    def _broken_module(name: str, error: ResolutionError) -> ResolutionError:
        return ResolutionError(
            f"Module '{name}' cannot be resolved even on its own, so this is not a "
            f"conflict between modules. Fix its requirements:\n{error}",
            module=name,
        )


def _enforce_core_pins(merged: Pins, conflicts: List[Dict]):
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
