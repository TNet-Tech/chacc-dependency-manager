# ChaCC Dependency Manager (Pip Accelerator)

**Smart dependency resolution with intelligent caching.** ChaCC Dependency Manager (Cache-Checked) is a dependency manager for Python that is up to 20x faster than pip for repeated installations. It's designed to accelerate your development workflows, especially in environments like Docker and CI/CD pipelines.

## Our Users
Our users include, but are not limited to:
- [ChaCC API](https://pypi.org/project/chacc-api/)

Want to join this list? Start using it today.

## When to Use ChaCC Dependency Manager

ChaCC Dependency Manager is a powerful tool, but it's not always a replacement for pip. Here’s when to choose one over the other:

**Use ChaCC Dependency Manager when you:**
*   Frequently build Docker images.
*   Work with projects that have many dependencies.
*   Manage applications with a modular structure.
*   Need to speed up your CI/CD pipelines.
*   Want automatic and intelligent cache management.

**Keep using pip for:**
*   Simple, single-file projects.
*   One-off installations of a few packages.
*   Basic development environments where speed is not a primary concern.

## Getting Started

Ready to give ChaCC Dependency Manager a try? Here's how to get up and running in a few simple steps.

### Installation

You can install ChaCC Dependency Manager using pip. It needs Python 3.9 or newer, and the plain install includes everything: the Python library, the `cdm` command and pip-tools.

```bash
pip install chacc-dependency-manager

# Development setup (tests, linters, build tools)
pip install "chacc-dependency-manager[dev]"
```

The `[resolver]` extra from earlier versions still works, but it adds nothing anymore.

### 🏃‍♀️ Quick Usage

The easiest way to use ChaCC Dependency Manager is through its command-line interface.

1.  **Navigate to your project directory** that contains a `requirements.txt` file.
    ```bash
    cd your_project
    ```
2.  **Run the install command.** ChaCC Dependency Manager provides a few aliases for its commands: `chacc-dependency-manager`, `chacc-dm`, or the shortest, `cdm`.
    ```bash
    cdm install
    ```

ChaCC Dependency Manager will find your `requirements.txt`, resolve the dependencies, and install them. If you run it again, it will use its smart cache to complete the process almost instantly.

You can also install specific packages directly:
```bash
cdm install fastapi uvicorn
```

Global options can be passed before the subcommand:
```bash
cdm --timeout 120 --max-retries 10 install
```

## Usage Guide

ChaCC Dependency Manager can be used from the command line or programmatically in your Python code.

### Command-Line Interface (CLI)

The CLI is the most common way to interact with ChaCC Dependency Manager.

#### **Installing Dependencies**

```bash
# Auto-discover and install from requirements.txt in the current directory
cdm install

# Install from a specific requirements file
cdm install -r requirements-dev.txt

# Install specific packages
cdm install fastapi uvicorn sqlalchemy

# Auto-discover with a custom pattern and directories
cdm install -p "requirements-*.txt" --search-dirs services/api services/worker
```

Requirements are remembered between runs. Packages passed on the command line are kept as a module named `cli`. A `-r` file is registered as a module named after the file: a file in the current directory gets its name without the extension (`requirements-dev`), the same name auto-discovery gives it, and a file elsewhere also keeps its folder (`-r api/requirements.txt` → `api/requirements`), so files with the same name don't replace each other. To stop managing a module, run `cdm cache --remove --module cli`.

#### **Upgrading Dependencies**

```bash
# Upgrade every registered module (plus any newly discovered requirements files)
cdm upgrade

# Also pick up requirements files elsewhere
cdm upgrade -p "requirements-*.txt" --search-dirs services/api services/worker

# Add a requirements file, then upgrade
cdm upgrade -r requirements-dev.txt

# Add packages (to the `cli` module), then upgrade
cdm upgrade requests fastapi
```

With no arguments, `cdm upgrade` upgrades **every registered module** to the newest versions that work together, including modules discovered earlier with other `-p`/`--search-dirs` options. It never unregisters a module; newly discovered files are added. With `-r FILE` or package names, only those packages are upgraded, and every other module keeps its pinned versions. Both use the same conflict policy as install.


#### **Managing the Cache**

```bash
# View cache information
cdm cache --info

# Clear the entire cache
cdm cache --clear

# Force one module to be re-resolved on the next install
cdm cache --clear --module auth

# Unregister a module (its pins are dropped on the next install; nothing is uninstalled)
cdm cache --remove --module auth
```

#### **Resolving Dependencies Without Installing**

You can see what ChaCC Dependency Manager *would* install without actually installing the packages. `cdm resolve` is a dry run: it prints the resolved pins and how many packages would be installed or changed, and changes nothing, so the cache stays as it was and a `-r` file isn't registered. In Python, use `resolve_dependencies(..., dry_run=True)`.

```bash
# Check what would be installed from requirements.txt
cdm resolve

# Check for a specific file
cdm resolve -r requirements-dev.txt

# Print the resolved pins, then install them
cdm resolve --install
```

> In 1.3.0, `cdm resolve` also installed packages. Use `cdm resolve --install` (or `cdm install`) for that behavior.

#### **Checking Package Installation Status**

Verify that cached packages are installed at the resolved versions. Missing packages, version mismatches and [major-version conflicts](#handling-version-conflicts-between-requirements-files) are all reported.

```bash
# Check if all cached packages are properly installed
cdm check

# Also show packages installed but not in cache
cdm check --all
```

#### **Finding Outdated Packages**

Show which cached packages have newer versions available.

```bash
# Check for available updates to cached packages
cdm outdated
```

### Programmatic Usage (Python)

You can also use ChaCC Dependency Manager within your Python applications to manage dependencies dynamically.

#### **Simple Usage**

For most cases, a single function call is all you need. ChaCC Dependency Manager will automatically handle caching.

```python
import asyncio
from chacc import re_resolve_dependencies

# This function will find, resolve, and install dependencies
asyncio.run(re_resolve_dependencies())
```

#### **Advanced Usage with a Config Object**

If you need more control, you can use a `Config` object to customize behavior, such as setting a custom cache directory or adding hooks.

```python
import asyncio
from chacc import Config, re_resolve_dependencies

# Create a configuration object
config = Config(
    cache_dir='./my_custom_cache',
    pre_resolve_hook=lambda name, req: print(f"Resolving {name}")
)

# Pass the config to the function
asyncio.run(re_resolve_dependencies(config=config))
```

#### **Full Control with `DependencyManager`**

For complete control over every aspect of the dependency resolution process, you can use the `DependencyManager` class.

```python
import asyncio
from chacc import DependencyManager

# Instantiate the manager with your desired settings
dm = DependencyManager(
    cache_dir='./my_cache'
)

# Resolve dependencies
asyncio.run(dm.resolve_dependencies())
```

## Why is ChaCC Dependency Manager Faster?

ChaCC Dependency Manager's primary advantage is its intelligent caching. On the first run, its speed is comparable to pip. However, for subsequent runs, the speed improvements are significant, especially in automated environments.

| Scenario | `pip install` | `chacc-dependency-manager` | Speed Improvement |
| :--- | :--- | :--- | :--- |
| **Docker rebuild (no changes)** | 45s | <2s | **22x faster** ⚡ |
| **CI/CD pipeline** | 60s | 3s | **20x faster** ⚡ |
| **Large monorepo** | 120s | 15s | **8x faster** ⚡ |
| **First run** | 45s | 45s | Same (as expected) |

### Key Features

*   **Smart Caching**: Only re-installs dependencies that have changed.
*   **Incremental Updates**: Avoids full reinstalls, saving significant time.
*   **Multi-File Support**: Handles complex projects with multiple `requirements.txt` files.
*   **Conflict Handling**: All requirements files share one set of versions. When two files clash on the same major version the latest is used; a clash across major versions is skipped and flagged, and the rest of that file is still installed.
*   **Docker-Optimized**: Works seamlessly with Docker's layer caching for faster image builds.
*   **Package Validation**: Verifies that cached packages are still installed correctly.
*   **Debug Logging**: Provides detailed output to help you understand caching behavior.

## Docker and CI/CD Integration

ChaCC Dependency Manager is particularly effective in containerized and automated environments.

### Docker Usage

Here is a simple, cache-friendly Dockerfile that leverages ChaCC Dependency Manager.

```dockerfile
FROM python:3.11-slim

# Install the dependency manager
RUN pip install chacc-dependency-manager

# Copy your requirements files
COPY requirements*.txt ./

# Install dependencies using ChaCC Dependency Manager's intelligent caching
# This step will be almost instant if requirements haven't changed
RUN cdm install

# Copy the rest of your application code
COPY . .

# Run your application
CMD ["python", "app.py"]
```

### CI/CD Pipeline Caching (GitHub Actions)

You can use caching in your CI/CD pipelines to persist the ChaCC Dependency Manager cache between runs.

```yaml
- name: Cache dependencies
  uses: actions/cache@v4
  with:
    path: .dependency_cache
    key: deps-${{ hashFiles('requirements*.txt', 'pyproject.toml') }}

- name: Install dependencies
  run: cdm install
```

## Advanced Topics

### Auto-Discovery of Requirements

ChaCC Dependency Manager can automatically discover requirement files based on common patterns.

**Supported Patterns:**
*   `"requirements.txt"` (default)
*   `"*.txt"`
*   `"requirements-*.txt"`

ChaCC Dependency Manager will search for these files in the current directory, or in the directories given by `search_dirs` (`cdm install --search-dirs ...`). Each file becomes a module named after the file without its extension (`requirements-api`). Paths inside a file (`-r base.txt`, `-c constraints.txt`, `-e ./libs/core`, `./libs/plugin`) are relative to that file, as pip reads them; in requirements text passed from Python they are relative to the current folder. Editing an included file (`-r` / `-c`) re-resolves the modules that include it. When several discovered files share a name, such as a `requirements.txt` in each service folder, each one is named after its path instead (`services/api/requirements`), so none of them is dropped. The discovered files are the complete set: if a file disappears, its pins are dropped on the next run. Modules added explicitly (in code, with `-r`, or as command-line packages) are kept. If no file is found at all (for example when run from the wrong folder), nothing is dropped and the registered modules are used.

### Protecting Core Packages with Constraints

Each resolve checks every registered requirements file (or set of requirements passed from Python) together, whether they came in the same run or in earlier ones, so they share a single, consistent set of versions. See [Handling Version Conflicts Between Requirements Files](#handling-version-conflicts-between-requirements-files) for what happens when they can't agree.

To stop your other requirements files from changing the versions your application depends on, pass those pins as **constraints** (pip `-c` format). A requirements file can add packages, but it can't move a constrained version:

```bash
# core-constraints.txt contains lines like: fastapi==0.115.0
cdm -c core-constraints.txt install
```

```python
dm = DependencyManager(constraint_files=["core-constraints.txt"])
# or
config = Config(constraint_files=["core-constraints.txt"])
```

Editing a constraints file triggers a new resolve on the next run. Constraints always win a conflict, even within the same major version.

### Handling Version Conflicts Between Requirements Files

ChaCC manages each requirements file (or each set of requirements passed from Python) as a separate **module**, named after the file. If they can't all be satisfied together, ChaCC resolves each one on its own and settles each package they disagree on, instead of failing:

| Situation | Example | Result |
| :--- | :--- | :--- |
| Same major version | `requirements.txt` wants `urllib3 1.26.9`, `requirements-dev.txt` wants `1.26.20` | Both get the **latest**, `1.26.20` |
| Different major version | `requirements.txt` wants `urllib3 1.26.20`, `legacy.txt` wants `2.8.0` | The version from the file added first, `1.26.20`, is kept. `legacy.txt`'s requirement for urllib3 is **skipped and flagged**; the rest of `legacy.txt` is still installed. |
| Constraint involved | `core-constraints.txt` pins `urllib3==1.26.5` | The constrained version always wins |

For `0.x` packages the minor version counts as the major (`0.9` → `0.10` is a major change), following semver. Every adjustment is logged with the files and versions on each side:

```
WARNING: ⚠️  Dependency conflict on 'urllib3': requirements wants 1.26.20, legacy wants 2.8.0. Major versions differ,
so 1.26.20 (from requirements) is kept and the requirement from legacy is skipped. legacy may not work correctly
```

A single file that contradicts itself, for example `idna==3.7` in a file it includes with `-r` and `idna==3.8` in the file itself, can't be settled and fails with pip's error.

Conflicts are saved in the cache and shown by `cdm check` and `cdm cache --info`, which also lists each module's skipped packages. Major-version conflicts are warned about again on every run until the requirements change. In code they're available as `dm.last_conflicts`, a list of dicts with `package`, `chosen`, `chosen_from`, `requested` (module → version), `kind` (`"minor"` or `"major"`) and `skipped_modules`.

After a merge, packages are installed exactly as pinned (`pip install --no-deps`), because the merged versions deliberately break some declared requirements. Any requirements left unmet (from `pip check`) are logged as warnings. Only new or changed modules are resolved again when a conflict happens; unchanged ones reuse their cached package set.

To stop on conflicts instead, use `--on-conflict fail` / `on_conflict="fail"`. ChaCC then raises `ResolutionError` naming each package, file and version, and leaves the cache untouched. The error's `.conflicts` holds the same dicts as `last_conflicts`. A file that can't resolve even on its own (for example a misspelled package) always raises `ResolutionError`, with `.module` set to its module name.

### Using ChaCC from Async Code

`resolve_dependencies()`, `upgrade_dependencies()` and `re_resolve_dependencies()` run pip in a worker thread, so awaiting them doesn't block the event loop. Calls that use the same cache directory run one at a time: across separate `DependencyManager` or `Config` instances in one process, and across processes too (for example several server workers or parallel CI jobs), using a lock file (`.lock`) in the cache directory. A process that has to wait logs "Waiting for another process using the cache". On a file system without file locking (some network mounts), ChaCC logs a warning and only coordinates within the process. In scripts, use the blocking `resolve_dependencies_sync()` / `upgrade_dependencies_sync()` instead of `asyncio.run(...)`.

For example, an application that installs the dependencies of plugins added at runtime:

```python
from chacc import DependencyManager, ResolutionError

dm = DependencyManager()

async def on_module_uploaded(name: str, requirements: str):
    try:
        # Additive: modules registered by earlier calls stay part of the set
        await dm.resolve_dependencies({name: requirements})
    except ResolutionError as e:
        # The module can't be resolved even on its own (or on_conflict="fail");
        # the cache is untouched and the next run retries
        log.error(str(e))
    for conflict in dm.last_conflicts:
        if conflict["kind"] == "major":
            log.warning(f"{name}: skipped {conflict['package']}")

async def on_module_deleted(name: str):
    dm.remove_module(name)            # its pins are dropped on the next resolve
    await dm.resolve_dependencies()   # nothing is uninstalled
```

Modules can be passed one per call (for example from an upload handler, or in a loop) or all in a single call. Each call resolves the passed modules **together with every module registered earlier**, so each new module is checked against all the others, and the end result is the same either way:

```python
# One at a time: each call adds a module
for name, requirements in modules.items():
    dm.resolve_dependencies_sync({name: requirements})

# All at once: same pins and conflict outcome, with a single resolve
dm.resolve_dependencies_sync(modules)
```

The loop does one resolve per module, so when you already have every module up front, a single call is faster. With both styles, modules registered earlier win major-version conflicts (dict order counts as registration order in a single call). Pass `replace=True` to make a call the complete module set. Auto-discovered requirement files are always the complete set of discovered files.

### Custom Hooks for Deeper Integration

For advanced use cases, you can inject your own logic into the resolution process using hooks.

*   `pre_resolve_hook`: A function that runs *before* dependencies are resolved.
*   `post_resolve_hook`: A function that runs *after* dependencies are resolved.
*   `install_hook`: A function to handle the installation of packages, allowing you to override the default installation logic. It receives the resolved pins and the set of installed package names (canonicalized), and returns `True` if it handled the install. Use `chacc.utils.get_installed_versions()` if you need the installed versions.

The pre- and post-resolve hooks are called once per module. `post_resolve_hook` receives the packages that module pulls in, directly or through dependencies. When the async methods are used, hooks run in a worker thread, so don't touch event-loop objects from inside them.

```python
def log_start(module_name, requirements):
    print(f"Starting resolution for {module_name}")

def log_complete(module_name, resolved_packages):
    print(f"Resolved {len(resolved_packages)} packages for {module_name}")

dm = DependencyManager(
    pre_resolve_hook=log_start,
    post_resolve_hook=log_complete
)
```

### API Reference

#### `Config` Class
A simple data class for holding configuration.

```python
@dataclass
class Config:
    cache_dir: Optional[str] = None
    logger: Optional[logging.Logger] = None
    pre_resolve_hook: Optional[Callable] = None
    post_resolve_hook: Optional[Callable] = None
    install_hook: Optional[Callable] = None
    install_timeout: Optional[int] = 300       # per pip install attempt; None disables
    max_retries: int = 5
    resolve_timeout: Optional[int] = 600       # per pip-compile run; None disables
    constraint_files: Optional[List[str]] = None
    on_conflict: str = "resolve"               # or "fail"
    log_level: Optional[Union[int, str]] = None  # e.g. "INFO" or logging.WARNING
```

#### `DependencyManager` Class
The core class that manages dependency resolution.

```python
class DependencyManager:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        pre_resolve_hook: Optional[Callable] = None,
        post_resolve_hook: Optional[Callable] = None,
        install_hook: Optional[Callable] = None,
        install_timeout: Optional[int] = 300,
        max_retries: int = 5,
        resolve_timeout: Optional[int] = 600,
        constraint_files: Optional[List[str]] = None,
        on_conflict: str = "resolve",
        log_level: Optional[Union[int, str]] = None
    )
```

```python
class DependencyManager:
    async def resolve_dependencies(self, ..., install=True, replace=False, dry_run=False) -> Dict[str, str]: ...
    def resolve_dependencies_sync(self, ..., install=True, replace=False, dry_run=False) -> Dict[str, str]: ...
    async def upgrade_dependencies(self, ...) -> Dict[str, str]: ...
    def upgrade_dependencies_sync(self, ...) -> Dict[str, str]: ...
    def invalidate_cache(self): ...
    def invalidate_module_cache(self, module_name: str): ...  # force re-resolve
    def remove_module(self, module_name: str) -> bool: ...    # unregister
    last_conflicts: List[Dict]                               # conflicts from the last run
    def install_missing_packages(self, resolved_packages, installed_packages, no_deps=False): ...
    @staticmethod
    def packages_needing_install(resolved_packages, installed_versions): ...  # (missing, wrong_version, ok)
```

`resolve_module_dependencies(module_name, requirements, upgrade=False, raise_on_error=False)` resolves one module without caching. It returns `{}` on failure unless `raise_on_error=True`.

Module-level helpers mirror these and accept `config=`: `re_resolve_dependencies()`, `invalidate_dependency_cache()`, `invalidate_module_cache()` and `remove_module()`.

`log_level` sets how much ChaCC logs, as a name (`"INFO"`, `"warning"`) or a number (`logging.INFO`). It applies to the `logger` you pass, or to ChaCC's own `dependency_manager` logger (shared by every manager that uses it). Left out, nothing changes: the built-in logger logs at DEBUG, as in earlier versions. On the command line, output is INFO, or DEBUG with `-v`.

Resolution failures raise `chacc.ResolutionError` (with `.conflicts` and `.module`). pip install failures (after all retries) raise `subprocess.CalledProcessError`.

## Recent Updates (v1.4.0)

This release fixes how packages are resolved, cached and installed. See [CHANGELOG.md](CHANGELOG.md) for the full list.

> **Upgrade note:** the first run after upgrading does one full resolve, because the environment hash changed. From now on, a change of Python version, OS or architecture re-resolves every registered module but keeps them registered.

- **Failed resolutions are no longer cached.** A failure raises `ResolutionError` and leaves the cache untouched, so the next run tries again.
- **Version-aware installs.** Packages installed at a different version from the resolved pin are reinstalled, so upgrades are applied. `cdm check` reports version mismatches.
- **Requirements files are checked against each other, with a conflict policy.** Every file shares one set of versions. When two files clash: same major version → the latest is used; different major → the version from the file added first is kept, and the other file's requirement is skipped and flagged while the rest of it is still installed. Use `-c/--constraint` to protect core packages.
- **Files and modules can arrive one at a time.** Each `cdm install -r FILE` or explicit module adds to the set. `remove_module()` / `cdm cache --remove --module` unregisters one.
- **Upgrades apply.** `cdm upgrade` upgrades every registered module. `cdm upgrade PKG` / `-r FILE` upgrades only those packages and leaves other modules' pins alone.
- **Compatible with 1.3.0.** Existing CLI commands and Python calls keep working. The main difference: a resolution failure now raises `ResolutionError` (and `cdm` exits with status 1) instead of being cached as an empty result.
- **No stale pins.** Removing a module or a discovered requirements file drops its pins from the cache.
- **Non-blocking async API.** pip runs in a worker thread, so async servers stay responsive.
- **Safe with several processes.** Processes sharing a cache directory take turns, through a lock file.
- **Includes work.** `-r`, `-c`, `-e` and local paths inside requirements files resolve relative to the file, and editing an included file triggers a new resolve.
- **Timeouts.** pip-compile has a timeout (`--resolve-timeout`, default 600s). The pip install timeout default is back to 300s per attempt.
- **Install location is logged.** A fallback to a user install is reported as a warning.
- **No pip leftovers.** If an install is interrupted while pip is replacing a package, ChaCC removes the `~package` folders pip leaves behind and reinstalls cleanly, so pip stops warning "Ignoring incompletely removed distribution".
- **Python 3.9+.** Python 3.8 is no longer supported; on 3.8, pip keeps installing 1.3.0.
- **Stable cache.** The environment hash no longer includes the host kernel version, so host updates don't clear the cache. A Python upgrade re-resolves every module without forgetting the ones registered one call at a time.
- **Clear errors when offline.** If the package index can't be reached, the error says so, instead of being reported as a conflict between modules.
- **`cdm resolve` is a dry run.** It prints what would be installed and changes nothing. `cdm resolve --install` also installs (the 1.3.0 behavior), and `cdm install` / `cdm upgrade` now accept `-p/--pattern` and `--search-dirs`.
- **Same-named requirements files are all kept.** Several `requirements.txt` files in different folders become separate modules named after their path.

```bash
cdm --resolve-timeout 900 --timeout 600 -c core-constraints.txt install
```

## Recent Updates (v1.3.0)

This release adds robust retry logic and configurable timeouts for package installation, ensuring transient network failures do not abort the entire installation process.

### Installation Retry and Timeout

Package installation now retries failed pip install attempts with exponential backoff:

- **Default timeout**: 60 seconds per pip install attempt (raised to 300 seconds in v1.4.0)
- **Default retries**: 5 attempts per package batch
- **Backoff**: Exponential (1s, 2s, 4s, 8s, 16s, capped at 30s)

Configure via CLI:
```bash
cdm --timeout 120 --max-retries 10 install
```

Or programmatically via the Config object or DependencyManager constructor:
```python
# Via Config
config = Config(install_timeout=120, max_retries=10)
asyncio.run(re_resolve_dependencies(config=config))

# Via DependencyManager
dm = DependencyManager(install_timeout=120, max_retries=10)
```

In v1.3.0 the resolution phase (`piptools compile`) ran without a timeout. Since v1.4.0 it is limited by `--resolve-timeout` / `resolve_timeout` (default 600s).

## Recent Updates (v1.2.0)

This major release introduces a complete API overhaul, advanced module-based caching, and comprehensive improvements to user experience and performance.

> Module resolution changed in v1.4.0: modules are now resolved together rather than independently. See [Recent Updates (v1.4.0)](#recent-updates-v140).

### Major New Features

**Three-Tier API Architecture**
- **Simple Functions**: `re_resolve_dependencies()` - Just works with automatic caching
- **Config Object Pattern**: Clean configuration without parameter explosion
- **DependencyManager Class**: Full control over all aspects
- **Backward Compatible**: All existing code continues to work unchanged

**Advanced Module-Based Caching**
- **Module Separation**: Each module caches dependencies independently
- **Selective Resolution**: Only re-resolve changed modules (massive performance gains)
- **Individual Invalidation**: Clear cache for specific modules
- **Hash-Based Change Detection**: Precise tracking of requirement changes

**Intelligent Package Management**
- **Canonical Name Normalization**: Automatic handling of `package-name` vs `package_name`
- **Package Extras Support**: Proper handling of `package[extra]` specifications
- **Package Validation**: Verifies cached packages are actually installed
- **Smart Installation**: Only installs missing packages

**Enhanced Visibility & Debugging**
- **Visual Status Indicators**: ✅⚡📦🔄 for different operation types
- **Detailed Logging**: Clear messages for every cache scenario
- **Debug Information**: Comprehensive visibility into cache operations
- **Performance Metrics**: Understand cache hit/miss ratios

**🛠️ Developer Experience**
- **Demo Commands**: `cdm demo modules` and `cdm demo cache` for visualization
- **Type Safety**: Full type hints and IDE support
- **Extensible Hooks**: Pre/post resolution and custom installation hooks
- **Clean Configuration**: No more parameter explosion

**Package Upgrades**
- **Upgrade Command**: `cdm upgrade` to update all packages to latest versions
- **Selective Upgrades**: Upgrade specific packages or from specific requirements files
- **Smart Cache Handling**: Automatic cache invalidation and refresh for upgrades
- **Environment Awareness**: Respects module/environment separation during upgrades

### API Enhancements

**New Config Class:**
```python
@dataclass
class Config:
    cache_dir: Optional[str] = None
    logger: Optional[logging.Logger] = None
    pre_resolve_hook: Optional[Callable] = None
    post_resolve_hook: Optional[Callable] = None
    install_hook: Optional[Callable] = None

    def create_manager(self) -> DependencyManager: ...
```

**Enhanced Function Signatures:**
```python
# All functions now accept optional config parameter
await re_resolve_dependencies(config=config)
invalidate_dependency_cache(config=config)
invalidate_module_cache(module_name, config=config)
```

**New CLI Commands:**
```bash
cdm upgrade          # Upgrade packages to latest versions
cdm check            # Verify cached packages are installed
cdm outdated         # Show packages with available updates
cdm demo modules     # Show module separation
cdm demo cache       # Show cache structure
```

### Critical Bug Fixes

- **Cache Validation Logic**: Fixed package extras handling (`passlib[bcrypt]` detection)
- **Package Name Normalization**: Consistent hyphen/underscore handling
- **Misleading Messages**: Replaced generic messages with specific status indicators
- **Module Cache Invalidation**: Proper per-module cache clearing
- **Path Resolution**: Absolute paths for cache directories

### Performance Improvements

- **Selective Resolution**: Only resolve changed modules instead of everything
- **Smart Package Checking**: Canonical name matching for accurate validation
- **Efficient Caching**: Module-level granularity reduces unnecessary work
- **Batch Installation**: Optimized pip install operations

### Documentation & Examples

- **Comprehensive API Reference**: All classes, methods, and parameters documented
- **Integration Examples**: FastAPI, Django, Flask usage patterns
- **Migration Guide**: How to upgrade from old API to new three-tier system
- **Demo System**: Interactive visualization of internal mechanics

## Future Enhancements

*   **Dependency Graph Visualization**: Create visual representations of your project's dependencies.
*   **Security Scanning**: Integrate with vulnerability scanners to check your dependencies.

## Contributing

Contributions are welcome! If you'd like to contribute, please ensure that all tests pass, include comprehensive documentation, and follow semantic versioning.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.