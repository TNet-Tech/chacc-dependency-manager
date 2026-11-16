"""
Regression tests for the 1.4.0 package fixes.

pip / pip-compile are replaced by a fake ``subprocess.run`` so these tests are fast
and need no network access.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, 'src')

from chacc import DependencyManager, ResolutionError
from chacc import manager as manager_module
from chacc.cli import cmd_check
from chacc.utils import (
    attribute_packages,
    get_environment_hash,
    parse_lock_file,
    version_satisfies,
)


LOCK_A_B = """\
certifi==2024.2.2
    # via requests
idna==3.6
    # via
    #   -r /tmp/x/m001-b.in
    #   requests
requests==2.31.0
    # via -r /tmp/x/m000-a.in
"""


class FakeRunner:
    """Stands in for subprocess.run: pip-compile writes ``lock``; pip install is recorded."""

    def __init__(self, lock=LOCK_A_B, compile_rc=0, compile_stderr=""):
        self.lock = lock
        self.compile_rc = compile_rc
        self.compile_stderr = compile_stderr
        self.compile_calls = []
        self.install_calls = []
        self.install_cmds = []

    def __call__(self, cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if 'piptools' in cmd:
            output = cmd[cmd.index('--output-file') + 1]
            seed = open(output).read() if os.path.exists(output) else ""
            self.compile_calls.append({'cmd': cmd, 'seed': seed,
                                       'inputs': {os.path.basename(p): open(p).read()
                                                  for p in cmd if p.endswith('.in')}})
            result.returncode = self.compile_rc
            result.stderr = self.compile_stderr
            lock = self.lock
            if callable(lock):
                lock = lock(self.compile_calls[-1]['inputs'])
                if lock is None:
                    result.returncode, result.stderr = 1, "ResolutionImpossible: conflict"
            if result.returncode == 0:
                with open(output, 'w') as f:
                    f.write(lock)
        elif 'install' in cmd:
            self.install_calls.append([a for a in cmd if '==' in a])
            self.install_cmds.append(cmd)
        return result


@pytest.fixture
def cache_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def make_dm(cache_dir, **kwargs):
    return DependencyManager(cache_dir=cache_dir, max_retries=1, **kwargs)


# --- P1: failed resolution must not be cached -------------------------------

def test_failed_resolution_is_not_cached(cache_dir):
    dm = make_dm(cache_dir)
    failing = FakeRunner(compile_rc=1, compile_stderr="Could not find a version that satisfies nope")
    with patch.object(manager_module.subprocess, 'run', failing), \
         patch.object(manager_module, 'get_installed_versions', return_value={}):
        with pytest.raises(ResolutionError, match="Could not find a version"):
            dm.resolve_dependencies_sync({'a': 'nope==99'})

    assert dm.load_cache()['requirements_caches'] == {}

    # Next run with the same requirements must try to resolve again.
    working = FakeRunner()
    with patch.object(manager_module.subprocess, 'run', working), \
         patch.object(manager_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync({'a': 'nope==99'})
    assert len(working.compile_calls) == 1


def test_resolve_module_dependencies_keeps_1_3_contract(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(manager_module.subprocess, 'run', FakeRunner(compile_rc=1, compile_stderr="boom")):
        assert dm.resolve_module_dependencies('a', 'requests') == {}
        with pytest.raises(ResolutionError):
            dm.resolve_module_dependencies('a', 'requests', raise_on_error=True)


def test_resolution_timeout_raises(cache_dir):
    dm = make_dm(cache_dir, resolve_timeout=5)

    def timeout(cmd, **kwargs):
        assert kwargs['timeout'] == 5
        raise subprocess.TimeoutExpired(cmd, 5)

    with patch.object(manager_module.subprocess, 'run', timeout):
        with pytest.raises(ResolutionError, match="timed out after 5s"):
            dm.resolve_dependencies_sync({'a': 'requests'}, install=False)
    assert dm.load_cache()['resolved_packages'] == {}


# --- Version checks -----------------------------------------------------------

def test_installs_packages_at_wrong_version(cache_dir):
    dm = make_dm(cache_dir)
    runner = FakeRunner()
    installed = {'requests': '2.31.0', 'idna': '3.6', 'certifi': '2023.1.1'}
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value=installed):
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'})
    assert runner.install_calls == [['certifi==2024.2.2']]


def test_set_of_names_still_accepted(cache_dir):
    dm = make_dm(cache_dir)
    missing, wrong, ok = dm.packages_needing_install({'requests': '==2.31.0', 'idna': '==3.6'}, {'requests'})
    assert missing == ['idna==3.6'] and wrong == [] and ok == ['requests']


def test_version_satisfies_normalises():
    assert version_satisfies('1.0', '==1.0.0')
    assert not version_satisfies('1.0', '==1.1')
    assert not version_satisfies(None, '==1.0')
    assert version_satisfies('1.0', ' @ https://example.com/pkg.whl')


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


# --- Conflicts: one combined resolve with constraints ------------------------

def test_all_modules_resolved_in_one_run_with_constraints(cache_dir):
    core = os.path.join(cache_dir, 'core.txt')
    with open(core, 'w') as f:
        f.write("idna==3.6\n")
    dm = make_dm(cache_dir, constraint_files=[core])
    runner = FakeRunner()
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'}, install=False)
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna\nflask'}, install=False)
    assert runner.compile_calls[0]['seed'] == ""
    assert "requests==2.31.0" in runner.compile_calls[1]['seed']


# --- Stale pins -----------------------------------------------------------------

def test_removed_module_pins_are_dropped(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(manager_module.subprocess, 'run', FakeRunner()):
        dm.resolve_dependencies_sync({'a': 'requests', 'b': 'idna'}, install=False)

    only_a = "requests==2.31.0\n    # via -r /tmp/x/m000-a.in\n"
    runner = FakeRunner(lock=only_a)
    with patch.object(manager_module.subprocess, 'run', runner):
        pins = dm.resolve_dependencies_sync({'a': 'requests'}, install=False, replace=True)

    assert len(runner.compile_calls) == 1, "removing a module must trigger re-resolution"
    cache = dm.load_cache()
    assert set(cache['requirements_caches']) == {'a'}
    assert cache['resolved_packages'] == pins == {'requests': '==2.31.0'}


# --- Server freeze ----------------------------------------------------------------

def test_async_resolve_does_not_block_event_loop(cache_dir):
    dm = make_dm(cache_dir)
    runner = FakeRunner()

    def slow_run(cmd, **kwargs):
        time.sleep(0.5)
        return runner(cmd, **kwargs)

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        task = asyncio.ensure_future(ticker())
        with patch.object(manager_module.subprocess, 'run', slow_run):
            await dm.resolve_dependencies({'a': 'requests'}, install=False)
        task.cancel()
        return ticks

    assert asyncio.run(main()) >= 5


def test_concurrent_resolves_are_serialised(cache_dir):
    dm = make_dm(cache_dir)
    active = []
    overlap = []
    runner = FakeRunner()

    def run(cmd, **kwargs):
        active.append(1)
        if len(active) > 1:
            overlap.append(True)
        time.sleep(0.1)
        active.pop()
        return runner(cmd, **kwargs)

    with patch.object(manager_module.subprocess, 'run', run):
        threads = [threading.Thread(target=dm.resolve_dependencies_sync,
                                    args=({'a': f'requests\n# {i}'},), kwargs={'install': False})
                   for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not overlap


# --- Visibility ---------------------------------------------------------------------

def test_user_install_fallback_is_logged(cache_dir):
    logger = MagicMock()
    dm = DependencyManager(cache_dir=cache_dir, logger=logger, max_retries=1)

    def run(cmd, **kwargs):
        result = MagicMock(returncode=0, stderr="",
                           stdout="Defaulting to user installation because normal site-packages is not writeable")
        return result

    with patch.object(manager_module.subprocess, 'run', run):
        dm.install_missing_packages({'requests': '==2.31.0'}, {})

    warnings = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "user site-packages" in warnings
    infos = " ".join(str(c) for c in logger.info.call_args_list)
    assert "Install target:" in infos


# --- Over-sensitive cache ---------------------------------------------------------

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


# --- Lock parsing -------------------------------------------------------------------

def test_parse_lock_file_handles_markers_urls_and_options():
    pins, via = parse_lock_file(
        "--index-url https://example.com/simple\n"
        "colorama==0.4.6 ; sys_platform == \"win32\"\n"
        "    # via click\n"
        "mypkg @ https://example.com/mypkg-1.0.whl\n"
        "    # via -r /x/m000-a.in\n"
    )
    assert pins == {'colorama': '==0.4.6', 'mypkg': ' @ https://example.com/mypkg-1.0.whl'}
    assert via['colorama'] == {'click'}


def test_attribute_packages_follows_transitive_deps():
    pins, via = parse_lock_file(LOCK_A_B)
    per_module = attribute_packages(pins, via, {'a': 'm000-a.in', 'b': 'm001-b.in'})
    assert set(per_module['a']) == {'requests', 'idna', 'certifi'}
    assert per_module['b'] == {'idna': '==3.6'}


# --- Modules arriving one at a time -------------------------------------------------

def lock_for(inputs, versions):
    """Build a fake lock: ``versions`` maps a requirement line to (package, version)."""
    lines = []
    for basename, content in inputs.items():
        if basename == 'constraints.in':
            continue
        for req in content.split():
            package, version = versions[req]
            lines.append(f"{package}=={version}\n    # via -r /tmp/x/{basename}\n")
    return "".join(lines)


def test_explicit_modules_are_additive(cache_dir):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'cli': 'flask'}, install=False)
        dm.resolve_dependencies_sync(requirements_file_pattern='*.txt', search_dirs=[str(tmp_path)], install=False)
        (tmp_path / 'b.txt').unlink()
        pins = dm.resolve_dependencies_sync(requirements_file_pattern='*.txt', search_dirs=[str(tmp_path)], install=False)
    assert set(dm.load_cache()['requirements_caches']) == {'cli', 'a'}
    assert pins == {'flask': '==3.0.0', 'requests': '==2.31.0'}


def test_lock_is_shared_across_managers(cache_dir):
    assert make_dm(cache_dir)._lock is make_dm(cache_dir)._lock


# --- Conflict policy ------------------------------------------------------------------

def conflicting_runner(auth_version, payments_version):
    """auth needs urllib3 at one version, payments at another; together they can't resolve."""
    def lock(inputs):
        modules = [b for b in inputs if b != 'constraints.in']
        if len(modules) > 1:
            return None
        module = modules[0]
        version = auth_version if 'auth' in module else payments_version
        return f"urllib3=={version}\n    # via -r /tmp/x/{module}\n"
    return FakeRunner(lock=lock)


def test_same_major_conflict_uses_latest(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.5', '1.26.18')
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={'urllib3': '1.26.5'}):
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
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
        calls = len(runner.compile_calls)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
    assert len(runner.compile_calls) == calls
    assert dm.last_conflicts[0]['kind'] == 'major'


def test_unchanged_modules_are_not_resolved_again_on_conflict(cache_dir):
    dm = make_dm(cache_dir)
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'urllib3<2'}, install=False)
        before = len(runner.compile_calls)
        dm.resolve_dependencies_sync({'payments': 'urllib3>=2'}, install=False)
    new_calls = runner.compile_calls[before:]
    # one combined attempt + payments alone; auth reuses its cached closure
    assert [sorted(c['inputs']) for c in new_calls] == [['m000-auth.in', 'm001-payments.in'], ['m000-payments.in']]


def test_on_conflict_fail_names_modules_and_leaves_cache(cache_dir):
    dm = make_dm(cache_dir, on_conflict='fail')
    runner = conflicting_runner('1.26.18', '2.2.1')
    with patch.object(manager_module.subprocess, 'run', runner):
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

    with patch.object(manager_module.subprocess, 'run', FakeRunner(lock=lock)):
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

    with patch.object(manager_module.subprocess, 'run', FakeRunner(lock=lock)):
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


# --- Backward compatibility with 1.3.0 callers -----------------------------------------

def test_install_hook_still_receives_a_set(cache_dir):
    seen = {}

    def hook(resolved, installed):
        seen['installed'] = installed
        return True

    dm = DependencyManager(cache_dir=cache_dir, install_hook=hook)
    dm.install_missing_packages({'requests': '==2.31.0'}, {'requests': '2.31.0'})
    assert seen['installed'] == {'requests'}


def test_selective_upgrade_only_moves_given_module(cache_dir):
    dm = make_dm(cache_dir)
    versions = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6')}
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, versions))
    with patch.object(manager_module.subprocess, 'run', runner):
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
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.upgrade_dependencies_sync(search_dirs=[str(tmp_path)])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']


def test_1_3_style_async_usage_still_works(cache_dir):
    from chacc import Config, re_resolve_dependencies
    runner = FakeRunner()
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}):
        asyncio.run(re_resolve_dependencies({'a': 'requests'}, config=Config(cache_dir=cache_dir, max_retries=1)))
        asyncio.run(DependencyManager(cache_dir=cache_dir).upgrade_dependencies({'a': 'requests'}))
    assert runner.install_calls


# --- CLI: resolve --install and install discovery options ------------------------------

def _cli(argv):
    from chacc.cli import create_parser
    args = create_parser().parse_args(argv)
    args.func(args)


def test_cli_resolve_is_dry_run_and_resolve_install_installs(tmp_path):
    (tmp_path / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, {'requests': ('requests', '2.31.0')}))
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}), \
         patch('chacc.cli.get_installed_versions', return_value={}), \
         patch('builtins.print') as mock_print:
        _cli(['--cache-dir', cache, '--max-retries', '1', 'resolve', '--search-dirs', str(tmp_path)])
        assert runner.install_calls == []
        _cli(['--cache-dir', cache, '--max-retries', '1', 'resolve', '--install', '--search-dirs', str(tmp_path)])
    assert runner.install_calls == [['requests==2.31.0']]
    printed = " ".join(str(c) for c in mock_print.call_args_list)
    assert "requests==2.31.0" in printed and "resolved and installed" in printed


def test_cli_install_accepts_pattern_and_search_dirs(tmp_path):
    (tmp_path / 'requirements-web.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock=lambda inputs: lock_for(inputs, {'requests': ('requests', '2.31.0')}))
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        _cli(['--cache-dir', cache, '--max-retries', '1', 'install',
              '-p', 'requirements-*.txt', '--search-dirs', str(tmp_path)])
    assert runner.install_calls == [['requests==2.31.0']]
    assert set(DependencyManager(cache_dir=cache).load_cache()['requirements_caches']) == {'requirements-web'}


# --- Pre-release review fixes ----------------------------------------------------------

REQ_VERSIONS = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6'), 'flask': ('flask', '3.0.0')}


def versions_runner():
    return FakeRunner(lock=lambda inputs: lock_for(inputs, REQ_VERSIONS))


def test_same_named_files_in_different_folders_are_all_kept(cache_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'api').mkdir()
    (tmp_path / 'worker').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('requests')
    (tmp_path / 'worker' / 'requirements.txt').write_text('idna')
    (tmp_path / 'worker' / 'extra.txt').write_text('flask')
    dm = make_dm(cache_dir)
    with patch.object(manager_module.subprocess, 'run', versions_runner()):
        pins = dm.resolve_dependencies_sync(requirements_file_pattern='*.txt',
                                            search_dirs=['api', 'worker'], install=False)
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6', 'flask': '==3.0.0'}
    # Unique filenames keep the short 1.3 name; clashing ones get their folder.
    assert set(dm.load_cache()['requirements_caches']) == {'api/requirements', 'worker/requirements', 'extra'}


def test_cli_requirements_files_with_same_name_dont_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'requirements.txt').write_text('flask')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    with patch.object(manager_module.subprocess, 'run', versions_runner()), \
         patch.object(manager_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        _cli(['--cache-dir', cache, '--max-retries', '1', 'install', '-r', 'requirements.txt'])
        _cli(['--cache-dir', cache, '--max-retries', '1', 'install', '-r', 'api/requirements.txt'])
    cache_data = DependencyManager(cache_dir=cache).load_cache()
    # ./requirements.txt keeps the name auto-discovery gives it.
    assert set(cache_data['requirements_caches']) == {'requirements', 'api/requirements'}
    assert set(cache_data['resolved_packages']) == {'flask', 'requests'}


def test_environment_change_keeps_registered_modules(cache_dir):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        dm.resolve_dependencies_sync({'payments': 'idna'}, install=False)
        with patch.object(manager_module, 'get_environment_hash', return_value='new-python'):
            pins = dm.resolve_dependencies_sync({'reports': 'flask'}, install=False)
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6', 'flask': '==3.0.0'}
    # Fresh resolve for the new environment: no seed from the old one.
    assert runner.compile_calls[-1]['seed'] == ''
    assert set(dm.load_cache()['requirements_caches']) == {'auth', 'payments', 'reports'}


def test_environment_change_before_upgrade_keeps_registered_modules(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(manager_module.subprocess, 'run', versions_runner()):
        dm.resolve_dependencies_sync({'auth': 'requests', 'payments': 'idna'}, install=False)
        with patch.object(manager_module, 'get_environment_hash', return_value='new-python'), \
             patch.object(manager_module, 'get_installed_versions', return_value={}):
            pins = dm.upgrade_dependencies_sync({'payments': 'idna'})
    assert set(pins) == {'requests', 'idna'}


def test_dry_run_does_not_register_or_save(cache_dir):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
        before = dm.load_cache()
        pins = dm.resolve_dependencies_sync({'experimental': 'flask'}, dry_run=True)
    assert pins == {'requests': '==2.31.0', 'flask': '==3.0.0'}
    assert dm.load_cache() == before
    assert runner.install_calls == []


def test_cli_resolve_dry_run_leaves_cache_alone(tmp_path):
    (tmp_path / 'experimental.txt').write_text('flask')
    cache = str(tmp_path / 'cache')
    with patch.object(manager_module.subprocess, 'run', versions_runner()), \
         patch('chacc.cli.get_installed_versions', return_value={}), \
         patch('builtins.print'):
        _cli(['--cache-dir', cache, 'resolve', '-r', str(tmp_path / 'experimental.txt')])
    assert DependencyManager(cache_dir=cache).load_cache()['requirements_caches'] == {}


def test_discovery_finding_nothing_still_installs_registered_modules(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    runner = versions_runner()
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync({'cli': 'requests'}, install=False)
        (tmp_path / 'requirements.txt').write_text('idna')
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path)], install=False)
        (tmp_path / 'requirements.txt').unlink()
        pins = dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'empty')])
    # Nothing found (wrong folder?): every registered module is kept and installed.
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6'}
    assert sorted(sum(runner.install_calls, [])) == ['idna==3.6', 'requests==2.31.0']


def test_upgrade_without_arguments_keeps_modules_discovered_elsewhere(cache_dir, tmp_path):
    dm = make_dm(cache_dir)
    (tmp_path / 'services').mkdir()
    (tmp_path / 'services' / 'requirements-api.txt').write_text('requests')
    runner = versions_runner()
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}):
        dm.resolve_dependencies_sync(requirements_file_pattern='requirements-*.txt',
                                     search_dirs=[str(tmp_path / 'services')], install=False)
        dm.resolve_dependencies_sync({'cli': 'idna'}, install=False)
        pins = dm.upgrade_dependencies_sync(search_dirs=[str(tmp_path / 'nothing-here')])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']
    assert pins == {'requests': '==2.31.0', 'idna': '==3.6'}
    assert set(dm.load_cache()['requirements_caches']) == {'requirements-api', 'cli'}


def test_cli_upgrade_accepts_pattern_and_search_dirs(tmp_path):
    (tmp_path / 'requirements-web.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    runner = versions_runner()
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        _cli(['--cache-dir', cache, 'upgrade', '-p', 'requirements-*.txt', '--search-dirs', str(tmp_path)])
    assert '--upgrade' in runner.compile_calls[-1]['cmd']
    assert set(DependencyManager(cache_dir=cache).load_cache()['requirements_caches']) == {'requirements-web'}


def test_network_error_is_not_reported_as_a_conflict(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(manager_module.subprocess, 'run', versions_runner()):
        dm.resolve_dependencies_sync({'auth': 'requests'}, install=False)
    offline = FakeRunner(compile_rc=1, compile_stderr=(
        "WARNING: Retrying ... NewConnectionError('Failed to establish a new connection')\n"
        "ERROR: Could not find a version that satisfies the requirement idna"
    ))
    with patch.object(manager_module.subprocess, 'run', offline):
        with pytest.raises(ResolutionError, match="could not be reached") as info:
            dm.resolve_dependencies_sync({'payments': 'idna'}, install=False)
    assert info.value.module is None
    # One attempt only: no per-module fallback.
    assert len(offline.compile_calls) == 1
    assert set(dm.load_cache()['requirements_caches']) == {'auth'}


def test_cli_resolve_install_runs_pip_list_once(tmp_path):
    (tmp_path / 'requirements.txt').write_text('requests')
    cache = str(tmp_path / 'cache')
    with patch.object(manager_module.subprocess, 'run', versions_runner()), \
         patch.object(manager_module, 'get_installed_versions', return_value={}) as manager_list, \
         patch('chacc.cli.get_installed_versions', return_value={}) as cli_list, \
         patch('builtins.print'):
        _cli(['--cache-dir', cache, 'resolve', '--install', '--search-dirs', str(tmp_path)])
    assert manager_list.call_count == 1 and cli_list.call_count == 0


# --- Paths inside requirements files and the cross-process lock -------------------------

def test_absolutize_requirement_paths(tmp_path):
    from chacc.utils import absolutize_requirement_paths
    base = str(tmp_path)
    text = "\n".join([
        "-r base.txt",
        "--requirement=../shared/reqs.txt  # shared",
        "-c constraints.txt",
        "-e ./libs/core",
        "./libs/plugin[extra]",
        "-e git+https://example.com/repo.git#egg=x",
        "-r /abs/other.txt",
        "requests>=2  # normal line",
        "--index-url https://example.com/simple",
    ])
    out = absolutize_requirement_paths(text, base).splitlines()
    assert out[0] == f"-r {os.path.join(base, 'base.txt')}"
    assert out[1] == f"--requirement={os.path.normpath(os.path.join(base, '../shared/reqs.txt'))}  # shared"
    assert out[2] == f"-c {os.path.join(base, 'constraints.txt')}"
    assert out[3] == f"-e {os.path.join(base, 'libs/core')}"
    assert out[4] == os.path.join(base, 'libs/plugin[extra]')
    assert out[5:] == text.splitlines()[5:]


def test_included_files_are_resolved_from_the_file_and_tracked(cache_dir, tmp_path):
    (tmp_path / 'base.txt').write_text('idna')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api' / 'requirements.txt').write_text('-r ../base.txt\nrequests')
    dm = make_dm(cache_dir)
    runner = FakeRunner(lock="idna==3.6\nrequests==2.31.0\n")
    with patch.object(manager_module.subprocess, 'run', runner):
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        [module_input] = runner.compile_calls[0]['inputs'].values()
        assert f"-r {tmp_path / 'base.txt'}" in module_input

        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        assert len(runner.compile_calls) == 1  # unchanged: cache hit

        (tmp_path / 'base.txt').write_text('idna\nsix')
        dm.resolve_dependencies_sync(search_dirs=[str(tmp_path / 'api')], install=False)
        assert len(runner.compile_calls) == 2  # included file changed: re-resolved


def test_relative_paths_in_passed_text_and_cli_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'svc').mkdir()
    (tmp_path / 'svc' / 'reqs.txt').write_text('-e ./lib')
    cache = str(tmp_path / 'cache')
    runner = FakeRunner(lock="lib @ file:///x\n")
    with patch.object(manager_module.subprocess, 'run', runner), \
         patch.object(manager_module, 'get_installed_versions', return_value={}), \
         patch('builtins.print'):
        # Passed-in text: relative to the current folder.
        DependencyManager(cache_dir=cache).resolve_dependencies_sync({'plugin': '-e ./plugins/x'}, install=False)
        # cdm -r FILE: relative to the file's folder.
        _cli(['--cache-dir', cache, 'resolve', '-r', 'svc/reqs.txt'])
    inputs = [list(c['inputs'].values()) for c in runner.compile_calls]
    assert f"-e {tmp_path / 'plugins' / 'x'}" in inputs[0][0]
    assert any(f"-e {tmp_path / 'svc' / 'lib'}" in text for text in inputs[1])


@pytest.mark.skipif(sys.platform == 'win32', reason="uses a POSIX shell-free child process timing")
def test_cache_lock_is_shared_across_processes(cache_dir):
    child = subprocess.Popen(
        [sys.executable, '-c',
         "import sys, time; sys.path.insert(0, 'src')\n"
         "from chacc import DependencyManager\n"
         f"dm = DependencyManager(cache_dir={cache_dir!r})\n"
         "with dm._lock:\n"
         "    print('locked', flush=True)\n"
         "    time.sleep(1.5)\n"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == 'locked'
        started = time.monotonic()
        with make_dm(cache_dir)._lock:
            waited = time.monotonic() - started
    finally:
        child.wait(timeout=10)
    assert waited > 0.8


def test_cache_lock_is_reentrant(cache_dir):
    dm = make_dm(cache_dir)
    with dm._lock:
        with dm._lock:
            pass
        assert dm._lock._fd is not None
    assert dm._lock._fd is None


# --- log_level -------------------------------------------------------------------------

def test_log_level_option(cache_dir):
    import logging
    from chacc import Config
    own = logging.getLogger('chacc-test-own')
    make_dm(cache_dir, logger=own, log_level='warning')
    assert own.level == logging.WARNING
    Config(cache_dir=cache_dir, logger=own, log_level=logging.ERROR).create_manager()
    assert own.level == logging.ERROR

    default = logging.getLogger('dependency_manager')
    before = default.level
    try:
        make_dm(cache_dir)  # not passed: level untouched
        assert default.level == before
        make_dm(cache_dir, log_level='INFO')
        assert default.level == logging.INFO
    finally:
        default.setLevel(before)

    with pytest.raises(ValueError, match="Unknown log_level"):
        make_dm(cache_dir, log_level='loud')
