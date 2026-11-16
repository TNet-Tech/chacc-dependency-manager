"""
Async calls don't block the event loop; work on a cache directory is serialised across threads and processes.

pip / pip-compile are replaced by fakes (see helpers.py), so no network is needed.
"""

import asyncio
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from helpers import (
    FakeRunner, make_dm,
)


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
        with patch.object(subprocess, 'run', slow_run):
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

    with patch.object(subprocess, 'run', run):
        threads = [threading.Thread(target=dm.resolve_dependencies_sync,
                                    args=({'a': f'requests\n# {i}'},), kwargs={'install': False})
                   for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not overlap


def test_lock_is_shared_across_managers(cache_dir):
    assert make_dm(cache_dir)._lock is make_dm(cache_dir)._lock


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
