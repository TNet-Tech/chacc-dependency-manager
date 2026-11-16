# ChaCC Dependency Manager

**Smart dependency resolution with intelligent caching.** ChaCC (Cache-Checked) resolves your requirements with pip-tools, caches the result, and on later runs only installs what is missing or at the wrong version. Repeated installs (Docker rebuilds, CI) finish in seconds.

This is a short guide bundled with the package. The full documentation, including the conflict policy, constraints and the complete API reference, is in the [project README](https://github.com/Jonas1015/chacc-dependency-manager/blob/main/README.md). Changes per version are in the [CHANGELOG](https://github.com/Jonas1015/chacc-dependency-manager/blob/main/CHANGELOG.md).

## Command line

The commands are available as `cdm`, `chacc-dm` and `chacc-dependency-manager`.

```bash
cdm install                                   # discover requirements.txt here, resolve, install
cdm install -r requirements-dev.txt           # a specific file
cdm install fastapi uvicorn                   # packages (kept as the module "cli")
cdm install -p "requirements-*.txt" --search-dirs services/api services/worker

cdm resolve                                   # dry run: print the pins, change nothing
cdm resolve --install                         # print the pins, then install

cdm upgrade                                   # upgrade every registered module
cdm upgrade requests                          # upgrade only these packages

cdm check                                     # missing packages, version mismatches, conflicts
cdm cache --info                              # modules, package counts, conflicts
cdm cache --remove --module NAME              # unregister a module

cdm -c core-constraints.txt install           # protect core versions
cdm --on-conflict fail install                # stop on conflicts instead of resolving them
```

## Python

```python
from chacc import DependencyManager, ResolutionError

dm = DependencyManager(cache_dir=".dependency_cache", constraint_files=["core-constraints.txt"])

# Modules can be registered one per call (e.g. when a plugin is uploaded)...
await dm.resolve_dependencies({"payments": "stripe>=10\nrequests"})

# ...or all at once. Either way, each call checks the passed modules against
# every module registered earlier, and the result is the same.
dm.resolve_dependencies_sync({"auth": "passlib[bcrypt]", "reports": "pandas"})

dm.remove_module("payments")          # its pins are dropped on the next resolve
print(dm.last_conflicts)              # conflicts handled in the last run
```

The async methods run pip in a worker thread, so they don't block an event loop. Work on one cache directory is serialised across threads and processes.

## When modules conflict

- **Same major version**: the latest requested version is used by every module.
- **Different major version**: the module registered first keeps its version. The newcomer's requirement for that package is skipped and flagged, and the module still loads.
- **Core constraints** (`constraint_files` / `-c`) always win.

Conflicts are logged with the modules and versions involved, and shown by `cdm check` and `cdm cache --info`. Use `on_conflict="fail"` to raise `ResolutionError` instead.

## License

MIT
