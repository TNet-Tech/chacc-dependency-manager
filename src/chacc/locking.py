"""
The lock that makes work on one cache directory happen one at a time.
"""

import errno
import os
import threading
import time
from typing import Dict, Optional

from .utils import default_logger

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None
try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


class CacheLock:
    """
    Serialises work on one cache directory across threads *and* processes.

    Threads in a process share a re-entrant lock. The outermost acquisition also takes
    an OS file lock on ``<cache_dir>/.lock`` (``flock`` on POSIX, ``msvcrt.locking`` on
    Windows), so several server workers or parallel CI jobs sharing a cache directory
    take turns instead of overwriting each other's cache. The file is opened per
    acquisition, so forked workers never share a lock by accident. If the file system
    doesn't support locking, ChaCC logs a warning and carries on with the thread lock.
    """

    def __init__(self, cache_dir: str):
        self._thread_lock = threading.RLock()
        self._path = os.path.join(cache_dir, ".lock")
        self._depth = 0
        self._fd: Optional[int] = None

    def __enter__(self) -> "CacheLock":
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self._fd = self._lock_file()
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    if _fcntl is not None:
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                    elif _msvcrt is not None:
                        os.lseek(fd, 0, os.SEEK_SET)
                        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                os.close(fd)
        finally:
            self._thread_lock.release()

    def _lock_file(self) -> Optional[int]:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            default_logger.warning(f"Can't create the cache lock file ({e}); other processes are not coordinated")
            return None
        try:
            if self._try_lock(fd, blocking=False):
                return fd
            default_logger.info(f"Waiting for another process using the cache in {os.path.dirname(self._path)}...")
            self._try_lock(fd, blocking=True)
            return fd
        except OSError as e:
            default_logger.warning(f"File locking isn't supported here ({e}); other processes are not coordinated")
            os.close(fd)
            return None

    @staticmethod
    def _try_lock(fd: int, blocking: bool) -> bool:
        """Take the file lock. Returns False if it's held elsewhere (non-blocking only)."""
        if _fcntl is not None:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | (0 if blocking else _fcntl.LOCK_NB))
                return True
            except OSError as e:
                if not blocking and e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    return False
                raise
        if _msvcrt is not None:
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    if not blocking:
                        return False
                    time.sleep(0.1)
        return True


_CACHE_LOCKS: Dict[str, CacheLock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


def lock_for(cache_dir: str) -> CacheLock:
    """The lock for ``cache_dir`` (an absolute path), created on first use."""
    with _CACHE_LOCKS_GUARD:
        if cache_dir not in _CACHE_LOCKS:
            _CACHE_LOCKS[cache_dir] = CacheLock(cache_dir)
        return _CACHE_LOCKS[cache_dir]
