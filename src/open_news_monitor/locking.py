from __future__ import annotations

import os
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
    """Windows advisory lock held for one complete monitor cycle.

    The lock is an OS file lock, not a marker file. Windows releases it when
    the process exits, including an unexpected process termination. The small
    adjacent .lock file may remain, but it is harmless and never blocks a run
    by itself.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.lock_path = Path(f"{self.database_path}.lock")
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "DatabaseRunLock":
        try:
            import msvcrt
        except ImportError as error:  # pragma: no cover - project scheduler targets Windows
            raise RuntimeError("Open News Monitor run locking currently requires Windows.") from error

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            if self.lock_path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            handle.close()
            raise MonitorAlreadyRunningError(self.database_path) from error
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self._handle is None:
            return False
        try:
            import msvcrt
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None
        return False
