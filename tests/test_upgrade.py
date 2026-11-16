"""
Upgrades: selective and full, without dropping registered modules.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import subprocess
from unittest.mock import patch


from chacc import cache as cache_module
from chacc import installer as installer_module
from helpers import (
    FakeRunner, lock_for, make_dm, versions_runner,
)


def test_selective_upgrade_only_moves_given_module(cache_dir):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests', 'payments': 'idna'}, install=False)
        dm.upgrade_dependencies_sync({'payments': 'idna'})
    cmd = runner.compile_calls[-1]['cmd']
    assert '--upgrade' not in cmd
    assert cmd[cmd.index('--upgrade-package') + 1] == 'idna'
    assert 'requests==2.31.0' in runner.compile_calls[-1]['seed']


def test_full_upgrade_without_arguments(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    (tmp_path / 'requirements.txt').write_text('requests')
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, {'requests': ('requests', '2.32.0')}))
    with patch.object(subprocess, 'run', runner):
        dm.upgrade_dependencies_sync(search_dirs=[str(tmp_path)])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']


def test_upgrade_without_arguments_keeps_modules_discovered_elsewhere(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    (tmp_path / 'services').mkdir()
    (tmp_path / 'services' / 'requirements-api.txt').write_text('requests')
    runner = versions_runner()
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync(requirements_file_pattern='requirements-*.txt',
                                     search_dirs=[str(tmp_path / 'services')], install=False)
        dm.resolve_dependencies_sync({'cli': 'idna'}, install=False)
        pins = dm.upgrade_dependencies_sync(search_dirs=[str(tmp_path / 'nothing-here')])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6'}
    assert set(dm.load_cache()['requirements_caches']) == {'requirements-api', 'cli'}


def test_environment_change_before_upgrade_keeps_registered_modules(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(subprocess, 'run', versions_runner()):
        dm.resolve_dependencies_sync({'auth': 'requests', 'payments': 'idna'}, install=False)
        with patch.object(cache_module, 'get_environment_hash', return_value='new-python'), \
             patch.object(installer_module, 'get_installed_versions', return_value={}):
            pins = dm.upgrade_dependencies_sync({'payments': 'idna'})
    assert set(pins) == {'requests', 'idna'}
