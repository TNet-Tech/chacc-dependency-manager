"""
Shared fakes for the tests: pip and pip-compile replaced by :class:`FakeRunner`.
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, 'src')

from chacc import DependencyManager  # noqa: E402


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


def make_dm(cache_dir, **kwargs):
    return DependencyManager(cache_dir=cache_dir, max_retries=1, **kwargs)


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


REQ_VERSIONS = {'requests': ('requests', '2.31.0'), 'idna': ('idna', '3.6'), 'flask': ('flask', '3.0.0')}


def versions_runner():
    return FakeRunner(lock=lambda inputs: lock_for(inputs, REQ_VERSIONS))


def run_cli(argv):
    from chacc.cli import create_parser
    args = create_parser().parse_args(argv)
    args.func(args)
