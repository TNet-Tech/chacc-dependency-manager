"""
CLI behavior added in 1.4: dry runs, --install, discovery options, module names, check.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import subprocess
from unittest.mock import MagicMock, patch


from chacc.requirements import pip_path
from chacc import DependencyManager
from chacc import installer as installer_module
from chacc.cli import cmd_check
from helpers import (
    FakeRunner, lock_for, make_dm, run_cli, versions_runner,
)


def test_cmd_check_reports_version_mismatch(cache_dir):
    dm = make_dm(cache_dir)
    dm.save_cache({'resolved_packages': {'requests': '==2.31.0'}})
    args = MagicMock(cache_dir=cache_dir, verbose=False, all=False)
    with patch('chacc.utils.get_installed_versions', return_value={'requests': '2.28.0'}), \
         patch('builtins.print') as mock_print:
        cmd_check(args)
    printed = " ".join(str(c) for c in mock_print.call_args_list)
    assert "1 version mismatches" in printed
    assert "expected 2.31.0, installed 2.28.0" in printed


def test_cli_resolve_is_dry_run_and_resolve_install_installs(tmp_path):
    (tmp_path / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, {'requests': ('requests', '2.31.0')}))
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}), \
         patch('chacc.commands.get_installed_versions', return_value={}), \
         patch('builtins.print') as mock_print:
        run_cli(['--cache-dir', cache, '--max-retries', '1', 'resolve', '--search-dirs', str(tmp_path)])
        assert runner.install_calls == []
        run_cli(['--cache-dir', cache, '--max-retries', '1', 'resolve', '--install', '--search-dirs', str(tmp_path)])
    assert runner.install_calls == [['requests==2.31.0']]
    printed = " ".join(str(c) for c in mock_print.call_args_list)
    assert "requests==2.31.0" in printed and "resolved and installed" in printed


def test_cli_install_accepts_pattern_and_search_dirs(tmp_path):
    (tmp_path / 'requirements-web.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, {'requests': ('requests', '2.31.0')}))
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        run_cli(['--cache-dir', cache, '--max-retries', '1', 'install',
                 '-p', 'requirements-*.txt', '--search-dirs', str(tmp_path)])
    assert runner.install_calls == [['requests==2.31.0']]
    assert set(DependencyManager(cache_dir=cache).load_cache()['requirements_caches']) == {'requirements-web'}


def test_cli_requirements_files_with_same_name_dont_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'requirements.txt').write_text('flask')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    with patch.object(subprocess, 'run', versions_runner()), \
         patch.object(installer_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        run_cli(['--cache-dir', cache, '--max-retries', '1', 'install', '-r', 'requirements.txt'])
        run_cli(['--cache-dir', cache, '--max-retries', '1', 'install', '-r', 'api/requirements.txt'])
    cache_data = DependencyManager(cache_dir=cache).load_cache()
    # ./requirements.txt keeps the name auto-discovery gives it.
    assert set(cache_data['requirements_caches']) == {'requirements', 'api/requirements'}
    assert set(cache_data['resolved_packages']) == {'flask', 'requests'}


def test_cli_resolve_dry_run_leaves_cache_alone(tmp_path):
    (tmp_path / 'experimental.txt').write_text('flask')
    cache = str(tmp_path / 'cache')
    with patch.object(subprocess, 'run', versions_runner()), \
         patch('chacc.commands.get_installed_versions', return_value={}), \
         patch('builtins.print'):
        run_cli(['--cache-dir', cache, 'resolve', '-r', str(tmp_path / 'experimental.txt')])
    assert DependencyManager(cache_dir=cache).load_cache()['requirements_caches'] == {}


def test_cli_upgrade_accepts_pattern_and_search_dirs(tmp_path):
    (tmp_path / 'requirements-web.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = versions_runner()
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        run_cli(['--cache-dir', cache, 'upgrade', '-p', 'requirements-*.txt', '--search-dirs', str(tmp_path)])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']
    assert set(DependencyManager(cache_dir=cache).load_cache()['requirements_caches']) == {'requirements-web'}


def test_cli_resolve_install_runs_pip_list_once(tmp_path):
    (tmp_path / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    with patch.object(subprocess, 'run', versions_runner()), \
         patch.object(installer_module, 'get_installed_versions', return_value={}) as manager_list, \
         patch('chacc.commands.get_installed_versions', return_value={}) as cli_list, \
         patch('builtins.print'):
        run_cli(['--cache-dir', cache, 'resolve', '--install', '--search-dirs', str(tmp_path)])
    assert manager_list.call_count == 1 and cli_list.call_count == 0


def test_relative_paths_in_passed_text_and_cli_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'svc').mkdir()
    (tmp_path / 'svc' / 'reqs.txt').write_text('-e ./lib')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock="lib @ file:///x\n")
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        # Passed-in text: relative to the current folder.
        DependencyManager(cache_dir=cache).resolve_dependencies_sync({'plugin': '-e ./plugins/x'}, install=False)
        # cdm -r FILE: relative to the file's folder.
        run_cli(['--cache-dir', cache, 'resolve', '-r', 'svc/reqs.txt'])
    inputs = [list(c['inputs'].values()) for c in runner.compile_calls]
    assert f"-e {pip_path(str(tmp_path / 'plugins' / 'x'))}" in inputs[0][0]
    assert any(f"-e {pip_path(str(tmp_path / 'svc' / 'lib'))}" in text for text in inputs[1])
