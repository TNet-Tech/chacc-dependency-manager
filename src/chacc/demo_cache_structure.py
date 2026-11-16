"""
Show the structure of ChaCC's cache file (dependency_cache.json).

Uses a sample cache in a temporary directory, so no network access is needed.
"""

import json
import tempfile

from .manager import DependencyManager
from .utils import format_conflict, get_environment_hash


def _sample_cache() -> dict:
    return {
        'requirements_caches': {
            'web': {
                'hash': 'a1b2c3d4e5f6…',
                'requirements': 'fastapi>=0.100\nuvicorn',
                'packages': {'fastapi': '==0.115.0', 'starlette': '==0.38.6', 'uvicorn': '==0.30.6'},
                'complete': True,
                'order': 1,
                'source': 'discovered',
                'skipped_packages': [],
                'last_updated': '1763290000.0',
            },
            'payments': {
                'hash': 'f6e5d4c3b2a1…',
                'requirements': 'stripe\nurllib3<2',
                'packages': {'stripe': '==10.12.0', 'urllib3': '==1.26.20'},
                'complete': True,
                'order': 2,
                'source': 'explicit',
                'skipped_packages': ['urllib3'],
                'last_updated': '1763290100.0',
            },
        },
        'combined_hash': '9f8e7d6c5b4a…',
        'environment_hash': get_environment_hash(),
        'resolved_packages': {
            'fastapi': '==0.115.0', 'starlette': '==0.38.6', 'uvicorn': '==0.30.6',
            'stripe': '==10.12.0', 'urllib3': '==2.2.3',
        },
        'conflicts': [{
            'package': 'urllib3', 'chosen': '2.2.3', 'chosen_from': 'web',
            'requested': {'web': '2.2.3', 'payments': '1.26.20'},
            'kind': 'major', 'skipped_modules': ['payments'],
        }],
        'last_updated': '1763290100.0',
    }


def demo_cache_structure():
    """Print each part of the cache and what it is used for."""
    print("=== ChaCC Cache Structure ===\n")

    with tempfile.TemporaryDirectory(prefix="chacc-demo-") as cache_dir:
        dm = DependencyManager(cache_dir=cache_dir)
        dm.save_cache(_sample_cache())
        cache = dm.load_cache()

    print("📂 requirements_caches: one entry per registered module")
    for name, entry in cache['requirements_caches'].items():
        print(f"\n🔹 {name}")
        print(f"   requirements     : {entry['requirements']!r}  (kept so the full set can be rebuilt)")
        print(f"   order / source   : #{entry['order']} / {entry['source']}  (earlier wins major conflicts; "
              "discovered modules are dropped when their file disappears)")
        print(f"   packages         : {len(entry['packages'])} (everything this module pulls in)")
        if entry['skipped_packages']:
            print(f"   skipped_packages : {', '.join(entry['skipped_packages'])} (major-version conflict)")

    print("\n📊 Shared state:")
    print(f"   resolved_packages : {len(cache['resolved_packages'])} pins, the versions that get installed")
    print("   combined_hash     : all module hashes plus constraint files; a change triggers a resolve")
    print("   environment_hash  : Python version, OS and CPU; a change re-resolves every module")
    print("   conflicts         :")
    for conflict in cache['conflicts']:
        print(f"      • {format_conflict(conflict)}")

    print("\n📄 Raw JSON for one module:")
    print(json.dumps(cache['requirements_caches']['payments'], indent=2))

    print("\n🔧 Operations:")
    print("   • cdm cache --info                      / dm.load_cache()")
    print("   • cdm cache --clear --module web        / dm.invalidate_module_cache('web')  (re-resolve it)")
    print("   • cdm cache --remove --module payments  / dm.remove_module('payments')       (unregister it)")
    print("   • cdm cache --clear                     / dm.invalidate_cache()              (forget everything)")


if __name__ == "__main__":
    demo_cache_structure()
