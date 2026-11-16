"""
Utility functions for the ChaCC Dependency Manager.

This module holds the logger, the hashing used for cache keys and the lookups of
installed packages. The helpers for names, versions and requirements text live in
:mod:`chacc.names`, :mod:`chacc.versions` and :mod:`chacc.requirements`; they are
re-exported here so ``from chacc.utils import ...`` keeps working.
"""

import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
from typing import Dict, Iterable, Set

from .names import base_package_name, canonicalize_name
from .requirements import (
    absolutize_requirement_paths,
    attribute_packages,
    included_files,
    module_name_for_path,
    parse_lock_file,
    requirement_names,
)
from .versions import (
    format_conflict,
    major_version_key,
    merge_with_version_policy,
    version_satisfies,
    version_sort_key,
)


default_logger = logging.getLogger('dependency_manager')
default_logger.setLevel(logging.DEBUG)
if not default_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s: %(name)s - %(message)s')
    handler.setFormatter(formatter)
    default_logger.addHandler(handler)


def get_environment_hash() -> str:
    """
    Calculate hash of the current environment.

    Covers what actually changes which packages get resolved/installed:
    Python implementation and version, OS family and CPU architecture.

    The OS release (``platform.release()``) is deliberately excluded: on Linux it is
    the *host kernel* version, which changes on every host update (and inside Docker
    it is the host's kernel, not the image's), needlessly wiping the cache.
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    implementation = platform.python_implementation()
    os_info = f"{platform.system()}-{platform.machine()}"
    env_content = f"python:{implementation}:{python_version}:os:{os_info}"
    return hashlib.sha256(env_content.encode()).hexdigest()


def calculate_module_hash(module_name: str, requirements_content: str) -> str:
    """Calculate hash of a specific module's requirements including environment."""
    env_hash = get_environment_hash()
    content = f"{module_name}:{requirements_content}:env:{env_hash}"
    return hashlib.sha256(content.encode()).hexdigest()


def calculate_combined_requirements_hash(module_hashes: Dict[str, str]) -> str:
    """Calculate hash of all module requirement hashes combined."""
    sorted_hashes = sorted(module_hashes.items())
    combined = "|".join(f"{name}:{hash}" for name, hash in sorted_hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


def calculate_files_hash(paths: Iterable[str]) -> str:
    """Hash the paths and contents of a set of files (missing files hash as empty)."""
    digest = hashlib.sha256()
    for path in sorted(os.path.abspath(p) for p in paths):
        digest.update(path.encode())
        try:
            with open(path, 'rb') as f:
                digest.update(f.read())
        except OSError:
            digest.update(b'<missing>')
    return digest.hexdigest()


def get_installed_versions() -> Dict[str, str]:
    """Get currently installed packages as ``{canonical_name: version}``."""
    try:
        result = subprocess.run([
            sys.executable, '-m', 'pip', 'list', '--format=json', '--disable-pip-version-check'
        ], capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            default_logger.warning(f"Failed to get installed packages: {result.stderr}")
            return {}
        return {
            canonicalize_name(entry['name']): entry['version']
            for entry in json.loads(result.stdout or '[]')
        }
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError,
            ValueError, KeyError, TypeError) as e:
        default_logger.warning(f"Error getting installed packages: {e}")
        return {}


def get_installed_packages() -> Set[str]:
    """Get set of currently installed packages (canonicalized names)."""
    return set(get_installed_versions())


__all__ = [
    # logging and hashing
    "default_logger", "get_environment_hash", "calculate_module_hash",
    "calculate_combined_requirements_hash", "calculate_files_hash",
    # installed packages
    "get_installed_versions", "get_installed_packages",
    # re-exported from chacc.names / chacc.versions / chacc.requirements
    "canonicalize_name", "base_package_name",
    "version_satisfies", "version_sort_key", "major_version_key",
    "merge_with_version_policy", "format_conflict",
    "module_name_for_path", "absolutize_requirement_paths", "included_files",
    "requirement_names", "parse_lock_file", "attribute_packages",
]
