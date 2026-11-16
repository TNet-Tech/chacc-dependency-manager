"""
Demonstrate how ChaCC handles modules that register their requirements one at a time.

Resolves real packages (needs network access) into a temporary cache and installs nothing.
"""

import tempfile

from .manager import DependencyManager
from .utils import format_conflict


MODULES = {
    'web': 'requests',
    'reports': 'urllib3>=2',
    # Asks for an older major version of a package another module already uses.
    'legacy': 'urllib3<2',
}


def demo_module_separation():
    """Register modules one per call and show what ChaCC records for each."""
    print("=== ChaCC Module Demo ===\n")

    with tempfile.TemporaryDirectory(prefix="chacc-demo-") as cache_dir:
        dm = DependencyManager(cache_dir=cache_dir)

        print("📦 Registering modules one at a time (nothing is installed):")
        for module_name, requirements in MODULES.items():
            print(f"\n🔍 Module: {module_name}  (requirements: {requirements.replace(chr(10), ', ')})")
            pins = dm.resolve_dependencies_sync({module_name: requirements}, install=False)
            print(f"   Checked against the modules registered before it → {len(pins)} packages in total")
            for conflict in dm.last_conflicts:
                print(f"   ⚠️  {format_conflict(conflict)}")

        cache = dm.load_cache()
        print("\n💾 What the cache records per module:")
        for module_name, entry in sorted(cache['requirements_caches'].items(), key=lambda i: i[1].get('order', 0)):
            packages = entry.get('packages', {})
            skipped = entry.get('skipped_packages') or []
            note = f", skipped: {', '.join(skipped)}" if skipped else ""
            print(f"  📁 {module_name} (#{entry.get('order')}, {entry.get('source')}): "
                  f"{len(packages)} packages{note}")
            for package, version in list(packages.items())[:3]:
                print(f"     └─ {package}{version}")

        print(f"\n📊 Shared resolved_packages: {len(cache['resolved_packages'])} packages, one version each")

    print("\n✅ Key points:")
    print("   • Each call adds a module and checks it against every module registered earlier")
    print("   • Unchanged modules are not resolved again, and existing pins are kept where possible")
    print("   • Same major version → the latest is used; different major → the earlier module wins")
    print("     and the newcomer's requirement is skipped and flagged (the module still loads)")
    print("   • dm.remove_module('legacy') unregisters a module; its pins go on the next resolve")


if __name__ == "__main__":
    demo_module_separation()
