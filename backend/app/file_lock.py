"""Reentrant path locks shared by threads, store instances and local processes."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary

_guard = threading.Lock()
_locks: WeakValueDictionary = WeakValueDictionary()
_held = threading.local()


@contextmanager
def file_lock(path: Path):
    path = path.resolve()
    with _guard:
        lock = _locks.get(path)
        if lock is None:
            lock = threading.RLock()
            _locks[path] = lock
    with lock:
        held = getattr(_held, "paths", set())
        if path in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the lock anchor: unlinking it could split ownership between writers.
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            _held.paths = held
            held.add(path)
            try:
                yield
            finally:
                held.remove(path)
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
