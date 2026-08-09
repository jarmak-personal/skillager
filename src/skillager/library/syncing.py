from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import load_collections
from ..exposure.drift import classify_exposure_target, list_project_exposures
from ..exposure.impl import MATERIALIZED_SCHEMA, render_stub_skill
from ..exposure.reconcile import require_exposure, write_reconciled_sidecar
from ..simple_yaml import load_mapping
from ..skills.tree import content_tree_fingerprint, copy_content_tree, iter_content_files
from ..state.locking import resource_lock, resource_locks
from ..trust import content_hash, content_hash_entries
from .model import LIBRARY_NAMESPACE, normalize_skill_name
from .service import _library_skill_entry, _require_library_identity, library_where
from .versioning import library_history, resolve_history_version


SYNC_SCHEMA = "skillager.sync.v1"
PIN_SCHEMA = "skillager.pin.v1"


def sync_preview(
    catalog_root: Path,
    project_dir: Path,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    registration, _identity = _require_library_identity(catalog_root)
    records = list_project_exposures(project_dir, agent=agent, catalog_root=catalog_root)
    items = [
        _evaluate_sync_record(catalog_root, project_dir, registration.library_id, record)
        for record in records
    ]
    return _sync_payload(project_dir, agent, items, applied=False)


def sync_project(
    catalog_root: Path,
    project_dir: Path,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    preview = sync_preview(catalog_root, project_dir, agent=agent)
    results: list[dict[str, Any]] = []
    for item in preview["items"]:
        if item["status"] != "update-available":
            results.append(item)
            continue
        try:
            results.append(_apply_sync_item(catalog_root, project_dir, item))
        except (OSError, ValueError) as exc:
            changed = dict(item)
            changed.update(
                {
                    "status": "skipped",
                    "reason": "changed-since-preview",
                    "detail": str(exc),
                    "will_update": False,
                    "updated": False,
                }
            )
            results.append(changed)
    return _sync_payload(project_dir, agent, results, applied=True)


def pin_exposure(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    to_hash: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    _require_pinnable(record)
    target = Path(record["target"]).resolve()
    _require_project_target(project_dir, target)
    with resource_lock(target):
        current = _classify_selected_target(catalog_root, target, agent=str(record["agent"]))
        _require_pinnable(current)
        data = _load_sidecar(target)
        _require_matching_library_sidecar(catalog_root, data)
        source_hash = _required_hash(data.get("source_hash"), "exposure source")
        selected_hash = _resolve_pin_hash(
            catalog_root,
            str(current["skill_id"]),
            source_hash,
            to_hash,
            project_dir=project_dir,
        )
        previous = data.get("pin_hash")
        data["pin_hash"] = selected_hash
        write_reconciled_sidecar(target / "skillager.materialized.yaml", data)
    return {
        "schema": PIN_SCHEMA,
        "status": "already-pinned" if previous == selected_hash else "pinned",
        "action": "pin",
        "skill_id": current["skill_id"],
        "agent": current["agent"],
        "target": str(target),
        "pin_hash": selected_hash,
        "source_hash": source_hash,
    }


def unpin_exposure(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    _require_direct_library_exposure(record)
    target = Path(record["target"]).resolve()
    _require_project_target(project_dir, target)
    with resource_lock(target):
        current = _classify_selected_target(catalog_root, target, agent=str(record["agent"]))
        _require_direct_library_exposure(current)
        data = _load_sidecar(target)
        _require_matching_library_sidecar(catalog_root, data)
        previous = data.pop("pin_hash", None)
        if previous is not None:
            write_reconciled_sidecar(target / "skillager.materialized.yaml", data)
    return {
        "schema": PIN_SCHEMA,
        "status": "unpinned" if previous is not None else "already-unpinned",
        "action": "unpin",
        "skill_id": current["skill_id"],
        "agent": current["agent"],
        "target": str(target),
        "pin_hash": None,
        "previous_pin_hash": previous,
    }


def _evaluate_sync_record(
    catalog_root: Path,
    project_dir: Path,
    library_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    item = {
        "skill_id": record.get("skill_id"),
        "agent": record.get("agent"),
        "mode": record.get("mode"),
        "target": record.get("target"),
        "ownership": record.get("ownership"),
        "exposure_status": record.get("status"),
        "from_hash": record.get("source_hash"),
        "to_hash": None,
        "pin_hash": record.get("pin_hash"),
        "status": "skipped",
        "reason": None,
        "will_update": False,
        "updated": False,
    }
    status = record.get("status")
    if status == "sidecar_error":
        return _skip(item, "malformed-sidecar")
    if status == "unmanaged":
        return _skip(item, "unmanaged")
    if record.get("ownership") != "library":
        return _skip(item, "external-source")
    if record.get("mode") not in {"native", "stub"}:
        return _skip(item, "unsupported-mode")
    if status == "target_missing":
        return _skip(item, "target-missing")
    if status == "blocked":
        return _skip(item, "blocked")
    if status == "local_edit":
        return _skip(item, "dirty")
    if status == "kept_local":
        return _skip(item, "customized")
    if record.get("pin_hash") is not None:
        return _skip(item, "pinned")
    if status != "current":
        return _skip(item, "unresolved-drift")

    target = Path(str(record["target"])).resolve()
    try:
        _require_project_target(project_dir, target)
        data = _load_sidecar(target)
    except (OSError, ValueError):
        return _skip(item, "malformed-sidecar")
    if _has_noncanonical_target_entries(target):
        return _skip(item, "unresolved-drift")
    if data.get("source_library_id") != library_id:
        return _skip(item, "library-identity-mismatch")
    source_id = str(record.get("skill_id") or "")
    if not source_id.startswith(f"{LIBRARY_NAMESPACE}/"):
        return _skip(item, "invalid-library-source")
    try:
        where = library_where(catalog_root, source_id, project_dir=project_dir)["skill"]
    except (OSError, ValueError):
        return _skip(item, "source-missing")
    item["source_status"] = where.get("status")
    item["source_acceptance"] = where.get("acceptance")
    item["to_hash"] = where.get("accepted_hash")
    if where.get("acceptance") != "accepted" or where.get("working_hash") != where.get("accepted_hash"):
        return _skip(item, "unaccepted-source")
    if where.get("status") not in {"clean", "no_git"}:
        return _skip(item, "source-not-clean")
    if record.get("source_hash") == where.get("accepted_hash"):
        item["status"] = "up-to-date"
        item["reason"] = "source-current"
        return item
    try:
        source = _library_skill_entry(catalog_root, source_id)
    except ValueError:
        return _skip(item, "source-missing")
    if source.get("trust") not in {"reviewed", "trusted", "pinned"}:
        return _skip(item, "unaccepted-source")
    prospective_hash = _prospective_materialized_hash(record, source)
    blocked_hashes = {str(value) for value in data.get("exposure_blocked_hashes") or []}
    if prospective_hash in blocked_hashes:
        return _skip(item, "blocked")
    item.update(
        {
            "status": "update-available",
            "reason": "behind",
            "will_update": True,
            "prospective_materialized_hash": prospective_hash,
        }
    )
    return item


def _apply_sync_item(
    catalog_root: Path,
    project_dir: Path,
    preview: dict[str, Any],
) -> dict[str, Any]:
    target = Path(str(preview["target"])).resolve()
    _require_project_target(project_dir, target)
    name = normalize_skill_name(str(preview["skill_id"]))
    resources = [catalog_root / f"library-skill-{name}", target]
    with resource_locks(resources):
        registration, _identity = _require_library_identity(catalog_root)
        current = _classify_selected_target(catalog_root, target, agent=str(preview["agent"]))
        evaluated = _evaluate_sync_record(catalog_root, project_dir, registration.library_id, current)
        if evaluated.get("status") != "update-available":
            raise ValueError(f"exposure is no longer safely syncable: {evaluated.get('reason')}")
        for key in ("skill_id", "mode", "target", "from_hash", "to_hash", "prospective_materialized_hash"):
            if evaluated.get(key) != preview.get(key):
                raise ValueError(f"sync {key.replace('_', ' ')} changed since preview")
        source = _library_skill_entry(catalog_root, str(preview["skill_id"]))
        source_root = Path(source["root"]).resolve()
        expected_source_hash = str(preview["to_hash"])
        if content_hash(source_root) != expected_source_hash:
            raise ValueError("accepted library source changed since sync preview")
        data = _load_sidecar(target)
        _require_matching_library_sidecar(catalog_root, data)
        if data.get("pin_hash") is not None:
            raise ValueError("exposure was pinned since sync preview")

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".skillager-sync-", dir=target.parent) as tmp:
            temp_root = Path(tmp)
            candidate = temp_root / "candidate"
            backup = temp_root / "previous"
            if preview["mode"] == "native":
                copy_content_tree(source_root, candidate)
            else:
                candidate.mkdir()
                (candidate / "SKILL.md").write_text(render_stub_skill(source), encoding="utf-8")
            if content_hash(source_root) != expected_source_hash:
                raise ValueError("accepted library source changed while sync was being prepared")
            materialized_hash = content_hash(candidate)
            if materialized_hash != preview["prospective_materialized_hash"]:
                raise ValueError("sync candidate changed since preview")
            updated = dict(data)
            updated.update(
                {
                    "source_entrypoint": source.get("entrypoint"),
                    "source_hash": expected_source_hash,
                    "source_trust": source.get("trust"),
                    "materialized_hash": materialized_hash,
                    "materialized_fingerprint": content_tree_fingerprint(candidate),
                    "materialized_at": datetime.now(timezone.utc).isoformat(),
                    "customized": False,
                }
            )
            for key in ("customization_decision", "customized_hash", "customized_fingerprint", "customized_at"):
                updated.pop(key, None)
            write_reconciled_sidecar(candidate / "skillager.materialized.yaml", updated)
            os.replace(target, backup)
            try:
                os.replace(candidate, target)
                final = _classify_selected_target(catalog_root, target, agent=str(preview["agent"]))
                if final.get("status") != "current" or final.get("source_hash") != expected_source_hash:
                    raise ValueError("synced exposure could not be verified")
            except Exception:
                if target.exists():
                    shutil.rmtree(target)
                os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
    result = dict(preview)
    result.update(
        {
            "status": "updated",
            "reason": "synced",
            "will_update": False,
            "updated": True,
            "from_hash": preview["from_hash"],
            "to_hash": expected_source_hash,
        }
    )
    return result


def _sync_payload(
    project_dir: Path,
    agent: str | None,
    items: list[dict[str, Any]],
    *,
    applied: bool,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
    updated = counts.get("updated", 0)
    available = counts.get("update-available", 0)
    agent_option = f" --agent {agent}" if agent else ""
    return {
        "schema": SYNC_SCHEMA,
        "status": "applied" if applied and updated else "no-changes" if applied else "preview",
        "read_only": not applied,
        "will_write": bool(applied and updated),
        "applied": applied,
        "project": str(project_dir.resolve()),
        "filter": {"agent": agent},
        "counts": counts,
        "update_count": updated if applied else available,
        "next_command": f"skillager sync{agent_option} --apply" if not applied and available else None,
        "items": items,
    }


def _classify_selected_target(catalog_root: Path, target: Path, *, agent: str) -> dict[str, Any]:
    registration = load_collections(catalog_root).get("collections", {}).get(LIBRARY_NAMESPACE)
    record = classify_exposure_target(
        target,
        fallback_agent=agent,
        registration=registration if isinstance(registration, dict) else None,
    )
    if record is None:
        raise ValueError("selected target is not a managed skill exposure")
    return record


def _prospective_materialized_hash(record: dict[str, Any], source: dict[str, Any]) -> str:
    if record.get("mode") == "native":
        return str(source["content_hash"])
    rendered = render_stub_skill(source).encode("utf-8")
    return content_hash_entries([("SKILL.md", rendered)])


def _skip(item: dict[str, Any], reason: str) -> dict[str, Any]:
    item["reason"] = reason
    return item


def _require_pinnable(record: dict[str, Any]) -> None:
    _require_direct_library_exposure(record)
    if record.get("status") != "current":
        raise ValueError(f"pin requires a clean current exposure; status is {record.get('status')}")


def _require_direct_library_exposure(record: dict[str, Any]) -> None:
    if record.get("ownership") != "library":
        raise ValueError("pin operations are available only for personal-library exposures")
    if record.get("mode") not in {"native", "stub"}:
        raise ValueError("pin operations are available only for direct native or stub exposures")


def _resolve_pin_hash(
    catalog_root: Path,
    skill_id: str,
    source_hash: str,
    requested: str | None,
    *,
    project_dir: Path,
) -> str:
    if requested is None:
        return source_hash
    prefix = requested.strip().lower()
    if source_hash.startswith(prefix):
        return source_hash
    history = library_history(catalog_root, skill_id, project_dir=project_dir)
    if not history["available"]:
        raise ValueError(f"library history is unavailable: {history['reason']}")
    selected = resolve_history_version(history["versions"], requested)
    if selected["content_hash"] != source_hash:
        raise ValueError(
            "pin --to must identify the exposure's current source hash; use reconcile rollback or re-expose to change bodies"
        )
    return str(selected["content_hash"])


def _load_sidecar(target: Path) -> dict[str, Any]:
    sidecar = target / "skillager.materialized.yaml"
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ValueError("managed exposure sidecar is missing or unsafe")
    data = load_mapping(sidecar)
    if data.get("schema") != MATERIALIZED_SCHEMA:
        raise ValueError("pin and sync require a direct managed exposure sidecar")
    if not isinstance(data.get("source_id") or data.get("id"), str):
        raise ValueError("managed exposure sidecar has no source identity")
    return data


def _require_matching_library_sidecar(catalog_root: Path, data: dict[str, Any]) -> None:
    registration, _identity = _require_library_identity(catalog_root)
    source_id = str(data.get("source_id") or data.get("id") or "")
    if data.get("source_library_id") != registration.library_id:
        raise ValueError("exposure does not belong to the registered personal library")
    if not source_id.startswith(f"{LIBRARY_NAMESPACE}/"):
        raise ValueError("exposure has invalid personal-library source identity")


def _required_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"managed exposure sidecar has no {label} hash")
    return value


def _has_noncanonical_target_entries(target: Path) -> bool:
    canonical = {path.relative_to(target).as_posix() for path in iter_content_files(target)}
    allowed = canonical | {"skillager.materialized.yaml"}
    for path in target.rglob("*"):
        relative = path.relative_to(target).as_posix()
        if path.is_symlink() or (path.is_file() and relative not in allowed):
            return True
    return False


def _require_project_target(project_dir: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("sync and pin operations are limited to the current project") from exc


__all__ = [
    "PIN_SCHEMA",
    "SYNC_SCHEMA",
    "pin_exposure",
    "sync_preview",
    "sync_project",
    "unpin_exposure",
]
