from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..paths import git_root

DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_EVENT_MB = 5
DEFAULT_MAX_EVENTS_PER_SESSION = 200


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sessions_root(state_root: Path) -> Path:
    return state_root / "sessions"


def current_path(state_root: Path) -> Path:
    return sessions_root(state_root) / "current.json"


def lock_path(state_root: Path) -> Path:
    return sessions_root(state_root) / ".lock"


def session_path(state_root: Path, session_id: str) -> Path:
    validate_session_id(session_id)
    return sessions_root(state_root) / f"{session_id}.jsonl"


def new_session_id() -> str:
    return "sks_" + secrets.token_hex(8)


def validate_session_id(session_id: str) -> None:
    if not re.fullmatch(r"sks_[0-9a-f]+", session_id):
        raise ValueError(f"invalid session id: {session_id}")


@contextlib.contextmanager
def _session_lock(state_root: Path):
    sessions_root(state_root).mkdir(parents=True, exist_ok=True)
    path = lock_path(state_root)
    with path.open("a+b") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            yield


def start_session(
    state_root: Path,
    *,
    agent: str = "unknown",
    external_session_id: str | None = None,
    external_conversation_id: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    cwd = (cwd or Path.cwd()).resolve()
    session_id = new_session_id()
    meta: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "external_session_id": external_session_id,
        "external_conversation_id": external_conversation_id,
        "cwd": str(cwd),
        "project_root": str(git_root(cwd) or cwd),
        "started_at": now(),
        "ended_at": None,
    }
    sessions_root(state_root).mkdir(parents=True, exist_ok=True)
    with _session_lock(state_root):
        current_path(state_root).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_event(state_root, session_id, "session_started", meta)
    prune_sessions(state_root)
    return meta


def current_session(state_root: Path) -> dict[str, Any] | None:
    path = current_path(state_root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def end_session(state_root: Path, *, agent: str | None = None, external_session_id: str | None = None) -> dict[str, Any]:
    meta = current_session(state_root)
    if not meta:
        raise KeyError("no current Skillager session; run `skillager session current` to inspect sessions")
    if agent and meta.get("agent") != agent:
        raise KeyError(f"current Skillager session agent is {meta.get('agent')!r}, not {agent!r}; run `skillager session current --json`")
    if external_session_id and meta.get("external_session_id") != external_session_id:
        raise KeyError("current Skillager session does not match external session id; run `skillager session current --json`")
    meta["ended_at"] = now()
    with _session_lock(state_root):
        current_path(state_root).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_event(state_root, meta["session_id"], "session_ended", meta)
    prune_sessions(state_root)
    return meta


def ensure_session(
    state_root: Path,
    *,
    agent: str | None = None,
    external_session_id: str | None = None,
    no_create: bool = False,
) -> dict[str, Any] | None:
    meta = current_session(state_root)
    if meta:
        if agent and meta.get("agent") != agent:
            meta["agent"] = agent
        if external_session_id and meta.get("external_session_id") != external_session_id:
            meta["external_session_id"] = external_session_id
        return meta
    if no_create:
        return None
    return start_session(state_root, agent=agent or "unknown", external_session_id=external_session_id)


def append_event(state_root: Path, session_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    sessions_root(state_root).mkdir(parents=True, exist_ok=True)
    record = {
        "event": event,
        "timestamp": now(),
        "session_id": session_id,
        **payload,
    }
    line = json.dumps(record, sort_keys=True) + "\n"
    with _session_lock(state_root):
        with session_path(state_root, session_id).open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return record


def record_search_event(
    state_root: Path,
    *,
    query: str,
    results: list[dict[str, Any]],
    agent: str | None = None,
    tag: str | None = None,
    trusted_only: bool = False,
    limit: int | None = None,
    no_record: bool = False,
) -> dict[str, Any] | None:
    if no_record:
        return None
    meta = ensure_session(state_root, agent=agent)
    if not meta:
        return None
    top_ids = [str(item.get("id")) for item in results[:10] if item.get("id")]
    payload = {
        "agent": meta.get("agent"),
        "external_session_id": meta.get("external_session_id"),
        "external_conversation_id": meta.get("external_conversation_id"),
        "query_hash": sha256(query.encode("utf-8")).hexdigest(),
        "query_preview": _preview(query),
        "top_ids": top_ids,
        "result_count": len(results),
        "tag": tag,
        "trusted_only": trusted_only,
        "limit": limit,
    }
    return append_event(state_root, meta["session_id"], "skill_search", payload)


def record_materialize_events(
    state_root: Path,
    results: list[dict[str, Any]],
    *,
    no_record: bool = False,
) -> list[dict[str, Any]]:
    if no_record:
        return []
    meta = ensure_session(state_root, no_create=True)
    if not meta:
        return []
    records = []
    for result in results:
        skill_id = result.get("skill_id")
        if not skill_id:
            continue
        records.append(
            append_event(
                state_root,
                meta["session_id"],
                "skill_materialized",
                {
                    "agent": meta.get("agent"),
                    "external_session_id": meta.get("external_session_id"),
                    "skill_id": skill_id,
                    "status": result.get("status"),
                    "target": result.get("target"),
                    "target_agent": result.get("agent"),
                    "scope": result.get("scope"),
                    "reason": result.get("reason"),
                },
            )
        )
    return records


def record_doctor_event(
    state_root: Path,
    *,
    result: dict[str, Any],
    fix_result: dict[str, Any] | None = None,
    agent: str | None = None,
    no_record: bool = False,
) -> dict[str, Any] | None:
    if no_record:
        return None
    meta = ensure_session(state_root, agent=agent or result.get("agent"))
    if not meta:
        return None
    readiness = result.get("readiness") or {}
    exposure = readiness.get("exposure") or {}
    state = result.get("state") or {}
    payload = {
        "agent": meta.get("agent"),
        "external_session_id": meta.get("external_session_id"),
        "external_conversation_id": meta.get("external_conversation_id"),
        "command": "doctor",
        "fix": bool(fix_result and fix_result.get("requested")),
        "fix_applied": bool(fix_result and fix_result.get("applied")),
        "fix_reason_code": (fix_result or {}).get("reason_code"),
        "status": result.get("status"),
        "readiness_status": _doctor_readiness_status(readiness),
        "review_ready": bool(readiness.get("review_ready")),
        "handoff_ready": bool(readiness.get("handoff_ready")),
        "ready": bool(readiness.get("ready")),
        "next_action_code": _doctor_next_action_code(result),
        "counts": {
            "review_needed": int(((state.get("review") or {}).get("needed")) or 0),
            "lint_blocked": int(((state.get("lint_blocked") or {}).get("count")) or 0),
            "approved_hidden": int(exposure.get("available_on_demand") or 0),
            "exposed": int(exposure.get("exposed") or 0),
            "native": int(exposure.get("native") or 0),
            "stubbed": int(exposure.get("stubbed") or 0),
            "routed": int(exposure.get("routed") or 0),
        },
    }
    return append_event(state_root, meta["session_id"], "doctor_run", payload)


def record_skill_event(
    state_root: Path,
    event: str,
    skill: dict[str, Any],
    *,
    agent: str | None = None,
    external_session_id: str | None = None,
    note: str | None = None,
    query: str | None = None,
    no_record: bool = False,
) -> dict[str, Any] | None:
    if no_record:
        return None
    meta = ensure_session(state_root, agent=agent, external_session_id=external_session_id)
    if not meta:
        return None
    payload = {
        "agent": meta.get("agent"),
        "external_session_id": meta.get("external_session_id"),
        "external_conversation_id": meta.get("external_conversation_id"),
        "skill_id": skill.get("id"),
        "content_hash": skill.get("content_hash"),
        "source": skill.get("source"),
        "source_path": skill.get("entrypoint"),
        "note": note,
        "query": query,
    }
    return append_event(state_root, meta["session_id"], event, payload)


def _doctor_readiness_status(readiness: dict[str, Any]) -> str:
    if readiness.get("ready"):
        return "ready"
    if not readiness.get("review_ready"):
        return "review-needed"
    if not readiness.get("handoff_ready"):
        return "handoff-not-ready"
    return "inconsistent"


def _doctor_next_action_code(result: dict[str, Any]) -> str | None:
    handoff = ((result.get("readiness") or {}).get("handoff") or {})
    reason_code = handoff.get("reason_code")
    if reason_code:
        return str(reason_code)
    status = result.get("status")
    return str(status) if status else None


def read_events(state_root: Path, session_id: str) -> list[dict[str, Any]]:
    path = session_path(state_root, session_id)
    if not path.exists():
        raise KeyError(f"unknown session: {session_id}")
    with _session_lock(state_root):
        return _read_events_unlocked(path)


def prune_sessions(
    state_root: Path,
    *,
    days: int | None = None,
    max_mb: int | None = None,
    max_events_per_session: int | None = None,
) -> dict[str, Any]:
    days = _int_setting("SKILLAGER_RETENTION_DAYS", DEFAULT_RETENTION_DAYS) if days is None else days
    max_mb = _int_setting("SKILLAGER_MAX_EVENT_MB", DEFAULT_MAX_EVENT_MB) if max_mb is None else max_mb
    max_events_per_session = (
        _int_setting("SKILLAGER_MAX_EVENTS_PER_SESSION", DEFAULT_MAX_EVENTS_PER_SESSION)
        if max_events_per_session is None
        else max_events_per_session
    )
    root = sessions_root(state_root)
    if not root.exists():
        return {"deleted_sessions": 0, "trimmed_sessions": 0, "bytes_after": 0}
    with _session_lock(state_root):
        return _prune_sessions_locked(state_root, root, days=days, max_mb=max_mb, max_events_per_session=max_events_per_session)


def _prune_sessions_locked(
    state_root: Path,
    root: Path,
    *,
    days: int,
    max_mb: int,
    max_events_per_session: int,
) -> dict[str, Any]:
    current = current_session(state_root)
    deleted: set[str] = set()
    trimmed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    for path in sorted(root.glob("sks_*.jsonl")):
        events = _read_event_file(path)
        if not events:
            path.unlink(missing_ok=True)
            deleted.add(path.stem)
            continue
        last_at = _event_time(events[-1])
        if days >= 0 and last_at and last_at < cutoff:
            path.unlink(missing_ok=True)
            deleted.add(path.stem)
            continue
        if max_events_per_session > 0 and len(events) > max_events_per_session:
            kept = _trim_events(events, max_events_per_session)
            path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in kept), encoding="utf-8")
            trimmed += 1
    max_bytes = max(0, max_mb) * 1024 * 1024
    files = [path for path in root.glob("sks_*.jsonl") if path.exists()]
    total = sum(path.stat().st_size for path in files)
    if max_bytes and total > max_bytes:
        for path in sorted(files, key=_last_event_sort_key):
            if total <= max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
            deleted.add(path.stem)
    if current and current.get("session_id") in deleted:
        current_path(state_root).unlink(missing_ok=True)
    return {
        "deleted_sessions": len(deleted),
        "trimmed_sessions": trimmed,
        "bytes_after": sum(path.stat().st_size for path in root.glob("sks_*.jsonl")),
        "retention_days": days,
        "max_event_mb": max_mb,
        "max_events_per_session": max_events_per_session,
    }


def find_sessions(state_root: Path, *, agent: str | None = None, external_session_id: str | None = None) -> list[str]:
    root = sessions_root(state_root)
    if not root.exists():
        return []
    matches: list[str] = []
    with _session_lock(state_root):
        for path in sorted(root.glob("sks_*.jsonl")):
            events = _read_events_unlocked(path)
            if not events:
                continue
            first = events[0]
            if agent and first.get("agent") != agent:
                continue
            if external_session_id and first.get("external_session_id") != external_session_id:
                continue
            matches.append(path.stem)
    return matches


def list_sessions(state_root: Path, *, agent: str | None = None) -> list[dict[str, Any]]:
    root = sessions_root(state_root)
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    with _session_lock(state_root):
        for path in sorted(root.glob("sks_*.jsonl")):
            events = _read_events_unlocked(path)
            if not events:
                continue
            first = events[0]
            if agent and first.get("agent") != agent:
                continue
            ended_at = None
            last_at = first.get("timestamp") or first.get("started_at")
            for event in events:
                last_at = event.get("timestamp") or last_at
                if event.get("event") == "session_ended":
                    ended_at = event.get("ended_at") or event.get("timestamp")
            records.append(
                {
                    "session_id": path.stem,
                    "agent": first.get("agent"),
                    "external_session_id": first.get("external_session_id"),
                    "started_at": first.get("started_at") or first.get("timestamp"),
                    "last_event_at": last_at,
                    "ended_at": ended_at,
                    "active": ended_at is None,
                }
            )
    return sorted(records, key=lambda item: item.get("last_event_at") or "", reverse=True)


def clear_sessions(state_root: Path) -> int:
    root = sessions_root(state_root)
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"refusing to clear unsafe sessions path: {root}")
    session_count = len(list(root.glob("sks_*.jsonl")))
    shutil.rmtree(root)
    return session_count


def redact_session(state_root: Path, session_id: str) -> None:
    path = session_path(state_root, session_id)
    with _session_lock(state_root):
        events = _read_events_unlocked(path)
        redacted = []
        for event in events:
            event["external_session_id"] = None
            event["external_conversation_id"] = None
            event["note"] = None
            redacted.append(event)
        path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in redacted), encoding="utf-8")
        current = current_session(state_root)
        if current and current.get("session_id") == session_id:
            current["external_session_id"] = None
            current["external_conversation_id"] = None
            current_path(state_root).write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def detect_agent_session() -> tuple[str, str | None]:
    for name, agent in (("CODEX_SESSION_ID", "codex"), ("CLAUDE_SESSION_ID", "claude")):
        value = os.environ.get(name)
        if value:
            return agent, value
    return "unknown", None


def _preview(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _int_setting(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _read_event_file(path: Path) -> list[dict[str, Any]]:
    try:
        return _read_events_unlocked(path)
    except (OSError, json.JSONDecodeError):
        return []


def _read_events_unlocked(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event_time(event: dict[str, Any]) -> datetime | None:
    value = event.get("timestamp") or event.get("started_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trim_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(events) <= limit:
        return events
    if limit == 1:
        return [events[-1]]
    first = events[0]
    tail = events[-(limit - 1) :]
    if first in tail:
        return tail
    return [first, *tail]


def _last_event_sort_key(path: Path) -> str:
    events = _read_event_file(path)
    if not events:
        return ""
    return str(events[-1].get("timestamp") or events[-1].get("started_at") or "")
