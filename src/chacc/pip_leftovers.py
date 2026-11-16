"""
Leftovers of interrupted pip runs: ``~``-prefixed folders in site-packages.

pip moves a package it is replacing to ``~ackage`` and deletes it once the new version
is in place. If pip is stopped in between (e.g. by the install timeout), the folder
stays behind and every later pip run warns "Ignoring incompletely removed
distribution ~...".
"""

import logging
import os
import shutil
import site
import sysconfig
from typing import Set

_REPORTED_LEFTOVERS: Set[str] = set()


def find_leftovers() -> Set[str]:
    """Paths of ``~``-prefixed entries in the site-packages folders pip installs into."""
    paths = sysconfig.get_paths()
    dirs = {paths.get('purelib'), paths.get('platlib')}
    try:
        dirs.add(site.getusersitepackages())
    except Exception:
        pass
    found: Set[str] = set()
    for directory in dirs:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            found.update(os.path.join(directory, n) for n in os.listdir(directory) if n.startswith('~'))
        except OSError:
            continue
    return found


def remove_new_leftovers(before: Set[str], logger: logging.Logger) -> bool:
    """
    Remove leftovers that appeared since ``before`` was taken, i.e. during our own pip
    run (never ones that were already there). Returns True if anything was found.
    """
    created = sorted(find_leftovers() - before)
    for path in created:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            logger.info(f"Removed leftover of the interrupted pip run: {path}")
        except OSError as e:
            logger.warning(f"Could not remove pip leftover {path}: {e}")
    return bool(created)


def warn_about_existing(leftovers: Set[str], logger: logging.Logger):
    """Report leftovers ChaCC didn't create (once per process); they are left alone."""
    new = leftovers - _REPORTED_LEFTOVERS
    if not new:
        return
    _REPORTED_LEFTOVERS.update(new)
    logger.warning(
        "Found leftovers of an earlier interrupted pip install, which make pip warn "
        "'Ignoring incompletely removed distribution'. ChaCC didn't create them, so it "
        "leaves them alone; they are safe to delete once the package works: "
        + ", ".join(sorted(new))
    )
