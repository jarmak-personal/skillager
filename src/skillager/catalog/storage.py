from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from ..state.database import connect_database
from ..state.locking import resource_lock
from ..state.statefiles import _assert_user_owned_regular_file


def cache_path(root: Path) -> Path:
    return root / "catalog.sqlite3"


def read_collection(root: Path, name: str) -> dict[str, Any] | None:
    path = cache_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    try:
        with closing(connect_database(path)) as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT metadata FROM collections WHERE name = ?", (name,)).fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            data["skills"] = [json.loads(record) for (record,) in conn.execute("SELECT metadata FROM skills WHERE collection = ? ORDER BY position", (name,))]
            return data
    except (sqlite3.Error, json.JSONDecodeError):
        return None


def write_collection(root: Path, name: str, data: dict[str, Any]) -> None:
    path = cache_path(root)
    with resource_lock(path):
        try:
            _write_collection(root, name, data)
        except sqlite3.DatabaseError as exc:
            # Rebuild only corrupt derived data, never a busy or unwritable database.
            if getattr(exc, "sqlite_errorcode", None) not in {11, 26} and str(exc) not in {"file is not a database", "database disk image is malformed"}:
                raise ValueError(f"cannot update collection cache {path}: {exc}") from exc
            _assert_user_owned_regular_file(path)
            path.unlink()
            _write_collection(root, name, data)


def _write_collection(root: Path, name: str, data: dict[str, Any]) -> None:
    with closing(connect_database(cache_path(root), writable=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS skills(collection TEXT NOT NULL REFERENCES collections(name) ON DELETE CASCADE, position INTEGER NOT NULL, skill_id TEXT, content_hash TEXT, metadata TEXT NOT NULL, PRIMARY KEY(collection, position))")
        conn.execute("CREATE INDEX IF NOT EXISTS skills_identity ON skills(skill_id, content_hash)")
        conn.execute("INSERT OR REPLACE INTO collections VALUES (?, ?)", (name, json.dumps({key: value for key, value in data.items() if key != "skills"})))
        conn.executemany("INSERT INTO skills VALUES (?, ?, ?, ?, ?)",
                         [(name, position, skill.get("id"), skill.get("content_hash"), json.dumps(skill)) for position, skill in enumerate(data.get("skills", []))])


def remove_collection(root: Path, name: str) -> None:
    if not cache_path(root).exists():
        return
    try:
        with closing(connect_database(cache_path(root), writable=True)) as conn, conn:
            conn.execute("DELETE FROM collections WHERE name = ?", (name,))
    except sqlite3.Error:
        # Registration was removed; a stale cache cannot put it back in inventory.
        pass
