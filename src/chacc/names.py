"""
Package-name helpers: normalising names so ``Foo_Bar``, ``foo-bar`` and ``foo.bar`` match.
"""

try:
    from packaging.utils import canonicalize_name
except ImportError:
    def canonicalize_name(name: str) -> str:
        return name.replace('_', '-').replace('.', '-').lower()


def base_package_name(name: str) -> str:
    """Strip extras from a requirement name: ``passlib[bcrypt]`` -> ``passlib``."""
    return name.split('[', 1)[0].strip()


__all__ = ["canonicalize_name", "base_package_name"]
