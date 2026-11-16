"""
Command-line interface for the Dependency Manager.

Provides pip-like commands for dependency management with intelligent caching.
"""

import argparse
import sys
import logging
from pathlib import Path

from .chacc import DependencyManager
from .utils import (
    absolutize_requirement_paths, canonicalize_name, base_package_name, format_conflict, get_installed_versions,
    module_name_for_path, version_satisfies,
)


def setup_logging(verbose: bool = False):
    """Set up logging for CLI usage."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s'
    )
    # The library logger has its own handler (and DEBUG level) for use without any
    # logging setup. Under the CLI, let the root handler print it once, at the CLI level.
    library_logger = logging.getLogger('dependency_manager')
    for handler in list(library_logger.handlers):
        library_logger.removeHandler(handler)
    library_logger.setLevel(level)
    library_logger.propagate = True


def _make_manager(args) -> DependencyManager:
    """Build a DependencyManager from the global CLI options."""
    resolve_timeout = getattr(args, 'resolve_timeout', 600)
    install_timeout = getattr(args, 'timeout', 300)
    return DependencyManager(
        cache_dir=args.cache_dir,
        install_timeout=install_timeout if install_timeout and install_timeout > 0 else None,
        max_retries=getattr(args, 'max_retries', 5),
        resolve_timeout=resolve_timeout if resolve_timeout and resolve_timeout > 0 else None,
        constraint_files=getattr(args, 'constraint', None),
        on_conflict=getattr(args, 'on_conflict', 'resolve'),
    )


def _requirements_file(path: str) -> dict:
    """``-r FILE`` as a module: ``requirements.txt`` -> ``requirements``, ``api/reqs.txt`` -> ``api/reqs``."""
    # Paths inside the file (-r base.txt, -e ./lib) are relative to the file itself.
    content = absolutize_requirement_paths(Path(path).read_text(), str(Path(path).resolve().parent))
    return {module_name_for_path(path): content}


def cmd_install(args):
    """Install packages with dependency resolution."""
    setup_logging(args.verbose)

    dm = _make_manager(args)

    if args.requirements:
        print(f"Installing from {args.requirements}...")
        requirements = _requirements_file(args.requirements)
        dm.resolve_dependencies_sync(requirements)
    elif args.packages:
        print(f"Installing packages: {', '.join(args.packages)}...")
        requirements = {"cli": "\n".join(args.packages)}
        dm.resolve_dependencies_sync(requirements)
    else:
        print("Auto-discovering and installing requirements...")
        dm.resolve_dependencies_sync(
            requirements_file_pattern=getattr(args, 'pattern', 'requirements.txt'),
            search_dirs=getattr(args, 'search_dirs', None),
        )

    print("✅ Installation completed")


def cmd_resolve(args):
    """Resolve dependencies and print the result; install only with --install."""
    setup_logging(args.verbose)

    dm = _make_manager(args)

    if args.requirements:
        requirements = _requirements_file(args.requirements)
    else:
        requirements = None

    install = getattr(args, 'install', False)
    print("Resolving dependencies...")
    pins = dm.resolve_dependencies_sync(
        modules_requirements=requirements,
        requirements_file_pattern=args.pattern,
        search_dirs=args.search_dirs,
        install=install,
        dry_run=not install,
    )

    for package_name, version_spec in sorted(pins.items(), key=lambda item: item[0].lower()):
        print(f"   {package_name}{version_spec}")
    if install:
        print(f"✅ Dependencies resolved and installed: {len(pins)} packages")
        return

    missing, wrong_version, _ = dm.packages_needing_install(pins, get_installed_versions())
    print(f"✅ Dependencies resolved: {len(pins)} packages "
          f"({len(missing)} would be installed, {len(wrong_version)} would change version). "
          f"Nothing was installed or saved; use --install to apply")


def cmd_cache(args):
    """Manage dependency cache."""
    setup_logging(args.verbose)

    dm = DependencyManager(cache_dir=args.cache_dir)

    if getattr(args, 'remove', False):
        if not args.module:
            print("❌ --remove requires --module <name>")
            return
        if dm.remove_module(args.module):
            print(f"✅ Removed module: {args.module} (its pins are dropped on the next install)")
        else:
            print(f"ℹ️  Module not in cache: {args.module}")
    elif args.clear:
        if args.module:
            dm.invalidate_module_cache(args.module)
            print(f"✅ Cleared cache for module: {args.module}")
        else:
            dm.invalidate_cache()
            print("✅ Cleared entire dependency cache")
    elif args.info:
        cache = dm.load_cache()
        print(f"Cache directory: {dm.cache_dir}")
        print(f"Combined hash: {cache.get('combined_hash', 'None')}")
        print(f"Last updated: {cache.get('last_updated', 'Never')}")
        print(f"Resolved packages: {len(cache.get('resolved_packages', {}))}")
        module_caches = cache.get('requirements_caches', {})
        print(f"Module caches: {len(module_caches)}")
        for module_name, module_cache in sorted(module_caches.items()):
            skipped = module_cache.get('skipped_packages') or []
            note = f" (skipped due to major-version conflict: {', '.join(skipped)})" if skipped else ""
            print(f"   • {module_name}: {len(module_cache.get('packages', {}))} packages{note}")
        conflicts = cache.get('conflicts') or []
        print(f"Conflicts handled: {len(conflicts)}")
        for conflict in conflicts:
            print(f"   • {format_conflict(conflict)}")


def cmd_check(args):
    """Check cached packages against installed packages."""
    setup_logging(args.verbose)

    dm = DependencyManager(cache_dir=args.cache_dir)
    cache = dm.load_cache()

    resolved_packages = cache.get('resolved_packages', {})
    if not resolved_packages:
        print("❌ No cached packages found. Run 'cdm install' first.")
        return

    from . import utils
    installed_packages = utils.get_installed_versions()

    print(f"Checking {len(resolved_packages)} cached packages against {len(installed_packages)} installed packages...")

    missing_packages = []
    extra_packages = []
    version_mismatches = []

    for package_name, version_spec in resolved_packages.items():
        canonical_name = canonicalize_name(base_package_name(package_name))

        if canonical_name not in installed_packages:
            missing_packages.append(f"{package_name}{version_spec}")
        elif not version_satisfies(installed_packages[canonical_name], version_spec):
            version_mismatches.append(
                f"{package_name}: expected {version_spec.lstrip('=')}, installed {installed_packages[canonical_name]}"
            )

    cached_canonical_names = {
        canonicalize_name(base_package_name(package_name)) for package_name in resolved_packages
    }

    for installed_name in installed_packages:
        if installed_name not in cached_canonical_names:
            if args.all:
                extra_packages.append(installed_name)

    conflicts = cache.get('conflicts') or []
    major_conflicts = [c for c in conflicts if c.get('kind') == 'major']
    if major_conflicts:
        print(f"⚠️  {len(major_conflicts)} major-version conflicts (some module requirements were skipped):")
        for conflict in major_conflicts:
            print(f"   • {format_conflict(conflict)}")

    if not missing_packages and not version_mismatches:
        print("✅ All cached packages are properly installed")
        if extra_packages and args.all:
            print(f"ℹ️  {len(extra_packages)} additional packages installed but not in cache")
    else:
        if missing_packages:
            print(f"❌ {len(missing_packages)} cached packages are missing:")
            for package in missing_packages:
                print(f"   • {package}")

        if version_mismatches:
            print(f"⚠️  {len(version_mismatches)} version mismatches found:")
            for mismatch in version_mismatches:
                print(f"   • {mismatch}")

        if extra_packages and args.all:
            print(f"ℹ️  {len(extra_packages)} additional packages installed but not in cache:")
            for package in extra_packages:
                print(f"   • {package}")


def cmd_outdated(args):
    """Show packages that have newer versions available."""
    setup_logging(args.verbose)

    dm = DependencyManager(cache_dir=args.cache_dir)
    cache = dm.load_cache()

    resolved_packages = cache.get('resolved_packages', {})
    if not resolved_packages:
        print("❌ No cached packages found. Run 'cdm install' first.")
        return

    print(f"Checking {len(resolved_packages)} packages for available updates...")

    try:
        import subprocess
        import sys

        result = subprocess.run([
            sys.executable, '-m', 'pip', 'list', '--outdated', '--format=json'
        ], capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            print(f"❌ Failed to check for outdated packages: {result.stderr}")
            return

        import json
        outdated_data = json.loads(result.stdout)

        # Filter to only packages in our cache
        cached_package_names = {
            canonicalize_name(base_package_name(package_name)) for package_name in resolved_packages
        }

        outdated_cached_packages = []
        for package_info in outdated_data:
            package_name = canonicalize_name(package_info['name'])
            if package_name in cached_package_names:
                current_version = package_info['version']
                latest_version = package_info['latest_version']
                outdated_cached_packages.append({
                    'name': package_info['name'],
                    'current': current_version,
                    'latest': latest_version
                })

        if not outdated_cached_packages:
            print("✅ All cached packages are up-to-date")
        else:
            print(f"📦 {len(outdated_cached_packages)} packages have newer versions available:")
            for package in outdated_cached_packages:
                print(f"   • {package['name']}: {package['current']} → {package['latest']}")

    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        print(f"❌ Error checking for outdated packages: {e}")


def cmd_upgrade(args):
    """Upgrade packages to their latest versions."""
    setup_logging(args.verbose)

    dm = _make_manager(args)

    if args.requirements:
        print(f"Upgrading from {args.requirements}...")
        requirements = _requirements_file(args.requirements)
        dm.upgrade_dependencies_sync(requirements)
    elif args.packages:
        print(f"Upgrading packages: {', '.join(args.packages)}...")
        requirements = {"cli": "\n".join(args.packages)}
        dm.upgrade_dependencies_sync(requirements)
    else:
        print("Upgrading every registered module (and any newly discovered requirements files)...")
        dm.upgrade_dependencies_sync(
            requirements_file_pattern=getattr(args, 'pattern', 'requirements.txt'),
            search_dirs=getattr(args, 'search_dirs', None),
        )

    print("✅ Upgrade completed")


def cmd_demo(args):
    """Run demonstration scripts to show ChaCC features."""
    setup_logging(args.verbose)

    if args.type == 'modules':
        print("🚀 Running module demo (resolves real packages, installs nothing)...")
        from .demo_module_separation import demo_module_separation
        demo_module_separation()
    elif args.type == 'cache':
        print("🚀 Running cache structure demo...")
        from .demo_cache_structure import demo_cache_structure
        demo_cache_structure()
    else:
        print("Available demos:")
        print("  cdm demo modules  - Register modules one at a time and see how conflicts are handled")
        print("  cdm demo cache    - Show what the cache file records")


def create_parser():
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        description="Intelligent dependency management with caching",
        prog="cdm"
    )

    parser.add_argument(
        "--cache-dir",
        default=".dependency_cache",
        help="Cache directory (default: .dependency_cache)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for each pip install attempt, 0 disables (default: 300)"
    )

    parser.add_argument(
        "--resolve-timeout",
        type=int,
        default=600,
        metavar="SECONDS",
        help="Timeout in seconds for dependency resolution (pip-compile), 0 disables (default: 600)"
    )

    parser.add_argument(
        "-c", "--constraint",
        action="append",
        metavar="FILE",
        help="Constraints file applied to every resolution, e.g. core/pinned packages (repeatable)"
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        metavar="N",
        help="Maximum retry attempts for failed package installs (default: 5)"
    )

    parser.add_argument(
        "--on-conflict",
        choices=["resolve", "fail"],
        default="resolve",
        help="When modules conflict: 'resolve' uses the latest version within the same major and "
             "skips+flags different majors; 'fail' stops with a report (default: resolve)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    install_parser = subparsers.add_parser(
        "install",
        help="Install packages with dependency resolution"
    )
    install_parser.add_argument(
        "packages",
        nargs="*",
        help="Packages to install"
    )
    install_parser.add_argument(
        "-r", "--requirements",
        help="Install from requirements file"
    )
    install_parser.add_argument(
        "-p", "--pattern",
        default="requirements.txt",
        help="File pattern to auto-discover (default: requirements.txt)"
    )
    install_parser.add_argument(
        "--search-dirs",
        nargs="*",
        help="Directories to auto-discover requirements files in (default: current dir)"
    )
    install_parser.set_defaults(func=cmd_install)

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Upgrade packages to their latest versions"
    )
    upgrade_parser.add_argument(
        "packages",
        nargs="*",
        help="Packages to upgrade"
    )
    upgrade_parser.add_argument(
        "-r", "--requirements",
        help="Upgrade from requirements file"
    )
    upgrade_parser.add_argument(
        "-p", "--pattern",
        default="requirements.txt",
        help="File pattern to auto-discover new requirements files (default: requirements.txt)"
    )
    upgrade_parser.add_argument(
        "--search-dirs",
        nargs="*",
        help="Directories to auto-discover requirements files in (default: current dir)"
    )
    upgrade_parser.set_defaults(func=cmd_upgrade)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve dependencies and show the result (installs only with --install)"
    )
    resolve_parser.add_argument(
        "--install",
        action="store_true",
        help="Also install missing/outdated packages (what 'cdm resolve' did in 1.3.0)"
    )
    resolve_parser.add_argument(
        "-r", "--requirements",
        help="Requirements file to resolve"
    )
    resolve_parser.add_argument(
        "-p", "--pattern",
        default="requirements.txt",
        help="File pattern to search for (default: requirements.txt)"
    )
    resolve_parser.add_argument(
        "--search-dirs",
        nargs="*",
        help="Directories to search for requirements files"
    )
    resolve_parser.set_defaults(func=cmd_resolve)

    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage dependency cache"
    )
    cache_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear cache"
    )
    cache_parser.add_argument(
        "--module",
        help="Module name for --clear (force re-resolve) or --remove (unregister)"
    )
    cache_parser.add_argument(
        "--remove",
        action="store_true",
        help="Unregister --module; its pins are dropped on the next install (nothing is uninstalled)"
    )
    cache_parser.add_argument(
        "--info",
        action="store_true",
        help="Show cache information"
    )
    cache_parser.set_defaults(func=cmd_cache)

    check_parser = subparsers.add_parser(
        "check",
        help="Verify cached packages are installed and match expectations"
    )
    check_parser.add_argument(
        "--all",
        action="store_true",
        help="Also show packages installed but not in cache"
    )
    check_parser.set_defaults(func=cmd_check)

    outdated_parser = subparsers.add_parser(
        "outdated",
        help="Show which packages have newer versions available"
    )
    outdated_parser.set_defaults(func=cmd_outdated)

    demo_parser = subparsers.add_parser(
        "demo",
        help="Run demonstration scripts"
    )
    demo_parser.add_argument(
        "type",
        choices=["modules", "cache"],
        help="Type of demo to run"
    )
    demo_parser.set_defaults(func=cmd_demo)

    return parser


def main():
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
