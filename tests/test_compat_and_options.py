"""
1.3.0-style calls keep working, and manager options such as log_level.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import asyncio
import subprocess
from unittest.mock import patch

import pytest

from chacc import DependencyManager, ResolutionError
from chacc import installer as installer_module
from helpers import (
    FakeRunner, make_dm,
)


def test_resolve_module_dependencies_keeps_1_3_contract(cache_dir):
    dm = make_dm(cache_dir)
    with patch.object(subprocess, 'run', FakeRunner(compile_rc=1, compile_stderr="boom")):
        assert dm.resolve_module_dependencies('a', 'requests') == {}
        with pytest.raises(ResolutionError):
            dm.resolve_module_dependencies('a', 'requests', raise_on_error=True)


def test_1_3_style_async_usage_still_works(cache_dir):
    from chacc import Config, re_resolve_dependencies
    runner = FakeRunner()
    with patch.object(subprocess, 'run', runner), \
         patch.object(installer_module, 'get_installed_versions', return_value={}):
        asyncio.run(re_resolve_dependencies({'a': 'requests'}, config=Config(cache_dir=cache_dir, max_retries=1)))
        asyncio.run(DependencyManager(cache_dir=cache_dir).upgrade_dependencies({'a': 'requests'}))
    assert runner.install_calls


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
