"""
Command-line interface for the Dependency Manager (``cdm``, ``chacc-dm``,
``chacc-dependency-manager``).

This module defines the arguments; the commands themselves are in :mod:`chacc.commands`.
"""

import argparse
import sys

from .commands import (  # noqa: F401 - cmd_* and setup_logging are importable from chacc.cli
    cmd_cache,
    cmd_check,
    cmd_demo,
    cmd_install,
    cmd_outdated,
    cmd_resolve,
    cmd_upgrade,
    setup_logging,
)


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
