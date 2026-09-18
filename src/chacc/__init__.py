"""
ChaCC Dependency Manager - Intelligent dependency resolution for Python applications.

This package provides incremental dependency resolution, caching, and intelligent
package installation for modular Python applications.
"""

from .manager import DependencyManager
from .chacc import (
    Config,
    re_resolve_dependencies,
    invalidate_dependency_cache,
    invalidate_module_cache,
    calculate_module_hash,
    load_dependency_cache,
    save_dependency_cache,
    get_installed_packages,
    resolve_module_dependencies,
    merge_resolved_packages,
    install_missing_packages,
)

__version__ = "1.3.0"
__author__ = "Jonas G Mwambimbi"
__email__ = "jonasgeorge1015@gmail.com"

__all__ = [
    "Config",
    "DependencyManager",
    "re_resolve_dependencies",
    "invalidate_dependency_cache",
    "invalidate_module_cache",
    "calculate_module_hash",
    "load_dependency_cache",
    "save_dependency_cache",
    "get_installed_packages",
    "resolve_module_dependencies",
    "merge_resolved_packages",
    "install_missing_packages",
]