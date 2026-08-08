from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import select_collection_skills
from ..library.model import LIBRARY_NAMESPACE
from ..library.service import library_where
from ..simple_yaml import dumps, load_mapping
from ..skills.index import build_index
from ..skills.tree import content_tree_fingerprint
from ..state.locking import resource_lock
from ..state.statefiles import read_user_json
from ..trust import APPROVED_TRUST_STATES, content_hash
from .drift import classify_exposure_target, list_project_exposures
from .impl import (
    MATERIALIZED_SCHEMA,
    ROUTER_SCHEMA,
    content_hashes,
    render_router_skill,
    render_stub_skill,
)


RECONCILE_SCHEMA = "skillager.reconcile.v1"
RECONCILE_ACTION_SCHEMA = "skillager.reconcile-action.v1"
QUARANTINE_DIR = ".skillager-quarantine"
_SAFE_LABEL = re.compile(r"[^a-zA-Z0-9_.-]+")


def reconcile_inventory(
    project_state: Path,
    catalog_root: Path,
    project_dir: Path,
    *,
    skill_id: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return source-aware, metadata-only reconciliation inventory."""

    records = list_project_exposures(project_dir, agent=agent, catalog_root=catalog_root)
    if skill_id is not None:
        records = [record for record in records if _record_matches(record, skill_id)]
    items = [
        _inventory_item(project_state, catalog_root, project_dir, record)
        for record in records
    ]
    counts: dict[str, int] = {}
    for item in items:
        state = str(item["status"])
        counts[state] = counts.get(state, 0) + 1
    return {
        "schema": RECONCILE_SCHEMA,
        "status": "ready",
        "read_only": True,
        "project": str(project_dir.resolve()),
        "filter": {"skill_id": skill_id, "agent": agent},
        "counts": counts,
        "items": items,
        "fully_deleted_targets": "undetectable_without_ledger",
    }


def keep_local_preview(
    project_dir: Path,
    catalog_root: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    if record["mode"] not in {"native", "stub", "router"}:
        raise ValueError("keep-local requires a managed native, stub, or router exposure")
    if record["status"] == "kept_local":
        status = "already-kept"
    elif record["status"] == "local_edit":
        status = "preview"
    else:
        raise ValueError(f"keep-local requires a local edit; exposure status is {record['status']}")
    current_hash = _authoritative_target_hash(Path(record["target"]))
    return _action_preview(
        "keep-local",
        record,
        status=status,
        expected_hash=current_hash,
        next_command=f"skillager reconcile keep-local {record['skill_id']} --yes",
    )


def keep_local(
    project_dir: Path,
    catalog_root: Path,
    skill_id: str,
    *,
    expected_hash: str,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    target = Path(record["target"])
    with resource_lock(target):
        current_hash = _authoritative_target_hash(target)
        if current_hash != expected_hash:
            raise ValueError("exposure changed since preview; review reconcile output again")
        data = _load_managed_sidecar(target)
        now = _now_iso()
        data.update(
            {
                "customized": True,
                "customization_decision": "keep-local",
                "customized_hash": current_hash,
                "customized_fingerprint": content_tree_fingerprint(target),
                "customized_at": now,
            }
        )
        _write_sidecar(target / "skillager.materialized.yaml", data)
    final = classify_exposure_target(target)
    if final is None or final["status"] != "kept_local":
        raise ValueError("keep-local decision was written but could not be verified")
    return {
        "schema": RECONCILE_ACTION_SCHEMA,
        "status": "kept-local",
        "action": "keep-local",
        "will_write": True,
        "exposure": final,
        "customized_hash": current_hash,
    }


def quarantine_preview(
    project_dir: Path,
    catalog_root: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    target = Path(record["target"])
    if not (target / "SKILL.md").is_file():
        if record.get("status") == "target_missing":
            return _action_preview(
                "quarantine",
                record,
                status="already-quarantined" if _has_quarantine_record(target) else "target-missing",
                expected_hash=None,
            )
        raise ValueError("quarantine requires a readable managed target")
    current_hash = _authoritative_target_hash(target)
    return _action_preview(
        "quarantine",
        record,
        status="preview",
        expected_hash=current_hash,
        extra={"quarantine_root": str(_quarantine_root(project_dir))},
        next_command=f"skillager reconcile quarantine {record['skill_id']} --yes",
    )


def quarantine(
    project_dir: Path,
    catalog_root: Path,
    skill_id: str,
    *,
    expected_hash: str,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    target = Path(record["target"])
    with resource_lock(target):
        current_hash = _authoritative_target_hash(target)
        if current_hash != expected_hash:
            raise ValueError("exposure changed since quarantine preview; review it again")
        data = _load_managed_sidecar(target)
        destination, tombstone = _quarantine_locked(
            project_dir,
            target,
            data,
            current_hash=current_hash,
            block_hash=True,
        )
    final = classify_exposure_target(target)
    return {
        "schema": RECONCILE_ACTION_SCHEMA,
        "status": "quarantined",
        "action": "quarantine",
        "will_write": True,
        "exposure": final or tombstone,
        "blocked_hash": current_hash,
        "quarantine_path": str(destination),
        "recoverable": True,
    }


def repair_preview(
    project_state: Path,
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    if record["mode"] not in {"stub", "router"}:
        raise ValueError("repair is available only for generated stubs and routers")
    if record["status"] == "current":
        return _action_preview("repair", record, status="already-current", expected_hash=record.get("current_hash"))
    target = Path(record["target"])
    data = _load_managed_sidecar(target)
    rendered, resolved = _render_generated(project_state, catalog_root, data)
    expected_hash = _authoritative_target_hash(target) if (target / "SKILL.md").is_file() else None
    return _action_preview(
        "repair",
        record,
        status="preview",
        expected_hash=expected_hash,
        extra={"resolved_source_count": resolved, "generated_hash": _rendered_hash(rendered)},
        next_command=f"skillager reconcile repair {record['skill_id']} --yes",
    )


def repair_generated(
    project_state: Path,
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    expected_hash: str | None,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    if record["mode"] not in {"stub", "router"}:
        raise ValueError("repair is available only for generated stubs and routers")
    target = Path(record["target"])
    with resource_lock(target):
        current_hash = _authoritative_target_hash(target) if (target / "SKILL.md").is_file() else None
        if current_hash != expected_hash:
            raise ValueError("exposure changed since repair preview; review it again")
        data = _load_managed_sidecar(target)
        rendered, resolved = _render_generated(project_state, catalog_root, data)
        quarantine_path: Path | None = None
        if current_hash is not None:
            quarantine_path, _tombstone = _quarantine_locked(
                project_dir,
                target,
                data,
                current_hash=current_hash,
                block_hash=True,
            )
            data = _load_managed_sidecar(target)
        _install_generated_locked(
            project_dir,
            target,
            data,
            rendered,
            quarantine_path=quarantine_path,
        )
    final = classify_exposure_target(target)
    if final is None or final["status"] != "current":
        raise ValueError("generated exposure was repaired but could not be verified")
    return {
        "schema": RECONCILE_ACTION_SCHEMA,
        "status": "repaired",
        "action": "repair",
        "will_write": True,
        "exposure": final,
        "resolved_source_count": resolved,
        "quarantine_path": str(quarantine_path) if quarantine_path else None,
    }


def require_exposure(
    project_dir: Path,
    catalog_root: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    records = [
        record
        for record in list_project_exposures(project_dir, agent=agent, catalog_root=catalog_root)
        if _record_matches(record, skill_id)
    ]
    if not records:
        raise ValueError(f"current-project exposure not found: {skill_id}")
    if len(records) > 1:
        agents = ", ".join(sorted({str(record.get("agent")) for record in records}))
        raise ValueError(f"exposure is ambiguous ({agents}); pass --agent codex or --agent claude")
    record = records[0]
    if record["status"] == "unmanaged":
        raise ValueError("unmanaged native skills have no Skillager exposure decision record")
    if record["status"] == "sidecar_error":
        raise ValueError(f"exposure sidecar must be repaired manually: {record.get('reason')}")
    return record


def resolve_source_skill(
    project_state: Path,
    catalog_root: Path,
    source_id: str,
    source_hash: str,
    *,
    source_library_id: str | None = None,
) -> dict[str, Any]:
    candidates = _effective_skills(project_state, catalog_root)
    matches = [
        skill
        for skill in candidates
        if skill.get("id") == source_id and skill.get("content_hash") == source_hash
    ]
    if source_library_id is not None:
        matches = [
            skill
            for skill in matches
            if (skill.get("source") or {}).get("library_id") == source_library_id
        ]
    unique: dict[Path, dict[str, Any]] = {}
    for skill in matches:
        unique[Path(skill["root"]).resolve()] = skill
    matches = list(unique.values())
    if not matches:
        raise ValueError(f"accepted source hash is no longer available for repair: {source_id}")
    approved = [skill for skill in matches if skill.get("trust") in APPROVED_TRUST_STATES]
    if len(approved) != 1:
        raise ValueError(f"source identity is ambiguous or unavailable for repair: {source_id}")
    selected = approved[0]
    if content_hash(Path(selected["root"])) != source_hash:
        raise ValueError(f"source changed since approval and cannot authorize repair: {source_id}")
    return selected


def write_reconciled_sidecar(path: Path, data: dict[str, Any]) -> None:
    _write_sidecar(path, data)


def quarantine_target_locked(
    project_dir: Path,
    target: Path,
    data: dict[str, Any],
    *,
    current_hash: str,
    block_hash: bool = True,
) -> tuple[Path, dict[str, Any]]:
    return _quarantine_locked(
        project_dir,
        target,
        data,
        current_hash=current_hash,
        block_hash=block_hash,
    )


def _inventory_item(
    project_state: Path,
    catalog_root: Path,
    project_dir: Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    item = dict(record)
    source = _source_context(catalog_root, project_dir, record)
    item["source"] = source
    item["actions"] = _available_actions(record, source)
    item.pop("next_command", None)
    return item


def _source_context(catalog_root: Path, project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    if record.get("ownership") != "library" or record.get("mode") == "router":
        return {
            "status": "external" if record.get("ownership") != "library" else "generated",
            "accepted_hash": None,
            "working_hash": None,
            "history": {"available": False, "reason": "external" if record.get("ownership") != "library" else "router"},
        }
    source_id = str(record.get("skill_id") or "")
    if not source_id.startswith(f"{LIBRARY_NAMESPACE}/"):
        return {
            "status": "invalid-library-source",
            "accepted_hash": None,
            "working_hash": None,
            "history": {"available": False, "reason": "invalid-library-source"},
        }
    try:
        where = library_where(catalog_root, source_id, project_dir=project_dir)["skill"]
    except Exception as exc:
        return {
            "status": "unavailable",
            "accepted_hash": None,
            "working_hash": None,
            "history": {"available": False, "reason": type(exc).__name__},
        }
    accepted_hash = where.get("accepted_hash")
    source_hash = record.get("source_hash")
    if accepted_hash == source_hash and where.get("working_hash") == source_hash:
        status = "current"
    elif accepted_hash and record.get("status") == "current":
        status = "behind"
    else:
        status = "diverged"
    return {
        "status": status,
        "accepted_hash": accepted_hash,
        "working_hash": where.get("working_hash"),
        "head_hash": where.get("head_hash"),
        "acceptance": where.get("acceptance"),
        "history": where.get("history"),
    }


def _available_actions(record: dict[str, Any], source: dict[str, Any]) -> list[str]:
    status = record.get("status")
    mode = record.get("mode")
    ownership = record.get("ownership")
    actions: list[str] = []
    if status == "local_edit":
        actions.append("keep-local")
    if status in {"local_edit", "kept_local", "blocked", "current"}:
        actions.append("quarantine")
    if mode in {"stub", "router"} and status not in {"current", "sidecar_error", "unmanaged"}:
        actions.append("repair")
    if mode == "native" and status in {"local_edit", "kept_local"}:
        actions.append("promote" if ownership == "library" else "import")
    if mode == "native" and ownership == "library" and (source.get("history") or {}).get("available"):
        actions.append("rollback")
    return actions


def _action_preview(
    action: str,
    record: dict[str, Any],
    *,
    status: str,
    expected_hash: str | None,
    extra: dict[str, Any] | None = None,
    next_command: str | None = None,
) -> dict[str, Any]:
    result = {
        "schema": RECONCILE_ACTION_SCHEMA,
        "status": status,
        "action": action,
        "will_write": False,
        "exposure": record,
        "expected_current_hash": expected_hash,
    }
    if extra:
        result.update(extra)
    if next_command:
        result["next_command"] = next_command
    return result


def _record_matches(record: dict[str, Any], value: str) -> bool:
    return value in {str(record.get("skill_id")), Path(str(record.get("target"))).name}


def _authoritative_target_hash(target: Path) -> str:
    if target.is_symlink() or not target.is_dir() or not (target / "SKILL.md").is_file():
        raise ValueError("managed exposure target is missing or unsafe")
    return content_hash(target)


def _load_managed_sidecar(target: Path) -> dict[str, Any]:
    sidecar = target / "skillager.materialized.yaml"
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ValueError("managed exposure sidecar is missing or unsafe")
    data = load_mapping(sidecar)
    if data.get("schema") not in {MATERIALIZED_SCHEMA, ROUTER_SCHEMA}:
        raise ValueError("managed exposure sidecar schema is unsupported")
    if not isinstance(data.get("source_id") or data.get("id"), str):
        raise ValueError("managed exposure sidecar has no source identity")
    if not isinstance(data.get("materialized_hash"), str):
        raise ValueError("managed exposure sidecar has no materialized hash")
    return data


def _write_sidecar(path: Path, data: dict[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked exposure sidecar: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(data)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _quarantine_locked(
    project_dir: Path,
    target: Path,
    data: dict[str, Any],
    *,
    current_hash: str,
    block_hash: bool,
) -> tuple[Path, dict[str, Any]]:
    root = _quarantine_root(project_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = _now_iso()
    destination = _quarantine_destination(root, target, data, current_hash)
    updated = dict(data)
    blocked = [str(value) for value in updated.get("exposure_blocked_hashes") or []]
    if block_hash and current_hash not in blocked:
        blocked.append(current_hash)
    updated.update(
        {
            "exposure_blocked_hashes": blocked,
            "quarantine_path": str(destination),
            "quarantined_at": now,
        }
    )
    original = dict(data)
    os.replace(target, destination)
    try:
        _write_sidecar(destination / "skillager.materialized.yaml", updated)
        target.mkdir(parents=True)
        _write_sidecar(target / "skillager.materialized.yaml", updated)
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        os.replace(destination, target)
        _write_sidecar(target / "skillager.materialized.yaml", original)
        raise
    return destination, updated


def _quarantine_root(project_dir: Path) -> Path:
    project = project_dir.resolve()
    raw = project / QUARANTINE_DIR / "exposures"
    base = project / QUARANTINE_DIR
    if base.is_symlink() or raw.is_symlink():
        raise ValueError("refusing symlinked project quarantine directory")
    resolved = raw.resolve()
    try:
        resolved.relative_to(project)
    except ValueError as exc:
        raise ValueError("project quarantine path escapes the current project") from exc
    return resolved


def _quarantine_destination(root: Path, target: Path, data: dict[str, Any], current_hash: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    label = _SAFE_LABEL.sub("-", f"{data.get('agent', 'agent')}-{target.name}").strip("-") or "exposure"
    base = f"{timestamp}-{label}-{current_hash[:12]}"
    destination = root / base
    counter = 1
    while destination.exists() or destination.is_symlink():
        destination = root / f"{base}-{counter}"
        counter += 1
    return destination


def _has_quarantine_record(target: Path) -> bool:
    try:
        data = _load_managed_sidecar(target)
    except Exception:
        return False
    path = data.get("quarantine_path")
    return isinstance(path, str) and Path(path).is_dir()


def _effective_skills(project_state: Path, catalog_root: Path) -> list[dict[str, Any]]:
    local = build_index(
        project_state,
        include_packages=True,
        approval_root=catalog_root,
        extra_paths=_saved_setup_paths(project_state),
        persist=False,
    ).get("skills", [])
    collections = select_collection_skills(
        catalog_root,
        trust_root=project_state,
        approval_root=catalog_root,
        include_blocked=True,
        include_lint_blocked=True,
        refresh_library=False,
    )
    return [*local, *collections]


def _saved_setup_paths(project_state: Path) -> list[Path] | None:
    data = read_user_json(project_state / "status_scope.json", {})
    paths = []
    for raw in data.get("paths") or []:
        if isinstance(raw, str) and Path(raw).expanduser().exists():
            paths.append(Path(raw).expanduser())
    return paths or None


def _render_generated(project_state: Path, catalog_root: Path, data: dict[str, Any]) -> tuple[str, int]:
    source_type = data.get("source_type")
    if source_type == "skillager-stub":
        source = resolve_source_skill(
            project_state,
            catalog_root,
            str(data.get("source_id") or data.get("id")),
            str(data["source_hash"]),
            source_library_id=str(data["source_library_id"]) if data.get("source_library_id") else None,
        )
        return render_stub_skill(source), 1
    if source_type != "skillager-router":
        raise ValueError("only generated stubs and routers can be repaired")
    skills = []
    effective = _effective_skills(project_state, catalog_root)
    for skill_id in data.get("skill_ids") or []:
        candidates = [
            skill
            for skill in effective
            if skill.get("id") == skill_id and skill.get("trust") in APPROVED_TRUST_STATES
        ]
        unique = {Path(skill["root"]).resolve(): skill for skill in candidates}
        if len(unique) != 1:
            raise ValueError(f"router member source is unavailable or ambiguous: {skill_id}")
        selected = next(iter(unique.values()))
        if content_hash(Path(selected["root"])) != selected.get("content_hash"):
            raise ValueError(f"router member changed since approval and cannot authorize repair: {skill_id}")
        skills.append(selected)
    if not skills or content_hashes(skills) != data.get("source_hash"):
        raise ValueError("router sources no longer reproduce the materialized source hash; re-expose the router")
    router_kind = str(data.get("router_kind") or "tag")
    tag = str(data["tag"]) if data.get("tag") is not None else None
    rendered = render_router_skill(
        tag,
        skills,
        agent=str(data.get("agent") or "codex"),
        router_slug=str(data.get("router_slug") or "skillager-router"),
        router_kind=router_kind,
    )
    return rendered, len(skills)


def _rendered_hash(rendered: str) -> str:
    with tempfile.TemporaryDirectory(prefix="skillager-rendered-hash-") as tmp:
        root = Path(tmp)
        (root / "SKILL.md").write_text(rendered, encoding="utf-8")
        return content_hash(root)


def _install_generated_locked(
    project_dir: Path,
    target: Path,
    data: dict[str, Any],
    rendered: str,
    *,
    quarantine_path: Path | None,
) -> None:
    temp_root = _quarantine_root(project_dir)
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="skillager-repair-", dir=temp_root) as tmp:
        candidate = Path(tmp) / target.name
        candidate.mkdir()
        (candidate / "SKILL.md").write_text(rendered, encoding="utf-8")
        materialized_hash = content_hash(candidate)
        updated = dict(data)
        updated.update(
            {
                "materialized_hash": materialized_hash,
                "materialized_fingerprint": content_tree_fingerprint(candidate),
                "materialized_at": _now_iso(),
                "customized": False,
            }
        )
        for key in ("customization_decision", "customized_hash", "customized_fingerprint", "customized_at"):
            updated.pop(key, None)
        if quarantine_path is not None:
            updated["quarantine_path"] = str(quarantine_path)
            updated["quarantined_at"] = _now_iso()
        _write_sidecar(candidate / "skillager.materialized.yaml", updated)
        displaced: Path | None = None
        if target.exists():
            displaced = Path(tmp) / f"{target.name}.tombstone"
            os.replace(target, displaced)
        try:
            os.replace(candidate, target)
        except Exception:
            if displaced is not None and displaced.exists():
                os.replace(displaced, target)
            raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "QUARANTINE_DIR",
    "RECONCILE_ACTION_SCHEMA",
    "RECONCILE_SCHEMA",
    "keep_local",
    "keep_local_preview",
    "quarantine",
    "quarantine_preview",
    "quarantine_target_locked",
    "reconcile_inventory",
    "repair_generated",
    "repair_preview",
    "require_exposure",
    "resolve_source_skill",
    "write_reconciled_sidecar",
]
