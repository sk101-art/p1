"""Cross-platform OS advisory process locking without lock file unlinking inode races."""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from phase2.config import DEFAULT_CHROMA_DIR

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class StoreLockError(RuntimeError):
    """Raised when process fails to acquire store lock within timeout."""


class StoreLock:
    """Exclusive OS advisory lock derived from store persist_dir or lock_file. Never unlinks lock files."""

    def __init__(
        self,
        persist_dir: str | Path | None = None,
        lock_file: str | Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        if lock_file is not None:
            self.lock_file_path = Path(lock_file).resolve()
            self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            store_path = Path(persist_dir or DEFAULT_CHROMA_DIR).resolve()
            store_path.mkdir(parents=True, exist_ok=True)
            # Lock file is a SIBLING of the store directory (derived from
            # persist_dir) so the store directory itself can be atomically
            # renamed (tombstone/staged swaps) while the lock is held.
            # Never unlinked: persists across processes to avoid inode races.
            self.lock_file_path = store_path.parent / f".{store_path.name}.store.lock"
        self.timeout = timeout
        self._fd: int | None = None

    def acquire(self) -> None:
        start_time = time.time()
        while True:
            try:
                # Open with O_RDWR | O_CREAT
                self._fd = os.open(
                    str(self.lock_file_path),
                    os.O_RDWR | os.O_CREAT,
                    0o666,
                )

                if sys.platform == "win32":
                    # Lock first byte non-blocking
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Lock acquired successfully! Truncate and write lock metadata
                os.lseek(self._fd, 0, os.SEEK_SET)
                os.ftruncate(self._fd, 0)
                metadata = {
                    "pid": os.getpid(),
                    "hostname": os.getenv("COMPUTERNAME", os.getenv("HOSTNAME", "localhost")),
                    "acquired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "process_name": sys.argv[0],
                }
                os.write(self._fd, json.dumps(metadata, indent=2).encode("utf-8"))
                os.fsync(self._fd)
                return
            except (OSError, IOError):
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None

                if time.time() - start_time >= self.timeout:
                    raise StoreLockError(
                        f"Could not acquire lock for {self.lock_file_path} within {self.timeout}s"
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None

    def __enter__(self) -> StoreLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any) -> None:
        self.release()
