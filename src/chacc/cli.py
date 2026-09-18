"""
Command-line interface for the Dependency Manager.

Provides pip-like commands for dependency management with intelligent caching.
"""

import argparse
import asyncio
import sys
import logging
from pathlib import Path

from .chacc import DependencyManager


def setup_logging(verbose: bool = False):
    """Set up logging for CLI usage."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s'
    )


def cmd_install(args):
    """Install packages with dependency resolution."""
    setup_logging(args.verbose)

    dm = DependencyManager(
        cache_dir=args.cache_dir,
        install_timeout=args.timeout,
        max_retries=args.max_retries
    )

    if args.requirements:
        print(f"Installing from {args.requirements}...")
        requirements = {args.requirements: Path(args.requirements).read_text()}
        asyncio.run(dm.resolve_dependencies(requirements))
    elif args.packages:
        print(f"Installing packages: {', '.join(args.packages)}...")
        requirements = {"cli": "\n".join(args.packages)}
        asyncio.run(dm.resolve_dependencies(requirements))
    else:
        print("Auto-discovering and installing requirements...")
        asyncio.run(dm.resolve_dependencies())

    print("✅ Installation completed")


def cmd_resolve(args):
    """Resolve dependencies without installing."""
    setup_logging(args.verbose)

    dm = DependencyManager(
        cache_dir=args.cache_dir,
        install_timeout=args.timeout,
        max_retries=args.max_retries
    )

    if args.requirements:
        requirements = {args.requirements: Path(args.requirements).read_text()}
    else:
        requirements = None

    print("Resolving dependencies...")
    asyncio.run(dm.resolve_dependencies(
        modules_requirements=requirements,
        requirements_file_pattern=args.pattern,
        search_dirs=args.search_dirs
    ))

    print("✅ Dependencies resolved")


def cmd_cache(args):
    """Manage dependency cache."""
    setup_logging(args.verbose)

    dm = DependencyManager(cache_dir=args.cache_dir)

    if args.clear:
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
        print(f"Module caches: {len(cache.get('requirements_caches', {}))}")


def cmd_check(args):
    """Check cached packages against installed packages."""
    setup_logging(args.verbose)

    dm = DependencyManager(cache_dir=args.cache_dir)
    cache = dm.load_cache()

    resolved_packages = cache.get('resolved_packages', {})
    if not resolved_packages:
        print("❌ No cached packages found. Run 'cdm install' first.")
        return

    from .utils import get_installed_packages, canonicalize_name
    installed_packages = get_installed_packages()

    print(f"Checking {len(resolved_packages)} cached packages against {len(installed_packages)} installed packages...")

    missing_packages = []
    extra_packages = []
    version_mismatches = []

    for package_name, version_spec in resolved_packages.items():
        base_name = package_name.split('[')[0] if '[' in package_name else package_name
        canonical_name = canonicalize_name(base_name)

        if canonical_name not in installed_packages:
            missing_packages.append(f"{package_name}{version_spec}")
        else:
            # Check version if we have exact version info
            if version_spec.startswith('=='):
                expected_version = version_spec[2:]  # Remove '=='
                # We would need to get the actual installed version to compare
                # For now, just check presence
                pass

    cached_canonical_names = set()
    for package_name in resolved_packages.keys():
        base_name = package_name.split('[')[0] if '[' in package_name else package_name
        cached_canonical_names.add(canonicalize_name(base_name))

    for installed_name in installed_packages:
        if installed_name not in cached_canonical_names:
            if args.all:
                extra_packages.append(installed_name)

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
        cached_package_names = set()
        for package_name in resolved_packages.keys():
            base_name = package_name.split('[')[0] if '[' in package_name else package_name
            cached_package_names.add(base_name.lower())

        outdated_cached_packages = []
        for package_info in outdated_data:
            package_name = package_info['name'].lower()
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

    dm = DependencyManager(
        cache_dir=args.cache_dir,
        install_timeout=args.timeout,
        max_retries=args.max_retries
    )

    if args.requirements:
        print(f"Upgrading from {args.requirements}...")
        requirements = {args.requirements: Path(args.requirements).read_text()}
        asyncio.run(dm.upgrade_dependencies(requirements))
    elif args.packages:
        print(f"Upgrading packages: {', '.join(args.packages)}...")
        requirements = {"cli": "\n".join(args.packages)}
        asyncio.run(dm.upgrade_dependencies(requirements))
    else:
        print("Auto-discovering and upgrading requirements...")
        asyncio.run(dm.upgrade_dependencies())

    print("✅ Upgrade completed")


def cmd_demo(args):
    """Run demonstration scripts to show ChaCC features."""
    setup_logging(args.verbose)

    if args.type == 'modules':
        print("🚀 Running module separation demo...")
        from .demo_module_separation import demo_module_separation
        demo_module_separation()
    elif args.type == 'cache':
        print("🚀 Running cache structure demo...")
        from .demo_cache_structure import demo_cache_structure
        demo_cache_structure()
    else:
        print("Available demos:")
        print("  cdm demo modules  - Show module-based dependency separation")
        print("  cdm demo cache    - Show cache structure and organization")


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
        default=60,
        help="Timeout in seconds for each pip install attempt (default: 60)"
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        metavar="N",
        help="Maximum retry attempts for failed package installs (default: 5)"
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
    upgrade_parser.set_defaults(func=cmd_upgrade)

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve dependencies without installing"
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
        help="Clear cache for specific module (use with --clear)"
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