from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from ..simple_yaml import load_mapping
from ..trust import content_hash
from .impl import MATERIALIZED_SCHEMA, ROUTER_SCHEMA, WORKING_SKILL_ID, content_hashes
from .target_state import matches_materialized_target

EXPOSURE_CHANGES_SCHEMA = "skillager.exposure-changes.v1"
ACTIONABLE_EXPOSURE_STATES = {
    "source_update",
    "source_unavailable",
    "local_edit",
    "target_missing",
    "blocked",
    "sidecar_error",
    "unmanaged",
}
_COUNT_KEYS = {
    "current": "current",
    "source_update": "source_updates",
    "source_unavailable": "source_unavailable",
    "local_edit": "local_edits",
    "target_missing": "target_missing",
    "blocked": "blocked",
    "sidecar_error": "sidecar_errors",
    "unmanaged": "unmanaged",
}


def scan_project_exposures(
    project_dir: Path,
    *,
    agent: str | None = None,
    catalog_root: Path | None = None,
    known_native_roots: set[Path] | None = None,
    current_source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Classify live current-project exposure targets without mutating state."""

    records = list_project_exposures(
        project_dir,
        agent=agent,
        catalog_root=catalog_root,
        authoritative=False,
        known_native_roots=known_native_roots,
        current_source_hashes=current_source_hashes,
    )
    counts = {key: 0 for key in _COUNT_KEYS.values()}
    for record in records:
        counts[_COUNT_KEYS[record["status"]]] += 1
    items = [record for record in records if record["status"] in ACTIONABLE_EXPOSURE_STATES]
    return {
        "schema": EXPOSURE_CHANGES_SCHEMA,
        **counts,
        "items": sorted(items, key=lambda item: (str(item.get("agent")), str(item.get("target")))),
    }


def list_project_exposures(
    project_dir: Path,
    *,
    agent: str | None = None,
    catalog_root: Path | None = None,
    authoritative: bool = True,
    known_native_roots: set[Path] | None = None,
    current_source_hashes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return every discoverable current-project exposure classification."""

    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root_agent, roots in _project_skill_roots(project_dir, agent=agent).items():
        for root in roots:
            if not root.is_dir() or root.is_symlink():
                continue
            try:
                targets = sorted(root.iterdir(), key=lambda path: path.name)
            except OSError:
                continue
            for target in targets:
                try:
                    resolved = target.resolve()
                except OSError:
                    continue
                if resolved in seen or target.is_symlink() or not target.is_dir():
                    continue
                seen.add(resolved)
                if target.name == "skillager-working":
                    continue
                sidecar = target / "skillager.materialized.yaml"
                if sidecar.exists():
                    record = classify_exposure_target(
                        target,
                        sidecar=sidecar,
                        fallback_agent=root_agent,
                        authoritative=authoritative,
                        current_source_hashes=current_source_hashes,
                    )
                    if record is not None:
                        records.append(record)
                elif (
                    (target / "SKILL.md").is_file()
                    and target.name != "skillager-working"
                    and resolved not in (known_native_roots or set())
                ):
                    records.append(_unmanaged_record(target, agent=root_agent))
    return sorted(records, key=lambda item: (str(item.get("agent")), str(item.get("target"))))


def classify_exposure_target(
    target: Path,
    *,
    sidecar: Path | None = None,
    fallback_agent: str = "codex",
    authoritative: bool = True,
    current_source_hashes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Classify one sidecar-backed target; return None for Skillager Working."""

    target = target.resolve()
    sidecar = sidecar or target / "skillager.materialized.yaml"
    try:
        data = load_mapping(sidecar)
    except Exception as exc:
        return _sidecar_error_record(target, fallback_agent, f"unreadable sidecar: {type(exc).__name__}")
    if data.get("source_type") == "skillager-working" or (data.get("source_id") or data.get("id")) == WORKING_SKILL_ID:
        return None
    validation_error = _sidecar_validation_error(data)
    if validation_error:
        return _sidecar_error_record(target, fallback_agent, validation_error, data=data)

    record = _base_record(target, data, fallback_agent=fallback_agent)
    if not (target / "SKILL.md").is_file():
        record.update(
            {
                "status": "target_missing",
                "current_hash": None,
                "sidecar_status": "readable",
                "reason": "managed target is missing SKILL.md",
            }
        )
        return record

    try:
        current_hash = content_hash(target)
    except OSError as exc:
        record.update(
            {
                "status": "target_missing",
                "current_hash": None,
                "sidecar_status": "readable",
                "reason": f"managed target is unreadable: {type(exc).__name__}",
            }
        )
        return record

    record["current_hash"] = current_hash
    record["sidecar_status"] = "readable"
    target_matches = matches_materialized_target(target, data)
    blocked_hashes = {str(value) for value in data.get("exposure_blocked_hashes") or []}
    if current_hash in blocked_hashes:
        status = "blocked"
    elif target_matches and current_hash == data["materialized_hash"]:
        source_change = _source_change(data, current_source_hashes)
        if source_change is None:
            status = "current"
        else:
            status = str(source_change.pop("_status"))
            record.update(source_change)
            if status == "source_update":
                command = _reexpose_command(record, data)
                if command:
                    record["command"] = shlex.join(command)
                    record["next_command_argv"] = command
    else:
        status = "local_edit"
    record["status"] = status
    return record


def _sidecar_validation_error(data: dict[str, Any]) -> str | None:
    if data.get("schema") not in {MATERIALIZED_SCHEMA, ROUTER_SCHEMA}:
        return "unsupported or missing sidecar schema"
    if not isinstance(data.get("source_id") or data.get("id"), str):
        return "sidecar is missing source identity"
    if not isinstance(data.get("source_type"), str):
        return "sidecar is missing source type"
    if not isinstance(data.get("materialized_hash"), str):
        return "sidecar is missing materialized hash"
    if data.get("materialized_target_hash") is not None and not isinstance(data.get("materialized_target_hash"), str):
        return "sidecar materialized target hash must be a string"
    blocked = data.get("exposure_blocked_hashes")
    if blocked is not None and not isinstance(blocked, list):
        return "sidecar blocked hashes must be a list"
    return None


def _base_record(
    target: Path,
    data: dict[str, Any],
    *,
    fallback_agent: str,
) -> dict[str, Any]:
    source_type = data.get("source_type")
    if source_type == "skillager-router":
        mode = "router"
    elif source_type == "skillager-stub":
        mode = "stub"
    else:
        mode = "native"
    record = {
        "skill_id": str(data.get("source_id") or data.get("id")),
        "agent": str(data.get("agent") or fallback_agent),
        "scope": "project",
        "mode": mode,
        "target": str(target),
        "source_hash": data.get("source_hash"),
        "materialized_hash": data.get("materialized_hash"),
    }
    if source_type == "skillager-router":
        record.update(
            {
                "router_kind": data.get("router_kind") or data.get("selection_kind"),
                "router_slug": data.get("router_slug") or target.name,
                "tag": data.get("tag"),
                "skill_ids": [str(value) for value in data.get("skill_ids") or []],
            }
        )
    return record


def _source_change(data: dict[str, Any], current_source_hashes: dict[str, str] | None) -> dict[str, Any] | None:
    if current_source_hashes is None:
        return None
    source_type = data.get("source_type")
    if source_type == "skillager-router":
        skill_ids = [str(value) for value in data.get("skill_ids") or []]
        missing = [skill_id for skill_id in skill_ids if skill_id not in current_source_hashes]
        if missing:
            return {
                "_status": "source_unavailable",
                "expected_source_hash": None,
                "reason": "router members are no longer all in the current approved inventory",
                "unavailable_skill_ids": missing,
            }
        expected = content_hashes(
            [{"id": skill_id, "content_hash": current_source_hashes[skill_id]} for skill_id in skill_ids]
        )
        if data.get("source_hash") != expected:
            return {
                "_status": "source_update",
                "expected_source_hash": expected,
                "reason": "approved router member content changed; re-expose to refresh this projection",
            }
        return None

    skill_id = str(data.get("source_id") or data.get("id"))
    expected_source_hash = current_source_hashes.get(skill_id)
    if expected_source_hash is None:
        return {
            "_status": "source_unavailable",
            "expected_source_hash": None,
            "reason": "source is no longer in the current approved inventory",
        }
    if data.get("source_hash") != expected_source_hash:
        return {
            "_status": "source_update",
            "expected_source_hash": expected_source_hash,
            "reason": "approved source content changed; re-expose to refresh this projection",
        }
    return None


def _reexpose_command(record: dict[str, Any], data: dict[str, Any]) -> list[str] | None:
    agent = str(record.get("agent") or "codex")
    common = ["--agent", agent, "--scope", "project"]
    if record.get("mode") == "router":
        if record.get("router_kind") == "tag" and record.get("tag"):
            return ["skillager", "expose", "--tag", str(record["tag"]), "--mode", "router", *common]
        skill_ids = [str(value) for value in data.get("skill_ids") or []]
        if skill_ids:
            return ["skillager", "expose", *skill_ids, "--mode", "router", *common]
        return None
    return ["skillager", "expose", str(record["skill_id"]), "--mode", str(record["mode"]), *common]


def _sidecar_error_record(
    target: Path,
    agent: str,
    reason: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = data or {}
    skill_id = str(data.get("source_id") or data.get("id") or target.name)
    record = _base_record(target, data, fallback_agent=agent)
    record.update(
        {
            "skill_id": skill_id,
            "status": "sidecar_error",
            "sidecar_status": "error",
            "current_hash": None,
            "reason": reason,
        }
    )
    return record


def _unmanaged_record(target: Path, *, agent: str) -> dict[str, Any]:
    skill_id = f"project/{target.name}"
    return {
        "skill_id": skill_id,
        "agent": agent,
        "scope": "project",
        "mode": "native",
        "target": str(target.resolve()),
        "source_hash": None,
        "materialized_hash": None,
        "current_hash": None,
        "sidecar_status": "missing",
        "status": "unmanaged",
        "reason": "native skill exists without Skillager provenance",
    }


def _project_skill_roots(project_dir: Path, *, agent: str | None) -> dict[str, list[Path]]:
    project = project_dir.resolve()
    roots = {
        "codex": [
            project / ".agents" / "skills",
            project / ".agents" / "codex" / "skills",
            project / ".codex" / "skills",
        ],
        "claude": [
            project / ".claude" / "skills",
            project / ".agents" / "claude" / "skills",
        ],
    }
    return {agent: roots.get(agent, [])} if agent else roots


__all__ = [
    "ACTIONABLE_EXPOSURE_STATES",
    "EXPOSURE_CHANGES_SCHEMA",
    "classify_exposure_target",
    "list_project_exposures",
    "scan_project_exposures",
]
