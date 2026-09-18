"""
Dependency Manager - Core functionality for managing Python package dependencies.

Provides intelligent, incremental dependency resolution and caching for modular applications.
"""

import os
import json
import glob
import logging
import subprocess
import sys
import time
from typing import Dict, Set, Optional, Callable, List

from .utils import (
    default_logger,
    calculate_module_hash,
    calculate_combined_requirements_hash,
    get_environment_hash,
    get_installed_packages,
    canonicalize_name
)


class DependencyManager:
    """
    Manages Python package dependencies for modular applications.

    Provides incremental dependency resolution, caching, and intelligent
    package installation to optimize performance in development and production.

    This is a pure dependency management tool that works with requirements files
    and does not have any external dependencies. It can be extended with custom
    hooks for integration with other systems.

    Args:
        cache_dir: Directory for dependency cache files (default: ".dependency_cache")
        logger: Logger instance to use (default: built-in logging)
        pre_resolve_hook: Optional callback called before dependency resolution
        post_resolve_hook: Optional callback called after dependency resolution
        install_hook: Optional callback for custom package installation logic
        install_timeout: Timeout in seconds for each pip install attempt (default: 60)
        max_retries: Maximum number of retry attempts for failed package batches (default: 5)
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        pre_resolve_hook: Optional[Callable[[str, str], None]] = None,
        post_resolve_hook: Optional[Callable[[str, Dict[str, str]], None]] = None,
        install_hook: Optional[Callable[[Dict[str, str], Set[str]], bool]] = None,
        install_timeout: int = 60,
        max_retries: int = 5
    ):
        """Initialize the dependency manager with configurable paths and hooks."""
        self.cache_dir = os.path.abspath(cache_dir or ".dependency_cache")
        self.cache_file = os.path.join(self.cache_dir, "dependency_cache.json")

        self.logger = logger or default_logger

        self.pre_resolve_hook = pre_resolve_hook
        self.post_resolve_hook = post_resolve_hook
        self.install_hook = install_hook
        self.install_timeout = install_timeout
        self.max_retries = max_retries

        os.makedirs(self.cache_dir, exist_ok=True)

    def load_cache(self) -> Dict:
        """Load dependency cache from file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
                    if 'requirements_caches' not in cache:
                        cache['requirements_caches'] = {}
                    if 'combined_hash' not in cache:
                        cache['combined_hash'] = None
                    if 'environment_hash' not in cache:
                        cache['environment_hash'] = None
                    return cache
            except (json.JSONDecodeError, IOError) as e:
                self.logger.warning(f"Failed to load dependency cache: {e}")
        return {
            'requirements_caches': {},
            'backbone_hash': None,
            'combined_hash': None,
            'environment_hash': None,
            'resolved_packages': {},
            'last_updated': None
        }

    def save_cache(self, cache_data: Dict):
        """Save dependency cache to file."""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except IOError as e:
            self.logger.error(f"Failed to save dependency cache: {e}")

    def invalidate_cache(self):
        """Invalidate the entire dependency cache."""
        try:
            cache_data = {
                'requirements_caches': {},
                'backbone_hash': None,
                'combined_hash': None,
                'environment_hash': None,
                'resolved_packages': {},
                'last_updated': None
            }
            self.save_cache(cache_data)
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
        """Invalidate cache for a specific module."""
        try:
            cache = self.load_cache()
            if module_name in cache.get('requirements_caches', {}):
                del cache['requirements_caches'][module_name]
                cache['combined_hash'] = None
                self.save_cache(cache)
                self.logger.info(f"Cache invalidated for module: {module_name}")
        except Exception as e:
            self.logger.warning(f"Failed to invalidate cache for module {module_name}: {e}")

    def resolve_module_dependencies(self, module_name: str, requirements_content: str, upgrade: bool = False) -> Dict[str, str]:
        """Resolve dependencies for a specific module."""
        action = "Upgrading" if upgrade else "Resolving"
        self.logger.info(f"{action} dependencies for module: {module_name}")

        if self.pre_resolve_hook:
            try:
                self.pre_resolve_hook(module_name, requirements_content)
            except Exception as e:
                self.logger.warning(f"Pre-resolve hook failed: {e}")

        temp_req_file = os.path.join(os.path.dirname(self.cache_file), f"temp_{module_name}_requirements.txt")

        try:
            with open(temp_req_file, "w") as f:
                f.write(requirements_content)

            cmd = [
                sys.executable, '-m', 'piptools', 'compile',
                '--output-file', f"{temp_req_file}.lock",
                '--allow-unsafe'
            ]
            if upgrade:
                cmd.append('--upgrade')

            cmd.append(temp_req_file)

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                self.logger.error(f"Failed to {action.lower()} dependencies for {module_name}: {result.stderr}")
                return {}

            resolved_packages = {}
            lock_file = f"{temp_req_file}.lock"
            if os.path.exists(lock_file):
                with open(lock_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '==' in line:
                            parts = line.split('==')
                            if len(parts) >= 2:
                                package_name = parts[0]
                                version = parts[1]
                                resolved_packages[package_name] = f"=={version}"

            self.logger.info(f"{action} {len(resolved_packages)} packages for {module_name}")

            if self.post_resolve_hook:
                try:
                    self.post_resolve_hook(module_name, resolved_packages)
                except Exception as e:
                    self.logger.warning(f"Post-resolve hook failed: {e}")

            return resolved_packages

        except Exception as e:
            self.logger.error(f"Error {action.lower()} dependencies for {module_name}: {e}")
            return {}
        finally:
            for file in [temp_req_file, f"{temp_req_file}.lock"]:
                if os.path.exists(file):
                    os.remove(file)

    def merge_resolved_packages(self, *package_dicts: Dict[str, str]) -> Dict[str, str]:
        """Merge multiple resolved package dictionaries, resolving conflicts."""
        merged = {}

        for package_dict in package_dicts:
            for package_name, version_spec in package_dict.items():
                if package_name in merged:
                    existing_version = merged[package_name]
                    if version_spec != existing_version:
                        self.logger.warning(f"Version conflict for {package_name}: {existing_version} vs {version_spec}, using {version_spec}")
                merged[package_name] = version_spec

        return merged

    def install_missing_packages(self, resolved_packages: Dict[str, str], installed_packages: Set[str]):
        """Install only packages that are not already installed."""
        if self.install_hook:
            try:
                success = self.install_hook(resolved_packages, installed_packages)
                if success:
                    self.logger.info("Custom installation hook completed successfully")
                    return
                else:
                    self.logger.warning("Custom installation hook failed, falling back to default")
            except Exception as e:
                self.logger.warning(f"Custom installation hook failed: {e}, falling back to default")

        packages_to_install = []
        packages_to_skip = []

        for package_name, version_spec in resolved_packages.items():
            canonical_name = canonicalize_name(package_name)
            if canonical_name not in installed_packages:
                packages_to_install.append(f"{package_name}{version_spec}")
            else:
                packages_to_skip.append(f"{package_name}")

        if packages_to_skip:
            self.logger.info("Skipped Packages:")
            for package_name in packages_to_skip:
                self.logger.info(f"     ✓ {package_name}")

        if packages_to_install:
            self.logger.info(f"Installing {len(packages_to_install)} missing packages...")
            for package_name in packages_to_install:
                self.logger.info(f"     ✓ {package_name}")
            batch_size = 50
            for i in range(0, len(packages_to_install), batch_size):
                batch = packages_to_install[i:i + batch_size]
                last_error = None
                for attempt in range(1, self.max_retries + 1):
                    try:
                        result = subprocess.run([
                            sys.executable, '-m', 'pip', 'install', '--quiet'
                        ] + batch, capture_output=True, text=True, timeout=self.install_timeout)

                        if result.returncode != 0:
                            raise subprocess.CalledProcessError(
                                result.returncode, result.args, result.stdout, result.stderr
                            )
                        last_error = None
                        break
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

                if last_error:
                    self.logger.error(f"Failed to install package batch after {self.max_retries} attempts: {last_error}")
                    raise subprocess.CalledProcessError(1, [], None, last_error)

            self.logger.info("Package installation completed successfully")
        else:
            self.logger.info("All required packages are already installed")

    async def resolve_dependencies(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None
    ):
        """
        Perform incremental dependency resolution for requirements.

        This method:
        1. Checks which requirements have changed
        2. Only resolves dependencies for changed requirements
        3. Merges results with cached dependencies
        4. Installs only missing packages

        Args:
            modules_requirements: Dict of name -> requirements_content
                                  If None, auto-discovers from filesystem
            requirements_file_pattern: Glob pattern for requirements files (default: "requirements.txt")
            search_dirs: List of directories to search for requirements files (default: current dir)
        """
        self.logger.info("Starting incremental dependency resolution...")

        if modules_requirements:
            requirements_to_process = list(modules_requirements.items())
        else:
            requirements_to_process = []
            search_dirs = search_dirs or ["."]

            for base_dir in search_dirs:
                if os.path.exists(base_dir):
                    pattern = os.path.join(base_dir, requirements_file_pattern)
                    req_files = glob.glob(pattern)

                    for req_file in req_files:
                        try:
                            with open(req_file, 'r') as f:
                                req_content = f.read().strip()

                            if req_content:
                                filename = os.path.basename(req_file)
                                req_name = os.path.splitext(filename)[0]
                                requirements_to_process.append((req_name, req_content))
                                self.logger.info(f"Discovered requirements file: {req_file}")

                        except Exception as e:
                            self.logger.warning(f"Failed to read requirements file {req_file}: {e}")

            if not requirements_to_process:
                self.logger.info(f"No requirements files found matching pattern: {requirements_file_pattern} in {search_dirs}")
                return

        try:
            cache = self.load_cache()
            current_env_hash = get_environment_hash()
            cached_env_hash = cache.get('environment_hash')

            if cached_env_hash and cached_env_hash != current_env_hash:
                self.logger.info("Environment changed (Python version/OS), invalidating cache")
                self.invalidate_cache()
                cache = self.load_cache()

            req_caches = cache.get('requirements_caches', {})

            current_req_hashes = {}
            requirements_needing_resolution = []
            cache_was_used = False
            packages_were_installed = False
            cached_packages = {}
            missing_packages = []

            for req_name, req_content in requirements_to_process:
                req_hash = calculate_module_hash(req_name, req_content)
                current_req_hashes[req_name] = req_hash

                if req_name not in req_caches or req_caches[req_name].get('hash') != req_hash:
                    requirements_needing_resolution.append((req_name, req_content))
                    self.logger.info(f"Requirements '{req_name}' have changed")

            if not requirements_needing_resolution:
                cached_packages = cache.get('resolved_packages', {})
                if cached_packages:
                    cache_was_used = True
                    self.logger.debug(f"Checking {len(cached_packages)} cached packages against installed packages")
                    installed_packages = get_installed_packages()
                    self.logger.debug(f"Found {len(installed_packages)} installed packages")
                    missing_packages = []
                    for package_name in cached_packages.keys():
                        base_name = package_name.split('[')[0] if '[' in package_name else package_name
                        canonical_name = canonicalize_name(base_name)
                        if canonical_name not in installed_packages:
                            missing_packages.append(package_name)
                            self.logger.debug(f"Package '{package_name}' (base: '{base_name}', canonical: '{canonical_name}') not found in installed packages")

                    if not missing_packages:
                        self.logger.info("✅ Cache hit: All dependencies are up-to-date and installed")
                    else:
                        packages_were_installed = True
                        self.logger.info(f"⚡ Cache hit: Dependencies resolved from cache, installing {len(missing_packages)} missing packages")
                        self.logger.debug(f"Missing packages: {missing_packages}")
                        self.install_missing_packages(cached_packages, installed_packages)
                else:
                    self.logger.info("📦 Cache miss: No cached data found, performing full dependency resolution")
                    requirements_needing_resolution = requirements_to_process
            else:
                changed_modules = [req[0] for req in requirements_needing_resolution]
                self.logger.info(f"🔄 Requirements changed: Re-resolving dependencies for {len(requirements_needing_resolution)} module(s): {', '.join(changed_modules)}")

            if requirements_needing_resolution:
                resolved_packages = {}
                for req_name, req_content in requirements_needing_resolution:
                    if req_content.strip():
                        req_packages = self.resolve_module_dependencies(req_name, req_content, upgrade=False)
                        resolved_packages.update(req_packages)

                    req_caches[req_name] = {
                        'hash': current_req_hashes.get(req_name),
                        'packages': req_packages if req_content.strip() else {},
                        'last_updated': str(os.path.getmtime(os.path.join(self.cache_dir, '..')))  # Rough timestamp
                    }

                cached_packages = {}
                for req_name, req_cache in req_caches.items():
                    if req_name not in [r[0] for r in requirements_needing_resolution]:
                        cached_packages.update(req_cache.get('packages', {}))

                all_resolved_packages = self.merge_resolved_packages(cached_packages, resolved_packages)

                installed_packages = get_installed_packages()
                self.install_missing_packages(all_resolved_packages, installed_packages)

                combined_hash = calculate_combined_requirements_hash(current_req_hashes)
                cache_data = {
                    'requirements_caches': req_caches,
                    'combined_hash': combined_hash,
                    'environment_hash': current_env_hash,
                    'resolved_packages': all_resolved_packages,
                    'last_updated': str(os.path.getmtime(self.cache_dir)) if os.path.exists(self.cache_dir) else None
                }
                self.save_cache(cache_data)
                self.logger.info("💾 Cache updated: New dependency resolution results saved")

            if not requirements_needing_resolution:
                if cache_was_used and not packages_were_installed:
                    self.logger.info("🎉 Dependencies verified successfully - no action needed")
                elif cache_was_used and packages_were_installed:
                    self.logger.info("✅ Dependencies resolved and installed successfully")
                else:
                    self.logger.info("📦 Full dependency resolution completed successfully")
            else:
                self.logger.info("🚀 Dependencies resolved and installed successfully")

        except Exception as e:
            self.logger.error(f"Error during dependency resolution: {e}", exc_info=True)
            raise

    async def upgrade_dependencies(
        self,
        modules_requirements: Optional[Dict[str, str]] = None,
        requirements_file_pattern: str = "requirements.txt",
        search_dirs: Optional[List[str]] = None
    ):
        """
        Upgrade dependencies to their latest versions.

        This method forces resolution of all dependencies to their latest versions,
        invalidates the cache, and installs the upgraded packages.

        Args:
            modules_requirements: Dict of name -> requirements_content
                                   If None, auto-discovers from filesystem
            requirements_file_pattern: Glob pattern for requirements files (default: "requirements.txt")
            search_dirs: List of directories to search for requirements files (default: current dir)
        """
        self.logger.info("Starting dependency upgrade...")

        # Force cache invalidation for upgrades
        self.invalidate_cache()

        if modules_requirements:
            requirements_to_process = list(modules_requirements.items())
        else:
            requirements_to_process = []
            search_dirs = search_dirs or ["."]

            for base_dir in search_dirs:
                if os.path.exists(base_dir):
                    pattern = os.path.join(base_dir, requirements_file_pattern)
                    req_files = glob.glob(pattern)

                    for req_file in req_files:
                        try:
                            with open(req_file, 'r') as f:
                                req_content = f.read().strip()

                            if req_content:
                                filename = os.path.basename(req_file)
                                req_name = os.path.splitext(filename)[0]
                                requirements_to_process.append((req_name, req_content))
                                self.logger.info(f"Discovered requirements file: {req_file}")

                        except Exception as e:
                            self.logger.warning(f"Failed to read requirements file {req_file}: {e}")

            if not requirements_to_process:
                self.logger.info(f"No requirements files found matching pattern: {requirements_file_pattern} in {search_dirs}")
                return

        try:
            resolved_packages = {}
            for req_name, req_content in requirements_to_process:
                if req_content.strip():
                    req_packages = self.resolve_module_dependencies(req_name, req_content, upgrade=True)
                    resolved_packages.update(req_packages)

            if resolved_packages:
                installed_packages = get_installed_packages()
                self.install_missing_packages(resolved_packages, installed_packages)

                # Save updated cache
                cache_data = {
                    'requirements_caches': {},
                    'combined_hash': None,
                    'environment_hash': get_environment_hash(),
                    'resolved_packages': resolved_packages,
                    'last_updated': str(os.path.getmtime(self.cache_dir)) if os.path.exists(self.cache_dir) else None
                }
                self.save_cache(cache_data)
                self.logger.info("💾 Cache updated with upgraded dependencies")

            self.logger.info("🚀 Dependencies upgraded successfully")

        except Exception as e:
            self.logger.error(f"Error during dependency upgrade: {e}", exc_info=True)
            raise