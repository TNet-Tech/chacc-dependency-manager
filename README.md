***

# ChaCC Dependency Manager (Pip Accelerator)

**Smart dependency resolution with intelligent caching.** ChaCC Dependency Manager (Cache-Checked) is a dependency manager for Python that is up to 20x faster than pip for repeated installations. It's designed to accelerate your development workflows, especially in environments like Docker and CI/CD pipelines.

## Our Users
Our users includes but not limited to: 
- [ChaCC API](https://pypi.org/project/chacc-api/)

Want to be part of this history making list. Kindly, start to use it now

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

You can install ChaCC Dependency Manager using pip. For most users, installing it with the `[resolver]` extra is recommended as it includes the command-line interface (CLI).

```bash
# Recommended installation with CLI commands
pip install chacc-dependency-manager[resolver]

# Basic installation (library only)
pip install chacc-dependency-manager

# Full development setup
pip install chacc-dependency-manager[full]
```

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
```

#### **Upgrading Dependencies**

```bash
# Auto-discover and upgrade all packages in requirements files
cdm upgrade

# Upgrade from a specific requirements file
cdm upgrade -r requirements-dev.txt

# Upgrade specific packages
cdm upgrade requests fastapi
```

#### **Managing the Cache**

```bash
# View cache information
cdm cache --info

# Clear the entire cache
cdm cache --clear

# Clear the cache for a specific module
cdm cache --clear --module auth
```

#### **Resolving Dependencies Without Installing**

You can see what ChaCC Dependency Manager *would* install without actually installing the packages.

```bash
# Check what would be installed from requirements.txt
cdm resolve

# Check for a specific file
cdm resolve -r requirements-dev.txt
```

#### **Checking Package Installation Status**

Verify that cached packages are actually installed and match expectations.

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
RUN pip install chacc-dependency-manager[resolver]

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
  uses: actions/cache@v3
  with:
    path: .dependency_cache
    key: deps-${{ hashFiles('requirements*.txt', 'pyproject.toml') }}

- name: Install dependencies
  run: cdm install
```

## Advanced Topics

### Auto-Discovery of Requirements

 Dependency Manager can automatically discover requirement files based on common patterns.

**Supported Patterns:**
*   `"requirements.txt"` (default)
*   `"*.txt"`
*   `"requirements-*.txt"`

ChaCC Dependency Manager will search for these files in the current directory or a specified `modules_dir`.

### Custom Hooks for Deeper Integration

For advanced use cases, you can inject your own logic into the resolution process using hooks.

*   `pre_resolve_hook`: A function that runs *before* dependencies are resolved.
*   `post_resolve_hook`: A function that runs *after* dependencies are resolved.
*   `install_hook`: A function to handle the installation of packages, allowing you to override the default installation logic.

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
    install_timeout: int = 60
    max_retries: int = 5
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
        install_timeout: int = 60,
        max_retries: int = 5
    )
```

```python
class DependencyManager:
    async def resolve_dependencies(self, ...): ...
    async def upgrade_dependencies(self, ...): ...
    def invalidate_cache(self): ...
    def invalidate_module_cache(self, module_name: str): ...
    def install_missing_packages(self, resolved_packages, installed_packages): ...
```

## Recent Updates (v1.3.0)

This release adds robust retry logic and configurable timeouts for package installation, ensuring transient network failures do not abort the entire installation process.

### Installation Retry and Timeout

Package installation now retries failed pip install attempts with exponential backoff:

- **Default timeout**: 60 seconds per pip install attempt
- **Default retries**: 5 attempts per package batch
- **Backoff**: Exponential (1s, 2s, 4s, 8s, 16s, capped at 30s)

Configure via CLI:
```bash
cdm install --timeout 120 --max-retries 10
```

Or programmatically via the Config object or DependencyManager constructor:
```python
# Via Config
config = Config(install_timeout=120, max_retries=10)
asyncio.run(re_resolve_dependencies(config=config))

# Via DependencyManager
dm = DependencyManager(install_timeout=120, max_retries=10)
```

The resolution phase (`piptools compile`) runs without a timeout. Only the pip install phase uses the configurable timeout and retry logic.

## Recent Updates (v1.2.0)

This major release introduces a complete API overhaul, advanced module-based caching, and comprehensive improvements to user experience and performance.

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

*   **Parallel Resolution**: Resolve dependencies for multiple modules at the same time.
*   **Dependency Graph Visualization**: Create visual representations of your project's dependencies.
*   **Security Scanning**: Integrate with vulnerability scanners to check your dependencies.

## Contributing

Contributions are welcome! If you'd like to contribute, please ensure that all tests pass, include comprehensive documentation, and follow semantic versioning.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.