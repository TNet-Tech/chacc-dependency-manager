"""
Installing: version-aware checks, the install hook, user-install warnings and pip leftovers.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import subprocess
import time
from unittest.mock import MagicMock, patch


from chacc import DependencyManager
from chacc import installer as installer_module
from chacc.utils import version_satisfies
from helpers import (
    FakeRunner, make_dm,
)


def test_installs_packages_at_wrong_version(cache_dir):
    dm = make_dm(cache_dir)
    runner = FakeRunner()
    installed = {'requests': '2.31.0', 'idna': '3.6', 'certifi': '2023.1.1'}
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value=installed):
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


def test_user_install_fallback_is_logged(cache_dir):
    logger = MagicMock()
    dm = DependencyManager(cache_dir=cache_dir, logger=logger, max_retries=1)

    def run(cmd, **kwargs):
        result = MagicMock(returncode=0, stderr="",
                           stdout="Defaulting to user installation because normal site-packages is not writeable")
        return result

    with patch.object(subprocess, 'run', run):
        dm.install_missing_packages({'requests': '==2.31.0'}, {})

    warnings = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "user site-packages" in warnings
    infos = " ".join(str(c) for c in logger.info.call_args_list)
    assert "Install target:" in infos


def test_install_hook_still_receives_a_set(cache_dir):
    seen = {}

    def hook(resolved, installed):
        seen['installed'] = installed
        return True

    dm = DependencyManager(cache_dir=cache_dir, install_hook=hook)
    dm.install_missing_packages({'requests': '==2.31.0'}, {'requests': '2.31.0'})
    assert seen['installed'] == {'requests'}


def test_leftovers_of_a_killed_pip_are_removed_and_the_retry_reinstalls(cache_dir, fake_site):
    calls = []

    def pip(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            # pip got as far as moving the old version aside, then was killed.
            (fake_site / '~qlalchemy').mkdir()
            (fake_site / '~qlalchemy-2.0.53.dist-info').mkdir()
            raise subprocess.TimeoutExpired(cmd, 300)
        result = MagicMock()
        result.returncode, result.stdout, result.stderr = 0, "", ""
        return result

    dm = DependencyManager(cache_dir=cache_dir, max_retries=2)
    with patch.object(subprocess, 'run', pip), patch.object(time, 'sleep'):
        dm.installer.run_pip_install(['sqlalchemy==2.1.8'])

    assert list(fake_site.iterdir()) == []
    assert '--force-reinstall' not in calls[0]
    assert '--force-reinstall' in calls[1] and '--no-deps' in calls[1]


def test_existing_leftovers_are_reported_once_and_left_alone(cache_dir, fake_site):
    (fake_site / '~ld-package').mkdir()
    logger = MagicMock()
    dm = make_dm(cache_dir, logger=logger)
    with patch.object(subprocess, 'run', FakeRunner()):
        dm.installer.run_pip_install(['requests==2.31.0'])
        dm.installer.run_pip_install(['idna==3.6'])
    assert (fake_site / '~ld-package').exists()
    warnings = [c.args[0] for c in logger.warning.call_args_list if 'incompletely removed' in c.args[0]]
    assert len(warnings) == 1 and '~ld-package' in warnings[0]


def test_failed_attempt_without_leftovers_retries_normally(cache_dir, fake_site):
    runner = FakeRunner()
    calls = []

    def pip(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 300)
        return runner(cmd, **kwargs)

    dm = DependencyManager(cache_dir=cache_dir, max_retries=2)
    with patch.object(subprocess, 'run', pip), patch.object(time, 'sleep'):
        dm.installer.run_pip_install(['requests==2.31.0'])
    assert '--force-reinstall' not in calls[1]
