# ChaCC Dependency Manager v1.4.0 Release Notes

**Reliability Release: Correct Resolves, Version-Aware Installs & Settled Version Conflicts**

This release fixes how packages are resolved, cached and installed. Your existing 1.3.0 code and `cdm` commands keep working. Read [Before You Upgrade](#before-you-upgrade) first, because a few behaviors changed.

## Before You Upgrade

- **The first run does one full resolve.** The cache key changed, so the first run after upgrading resolves everything again. Later runs use the cache as usual.
- **Failures are no longer silent.** If dependencies can't be resolved, `resolve_dependencies()` / `re_resolve_dependencies()` raise `chacc.ResolutionError` and `cdm install` exits with status 1. 1.3.0 carried on with nothing cached for that module.
- **`cdm resolve` is now a dry run**, as its help text always said. It prints the pins and changes nothing. Use `cdm resolve --install` (or `cdm install`) to install.
- **The pip install timeout is back to 300s** per attempt (1.3.0 used 60s, which killed large installs).
- **Python 3.9 or newer is required.** Python 3.8 is end of life; on Python 3.8, pip keeps installing 1.3.0 automatically.

## Highlights

### Requirements Files Are Checked Against Each Other
ChaCC manages each requirements file (or each set of requirements passed from Python) as a separate module. 1.3.0 resolved each one on its own and merged the results with "last wins", which silently overwrote pins. Now each resolve checks them together, so every file shares one consistent set of versions. This holds whether the files come in one run or across several:

```bash
cdm install -p "requirements-*.txt" --search-dirs services/api services/worker   # all at once
cdm install -r requirements.txt && cdm install -r requirements-dev.txt           # one after another
```

```python
dm = DependencyManager()
dm.resolve_dependencies_sync({"api": api_requirements, "worker": worker_requirements})
```

### Clashing Versions Are Settled, Not Fatal
When two requirements files ask for versions of a package that can't be satisfied together, ChaCC settles it instead of failing:

| Situation | What happens |
| :--- | :--- |
| **Same major version** (`1.26.5` vs `1.26.18`) | The latest is installed for everyone |
| **Different major version** (`2.x` vs `1.x`) | The version from the file added first is kept; the other file's requirement for that package is skipped and flagged, and the rest of that file is still installed |
| **`0.x` packages** | The minor version counts as the major (`0.9` → `0.10` is a major change) |
| **Constraint files** | Always win |

Each clash is logged with the files and versions on each side, and shown by `cdm check`, `cdm cache --info` and `DependencyManager.last_conflicts`. Prefer to stop instead? Use `--on-conflict fail` or `on_conflict="fail"`.

A file that contradicts itself (for example `idna==3.7` in a file it includes and `idna==3.8` in the file itself) still fails with pip's error, since there is nothing to settle.

### Protect Your Core Packages
Constraint files (pip `-c` format) apply to every resolve, so your other requirements files can add packages but can't move your application's core versions:

```bash
cdm -c core-constraints.txt install
```

```python
DependencyManager(constraint_files=["core-constraints.txt"])
```

### Installs That Match the Pins
- Packages installed at a different version than the pin are now reinstalled, so upgrades are actually applied. 1.3.0 only checked package names.
- `cdm upgrade` upgrades every registered module. `cdm upgrade PKG` or `cdm upgrade -r FILE` upgrades only those packages and leaves the other modules' pins alone.
- `cdm check` now reports version mismatches and major-version conflicts, not just missing packages.

### ⚡ Safe for Servers and Parallel Jobs
- The async API runs pip in a worker thread, so async applications no longer freeze while dependencies install.
- Work on one cache directory happens one at a time, across threads **and** processes (several server workers or parallel CI jobs can share a cache).
- pip-compile has a timeout (`--resolve-timeout`, default 600s).

## New Options

### Python API
```python
DependencyManager(
    constraint_files=["core-constraints.txt"],  # core pins that always win
    on_conflict="resolve",                      # or "fail"
    resolve_timeout=600,                        # seconds per pip-compile run
    log_level="INFO",                           # or logging.WARNING, ...
)

dm.resolve_dependencies_sync(...)   # blocking versions for scripts
dm.upgrade_dependencies_sync(...)
dm.resolve_dependencies(..., dry_run=True)   # resolve without saving or installing
dm.remove_module("payments")                 # unregister a module
dm.last_conflicts                            # conflicts from the last run
```

The same options are available on `Config`.

### CLI
```bash
cdm resolve                                  # dry run: print the pins, change nothing
cdm resolve --install                        # print the pins, then install
cdm install -p "requirements-*.txt" --search-dirs services/api services/worker
cdm upgrade -p "requirements-*.txt" --search-dirs services/api services/worker
cdm cache --remove --module NAME             # unregister a module
cdm -c core-constraints.txt install          # apply constraints
cdm --on-conflict fail install               # stop on conflicts
cdm --resolve-timeout 900 install            # pip-compile timeout
```

## Critical Bug Fixes

- **Failed resolutions were cached as success**, so a module stayed broken until its requirements changed. Failures now leave the cache untouched and the next run tries again.
- **Version changes were never installed**, because installed packages were matched by name only.
- **Removed modules' pins stayed in the cache forever.** Removing a module or deleting a discovered file now drops its pins (nothing is uninstalled).
- **Async servers froze** during resolves and installs.
- **pip-compile could hang forever.** It now has a timeout.
- **Host or kernel updates cleared the cache** (even under Docker). The cache key no longer includes the kernel version.
- **Same-named requirements files overwrote each other.** A `requirements.txt` in each service folder now becomes its own module (`services/api/requirements`).
- **Relative paths inside requirements files broke.** `-r base.txt`, `-c constraints.txt`, `-e ./lib` and `./lib` now work, and editing an included file triggers a new resolve.
- **Interrupted installs left pip leftovers** that made pip warn "Ignoring incompletely removed distribution". ChaCC now cleans up after its own interrupted attempts and reinstalls cleanly.
- **pip's fallback to a user install went unnoticed.** It is now logged as a warning, with the install location.

## Other Improvements

- Clearer errors: pip's actual message without the Python traceback, and network problems are reported as such instead of as a conflict.
- The cache file is written atomically, so a crash can't corrupt it.
- CLI log lines are no longer printed twice.
- `cdm demo modules` and `cdm demo cache` show the 1.4 behavior.
- The code is split into smaller, focused modules. Everything you could import before still imports from the same place.
- No more setuptools license warnings when building.
- Tested on Python 3.9 to 3.14.

## Full Details

See [CHANGELOG.md](CHANGELOG.md) for every change, and the [README](README.md) for the conflict policy, constraints and the full API reference.

**Upgrade with `pip install -U chacc-dependency-manager`**
