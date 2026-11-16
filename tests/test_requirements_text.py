"""
Parsing requirements text and pip-compile output.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import os


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
    assert out[0] == f"-r {os.path.join(base, 'base.txt')}"
    assert out[1] == f"--requirement={os.path.normpath(os.path.join(base, '../shared/reqs.txt'))}  # shared"
    assert out[2] == f"-c {os.path.join(base, 'constraints.txt')}"
    assert out[3] == f"-e {os.path.join(base, 'libs/core')}"
    assert out[4] == os.path.join(base, 'libs/plugin[extra]')
    assert out[5:] == text.splitlines()[5:]


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
