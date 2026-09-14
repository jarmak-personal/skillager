from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .statefiles import _assert_user_owned_regular_file


def connect_database(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    """Open user-owned SQLite state without following file/sidecar symlinks."""
    for candidate in (path, *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        if candidate.exists() or candidate.is_symlink():
            try:
                _assert_user_owned_regular_file(candidate)
            except (FileNotFoundError, ValueError):
                # SQLite can remove optional sidecars between validation stats.
                # A missing main database or any still-present unsafe path fails.
                if candidate == path or candidate.exists() or candidate.is_symlink():
                    raise
    if writable:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            _assert_user_owned_regular_file(path)
        else:
            os.close(fd)
    connection = sqlite3.connect(path.absolute().as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True, timeout=5)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
