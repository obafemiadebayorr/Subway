"""Cross-platform exclusive file locks on an open file descriptor.

POSIX uses fcntl.flock. Windows uses msvcrt.locking on a single byte at
offset 0, so the lock position is always reset with lseek before each call.

Contract (same on both platforms):
- lock(fd, nonblock=True) raises BlockingIOError when another holder has the lock.
- lock(fd, nonblock=False) waits until the lock is acquired.
- unlock(fd) releases the lock.

Windows caveat: byte-range locks are per-process-handle and do not give
flock's shared/exclusive upgrade semantics. Only exclusive locks are offered.
"""

from __future__ import annotations

import os
import sys
import time

__all__ = ["lock", "unlock"]

_POLL_SECONDS = 0.05

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def lock(fd: int, nonblock: bool = False) -> None:
    """Take an exclusive lock on fd. See module docstring for semantics."""
    if nonblock:
        _try_lock(fd)
        return
    while True:
        try:
            _try_lock(fd)
            return
        except BlockingIOError:
            time.sleep(_POLL_SECONDS)


def unlock(fd: int) -> None:
    """Release a lock taken with lock()."""
    _release(fd)
