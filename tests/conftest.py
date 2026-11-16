"""Fixtures shared by the test files."""

import os
import site
import sys
import sysconfig
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from chacc import pip_leftovers as leftovers_module  # noqa: E402


@pytest.fixture
def cache_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def fake_site(tmp_path):
    site_dir = tmp_path / 'site-packages'
    site_dir.mkdir()
    with patch.object(sysconfig, 'get_paths',
                      return_value={'purelib': str(site_dir), 'platlib': str(site_dir)}), \
         patch.object(site, 'getusersitepackages', return_value=str(tmp_path / 'none')):
        leftovers_module._REPORTED_LEFTOVERS.clear()
        yield site_dir
