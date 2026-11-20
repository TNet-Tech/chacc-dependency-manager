"""
Parsing requirements text and pip-compile output.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import os
from unittest.mock import patch

from chacc.requirements import pip_path
from chacc.utils import attribute_packages, parse_lock_file
from helpers import (
    LOCK_A_B,
)


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

    def at(relative):
        return os.path.normpath(os.path.join(base, relative))

    # Option lines are written the way pip reads them (forward slashes, quoted if needed).
    assert out[0] == f"-r {pip_path(at('base.txt'))}"
    assert out[1] == f"--requirement={pip_path(at('../shared/reqs.txt'))}  # shared"
    assert out[2] == f"-c {pip_path(at('constraints.txt'))}"
    assert out[3] == f"-e {pip_path(at('libs/core'))}"
    assert out[4] == at('libs/plugin[extra]')
    assert out[5] == text.splitlines()[5]  # VCS link untouched
    # An absolute path stays the same file (on Windows it gains the current drive letter).
    assert out[6] == f"-r {pip_path(at('/abs/other.txt'))}"
    assert out[7:] == text.splitlines()[7:]


def test_packages_from_included_files_are_credited_to_the_including_modules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = tmp_path / 'base.txt'
    lock = (
        "idna==3.8\n    # via -r base.txt\n"
        "six==1.17.0\n    # via -r w/m000-api.in\n"
        "click==8.1.0\n    # via -r w/m001-web.in\n"
    )
    pins, via = parse_lock_file(lock)
    per_module = attribute_packages(
        pins, via, {'api': 'm000-api.in', 'web': 'm001-web.in'}, {str(base): ['api', 'web']}
    )
    assert per_module == {
        'api': {'idna': '==3.8', 'six': '==1.17.0'},
        'web': {'click': '==8.1.0', 'idna': '==3.8'},
    }


def test_pip_path_survives_pips_shlex_parsing_on_windows_and_with_spaces():
    import shlex
    from pathlib import PureWindowsPath
    from chacc import requirements as requirements_module
    from chacc.requirements import pip_path

    with patch.object(requirements_module, 'PurePath', PureWindowsPath):
        windows = pip_path(r"C:\Users\Jonas\apicon\.chacc_cache\core-constraints.txt")
    # pip reads "-c <path>" with shlex.split; the backslashes must not be eaten.
    assert shlex.split(f"-c {windows}") == ['-c', 'C:/Users/Jonas/apicon/.chacc_cache/core-constraints.txt']
    assert shlex.split(f"-r {pip_path('/srv/my app/base.txt')}") == ['-r', '/srv/my app/base.txt']


def test_absolutized_option_paths_are_safe_for_pip(tmp_path):
    import shlex
    from chacc.utils import absolutize_requirement_paths, included_files
    base = tmp_path / 'my project'
    base.mkdir()
    (base / 'base.txt').write_text('idna')
    out = absolutize_requirement_paths('-r base.txt  # shared\n-e ./libs/core', str(base))
    first, second = out.splitlines()
    assert shlex.split(first.split('#')[0]) == ['-r', str(base / 'base.txt')]
    assert first.endswith('  # shared')
    assert shlex.split(second) == ['-e', str(base / 'libs' / 'core')]
    # The quoted path is still found when tracking included files.
    assert included_files(out) == [str(base / 'base.txt')]
    # Running it again leaves already-absolute, quoted paths as they are.
    assert absolutize_requirement_paths(out, str(tmp_path)) == out
