"""
Which modules are registered: discovery of requirements files, building the module set
for a resolve, and the hashes that tell whether a module changed.
"""

import glob
import logging
import os
from typing import Dict, List, Optional, Set, Tuple

from .requirements import absolutize_requirement_paths, included_files, module_name_for_path
from .utils import calculate_combined_requirements_hash, calculate_files_hash, calculate_module_hash

CONSTRAINTS_KEY = "__constraints__"

Requirements = List[Tuple[str, str]]


def discover_requirements(
    requirements_file_pattern: str,
    search_dirs: Optional[List[str]],
    logger: logging.Logger,
) -> Requirements:
    """
    Find requirements files matching the pattern in ``search_dirs`` (default: current folder).

    Modules are named after the file without its extension, as in earlier versions.
    Files that share a name (e.g. a ``requirements.txt`` in each service folder) are
    named after their relative path instead, so none of them is dropped.
    """
    found: Requirements = []
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
                    req_content = absolutize_requirement_paths(req_content, os.path.dirname(req_file))
                    found.append((req_file, req_content))
                    logger.info(f"Discovered requirements file: {req_file}")
            except Exception as e:
                logger.warning(f"Failed to read requirements file {req_file}: {e}")

    if not found:
        logger.info(f"No requirements files found matching pattern: {requirements_file_pattern} in {search_dirs}")
    short_names = [module_name_for_path(path, short=True) for path, _ in found]
    return [
        (short if short_names.count(short) == 1 else module_name_for_path(path), content)
        for short, (path, content) in zip(short_names, found)
    ]


def effective_requirements(
    req_caches: Dict[str, Dict],
    modules_requirements: Optional[Dict[str, str]],
    requirements_file_pattern: str,
    search_dirs: Optional[List[str]],
    replace: bool,
    logger: logging.Logger,
    prune: bool = True,
) -> Tuple[Requirements, Dict[str, Dict]]:
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
        incoming = [(n, absolutize_requirement_paths(c, os.getcwd())) for n, c in modules_requirements.items()]
        source = 'explicit'
        keep = {} if replace else {n: e for n, e in registered.items() if n not in modules_requirements}
    else:
        incoming = discover_requirements(requirements_file_pattern, search_dirs, logger)
        source = 'discovered'
        names = {n for n, _ in incoming}
        if not incoming and registered:
            logger.info(f"Using the {len(registered)} registered module(s)")
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
        order = req_caches.get(name, {}).get('order')
        if order is None:
            order, next_order = next_order, next_order + 1
        contents[name] = content
        meta[name] = {'order': order, 'source': source}

    ordered = sorted(contents, key=lambda n: (meta[n]['order'], n))
    return [(name, contents[name]) for name in ordered], meta


def module_hash(name: str, content: str) -> str:
    """
    Hash of a module's requirements and environment. Files included with ``-r``/``-c``
    count as part of the module: editing a shared ``base.txt`` re-resolves every module
    that includes it.
    """
    files = included_files(content)
    if files:
        content = f"{content}\n#included:{calculate_files_hash(files)}"
    return calculate_module_hash(name, content)


def requirement_hashes(requirements: Requirements, constraint_files: List[str]) -> Tuple[Dict[str, str], str]:
    """``({module: hash}, combined_hash)``; the combined hash also covers the constraint files."""
    module_hashes = {name: module_hash(name, content) for name, content in requirements}
    hashes_for_combined = dict(module_hashes)
    if constraint_files:
        hashes_for_combined[CONSTRAINTS_KEY] = calculate_files_hash(constraint_files)
    return module_hashes, calculate_combined_requirements_hash(hashes_for_combined)
