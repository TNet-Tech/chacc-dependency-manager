"""
ChaCC Dependency Manager - Main module with convenience functions.

This module provides the main API and convenience functions for the ChaCC
dependency manager package. The core functionality is split across:

- manager.py: DependencyManager class and core logic
- utils.py: Utility functions and logging setup
- cli.py: Command-line interface
- demo_*.py: Demonstration scripts showing features

API Levels:
1. Simple Functions: re_resolve_dependencies() - Just works with automatic caching
2. Config Object: Clean configuration without parameter explosion
3. DependencyManager: Full control over all aspects

Demos:
    cdm demo modules  - Show module-based dependency separation
    cdm demo cache    - Show cache structure and organization

Example:
    # Simple usage
    await re_resolve_dependencies()

    # Config-based customization
    config = Config(cache_dir='./cache', logger=my_logger)
    await re_resolve_dependencies(config=config)

    # Full control
    dm = DependencyManager(cache_dir='./cache', logger=my_logger)
    await dm.resolve_dependencies()
"""

# Re-exported for code that imports these from chacc.chacc (as in earlier versions).
from .manager import DependencyManager, ResolutionError  # noqa: F401
from .utils import (  # noqa: F401
    calculate_module_hash,
    calculate_combined_requirements_hash,
    get_installed_packages
)
import logging
from typing import Optional, Callable, Dict, Set, List, Union
from dataclasses import dataclass


@dataclass
class Config:
    """Configuration for dependency management operations."""
    cache_dir: Optional[str] = None
    logger: Optional[logging.Logger] = None
    pre_resolve_hook: Optional[Callable[[str, str], None]] = None
    post_resolve_hook: Optional[Callable[[str, Dict[str, str]], None]] = None
    install_hook: Optional[Callable[[Dict[str, str], Set[str]], bool]] = None
    install_timeout: Optional[int] = 300
    max_retries: int = 5
    resolve_timeout: Optional[int] = 600
    constraint_files: Optional[List[str]] = None
    on_conflict: str = "resolve"
    log_level: Optional[Union[int, str]] = None

    def create_manager(self) -> DependencyManager:
        """Create a DependencyManager with this configuration."""
        return DependencyManager(
            cache_dir=self.cache_dir,
            logger=self.logger,
            pre_resolve_hook=self.pre_resolve_hook,
            post_resolve_hook=self.post_resolve_hook,
            install_hook=self.install_hook,
            install_timeout=self.install_timeout,
            max_retries=self.max_retries,
            resolve_timeout=self.resolve_timeout,
            constraint_files=self.constraint_files,
            on_conflict=self.on_conflict,
            log_level=self.log_level,
        )


dependency_manager = DependencyManager()


def load_dependency_cache(config: Optional[Config] = None) -> Dict:
    """Load dependency cache from file."""
    if config:
        dm = config.create_manager()
        return dm.load_cache()
    return dependency_manager.load_cache()


def save_dependency_cache(cache_data: Dict, config: Optional[Config] = None):
    """Save dependency cache to file."""
    if config:
        dm = config.create_manager()
        dm.save_cache(cache_data)
    else:
        dependency_manager.save_cache(cache_data)


def resolve_module_dependencies(
    module_name: str,
    requirements_content: str,
    config: Optional[Config] = None
) -> Dict[str, str]:
    """Resolve dependencies for a specific module."""
    dm = config.create_manager() if config else dependency_manager
    return dm.resolve_module_dependencies(module_name, requirements_content)


def merge_resolved_packages(*package_dicts: Dict[str, str], config: Optional[Config] = None) -> Dict[str, str]:
    """Merge multiple resolved package dictionaries, resolving conflicts."""
    dm = config.create_manager() if config else dependency_manager
    return dm.merge_resolved_packages(*package_dicts)


def install_missing_packages(
    resolved_packages: Dict[str, str],
    installed_packages: Set[str],
    config: Optional[Config] = None
):
    """Install only packages that are not already installed."""
    dm = config.create_manager() if config else dependency_manager
    dm.install_missing_packages(resolved_packages, installed_packages)


def invalidate_dependency_cache(config: Optional[Config] = None):
    """Invalidate the dependency cache."""
    dm = config.create_manager() if config else dependency_manager
    dm.invalidate_cache()


def invalidate_module_cache(module_name: str, config: Optional[Config] = None):
    """Force a module to be re-resolved on the next run."""
    dm = config.create_manager() if config else dependency_manager
    dm.invalidate_module_cache(module_name)


def remove_module(module_name: str, config: Optional[Config] = None) -> bool:
    """Unregister a module; its pins are dropped on the next resolve (nothing is uninstalled)."""
    dm = config.create_manager() if config else dependency_manager
    return dm.remove_module(module_name)


async def re_resolve_dependencies(
    modules_requirements: Optional[Dict[str, str]] = None,
    requirements_file_pattern: str = "requirements.txt",
    search_dirs: Optional[List[str]] = None,
    config: Optional[Config] = None
):
    """
    Re-resolve and reinstall all dependencies with full caching support.

    Runs the blocking pip work in a worker thread, so it is safe to await from an
    async server. Returns the resolved pins.
    """
    dm = config.create_manager() if config else dependency_manager
    return await dm.resolve_dependencies(modules_requirements, requirements_file_pattern, search_dirs)
