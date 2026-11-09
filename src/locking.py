"""Cross-platform advisory OS file lock (msvcrt / fcntl) to eliminate lost updates."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def file_lock(lock_path: Path, timeout_seconds: float = 5.0):
    """Cross-platform advisory OS file lock to eliminate lost updates in concurrent hook executions."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.time()
    lock_fh = None
    acquired = False

    while not acquired:
        try:
            lock_fh = open(lock_path, "a+b")
            if sys.platform == "win32":
                lock_fh.seek(0)
                # LK_NBLCK: Non-blocking lock of byte 0
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, PermissionError, OSError):
            if lock_fh is not None:
                try:
                    lock_fh.close()
                except OSError:
                    pass
                lock_fh = None
            if time.time() - start_time >= timeout_seconds:
                break
            time.sleep(0.02)

    try:
        yield acquired
    finally:
        if lock_fh is not None:
            try:
                if acquired:
                    if sys.platform == "win32":
                        try:
                            lock_fh.seek(0)
                            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        try:
                            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                lock_fh.close()
            except OSError:
                pass
