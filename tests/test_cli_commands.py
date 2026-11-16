"""
Test CLI commands for visibility features.
"""

import sys
import os
import json
import tempfile
import subprocess
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, 'src')

from chacc.cli import cmd_check, cmd_outdated
from chacc import DependencyManager


class MockArgs:
    """Mock argparse namespace for testing."""
    def __init__(self, **kwargs):
        self.cache_dir = kwargs.get('cache_dir', '.dependency_cache')
        self.verbose = kwargs.get('verbose', False)
        self.all = kwargs.get('all', False)


def test_cmd_check_no_cache():
    """Test check command when no cache exists."""
    with tempfile.TemporaryDirectory() as temp_dir:
        args = MockArgs(cache_dir=temp_dir)

        with patch('builtins.print') as mock_print:
            cmd_check(args)

        mock_print.assert_called_with("❌ No cached packages found. Run 'cdm install' first.")


def test_cmd_check_all_packages_installed():
    """Test check command when all cached packages are installed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0',
                'packaging': '==21.3'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir)

        with patch('chacc.utils.get_installed_versions', return_value={'requests': '2.28.0', 'packaging': '21.3'}), \
             patch('builtins.print') as mock_print:

            cmd_check(args)

        assert any("✅ All cached packages are properly installed" in str(call) for call in mock_print.call_args_list)


def test_cmd_check_missing_packages():
    """Test check command when some cached packages are missing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0',
                'packaging': '==21.3',
                'missing-package': '==1.0.0'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir)

        with patch('chacc.utils.get_installed_versions', return_value={'requests': '2.28.0', 'packaging': '21.3'}), \
             patch('builtins.print') as mock_print:

            cmd_check(args)

        assert any("❌ 1 cached packages are missing:" in str(call) for call in mock_print.call_args_list)
        assert any("missing-package==1.0.0" in str(call) for call in mock_print.call_args_list)


def test_cmd_check_with_extra_packages():
    """Test check command with --all flag showing extra installed packages."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir, all=True)

        with patch('chacc.utils.get_installed_versions', return_value={'requests': '2.28.0', 'extra-package': '1.0'}), \
             patch('builtins.print') as mock_print:

            cmd_check(args)

        assert any("additional packages installed but not in cache" in str(call) for call in mock_print.call_args_list)


def test_cmd_outdated_no_cache():
    """Test outdated command when no cache exists."""
    with tempfile.TemporaryDirectory() as temp_dir:
        args = MockArgs(cache_dir=temp_dir)

        with patch('builtins.print') as mock_print:
            cmd_outdated(args)

        mock_print.assert_called_with("❌ No cached packages found. Run 'cdm install' first.")


def test_cmd_outdated_no_outdated_packages():
    """Test outdated command when no packages are outdated."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a mock cache with some packages
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0',
                'packaging': '==21.3'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[]'

        with patch('subprocess.run', return_value=mock_result), \
             patch('builtins.print') as mock_print:

            cmd_outdated(args)

        assert any("✅ All cached packages are up-to-date" in str(call) for call in mock_print.call_args_list)


def test_cmd_outdated_with_outdated_packages():
    """Test outdated command when some packages are outdated."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0',
                'packaging': '==21.3'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {
                'name': 'requests',
                'version': '2.28.0',
                'latest_version': '2.31.0'
            },
            {
                'name': 'some-other-package',
                'version': '1.0.0',
                'latest_version': '2.0.0'
            }
        ])

        with patch('subprocess.run', return_value=mock_result), \
             patch('builtins.print') as mock_print:

            cmd_outdated(args)

        assert any("📦 1 packages have newer versions available:" in str(call) for call in mock_print.call_args_list)
        assert any("requests: 2.28.0 → 2.31.0" in str(call) for call in mock_print.call_args_list)


def test_cmd_outdated_pip_error():
    """Test outdated command when pip command fails."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_data = {
            'resolved_packages': {
                'requests': '==2.28.0'
            }
        }

        cache_file = os.path.join(temp_dir, 'dependency_cache.json')
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        args = MockArgs(cache_dir=temp_dir)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'pip list failed'

        with patch('subprocess.run', return_value=mock_result), \
             patch('builtins.print') as mock_print:

            cmd_outdated(args)

        assert any("❌ Failed to check for outdated packages:" in str(call) for call in mock_print.call_args_list)


if __name__ == "__main__":
    pytest.main([__file__])