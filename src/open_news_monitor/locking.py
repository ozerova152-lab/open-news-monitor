from __future__ import annotations

import sys
from pathlib import Path
from typing import BinaryIO


class MonitorAlreadyRunningError(RuntimeError):
    """Raised when another process holds the lock for the same SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(
            "Этот монитор уже выполняется для той же базы данных. "
            "Дождитесь завершения предыдущего запуска и попробуйте снова."
        )


class DatabaseRunLock:
    """Cross-platform advisory lock held for one complete monitor cycle.

    The lock is an OS file lock, not a marker file. The adjacent .lock file
    may remain after a process exits, but it is harmless and never blocks a
    run by itself.
    """

    def __init__(self, database_path: str | Path, *, platform_name: str | None = None) -> None:
        self.database_path = Path(database_path)
        self.lock_path = Path(f"{self.database_path}.lock")
        self._handle: BinaryIO | None = None
        self._platform_name = platform_name or sys.platform

    def __enter__(self) -> "DatabaseRunLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            self._acquire(handle)
        except OSError as error:
            handle.close()
            raise MonitorAlreadyRunningError(self.database_path) from error
        except Exception:
            handle.close()
            raise
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self._handle is None:
            return False
        try:
            self._release(self._handle)
        finally:
            self._handle.close()
            self._handle = None
        return False

    def _acquire(self, handle: BinaryIO) -> None:
        if self._platform_name == "win32":
            import msvcrt
            if self.lock_path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        try:
            import fcntl
        except ImportError as error:  # pragma: no cover - non-POSIX embedded runtimes
            raise RuntimeError("Open News Monitor requires Windows or a POSIX file-locking API.") from error
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self, handle: BinaryIO) -> None:
        if self._platform_name == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        try:
            import fcntl
        except ImportError as error:  # pragma: no cover - paired with _acquire
            raise RuntimeError("Open News Monitor requires Windows or a POSIX file-locking API.") from error
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
