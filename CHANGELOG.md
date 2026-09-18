# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
