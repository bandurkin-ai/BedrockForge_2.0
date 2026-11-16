"""`project.lock` (ADR 0002 §5): the core keeps the file open and locked by the OS for the whole time it works
with the project. The OS drops the lock when the process dies, so a crash leaves no stale lock.
Busy → the project opens «только чтение».
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import sys
from pathlib import Path
from typing import IO

from bf_core.db.dialect import utcnow


class ProjectLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Takes the lock; False if another process holds it."""
        if not self.path.exists():
            self.path.write_bytes(b" ")
        fh = open(self.path, "r+b")  # noqa: SIM115 - kept open while the lock is held
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        info = {"pid": os.getpid(), "host": platform.node(), "user": _user(), "since": utcnow().isoformat()}
        # The first byte is the locked range on Windows: the owner info goes after it.
        fh.seek(1)
        fh.write(json.dumps(info, ensure_ascii=False).encode("utf-8"))
        fh.truncate()
        fh.flush()
        return True

    def owner(self) -> dict[str, object]:
        """Who holds the lock (for the «только чтение» warning); {} if unknown."""
        try:
            with open(self.path, "rb") as fh:  # byte 0 is locked on Windows: read past it
                fh.seek(1)
                raw = fh.read(4096)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    @property
    def held(self) -> bool:
        return self._fh is not None


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is not an error
        return ""
