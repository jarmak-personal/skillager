from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from .database import connect_database
from .locking import resource_lock, resource_locks
from .statefiles import read_user_json, write_user_json


T = TypeVar("T")
SCHEMA_VERSION = 1
_snapshots: ContextVar[dict[Path, dict[str, Any]] | None] = ContextVar("approval_snapshots", default=None)


def database_path(root: Path) -> Path:
    return root / "trust.sqlite3"


def _legacy_path(root: Path) -> Path:
    return root / "trust.json"


@contextmanager
def locked_records(roots: list[Path]) -> Iterator[dict[Path, dict[str, Any]]]:
    """Hold existing decision writer locks while reading; never initialize state."""
    canonical = sorted({root.resolve() for root in roots}, key=str)
    with resource_locks([_legacy_path(root) for root in canonical]):
        yield {root: load(root) for root in canonical}


def _backup_path(root: Path) -> Path:
    return root / "trust.json.migrated"


def _marker_path(root: Path) -> Path:
    return root / "trust.sqlite3.required"


def _has_database(root: Path) -> bool:
    path = database_path(root)
    if path.exists() or path.is_symlink():
        return True
    if any(path.exists() or path.is_symlink() for path in (_backup_path(root), _marker_path(root))):
        raise ValueError(f"approval database is missing: {path}; restore its backup, never import stale trust.json.migrated")
    return False


def _check_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported approval database version {version}; expected {SCHEMA_VERSION}")


def _validate(data: dict[str, Any]) -> None:
    for scope in ("skills", "global_approvals"):
        entries = data.get(scope, {})
        if not isinstance(entries, dict) or any(not isinstance(record, dict) for record in entries.values()):
            raise ValueError(f"invalid legacy approval records: {scope}")


def _load(conn: sqlite3.Connection) -> dict[str, Any]:
    data = {key: json.loads(value) for key, value in conn.execute("SELECT key, value FROM trust_metadata")}
    data.setdefault("skills", {})
    for scope, key, record in conn.execute("SELECT scope, key, record FROM approvals"):
        data.setdefault(scope, {})[key] = json.loads(record)
    return data


def load(root: Path) -> dict[str, Any]:
    if not _has_database(root):
        return read_user_json(_legacy_path(root), {"skills": {}})
    try:
        with closing(connect_database(database_path(root))) as conn:
            _check_schema(conn)
            return _load(conn)
    except sqlite3.Error as exc:
        raise ValueError(f"cannot read approval database {database_path(root)}: {exc}") from exc


@contextmanager
def snapshot(roots: list[Path]) -> Iterator[None]:
    """One inventory operation sees one read per authority, including legacy JSON."""
    current = dict(_snapshots.get() or {})
    for root in roots:
        key = root.resolve()
        if key not in current:
            current[key] = load(root)
        current[root] = current[key]
    token = _snapshots.set(current)
    try:
        yield
    finally:
        _snapshots.reset(token)


def get_record(root: Path, scope: str, key: str) -> dict[str, Any] | None:
    snapshots = _snapshots.get()
    if snapshots is not None:
        if root not in snapshots:
            canonical = root.resolve()
            if canonical in snapshots:
                snapshots[root] = snapshots[canonical]
        if root in snapshots:
            return deepcopy(snapshots[root].get(scope, {}).get(key))
    if not _has_database(root):
        return load(root).get(scope, {}).get(key)
    try:
        with closing(connect_database(database_path(root))) as conn:
            _check_schema(conn)
            row = conn.execute("SELECT record FROM approvals WHERE scope = ? AND key = ?", (scope, key)).fetchone()
            return json.loads(row[0]) if row else None
    except sqlite3.Error as exc:
        raise ValueError(f"cannot read approval database {database_path(root)}: {exc}") from exc


def _initialize(conn: sqlite3.Connection, root: Path) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version:
        _check_schema(conn)
        return
    if any(path.exists() or path.is_symlink() for path in (_backup_path(root), _marker_path(root))):
        raise ValueError("approval database lost its schema; restore the database backup")
    data = read_user_json(_legacy_path(root), {"skills": {}})
    _validate(data)
    for statement in (
        "CREATE TABLE approvals(scope TEXT NOT NULL, key TEXT NOT NULL, state TEXT, content_hash TEXT, record TEXT NOT NULL, PRIMARY KEY(scope, key))",
        "CREATE INDEX approvals_content ON approvals(content_hash, state)",
        "CREATE TABLE trust_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE decision_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, previous_record TEXT, record TEXT, version_reference TEXT)",
        "CREATE INDEX decision_history ON decision_events(scope, key, sequence)",
        "CREATE TABLE content_versions(source_key TEXT NOT NULL, content_hash TEXT NOT NULL, first_accepted_at TEXT NOT NULL, PRIMARY KEY(source_key, content_hash))",
        "CREATE TABLE version_references(source_key TEXT NOT NULL, content_hash TEXT NOT NULL, repository TEXT NOT NULL, git_commit TEXT NOT NULL, skill_path TEXT NOT NULL, PRIMARY KEY(source_key, content_hash, repository, git_commit, skill_path), FOREIGN KEY(source_key, content_hash) REFERENCES content_versions(source_key, content_hash))",
        "CREATE TRIGGER events_no_update BEFORE UPDATE ON decision_events BEGIN SELECT RAISE(ABORT, 'decision history is append-only'); END",
        "CREATE TRIGGER events_no_delete BEFORE DELETE ON decision_events BEGIN SELECT RAISE(ABORT, 'decision history is append-only'); END",
        "CREATE TRIGGER versions_no_update BEFORE UPDATE ON content_versions BEGIN SELECT RAISE(ABORT, 'content versions are immutable'); END",
        "CREATE TRIGGER versions_no_delete BEFORE DELETE ON content_versions BEGIN SELECT RAISE(ABORT, 'content versions are immutable'); END",
    ):
        conn.execute(statement)
    _replace(conn, {"skills": {}}, data, action="migrate")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _write_record(conn: sqlite3.Connection, scope: str, key: str, before: dict[str, Any] | None, after: dict[str, Any] | None, *, action: str, version: dict[str, str] | None = None) -> None:
    if before == after and version is None:
        return
    old = json.dumps(before, sort_keys=True) if before is not None else None
    new = json.dumps(after, sort_keys=True) if after is not None else None
    if after is None:
        conn.execute("DELETE FROM approvals WHERE scope = ? AND key = ?", (scope, key))
    else:
        conn.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope, key) DO UPDATE SET state=excluded.state, content_hash=excluded.content_hash, record=excluded.record",
                     (scope, key, after.get("state"), after.get("content_hash"), new))
    conn.execute("INSERT INTO decision_events(at, action, scope, key, previous_record, record, version_reference) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (datetime.now(timezone.utc).isoformat(), action, scope, key, old, new, json.dumps(version, sort_keys=True) if version is not None else None))


def _replace(conn: sqlite3.Connection, before: dict[str, Any], after: dict[str, Any], *, action: str) -> None:
    _validate(after)
    for scope in ("skills", "global_approvals"):
        old, new = before.get(scope, {}), after.get(scope, {})
        for key in sorted(old.keys() | new.keys()):
            _write_record(conn, scope, key, old.get(key), new.get(key), action=action)
    conn.execute("DELETE FROM trust_metadata")
    # Preserve empty sections and unknown top-level legacy metadata too.
    for key, value in after.items():
        conn.execute("INSERT INTO trust_metadata VALUES (?, ?)", (key, json.dumps({} if key in {"skills", "global_approvals"} else value)))


@contextmanager
def _writer(root: Path, *, already_locked: bool = False) -> Iterator[sqlite3.Connection]:
    # Retain the legacy lock resource so an older in-flight writer cannot race migration.
    with nullcontext() if already_locked else resource_lock(_legacy_path(root)):
        if not _has_database(root):
            _validate(read_user_json(_legacy_path(root), {"skills": {}}))
        try:
            with closing(connect_database(database_path(root), writable=True)) as conn:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    _initialize(conn, root)
                # Finalize the authority switch before any new decision can commit.
                # A crash before this point can only leave the original decisions.
                legacy, backup = _legacy_path(root), _backup_path(root)
                if legacy.exists() and not backup.exists():
                    legacy.rename(backup)
                marker = _marker_path(root)
                if not marker.exists():
                    write_user_json(marker, {"schema": "skillager.approvals.v1"})
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    yield conn
        except sqlite3.Error as exc:
            raise ValueError(f"cannot write approval database {database_path(root)}: {exc}") from exc
        finally:
            snapshots = _snapshots.get()
            if snapshots is not None:
                previous = snapshots.get(root.resolve())
                for alias in list(snapshots):
                    if snapshots[alias] is previous:
                        del snapshots[alias]


def mutate(root: Path, mutation: Callable[[dict[str, Any]], T]) -> T:
    before = load(root)
    after = deepcopy(before)
    result = mutation(after)
    if before == after:
        return result
    with _writer(root) as conn:
        before = _load(conn)
        after = deepcopy(before)
        result = mutation(after)
        _replace(conn, before, after, action="change")
        return result


def save(root: Path, data: dict[str, Any]) -> None:
    with _writer(root) as conn:
        _replace(conn, _load(conn), data, action="replace")


def _record_version(conn: sqlite3.Connection, key: str, after: dict[str, Any], version: dict[str, str]) -> None:
    if version["source_key"] != key or version["content_hash"] != after.get("content_hash"):
        raise ValueError("version reference does not match the approval identity and hash")
    conn.execute("INSERT OR IGNORE INTO content_versions VALUES (?, ?, ?)",
                 (key, version["content_hash"], datetime.now(timezone.utc).isoformat()))
    if version.get("git_commit"):
        conn.execute("INSERT OR IGNORE INTO version_references VALUES (?, ?, ?, ?, ?)",
                     (key, version["content_hash"], version["repository"], version["git_commit"], version["skill_path"]))


def mutate_record(root: Path, scope: str, key: str, mutation: Callable[[dict[str, Any] | None], dict[str, Any] | None], *, version: dict[str, str] | None = None) -> dict[str, Any] | None:
    with _writer(root) as conn:
        row = conn.execute("SELECT record FROM approvals WHERE scope = ? AND key = ?", (scope, key)).fetchone()
        before = json.loads(row[0]) if row else None
        after = mutation(deepcopy(before))
        _write_record(conn, scope, key, before, after, action="decide", version=version)
        if version is not None and after is not None:
            _record_version(conn, key, after, version)
        return after


def derive_library_records(
    root: Path,
    project_root: Path,
    keys: dict[Path, set[tuple[str, str]]],
    derive: Callable[[dict[Path, dict[str, Any]]], list[tuple[str, dict[str, Any], dict[str, str]]]],
) -> None:
    """Guard source decisions and append one bounded library derivation chunk.

    The trust owner supplies policy; this owner keeps source observation, canonical
    decisions and version rows inside the existing approval locking/transaction.
    """
    root, project_root = root.resolve(), project_root.resolve()
    with resource_locks([_legacy_path(root), _legacy_path(project_root)]):
        with _writer(root, already_locked=True) as conn:
            def selected(connection: sqlite3.Connection, wanted: set[tuple[str, str]]) -> dict[str, Any]:
                data: dict[str, Any] = {"skills": {}, "global_approvals": {}}
                for namespace, key in wanted:
                    row = connection.execute("SELECT record FROM approvals WHERE scope = ? AND key = ?", (namespace, key)).fetchone()
                    if row:
                        data[namespace][key] = json.loads(row[0])
                return data
            canonical = selected(conn, keys[root])
            records = {root: canonical}
            if project_root != root:
                if _has_database(project_root):
                    with closing(connect_database(database_path(project_root))) as source_conn:
                        _check_schema(source_conn)
                        records[project_root] = selected(source_conn, keys[project_root])
                else:
                    records[project_root] = load(project_root)
            changes = derive(records)
            for key, after, version in changes:
                before = canonical.get("global_approvals", {}).get(key)
                _write_record(conn, "global_approvals", key, before, after, action="derive-library", version=version)
                _record_version(conn, key, after, version)
