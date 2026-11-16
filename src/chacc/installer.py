"""
Installing resolved pins with pip: what needs installing, retries, and checks afterwards.
"""

import logging
import os
import site
import subprocess
import sys
import sysconfig
import time
from typing import Callable, Dict, List, Mapping, Optional, Set, Tuple, Union

from . import pip_leftovers
from .names import base_package_name, canonicalize_name
from .utils import get_installed_versions
from .versions import version_satisfies

InstalledPackages = Union[Set[str], Mapping[str, str]]

BATCH_SIZE = 50


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


class Installer:
    """
    Installs pins with pip.

    Built by :class:`chacc.manager.DependencyManager` from its current settings.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        timeout: Optional[int],
        max_retries: int,
        install_hook: Optional[Callable[[Dict[str, str], Set[str]], bool]] = None,
    ):
        self.logger = logger
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.install_hook = install_hook

    def sync(self, pins: Dict[str, str], no_deps: bool):
        """
        Make the environment match ``pins``: install what's missing or at the wrong version.

        ``no_deps`` (after a conflict merge) installs the pins exactly as given and then
        reports the declared requirements that are now unmet.
        """
        installed = get_installed_versions()
        self.logger.debug(f"Found {len(installed)} installed packages")
        missing, wrong, _ = packages_needing_install(pins, installed)
        if not (missing or wrong):
            self.logger.info("🎉 All dependencies are installed at the resolved versions")
            return
        self.install_missing(pins, installed, no_deps=no_deps)
        if no_deps:
            self.report_broken_requirements(pins)
        self.logger.info("🚀 Dependencies resolved and installed successfully")

    def install_missing(
        self,
        resolved_packages: Dict[str, str],
        installed_packages: InstalledPackages,
        no_deps: bool = False,
    ):
        """Install packages that are missing or installed at a different version (or run the install hook)."""
        if self.install_hook:
            try:
                if self.install_hook(resolved_packages, set(installed_packages)):
                    self.logger.info("Custom installation hook completed successfully")
                    return
                self.logger.warning("Custom installation hook failed, falling back to default")
            except Exception as e:
                self.logger.warning(f"Custom installation hook failed: {e}, falling back to default")

        missing, wrong_version, satisfied = packages_needing_install(resolved_packages, installed_packages)
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

        self.log_install_target()
        for i in range(0, len(packages_to_install), BATCH_SIZE):
            self.run_pip_install(packages_to_install[i:i + BATCH_SIZE], no_deps=no_deps)
        self.logger.info("Package installation completed successfully")

    def run_pip_install(self, batch: List[str], no_deps: bool = False):
        """
        ``pip install`` one batch, retrying with exponential backoff.

        If an attempt fails or times out, leftovers pip created are removed and the next
        attempt reinstalls the batch cleanly, since a package may have been half-replaced.
        Raises ``subprocess.CalledProcessError`` after the last attempt.
        """
        base_cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check']
        if no_deps:
            base_cmd.append('--no-deps')
        existing = pip_leftovers.find_leftovers()
        pip_leftovers.warn_about_existing(existing, self.logger)

        last_error = None
        reinstall = False
        for attempt in range(1, self.max_retries + 1):
            cmd = base_cmd + batch
            if reinstall:
                cmd = base_cmd + ['--force-reinstall'] + ([] if no_deps else ['--no-deps']) + batch
            try:
                self._pip(cmd)
                return
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {self.timeout}s"
                self.logger.warning(
                    f"pip install attempt {attempt}/{self.max_retries} timed out for {len(batch)} packages"
                )
            except subprocess.CalledProcessError as e:
                last_error = e.stderr or "unknown error"
                self.logger.warning(
                    f"pip install attempt {attempt}/{self.max_retries} failed for {len(batch)} packages"
                )
            reinstall = pip_leftovers.remove_new_leftovers(existing, self.logger) or reinstall

            if attempt < self.max_retries:
                backoff = min(2 ** (attempt - 1), 30)
                self.logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        self.logger.error(f"Failed to install package batch after {self.max_retries} attempts: {last_error}")
        raise subprocess.CalledProcessError(1, [], None, last_error)

    def _pip(self, cmd: List[str]):
        """Run one pip install; raises CalledProcessError / TimeoutExpired."""
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        for line in (result.stdout or '').splitlines():
            if line.startswith('Successfully installed'):
                self.logger.debug(line.strip())
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        if "Defaulting to user installation" in f"{result.stdout or ''}\n{result.stderr or ''}":
            self.logger.warning(
                "pip installed into the user site-packages "
                f"({site.getusersitepackages()}) because the environment is not writable"
            )

    def log_install_target(self):
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

    def report_broken_requirements(self, pins: Dict[str, str]):
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
