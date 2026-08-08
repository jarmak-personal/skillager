from __future__ import annotations

from pathlib import Path
from typing import Any

from ..catalog.impl import load_collections
from ..library.model import LIBRARY_COLLECTION_KIND, LIBRARY_NAMESPACE
from ..simple_yaml import load_mapping
from ..skills.tree import content_tree_fingerprint
from ..trust import content_hash
from .impl import MATERIALIZED_SCHEMA, ROUTER_SCHEMA, WORKING_SKILL_ID

EXPOSURE_CHANGES_SCHEMA = "skillager.exposure-changes.v1"
ACTIONABLE_EXPOSURE_STATES = {
    "local_edit",
    "target_missing",
    "blocked",
    "sidecar_error",
    "unmanaged",
}
_COUNT_KEYS = {
    "current": "current",
    "kept_local": "kept_local",
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
) -> dict[str, Any]:
    """Classify live current-project exposure targets without mutating state."""

    records = list_project_exposures(
        project_dir,
        agent=agent,
        catalog_root=catalog_root,
    )
    counts = {key: 0 for key in _COUNT_KEYS.values()}
    for record in records:
        counts[_COUNT_KEYS[record["status"]]] += 1
    items = [record for record in records if record["status"] in ACTIONABLE_EXPOSURE_STATES]
    return {
        "schema": EXPOSURE_CHANGES_SCHEMA,
        **counts,
        "items": sorted(items, key=lambda item: (str(item.get("agent")), str(item.get("target")))),
        "fully_deleted_targets": "undetectable_without_ledger",
    }


def list_project_exposures(
    project_dir: Path,
    *,
    agent: str | None = None,
    catalog_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return every discoverable current-project exposure classification."""

    registration = _library_registration(catalog_root)
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
                        registration=registration,
                    )
                    if record is not None:
                        records.append(record)
                elif (target / "SKILL.md").is_file() and target.name != "skillager-working":
                    records.append(_unmanaged_record(target, agent=root_agent))
    return sorted(records, key=lambda item: (str(item.get("agent")), str(item.get("target"))))


def classify_exposure_target(
    target: Path,
    *,
    sidecar: Path | None = None,
    fallback_agent: str = "codex",
    registration: dict[str, Any] | None = None,
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
        return _sidecar_error_record(target, fallback_agent, validation_error, data=data, registration=registration)

    skill_id = str(data.get("source_id") or data.get("id"))
    record = _base_record(target, data, fallback_agent=fallback_agent, registration=registration)
    if not (target / "SKILL.md").is_file():
        record.update(
            {
                "status": "target_missing",
                "current_hash": None,
                "sidecar_status": "readable",
                "reason": "managed target is missing SKILL.md",
                "next_command": f"skillager reconcile {skill_id} --json",
            }
        )
        return record

    try:
        fingerprint = content_tree_fingerprint(target)
        current_hash = _hash_from_matching_fingerprint(data, fingerprint)
        if current_hash is None:
            current_hash = content_hash(target)
    except OSError as exc:
        record.update(
            {
                "status": "target_missing",
                "current_hash": None,
                "sidecar_status": "readable",
                "reason": f"managed target is unreadable: {type(exc).__name__}",
                "next_command": f"skillager reconcile {skill_id} --json",
            }
        )
        return record

    record["current_hash"] = current_hash
    record["current_fingerprint"] = fingerprint
    record["sidecar_status"] = "readable"
    blocked_hashes = {str(value) for value in data.get("exposure_blocked_hashes") or []}
    if current_hash in blocked_hashes:
        status = "blocked"
    elif _is_kept_local(data, current_hash):
        status = "kept_local"
    elif current_hash == data["materialized_hash"]:
        status = "current"
    else:
        status = "local_edit"
    record["status"] = status
    if status in ACTIONABLE_EXPOSURE_STATES:
        record["next_command"] = f"skillager reconcile {skill_id} --json"
    return record


def _hash_from_matching_fingerprint(data: dict[str, Any], fingerprint: str) -> str | None:
    customized_hash = data.get("customized_hash")
    if (
        isinstance(customized_hash, str)
        and data.get("customized_fingerprint") == fingerprint
    ):
        return customized_hash
    materialized_hash = data.get("materialized_hash")
    if isinstance(materialized_hash, str) and data.get("materialized_fingerprint") == fingerprint:
        return materialized_hash
    return None


def _is_kept_local(data: dict[str, Any], current_hash: str) -> bool:
    customized_hash = data.get("customized_hash")
    if isinstance(customized_hash, str):
        return current_hash == customized_hash and (
            data.get("customized") is True or data.get("customization_decision") == "keep-local"
        )
    return data.get("customized") is True


def _sidecar_validation_error(data: dict[str, Any]) -> str | None:
    if data.get("schema") not in {MATERIALIZED_SCHEMA, ROUTER_SCHEMA}:
        return "unsupported or missing sidecar schema"
    if not isinstance(data.get("source_id") or data.get("id"), str):
        return "sidecar is missing source identity"
    if not isinstance(data.get("source_type"), str):
        return "sidecar is missing source type"
    if not isinstance(data.get("materialized_hash"), str):
        return "sidecar is missing materialized hash"
    blocked = data.get("exposure_blocked_hashes")
    if blocked is not None and not isinstance(blocked, list):
        return "sidecar blocked hashes must be a list"
    pin_hash = data.get("pin_hash")
    if pin_hash is not None and (not isinstance(pin_hash, str) or not pin_hash):
        return "sidecar pin hash must be a non-empty string"
    return None


def _base_record(
    target: Path,
    data: dict[str, Any],
    *,
    fallback_agent: str,
    registration: dict[str, Any] | None,
) -> dict[str, Any]:
    source_type = data.get("source_type")
    if source_type == "skillager-router":
        mode = "router"
    elif source_type == "skillager-stub":
        mode = "stub"
    else:
        mode = "native"
    return {
        "skill_id": str(data.get("source_id") or data.get("id")),
        "agent": str(data.get("agent") or fallback_agent),
        "scope": "project",
        "mode": mode,
        "target": str(target),
        "source_hash": data.get("source_hash"),
        "pin_hash": data.get("pin_hash"),
        "materialized_hash": data.get("materialized_hash"),
        "ownership": _sidecar_ownership(data, registration),
    }


def _sidecar_error_record(
    target: Path,
    agent: str,
    reason: str,
    *,
    data: dict[str, Any] | None = None,
    registration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = data or {}
    skill_id = str(data.get("source_id") or data.get("id") or target.name)
    record = _base_record(target, data, fallback_agent=agent, registration=registration)
    record.update(
        {
            "skill_id": skill_id,
            "status": "sidecar_error",
            "sidecar_status": "error",
            "current_hash": None,
            "reason": reason,
            "next_command": f"skillager reconcile {skill_id} --json",
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
        "ownership": "external",
        "reason": "native skill exists without Skillager provenance",
        "next_command": f"skillager reconcile {skill_id} --json",
    }


def _sidecar_ownership(data: dict[str, Any], registration: dict[str, Any] | None) -> str:
    if data.get("ownership") in {"library", "external"}:
        return str(data["ownership"])
    if registration is None:
        return "external"
    source_id = str(data.get("source_id") or data.get("id") or "")
    source_library_id = data.get("source_library_id")
    if source_library_id is not None:
        return "library" if source_library_id == registration.get("library_id") else "external"
    if (
        source_id.startswith(f"{LIBRARY_NAMESPACE}/")
        and data.get("source_type") == "collection"
        and data.get("source_package") == LIBRARY_NAMESPACE
    ):
        return "library"
    return "external"


def _library_registration(catalog_root: Path | None) -> dict[str, Any] | None:
    if catalog_root is None:
        return None
    try:
        value = load_collections(catalog_root).get("collections", {}).get(LIBRARY_NAMESPACE)
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("kind") != LIBRARY_COLLECTION_KIND:
        return None
    return value


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
