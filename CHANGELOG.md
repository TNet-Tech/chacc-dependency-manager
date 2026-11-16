# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] - 2026-11-12

This release fixes how packages are resolved, cached and installed. **Upgrading clears every existing
cache once**, because the environment hash changed (see below). The first run after upgrading does a
full resolve.

The 1.3.0 public API and CLI still work unchanged: every export, method, parameter and flag still exists
with the same order, and no new parameter is required. The behavior changes to plan for:

- resolution failures now raise `ResolutionError` from `resolve_dependencies()` / `re_resolve_dependencies()`,
  and `cdm install` exits with status 1, instead of carrying on with nothing cached for that module
- `cdm resolve` no longer installs, matching its help text. Use `cdm resolve --install` for the old behavior.
- the default pip install timeout is 300s instead of 60s

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
- `cdm resolve --install`: prints the resolved pins, then installs what's missing or at the wrong version
  (the 1.3.0 behavior of `cdm resolve`, plus the printed list)
- `-p/--pattern` and `--search-dirs` on `cdm install`, so auto-discovery options no longer require
  `cdm resolve`
- `no_deps=` parameter on `install_missing_packages()`
- `chacc.utils` helpers: `merge_with_version_policy()`, `format_conflict()`, `major_version_key()`,
  `version_satisfies()`, `parse_lock_file()`, `attribute_packages()`
- `DependencyManager.packages_needing_install()`, which reports missing packages and packages installed
  at the wrong version
- `chacc.utils.get_installed_versions()`, which returns `{canonical_name: version}`
- The install target (site-packages path, interpreter, virtualenv or not) is logged before pip runs
- Regression tests for every fix and for the conflict policy (`tests/test_package_fixes.py`)

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
  discovered modules: a deleted file counts as removed, and explicitly added modules are kept.
- `invalidate_module_cache()` / `cdm cache --clear --module` now force a re-resolve and keep the module
  registered. Use `remove_module()` / `--remove` to drop it.
- `upgrade_dependencies()` / `cdm upgrade` with no arguments upgrades every registered module. Called
  with modules (`cdm upgrade -r FILE`, `cdm upgrade PKG...`), it upgrades only those modules' packages
  (pip-compile `--upgrade-package`), and every other module keeps its pins.
- The concurrency lock is shared by every `DependencyManager` that uses the same cache directory (for
  example managers created through `Config`), not just one instance
- Hooks run in a worker thread when the async methods are used
- Each module's cache entry stores its requirements, registration order and source (explicit or
  discovered). Its `packages` lists everything the module pulls in, directly or through dependencies.
  That list comes from pip-compile's `# via` annotations, or from the module's own resolution after a
  conflict.
- `cdm install -r FILE` / `cdm upgrade -r FILE` / `cdm resolve -r FILE` register the file under its name
  without the extension, the same name auto-discovery uses. They used to use the path as given, so the
  same file could end up registered twice.
- `cdm install PKG...` / `cdm upgrade PKG...` keep the packages as a module named `cli` between runs
- `resolve_dependencies()`, `upgrade_dependencies()` and `re_resolve_dependencies()` run their pip work
  in a worker thread, and they return the resolved pins. Concurrent calls for the same cache directory
  run one at a time (within one process).
- Default `install_timeout` raised from 60s back to 300s per pip install attempt (per batch of up to 50
  packages). `--timeout 0` / `install_timeout=None` disables it.
- `resolve_module_dependencies()` still returns `{}` on failure (and logs the error), as before. The new
  `raise_on_error=True` parameter raises `ResolutionError` instead, so a failure can be told apart from
  "no dependencies".
- `cdm resolve` is now the dry run its help text always described: it prints the resolved pins and how
  many packages would be installed or changed, without installing. Scripts that used `cdm resolve` to
  install should use `cdm resolve --install` (or `cdm install`).
- `cdm check` reports version mismatches and major-version conflicts, not just missing packages
- `cdm cache --info` lists each module with its package count and skipped packages, plus the conflicts
  that were handled
- `cdm upgrade` keeps the existing cache until the upgrade resolves successfully. It used to clear the
  cache first.
- pip-compile runs with `--strip-extras`, so extras' dependencies are pinned as normal packages
- Resolution errors show pip's actual message without the Python traceback
- pip install output is no longer written to the debug log (only pip's "Successfully installed" line)
- `merge_resolved_packages()` is kept for backward compatibility but the resolver no longer uses it

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
