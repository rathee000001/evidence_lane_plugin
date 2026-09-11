"""Small OS-lock primitive shared by engine and project writer ownership."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import BinaryIO

from .errors import LaneError
from .storage import reject_links


class RuntimeLock:
    """A kernel-released lock, including when the owning process crashes."""

    def __init__(self, path: Path):
        self.path = path
        self.stream: BinaryIO | None = None

    def acquire(self) -> None:
        if self.stream is not None:
            raise LaneError("LOCK_ALREADY_HELD", "This owner already holds its runtime lock.")
        reject_links(self.path, Path(self.path.anchor))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b", buffering=0)
        try:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise LaneError("RUNTIME_IN_USE", "Another owner holds this directory lock.") from None
        self.stream = stream

    def release(self) -> None:
        if self.stream is None:
            return
        stream, self.stream = self.stream, None
        try:
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
