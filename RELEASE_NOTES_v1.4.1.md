# ChaCC Dependency Manager v1.4.1 Release Notes

**Patch Release: Windows Fix**

This release fixes dependency resolution on Windows. If you use ChaCC on Windows, including through ChaCC API, upgrade now. Nothing else changes, and upgrading from 1.4.0 needs no changes to your code or commands.

```bash
pip install -U chacc-dependency-manager
```

## What Was Broken

On Windows, 1.4.0 couldn't resolve dependencies whenever a constraint file was used (`-c`, `constraint_files=`). This affects every ChaCC API installation on Windows, since ChaCC API always passes one. Resolving failed with this misleading error, even though nothing actually conflicted:

```
Modules resolve individually but not together, and no differing versions were found to reconcile. Check the constraint files.
```

The log also showed `Module '...' only resolves without the core constraints`.

**The cause:** pip reads the path after `-c` the way a shell would, so the backslashes in a Windows path were dropped. `C:\Users\you\project\core-constraints.txt` became `C:Usersyouprojectcore-constraints.txt`, a file that doesn't exist. ChaCC then mistook the missing file for a conflict between your requirements files.

Linux and macOS weren't affected.

## What's Fixed

- **Constraint files work on Windows.** ChaCC writes the paths it gives pip with forward slashes (which Windows accepts) and quotes them, so paths containing spaces work too.
- **Includes inside requirements files work on Windows.** `-r base.txt`, `-c constraints.txt` and `-e ./lib` lines had the same problem and are fixed as well.
- **A file that can't be opened is reported as such.** Instead of a misleading conflict message, you now see that a requirements or constraints file couldn't be opened, with pip's own explanation, and ChaCC doesn't start resolving each file separately.
- **The real cause is never hidden.** If ChaCC does fall back to resolving files one by one, the warning includes pip's original error, and so does the final error if that fallback fails.

## Also in This Release

- The tests now also run on Windows in CI, so Windows-only problems like this one are caught before a release.

## Full Details

See [CHANGELOG.md](CHANGELOG.md) for the complete list of changes, and the [1.4.0 release notes](RELEASE_NOTES_v1.4.0.md) for everything new in 1.4.
