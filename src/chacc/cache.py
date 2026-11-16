"""
The cache file (``dependency_cache.json``): reading, writing and building it.

Layout::

    requirements_caches   one entry per registered module (see build_cache)
    combined_hash         all module hashes plus constraint files
    environment_hash      Python version, OS and CPU
    resolved_packages     the pins that get installed
    conflicts             conflicts handled by the version policy
    backbone_hash         kept for compatibility
    last_updated          timestamp
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from .utils import get_environment_hash


def empty_cache() -> Dict:
    return {
        'requirements_caches': {},
        'backbone_hash': None,
        'combined_hash': None,
        'environment_hash': None,
        'resolved_packages': {},
        'conflicts': [],
        'last_updated': None,
    }


def load_cache(cache_file: str, logger: logging.Logger) -> Dict:
    """Load the cache, filling in missing keys. A missing or unreadable file gives an empty cache."""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
            for key, value in empty_cache().items():
                cache.setdefault(key, value)
            return cache
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load dependency cache: {e}")
    return empty_cache()


def save_cache(cache_file: str, cache_data: Dict, logger: logging.Logger):
    """Save the cache atomically, so a crash can't leave half a file."""
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp_file = f"{cache_file}.tmp"
        with open(tmp_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp_file, cache_file)
    except IOError as e:
        logger.error(f"Failed to save dependency cache: {e}")


def reset_for_new_environment(cache: Dict, logger: logging.Logger) -> Dict:
    """
    Drop everything resolved for the old environment (Python version, OS, CPU) but
    keep the registered modules, so modules added one call at a time aren't lost.
    """
    if cache.get('environment_hash') in (None, get_environment_hash()):
        return cache
    logger.info("Environment changed (Python version/OS/architecture), re-resolving every module")
    fresh = empty_cache()
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


def build_cache(
    requirements: List[Tuple[str, str]],
    meta: Dict[str, Dict],
    module_hashes: Dict[str, str],
    combined_hash: str,
    pins: Dict[str, str],
    per_module: Dict[str, Dict[str, str]],
    conflicts: List[Dict],
    backbone_hash: Optional[str] = None,
) -> Dict:
    """
    The cache after a successful resolve.

    Each module entry holds its requirements text (so the full set can be rebuilt),
    hash, registration order and source (explicit/discovered), the packages it pulls
    in, and the packages skipped for it because of a major-version conflict.
    """
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
