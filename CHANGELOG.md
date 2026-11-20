# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.1] - 2026-11-20

A patch release for Windows. Upgrading from 1.4.0 needs no changes.

### Fixed

- **Windows:** resolving failed whenever constraint files were used, with the misleading error "Modules
  resolve individually but not together, and no differing versions were found to reconcile". pip reads
  `-c`/`-r`/`-e` lines with `shlex`, which drops the backslashes in Windows paths, so pip looked for a file
  like `C:UsersJonas…core-constraints.txt`. ChaCC now writes these paths with forward slashes and quotes
  them, so they also work with spaces. Include paths inside requirements files had the same problem and are
  fixed too.
- A requirements or constraints file pip can't open is reported as such, instead of starting the
  per-module conflict check. pip's own explanation is kept in the message when it's the only one, and
  the conflict check's final error includes the original error.

### Changed

- CI also runs the tests on Windows

## [1.4.0] - 2026-11-16

This release fixes how packages are resolved, cached and installed. **The first run after upgrading
does one full resolve**, because the environment hash changed (see below).

The 1.3.0 public API and CLI still work unchanged: every export, method, parameter and flag still exists
with the same order, and no new parameter is required. The behavior changes to plan for:

- resolution failures now raise `ResolutionError` from `resolve_dependencies()` / `re_resolve_dependencies()`,
  and `cdm install` exits with status 1, instead of carrying on with nothing cached for that module
- `cdm resolve` no longer installs, matching its help text. Use `cdm resolve --install` for the old behavior.
- the default pip install timeout is 300s instead of 60s
- Python 3.8 is no longer supported (end of life since October 2024). pip on Python 3.8 keeps installing
  1.3.0 automatically.

### Added

- **Conflict policy for modules that can't all be satisfied together.** ChaCC resolves each module on its
  own and merges the results per package:
  - same major version: the latest requested version is used by every module
  - different major version: the existing module's version is kept, and the newcomer's requirement for
    that package is skipped and flagged. The module still loads.
  - for `0.x` packages the minor version counts as the major (semver), so `0.9` → `0.10` is a major change
  - core constraints always win, even within the same major
  - every adjustment is logged with the modules and versions involved, stored in the cache (`conflicts`,
    plus `skipped_packages` per module), shown by `cdm check` and `cdm cache --info`, and exposed as
    `DependencyManager.last_conflicts`. Major-version conflicts are warned about again on every run until
    the requirements change.
  - if a module only conflicts with the core constraints, it is resolved without them and then merged
    with the core pins winning
  - after a merge, packages are installed exactly as pinned (`--no-deps`), and `pip check` results are
    logged as warnings
  - unchanged modules reuse their cached package set, so only new or changed modules are resolved
    again when a conflict happens
- `log_level` option on `DependencyManager` and `Config` (`"INFO"`, `logging.WARNING`, ...). It sets the
  level of the passed `logger`, or of ChaCC's built-in logger. Not passing it keeps the previous behavior.
- `on_conflict` option on `DependencyManager` and `Config`, plus `--on-conflict {resolve,fail}`.
  `"fail"` raises `ResolutionError` listing each conflicting package with the module and version on each side
- `DependencyManager.remove_module()` / `chacc.remove_module()` / `cdm cache --remove --module NAME` to
  unregister a module. Its pins are dropped on the next resolve.
- `replace=True` on `resolve_dependencies()` treats the passed modules as the complete set
- `ResolutionError`, raised when dependencies can't be resolved, exported from `chacc`. It has `.conflicts`
  and `.module` (the module that can't resolve even on its own, when that's the cause).
- `constraint_files` option on `DependencyManager` and `Config`, and a repeatable `-c/--constraint FILE`
  CLI flag. Constraint files (pip `-c` format, for example your core application's pins) apply to every
  resolution, so modules can add packages but cannot change the core versions. Editing a constraint
  file triggers a new resolve.
- `resolve_timeout` option on `DependencyManager` and `Config`, and a `--resolve-timeout SECONDS` CLI flag
  (default 600s, `0` disables it)
- `resolve_dependencies_sync()` and `upgrade_dependencies_sync()` blocking methods for scripts and CLIs
- `install=` parameter on `resolve_dependencies()` / `resolve_dependencies_sync()`
- `dry_run=` parameter on `resolve_dependencies()` / `resolve_dependencies_sync()`: resolves and returns
  the pins without saving the cache, registering the modules or installing
- `cdm resolve --install`: prints the resolved pins, then installs what's missing or at the wrong version
  (the 1.3.0 behavior of `cdm resolve`, plus the printed list)
- `-p/--pattern` and `--search-dirs` on `cdm install` and `cdm upgrade`, so auto-discovery options no
  longer require `cdm resolve`
- `no_deps=` parameter on `install_missing_packages()`
- `chacc.utils` helpers: `merge_with_version_policy()`, `format_conflict()`, `major_version_key()`,
  `version_satisfies()`, `parse_lock_file()`, `attribute_packages()`
- `DependencyManager.packages_needing_install()`, which reports missing packages and packages installed
  at the wrong version
- `chacc.utils.get_installed_versions()`, which returns `{canonical_name: version}`
- The install target (site-packages path, interpreter, virtualenv or not) is logged before pip runs
- Regression tests for every fix and for the conflict policy (`tests/test_*.py`, grouped by topic)

### Changed

- **New modules are checked against the ones already registered.** 1.3.0 resolved each module on its
  own and merged the results with "last wins", which silently overwrote conflicting pins. Both ways of
  calling `resolve_dependencies()` still work and give the same result:
  - one module per call (for example on each upload, or in a loop): each call resolves that module
    together with the modules registered by earlier calls, whose requirements are kept in the cache
  - several modules in one call (`{"auth": ..., "payments": ...}`): they are resolved together in one
    pip-compile run, which is faster when you already have them all

  Most mismatches were never real conflicts (separate resolves just picked different versions), and
  resolving together settles those on one version. Real conflicts go through the conflict
  policy above. The previous pins are passed to pip-compile as a starting point, so unchanged packages
  keep their versions.
- **Explicit `modules_requirements` are additive.** Each call adds or updates modules, and modules cached
  by earlier calls stay in the set, so modules can be supplied one at a time. The cache now stores each
  module's requirements so the full set can be rebuilt. Auto-discovered files are the full set of
  discovered modules: a deleted file counts as removed, and explicitly added modules are kept. When
  discovery finds no file at all (for example when run from the wrong folder), no module is dropped
  and the registered modules are resolved and installed.
- A change of Python version, OS or architecture re-resolves every registered module from scratch, but
  keeps the modules registered. It used to wipe the whole cache, which would have forgotten modules
  registered one call at a time.
- `invalidate_module_cache()` / `cdm cache --clear --module` now force a re-resolve and keep the module
  registered. Use `remove_module()` / `--remove` to drop it.
- `upgrade_dependencies()` / `cdm upgrade` with no arguments upgrades every registered module, including
  modules discovered earlier with other `-p`/`--search-dirs` options, and adds any newly discovered
  files. Upgrading never unregisters a module. Called
  with modules (`cdm upgrade -r FILE`, `cdm upgrade PKG...`), it upgrades only those modules' packages
  (pip-compile `--upgrade-package`), and every other module keeps its pins.
- Work on a cache directory is serialised across threads and processes: every `DependencyManager` using
  the directory (for example managers created through `Config`) shares one lock, and an OS file lock on
  `<cache_dir>/.lock` makes other processes (server workers, parallel CI jobs) wait their turn. If the file
  system doesn't support locking, a warning is logged and only threads are coordinated.
- Hooks run in a worker thread when the async methods are used
- Each module's cache entry stores its requirements, registration order and source (explicit or
  discovered). Its `packages` lists everything the module pulls in, directly or through dependencies.
  That list comes from pip-compile's `# via` annotations, or from the module's own resolution after a
  conflict.
- `cdm install -r FILE` / `cdm upgrade -r FILE` register the file as a module named after it. A file in
  the current directory gets its name without the extension (`requirements`), the same name
  auto-discovery uses; a file elsewhere keeps its folder (`api/requirements`). They used to use the path
  as given, so the same file could end up registered twice.
- Auto-discovered files are still named after the file without the extension. When several discovered
  files share a name (a `requirements.txt` in each service folder), each one is named after its path
  instead (`services/api/requirements`). 1.3.0 gave them all the same name, so their cache entries
  overwrote each other.
- `cdm install PKG...` / `cdm upgrade PKG...` keep the packages as a module named `cli` between runs
- `resolve_dependencies()`, `upgrade_dependencies()` and `re_resolve_dependencies()` run their pip work
  in a worker thread, and they return the resolved pins. Concurrent calls for the same cache directory
  run one at a time, also across processes.
- Default `install_timeout` raised from 60s back to 300s per pip install attempt (per batch of up to 50
  packages). `--timeout 0` / `install_timeout=None` disables it.
- `resolve_module_dependencies()` still returns `{}` on failure (and logs the error), as before. The new
  `raise_on_error=True` parameter raises `ResolutionError` instead, so a failure can be told apart from
  "no dependencies".
- `cdm resolve` is now the dry run its help text always described: it prints the resolved pins and how
  many packages would be installed or changed, without installing or changing the cache. Scripts that used `cdm resolve` to
  install should use `cdm resolve --install` (or `cdm install`).
- `cdm check` reports version mismatches and major-version conflicts, not just missing packages
- `cdm cache --info` lists each module with its package count and skipped packages, plus the conflicts
  that were handled
- `cdm upgrade` keeps the existing cache until the upgrade resolves successfully. It used to clear the
  cache first.
- pip-compile runs with `--strip-extras`, so extras' dependencies are pinned as normal packages
- Resolution errors show pip's actual message without the Python traceback
- When the package index can't be reached (network or index problem), the error says so and the
  per-module conflict check is skipped, instead of blaming a module's requirements
- The `install_hook` type on `Config` and `DependencyManager` is `Callable[[Dict[str, str], Set[str]], bool]`,
  matching what it has always received (the set of installed package names)
- pip install output is no longer written to the debug log (only pip's "Successfully installed" line)
- `merge_resolved_packages()` is kept for backward compatibility but the resolver no longer uses it
- The code is split into smaller modules, each with one job. `chacc.manager` keeps `DependencyManager`
  and the public API; the work is done by `registry` (module set and hashes), `resolver` and `pip_compile`
  (resolution and the conflict policy), `installer` and `pip_leftovers` (pip installs), `cache` (the cache
  file), `locking` and `errors`. The helpers in `chacc.utils` are grouped into `names`, `versions` and
  `requirements`, and the CLI commands moved to `chacc.commands`. Everything that could be imported
  before (from `chacc`, `chacc.manager`, `chacc.utils` and `chacc.cli`) still can.

### Removed

- Python 3.8 support. Supported versions are 3.9 to 3.14 (3.13 and 3.14 are now tested in CI).

### Fixed

- **Critical:** a failed resolution was cached as a success (`{}` packages with the new hash), so the
  module stayed broken until its requirements changed. Failures now raise and leave the cache untouched,
  so the next run tries again.
- Installed packages were matched by name only, so version changes and `cdm upgrade` were never applied.
  Packages installed at a different version from the pin are now reinstalled.
- Pins from removed requirement files/modules stayed in the cache forever. Removing a module
  (`remove_module()`, or deleting a discovered file) now triggers a new resolve, and its pins are dropped.
  Nothing is uninstalled.
- The `async` API ran blocking subprocesses on the event loop, which froze async servers (for example
  during a module upload) for the whole resolve and install
- pip-compile had no timeout and could hang forever
- pip's fallback to a user install ("Defaulting to user installation…") was hidden by `--quiet`. It is now
  logged as a warning, together with the target directory.
- The environment hash included `platform.release()`, the host kernel version, so a host or kernel
  update (even under Docker) cleared the cache. The hash now covers the Python implementation and
  version, the OS and the CPU architecture.
- Lock-file parsing handles environment markers (`pkg==1.0 ; sys_platform == "win32"`) and direct URL
  requirements (`pkg @ https://…`)
- The cache file is written atomically, so a crash mid-write no longer corrupts it
- `last_updated` holds a real timestamp. It used to hold the parent directory's modification time.
- `cdm outdated` uses canonical package names when matching
- Relative paths inside requirements (`-r base.txt`, `-c constraints.txt`, `-e ./lib`, `./lib`) pointed at the
  wrong place, because the text is resolved from a temporary folder. They are now resolved relative to
  the requirements file (or the current folder for text passed from Python), as pip does.
- Editing a file included with `-r` / `-c` didn't trigger a new resolve. Included files now count toward
  the module's hash.
- Packages that come from a file included with `-r` are credited to the module that includes it (they
  were missing from that module's package list, which the conflict fallback reuses)
- Under the CLI, every log line was printed twice, and debug messages showed without `-v`
- `cdm demo modules` / `cdm demo cache` described the 1.2 behavior and left a `./demo_cache` folder behind.
  They now show modules registered one at a time, the conflict policy and the 1.4 cache layout, using a
  temporary folder.
- The README bundled inside the package described 1.2.0. It is now a short, current guide that links to
  the full documentation.
- `packaging` is declared as a dependency (it was used but only installed through pip-tools)
- When an install attempt timed out or failed while pip was replacing a package, pip's `~package` backup
  folders stayed in site-packages, and every later pip run warned "Ignoring incompletely removed
  distribution". ChaCC now removes the folders left by its own attempt and reinstalls that batch cleanly
  (`--force-reinstall --no-deps`) on the retry. Leftovers that were already there are reported once,
  with their paths, and left alone.
- Building the package no longer shows setuptools' license deprecation warnings: the license is the SPDX
  expression `MIT` and the license classifier is gone (requires setuptools 77+ to build)
- `cdm upgrade PKG` / `cdm upgrade -r FILE` cleared every other module from the cache

## [1.3.0] - 2026-09-18

### Added

- Retry logic for `pip install` with configurable max attempts (default: 5)
- Exponential backoff between retry attempts (1s, 2s, 4s, 8s, capped at 30s)
- `--max-retries` CLI flag for global retry configuration
- `--timeout` CLI flag for per-attempt pip install timeout configuration
- `install_timeout` parameter on `DependencyManager` class
- `install_timeout` and `max_retries` fields on `Config` dataclass
- Separate CHANGELOG.md following Keep a Changelog conventions

### Changed

- Reduced default pip install timeout from 300s to 60s per attempt
- Package installation now retries transient failures instead of failing immediately
- Retry attempts and backoff delays are logged for visibility
- Final error message now reports the number of failed attempts

### Fixed

- (No fixes in this release)

## [1.2.0] - 2025-09-18

### Added

- Three-tier API architecture: simple functions, Config object, and DependencyManager class
- Module-based incremental dependency resolution with independent per-module caching
- Selective resolution: only re-resolve changed modules
- Individual module cache invalidation via `cdm cache --clear --module <name>`
- Canonical package name normalization (handles `package-name` vs `package_name`)
- Package extras support (proper handling of `package[extra]` specifications)
- `cdm check` command to verify cached packages are installed
- `cdm outdated` command to show packages with newer versions available
- `cdm upgrade` command to update packages to latest versions
- `cdm demo modules` and `cdm demo cache` demonstration commands
- Pre/post resolution hooks and custom installation hooks
- Full type hints and IDE support
- Comprehensive API reference documentation

### Changed

- Default cache directory changed to absolute paths
- Cache validation logic improved for package extras handling
- Logging messages replaced with specific status indicators
- Function signatures enhanced with optional config parameter

### Fixed

- Cache validation logic: fixed package extras handling (`passlib[bcrypt]` detection)
- Package name normalization: consistent hyphen/underscore handling
- Misleading messages: replaced generic messages with specific status indicators
- Module cache invalidation: proper per-module cache clearing
- Path resolution: absolute paths for cache directories

## [1.1.0] - 2025-08-15

### Added

- Initial public release
- Smart caching with hash-based change detection
- Requirements.txt auto-discovery across directories
- Batch pip install optimization
- Package skipping for already-installed dependencies

### Changed

- (N/A)

### Fixed

- (N/A)

## [1.0.0] - 2025-01-01

### Added

- Foundation dependency management framework
- Core DependencyManager class with caching
- Basic CLI interface (`cdm install`, `cdm cache`, `cdm resolve`)
- Requirements file parsing and discovery

### Changed

- (N/A)

### Fixed

- (N/A)
