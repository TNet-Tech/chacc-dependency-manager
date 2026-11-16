"""
Resolving and caching: failures, timeouts, constraints, seeding, dry runs, environment changes.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import os
import subprocess
from unittest.mock import patch

import pytest

from chacc import ResolutionError
from chacc import cache as cache_module
from chacc import installer as installer_module
from chacc.utils import get_environment_hash
from helpers import (
    FakeRunner, make_dm, versions_runner,
)


def test_failed_resolution_is_not_cached(cache_dir):
    dm = make_dm(cache_dir)
    failing = FakeRunner(compile_rc=1, compile_stderr="Could not find a version that satisfies nope")
    with patch.object(subprocess, 'run', failing), \
         patch.object(installer_module, 'get_installed_versions', return_value={}):
        with pytest.raises(ResolutionError, match="Could not find a version"):
            dm.resolve_dependencies_sync({'a': 'nope==99'})

    assert dm.load_cache()['requirements_caches'] == {}

    # Next run with the same requirements must try to resolve again.
    working = FakeRunner()
    with patch.object(subprocess, 'run', working), \
         patch.object(installer_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync({'a': 'nope==99'})
    assert len(working.compile_calls) == 1


def test_resolution_timeout_raises(cache_dir):
    dm = make_dm(cache_dir, resolve_timeout=5)

    def timeout(cmd, **kwargs):
        assert kwargs['timeout'] == 5
        raise subprocess.TimeoutExpired(cmd, 5)

    with patch.object(subprocess, 'run', timeout):
        with pytest.raises(ResolutionError, match="timed out after 5s"):
            dm.resolve_dependencies_sync({'a': 'requests'}, install=False)
    assert dm.load_cache()['resolved_packages'] == {}


def test_all_modules_resolved_in_one_run_with_constraints(cache_dir):
    core = os.path.join(cache_dir, 'core.txt')
    with open(core, 'w') as f:
        f.write("idna==3.6\n")
    dm = make_dm(cache_dir, constraint_files=[core])
    runner = FakeRunner()
    with patch.object(subprocess, 'run', runner):
        pins = dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'}, install=False)

    assert len(runner.compile_calls) == 1
    inputs = runner.compile_calls[0]['inputs']
    assert inputs['m000-a.in'] == "requests\n"
    assert inputs['m001-b.in'] == "idna\n"
    assert inputs['constraints.in'] == f"-c {core}\n"
    assert pins == {'certifi': '==2024.2.2', 'idna': '==3.6', 'requests': '==2.31.0'}

    module_caches = dm.load_cache()['requirements_caches']
    assert module_caches['a']['packages'] == pins
    assert module_caches['b']['packages'] == {'idna': '==3.6'}


def test_constraint_change_triggers_reresolve(cache_dir):
    core = os.path.join(cache_dir, 'core.txt')
    with open(core, 'w') as f:
        f.write("idna==3.6\n")
    dm = make_dm(cache_dir, constraint_files=[core])
    runner = FakeRunner()
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'a': 'requests'}, install=False)
        dm.resolve_dependencies_sync({'a': 'requests'}, install=False)
        assert len(runner.compile_calls) == 1
        with open(core, 'w') as f:
            f.write("idna==3.7\n")
        dm.resolve_dependencies_sync({'a': 'requests'}, install=False)
    assert len(runner.compile_calls) == 2


def test_missing_constraint_file_fails_cleanly(cache_dir):
    dm = make_dm(cache_dir, constraint_files=[os.path.join(cache_dir, 'nope.txt')])
    with pytest.raises(ResolutionError, match="Constraint file"):
        dm.resolve_dependencies_sync({'a': 'requests'}, install=False)


def test_previous_pins_seed_the_resolution(cache_dir):
    dm = make_dm(cache_dir)
    runner = FakeRunner()
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'}, install=False)
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna\nflask'}, install=False)
    assert runner.compile_calls[0]['seed'] == ""
    assert "requests==2.31.0" in runner.compile_calls[1]['seed']


def test_removed_module_pins_are_dropped(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(subprocess, 'run', FakeRunner()):
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'}, install=False)

    only_a = "requests==2.31.0\n    # via -r /tmp/x/m000-a.in\n"
    runner = FakeRunner(lock=only_a)
    with patch.object(subprocess, 'run', runner):
        pins = dm.resolve_dependencies_sync({'a': 'requests'}, install=False, replace=True)

    assert len(runner.compile_calls) == 1, "removing a module must trigger re-resolution"
    cache = dm.load_cache()
    assert set(cache['requirements_caches']) == {'a'}
    assert cache['resolved_packages'] == pins == {'requests': '==2.31.0'}


def test_dry_run_does_not_register_or_save(cache_dir):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        before = dm.load_cache()
        pins = dm.resolve_dependencies_sync({'experimental': 'flask'}, dry_run=True)
    assert pins == {'requests': '==2.31.0', 'flask': '==3.0.0'}
    assert dm.load_cache() == before
    assert runner.install_calls == []


def test_network_error_is_not_reported_as_a_conflict(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(subprocess, 'run', versions_runner()):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
    offline = FakeRunner(compile_rc=1, compile_stderr=(
        "WARNING: Retrying ... NewConnectionError('Failed to establish a new connection')\n"
        "ERROR: Could not find a version that satisfies the requirement idna"
    ))
    with patch.object(subprocess, 'run', offline):
        with pytest.raises(ResolutionError, match="could not be reached") as info:
            dm.resolve_dependencies_sync({'payments': 'idna'}, install=False)
    assert info.value.module is None
    # One attempt only: no per-module fallback.
    assert len(offline.compile_calls) == 1
    assert set(dm.load_cache()['requirements_caches']) == {'auth'}


def test_environment_change_keeps_registered_modules(cache_dir):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        dm.resolve_dependencies_sync({'payments': 'idna'}, install=False)
        with patch.object(cache_module, 'get_environment_hash', return_value='new-python'):
            pins = dm.resolve_dependencies_sync({'reports': 'flask'}, install=False)
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6', 'flask': '==3.0.0'}
    # Fresh resolve for the new environment: no seed from the old one.
    assert runner.compile_calls[-1]['seed'] == ''
    assert set(dm.load_cache()['requirements_caches']) == {'auth', 'payments', 'reports'}


def test_environment_hash_ignores_kernel_release():
    with patch('platform.release', return_value='5.15.0-1-generic'):
        first = get_environment_hash()
    with patch('platform.release', return_value='6.8.0-99-generic'):
        second = get_environment_hash()
    assert first == second


def test_environment_hash_tracks_architecture():
    with patch('platform.machine', return_value='x86_64'):
        first = get_environment_hash()
    with patch('platform.machine', return_value='aarch64'):
        second = get_environment_hash()
    assert first != second
