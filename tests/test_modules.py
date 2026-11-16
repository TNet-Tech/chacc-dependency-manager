"""
The module registry: modules added one at a time, removal, discovery and included files.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import subprocess
from unittest.mock import patch


from chacc import installer as installer_module
from helpers import (
    FakeRunner, lock_for, make_dm, versions_runner,
)


def test_explicit_modules_are_additive(cache_dir):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        pins = dm.resolve_dependencies_sync({'payments': 'idna'}, install=False)

    # The second call resolved both modules together.
    assert set(runner.compile_calls[1]['inputs']) == {'m000-auth.in', 'm001-payments.in'}
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6'}
    assert set(dm.load_cache()['requirements_caches']) == {'auth', 'payments'}


def test_remove_module(cache_dir):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests', 'payments': 'idna'}, install=False)
        assert dm.remove_module('payments')
        assert not dm.remove_module('payments')
        pins = dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
    assert pins == {'requests': '==2.31.0'}
    assert set(dm.load_cache()['requirements_caches']) == {'auth'}


def test_discovery_prunes_only_discovered_modules(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6'), 'flask': ('flask', '3.0.0')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    (tmp_path / 'a.txt').write_text('requests')
    (tmp_path / 'b.txt').write_text('idna')
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'cli': 'flask'}, install=False)
        dm.resolve_dependencies_sync(requirements_file_pattern='*.txt', search_dirs=[str(tmp_path)], install=False)
        (tmp_path / 'b.txt').unlink()
        pins = dm.resolve_dependencies_sync(
            requirements_file_pattern='*.txt', search_dirs=[str(tmp_path)], install=False
        )
    assert set(dm.load_cache()['requirements_caches']) == {'cli', 'a'}
    assert pins == {'flask': '==3.0.0', 'requests': '==2.31.0'}


def test_same_named_files_in_different_folders_are_all_kept(cache_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'api').mkdir()
    (tmp_path / 'worker').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('requests')
    (tmp_path / 'worker' / 'requirements.txt').write_text('idna')
    (tmp_path / 'worker' / 'extra.txt').write_text('flask')
    dm = make_dm(cache_dir)
    with patch.object(subprocess, 'run', versions_runner()):
        pins = dm.resolve_dependencies_sync(requirements_file_pattern='*.txt',
                                            search_dirs=['api', 'worker'], install=False)
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6', 'flask': '==3.0.0'}
    # Unique filenames keep the short 1.3 name; clashing ones get their folder.
    assert set(dm.load_cache()['requirements_caches']) == {'api/requirements', 'worker/requirements', 'extra'}


def test_discovery_finding_nothing_still_installs_registered_modules(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync({'cli': 'requests'}, install=False)
        (tmp_path / 'requirements.txt').write_text('idna')
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path)], install=False)
        (tmp_path / 'requirements.txt').unlink()
        pins = dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'empty')])
    # Nothing found (wrong folder?): every registered module is kept and installed.
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6'}
    assert sorted(sum(runner.install_calls, [])) == ['idna==3.6', 'requests==2.31.0']


def test_included_files_are_resolved_from_the_file_and_tracked(cache_dir, tmp_path):
    (tmp_path / 'base.txt').write_text('idna')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('-r ../base.txt\nrequests')
    dm = make_dm(cache_dir)
    runner = FakeRunner(lock="idna==3.6\nrequests==2.31.0\n")
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        [module_input] = runner.compile_calls[0]['inputs'].values()
        assert f"-r {tmp_path / 'base.txt'}" in module_input

        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        assert len(runner.compile_calls) == 1  # unchanged: cache hit

        (tmp_path / 'base.txt').write_text('idna\nsix')
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        assert len(runner.compile_calls) == 2  # included file changed: re-resolved
