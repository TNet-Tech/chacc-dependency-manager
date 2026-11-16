"""
The conflict policy: same major -> latest, different major -> skip and flag, core constraints win.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import os
import subprocess
from unittest.mock import patch

import pytest

from chacc import ResolutionError
from chacc import installer as installer_module
from helpers import (
    FakeRunner, conflicting_runner, make_dm,
)


def test_same_major_conflict_uses_latest(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.5', '1.26.18')
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={'urllib3': '1.26.5'}):
        dm.resolve_dependencies_sync({'auth': 'urllib3<1.26.10'})
        pins = dm.resolve_dependencies_sync({'payments': 'urllib3>=1.26.18'})

    assert pins == {'urllib3': '==1.26.18'}
    [conflict] = dm.last_conflicts
    assert conflict['kind'] == 'minor' and conflict['skipped_modules'] == []
    # Merged pins break a declared requirement, so they are installed exactly as pinned.
    assert runner.install_calls[-1] == ['urllib3==1.26.18']
    assert '--no-deps' in runner.install_cmds[-1]


def test_major_conflict_keeps_existing_and_flags_newcomer(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        pins = dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)

    assert pins == {'urllib3': '==1.26.18'}
    [conflict] = dm.last_conflicts
    assert conflict['kind'] == 'major'
    assert conflict['chosen_from'] == 'auth'
    assert conflict['skipped_modules'] == ['payments']
    cache = dm.load_cache()
    assert cache['requirements_caches']['payments']['skipped_packages'] == ['urllib3']
    # Both modules stay registered: the newcomer still loads.
    assert set(cache['requirements_caches']) == {'auth', 'payments'}


def test_major_conflict_on_cache_hit_does_not_reresolve(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
        calls = len(runner.compile_calls)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
    assert len(runner.compile_calls) == calls
    assert dm.last_conflicts[0]['kind'] == 'major'


def test_unchanged_modules_are_not_resolved_again_on_conflict(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        before = len(runner.compile_calls)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
    new_calls = runner.compile_calls[before:]
    # one combined attempt + payments alone; auth reuses its cached closure
    assert [sorted(c['inputs']) for c in new_calls] == [['m000-auth.in', 'm001-payments.in'], ['m000-payments.in']]


def test_on_conflict_fail_names_modules_and_leaves_cache(cache_dir):
    dm = make_dm(cache_dir, on_conflict='fail')
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        with pytest.raises(ResolutionError) as excinfo:
            dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
    message = str(excinfo.value)
    assert "auth wants 1.26.18" in message and "payments wants 2.2.1" in message
    assert excinfo.value.conflicts[0]['package'] == 'urllib3'
    assert set(dm.load_cache()['requirements_caches']) == {'auth'}


def test_module_broken_on_its_own_is_named(cache_dir):
    dm = make_dm(cache_dir)

    def lock(inputs):
        if any('broken' in b for b in inputs):
            return None
        return "".join(f"requests==2.31.0\n    # via -r /tmp/x/{b}\n" for b in inputs)

    with patch.object(subprocess, 'run', FakeRunner(lock=lock)):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        with pytest.raises(ResolutionError) as excinfo:
            dm.resolve_dependencies_sync({'broken': 'doesnotexist'}, install=False)
    assert excinfo.value.module == 'broken'
    assert "cannot be resolved even on its own" in str(excinfo.value)


def test_core_constraints_win_even_within_same_major(cache_dir):
    core = os.path.join(cache_dir, 'core.txt')
    with open(core, 'w') as f:
        f.write("urllib3==1.26.5\n")
    dm = make_dm(cache_dir, constraint_files=[core])

    def lock(inputs):
        if 'constraints.in' in inputs:
            return None  # payments can't live with the core pin
        return "urllib3==1.26.18\n    # via -r /tmp/x/m000-payments.in\n"

    with patch.object(subprocess, 'run', FakeRunner(lock=lock)):
        pins = dm.resolve_dependencies_sync({'payments': 'urllib3>=1.26.18'}, install=False)
    assert pins == {'urllib3': '==1.26.5'}
    [conflict] = dm.last_conflicts
    assert conflict['chosen_from'] == 'core constraints'
    assert conflict['kind'] == 'minor'


def test_merge_policy_rules():
    from chacc.utils import merge_with_version_policy, major_version_key
    merged, conflicts = merge_with_version_policy([
        ('auth', {'a': '==1.2.0', 'b': '==1.0.0', 'c': '==0.9.0', 'same': '==1.0'}),
        ('pay', {'a': '==1.5.1', 'b': '==2.0.0', 'c': '==0.10.0', 'same': '==1.0'}),
    ])
    assert merged == {'a': '==1.5.1', 'b': '==1.0.0', 'c': '==0.9.0', 'same': '==1.0'}
    kinds = {c['package']: c['kind'] for c in conflicts}
    assert kinds == {'a': 'minor', 'b': 'major', 'c': 'major'}
    assert major_version_key('0.9.1') == (0, 9)
    assert major_version_key('2.31.0') == (2,)
