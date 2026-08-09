from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import refresh_collection
from ..exposure.reconcile import (
    quarantine_target_locked,
    require_exposure,
    write_reconciled_sidecar,
)
from ..simple_yaml import load_mapping
from ..skills.tree import content_tree_fingerprint, content_tree_manifest, copy_content_tree
from ..state.locking import resource_locks
from ..trust import approval_key_for, content_hash, set_trust
from .candidate import index_library_candidate
from .git import LibraryGitError, commit_paths, git_tree_files, path_changes, repository_status
from .metadata import load_library_provenance, set_import_provenance
from .model import LIBRARY_NAMESPACE, normalize_skill_name
from .service import (
    _acceptance_overrides,
    _compact_lint,
    _compact_scan,
    _require_library_identity,
    _require_safe_git_mutation,
    library_where,
)
from .versioning import (
    _materialize_tree,
    _require_canonical_working_tree,
    library_history,
    resolve_history_version,
)


RECONCILE_PROMOTE_SCHEMA = "skillager.reconcile-promote.v1"
RECONCILE_IMPORT_SCHEMA = "skillager.reconcile-import.v1"
RECONCILE_ROLLBACK_SCHEMA = "skillager.reconcile-rollback.v1"


def promote_preview(
    project_state: Path,
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    del project_state  # Reserved for consistent reconciliation call signatures.
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    _require_library_native(record, action="promote")
    if record["status"] not in {"local_edit", "kept_local"}:
        raise ValueError(f"promote requires an edited native exposure; status is {record['status']}")
    target = Path(record["target"])
    data = _load_sidecar(target)
    current_hash = content_hash(target)
    registration, identity = _require_library_identity(catalog_root)
    _require_matching_library(data, registration.library_id)
    name = normalize_skill_name(str(record["skill_id"]))
    where = library_where(catalog_root, name, project_dir=project_dir)["skill"]
    base_hash = _required_text(data, "source_hash")
    candidate = _candidate_preview(target, registration.layout, registration.library_id, name)
    fast_forward = where.get("accepted_hash") == base_hash and where.get("working_hash") == base_hash
    if identity.git_mode == "system":
        fast_forward = fast_forward and where.get("head_hash") == base_hash
    status = "preview" if fast_forward else "diverged"
    changes = _promotion_changes(registration.layout.root, Path(where["path"]), target, base_hash, where)
    return {
        "schema": RECONCILE_PROMOTE_SCHEMA,
        "status": status,
        "action": "promote",
        "will_write": False,
        "can_apply": fast_forward,
        "exposure": record,
        "library": _compact_library(where),
        "base_hash": base_hash,
        "promoted_hash": current_hash,
        "expected_target": str(target),
        "lint": _compact_lint(candidate.get("lint")),
        "scan": _compact_scan(candidate.get("scan")),
        "requires_override": _requires_override(candidate),
        "comparisons": {
            "accepted_matches_base": where.get("accepted_hash") == base_hash,
            "working_matches_base": where.get("working_hash") == base_hash,
            "head_matches_base": where.get("head_hash") == base_hash if identity.git_mode == "system" else None,
        },
        "changes": changes,
        "next_command": _next_command("promote", record),
    }


def promote_exposure(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    expected_target: str,
    expected_base_hash: str,
    expected_exposure_hash: str,
    agent: str | None = None,
    override_lint: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(skill_id)
    selected_target = Path(expected_target).resolve()
    resources = [
        catalog_root / "library-mutation",
        catalog_root / f"library-skill-{name}",
        selected_target,
    ]
    with resource_locks(resources):
        record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
        _require_library_native(record, action="promote")
        target = Path(record["target"]).resolve()
        if target != selected_target:
            raise ValueError("selected exposure target changed since preview")
        current_hash = content_hash(target)
        if current_hash != expected_exposure_hash:
            raise ValueError("exposure changed since promote preview; review both diffs again")
        data = _load_sidecar(target)
        if data.get("source_hash") != expected_base_hash:
            raise ValueError("exposure base changed since promote preview")

        registration, identity = _require_library_identity(catalog_root)
        _require_matching_library(data, registration.library_id)
        where = library_where(catalog_root, name, project_dir=project_dir)["skill"]
        if where.get("accepted_hash") != expected_base_hash or where.get("working_hash") != expected_base_hash:
            raise ValueError("library and exposure diverged; no files were changed")
        if identity.git_mode == "system" and where.get("head_hash") != expected_base_hash:
            raise ValueError("library Git HEAD and exposure base diverged; no files were changed")
        library_target = registration.layout.skill_root(name)
        _require_canonical_working_tree(library_target)
        git = repository_status(registration.layout.root, mode=identity.git_mode)
        _require_safe_git_mutation(git, allow_target_staged=False)
        if any(path_changes(git, registration.layout.root, library_target).values()):
            raise ValueError("library skill has uncommitted Git changes; no files were changed")

        with tempfile.TemporaryDirectory(prefix="skillager-promote-", dir=registration.layout.root.parent) as tmp:
            temp_root = Path(tmp)
            candidate = temp_root / name
            backup = temp_root / f"{name}.previous"
            copy_content_tree(target, candidate)
            if content_hash(target) != expected_exposure_hash:
                raise ValueError("exposure changed while promotion was being prepared; no library files were changed")
            entry = index_library_candidate(candidate, registration.layout, registration.library_id, name)
            if entry["content_hash"] != expected_exposure_hash:
                raise ValueError("filtered exposure does not reproduce the previewed content hash")
            lint_override, risk_override = _acceptance_overrides(
                entry,
                override_lint=override_lint,
                reason=reason,
            )
            if content_hash(library_target) != expected_base_hash:
                raise ValueError("library skill changed while promotion was being prepared; no files were changed")
            os.replace(library_target, backup)
            try:
                os.replace(candidate, library_target)
            except Exception:
                os.replace(backup, library_target)
                raise

        commit = None
        if identity.git_mode == "system":
            try:
                commit = commit_paths(
                    registration.layout.root,
                    [library_target],
                    f"Promote exposure to library skill {name}",
                )
            except LibraryGitError as exc:
                raise ValueError(
                    f"{exc}; promoted content remains pending. Fix Git, then run "
                    f"`skillager library accept lib/{name} --yes`"
                ) from exc
        approval_key = approval_key_for(
            f"{LIBRARY_NAMESPACE}/{name}",
            library_target,
            entry["source"],
            entrypoint=library_target / "SKILL.md",
        )
        if not approval_key:
            raise ValueError("promoted library skill is missing a stable approval key")
        try:
            approval = set_trust(
                catalog_root,
                f"{LIBRARY_NAMESPACE}/{name}",
                "reviewed",
                expected_exposure_hash,
                entry["source"],
                lint=entry.get("lint"),
                lint_override=lint_override,
                risk_override=risk_override,
                reason=(reason or "").strip() or None,
                approval_key=approval_key,
                approval_root=catalog_root,
                global_scope=True,
            )
        except Exception as exc:
            raise ValueError(
                f"promoted content is committed but pending acceptance: {exc}; repair with "
                f"`skillager library accept lib/{name} --yes`"
            ) from exc
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        _mark_exposure_promoted(target, data, expected_exposure_hash)
        final = library_where(catalog_root, name, project_dir=project_dir)["skill"]
        return {
            "schema": RECONCILE_PROMOTE_SCHEMA,
            "status": "promoted",
            "action": "promote",
            "will_write": True,
            "library": final,
            "exposure": {
                "skill_id": record["skill_id"],
                "target": str(target),
                "source_hash": expected_exposure_hash,
                "materialized_hash": expected_exposure_hash,
            },
            "base_hash": expected_base_hash,
            "promoted_hash": expected_exposure_hash,
            "approval": {
                "state": approval["state"],
                "content_hash": approval["content_hash"],
                "approval_key": approval_key,
            },
            "commit": commit,
        }


def import_exposure_preview(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    destination_name: str,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    _require_external_native(record, action="import")
    if record["status"] not in {"local_edit", "kept_local"}:
        raise ValueError(f"reconcile import requires an edited native exposure; status is {record['status']}")
    target = Path(record["target"])
    data = _load_sidecar(target)
    current_hash = content_hash(target)
    name = normalize_skill_name(destination_name)
    registration, _identity = _require_library_identity(catalog_root)
    destination = registration.layout.skill_root(name)
    _require_new_destination(destination)
    candidate = _candidate_preview(target, registration.layout, registration.library_id, name)
    base_hash = _required_text(data, "source_hash")
    return {
        "schema": RECONCILE_IMPORT_SCHEMA,
        "status": "preview",
        "action": "import",
        "will_write": False,
        "can_apply": True,
        "exposure": record,
        "source_hash": base_hash,
        "imported_hash": current_hash,
        "expected_target": str(target),
        "destination": {"id": f"{LIBRARY_NAMESPACE}/{name}", "name": name, "path": str(destination)},
        "provenance": _exposure_provenance(data, base_hash),
        "lint": _compact_lint(candidate.get("lint")),
        "scan": _compact_scan(candidate.get("scan")),
        "requires_override": _requires_override(candidate),
        "next_command": _next_command("import", record, destination=name),
    }


def import_exposure(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    destination_name: str,
    expected_target: str,
    expected_hash: str,
    expected_source_hash: str,
    agent: str | None = None,
    override_lint: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(destination_name)
    selected_target = Path(expected_target).resolve()
    resources = [
        catalog_root / "library-mutation",
        catalog_root / f"library-skill-{name}",
        selected_target,
    ]
    with resource_locks(resources):
        record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
        _require_external_native(record, action="import")
        target = Path(record["target"]).resolve()
        if target != selected_target or content_hash(target) != expected_hash:
            raise ValueError("edited exposure changed since import preview; review it again")
        data = _load_sidecar(target)
        if data.get("source_hash") != expected_source_hash:
            raise ValueError("external exposure provenance changed since import preview")
        registration, identity = _require_library_identity(catalog_root)
        layout = registration.layout
        destination = layout.skill_root(name)
        _require_new_destination(destination)
        git = repository_status(layout.root, mode=identity.git_mode)
        _require_safe_git_mutation(git, allow_target_staged=False)
        if any(path_changes(git, layout.root, layout.provenance_path).values()):
            raise ValueError("library provenance has uncommitted changes; commit or restore it before importing")
        previous_provenance = load_library_provenance(layout)
        if previous_provenance is None:
            raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")

        with tempfile.TemporaryDirectory(prefix="skillager-reconcile-import-", dir=layout.root.parent) as tmp:
            candidate = Path(tmp) / name
            copied_files = copy_content_tree(target, candidate)
            if content_hash(target) != expected_hash:
                raise ValueError("edited exposure changed while import was being prepared; no library files were written")
            entry = index_library_candidate(candidate, layout, registration.library_id, name)
            if entry["content_hash"] != expected_hash:
                raise ValueError("filtered exposure does not reproduce the previewed content hash")
            lint_override, risk_override = _acceptance_overrides(
                entry,
                override_lint=override_lint,
                reason=reason,
            )
            _require_new_destination(destination)
            os.replace(candidate, destination)
            try:
                provenance_data = _exposure_provenance(data, expected_source_hash)
                provenance = set_import_provenance(
                    layout,
                    name,
                    source_key=str(provenance_data["imported_from"]["source_key"]),
                    source_skill=str(provenance_data["imported_from"]["skill_id"]),
                    source_hash=expected_source_hash,
                    source_type=str(provenance_data["imported_from"]["source_type"]),
                    imported_at=_now_iso(),
                    expected=previous_provenance,
                )
            except Exception:
                if destination.exists() and not candidate.exists():
                    os.replace(destination, candidate)
                raise

        commit = None
        if identity.git_mode == "system":
            try:
                commit = commit_paths(
                    layout.root,
                    [destination, layout.provenance_path],
                    f"Import reconciled exposure as library skill {name}",
                )
            except LibraryGitError as exc:
                raise ValueError(
                    f"{exc}; imported content remains pending. Fix Git, then run "
                    f"`skillager library accept lib/{name} --yes`"
                ) from exc
        approval_key = approval_key_for(
            f"{LIBRARY_NAMESPACE}/{name}",
            destination,
            entry["source"],
            entrypoint=destination / "SKILL.md",
        )
        if not approval_key:
            raise ValueError("imported library skill is missing a stable approval key")
        approval = set_trust(
            catalog_root,
            f"{LIBRARY_NAMESPACE}/{name}",
            "reviewed",
            expected_hash,
            entry["source"],
            lint=entry.get("lint"),
            lint_override=lint_override,
            risk_override=risk_override,
            reason=(reason or "").strip() or None,
            approval_key=approval_key,
            approval_root=catalog_root,
            global_scope=True,
        )
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        final = library_where(catalog_root, name, project_dir=project_dir)["skill"]
        return {
            "schema": RECONCILE_IMPORT_SCHEMA,
            "status": "imported",
            "action": "import",
            "will_write": True,
            "source": {
                "skill_id": record["skill_id"],
                "target": str(target),
                "base_hash": expected_source_hash,
                "imported_hash": expected_hash,
            },
            "destination": final,
            "provenance": provenance,
            "approval": {
                "state": approval["state"],
                "content_hash": approval["content_hash"],
                "approval_key": approval_key,
            },
            "copied_file_count": len(copied_files),
            "commit": commit,
        }


def rollback_preview(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
    if record.get("mode") != "native":
        return _rollback_unavailable(record, "generated-exposure", "use reconcile repair for a generated stub or router")
    if record.get("ownership") != "library":
        return _rollback_unavailable(record, "external-source", "re-expose from the reviewed external source")
    _require_library_native(record, action="rollback")
    target = Path(record["target"])
    data = _load_sidecar(target)
    history = library_history(catalog_root, str(record["skill_id"]), project_dir=project_dir)
    if not history["available"]:
        return {
            "schema": RECONCILE_ROLLBACK_SCHEMA,
            "status": "unavailable",
            "action": "rollback",
            "will_write": False,
            "can_apply": False,
            "reason": history["reason"],
            "exposure": record,
        }
    source_hash = _required_text(data, "source_hash")
    try:
        version = resolve_history_version(history["versions"], source_hash)
    except ValueError:
        return _rollback_unavailable(
            record,
            "source-history-missing",
            "quarantine the target or re-expose an accepted library version",
        )
    current_hash = content_hash(target) if (target / "SKILL.md").is_file() else None
    already_base = current_hash == source_hash
    registration, _identity = _require_library_identity(catalog_root)
    _require_matching_library(data, registration.library_id)
    difference = _historical_difference(registration.layout.root, Path(history["skill"]["path"]), version, target)
    return {
        "schema": RECONCILE_ROLLBACK_SCHEMA,
        "status": "already-current" if already_base else "preview",
        "action": "rollback",
        "will_write": False,
        "can_apply": not already_base,
        "exposure": record,
        "expected_target": str(target),
        "expected_current_hash": current_hash,
        "restore_hash": source_hash,
        "selected_version": version,
        "dirty_target_will_quarantine": current_hash is not None and current_hash != data.get("materialized_hash"),
        "changes": difference,
        "next_command": _next_command("rollback", record),
    }


def rollback_exposure(
    catalog_root: Path,
    project_dir: Path,
    skill_id: str,
    *,
    expected_target: str,
    expected_current_hash: str | None,
    expected_restore_hash: str,
    expected_commit: str,
    agent: str | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(skill_id)
    selected_target = Path(expected_target).resolve()
    resources = [catalog_root / f"library-skill-{name}", selected_target]
    with resource_locks(resources):
        record = require_exposure(project_dir, catalog_root, skill_id, agent=agent)
        _require_library_native(record, action="rollback")
        target = Path(record["target"]).resolve()
        if target != selected_target:
            raise ValueError("selected exposure target changed since rollback preview")
        current_hash = content_hash(target) if (target / "SKILL.md").is_file() else None
        if current_hash != expected_current_hash:
            raise ValueError("exposure changed since rollback preview; review it again")
        data = _load_sidecar(target)
        if data.get("source_hash") != expected_restore_hash:
            raise ValueError("exposure base changed since rollback preview")
        registration, identity = _require_library_identity(catalog_root)
        _require_matching_library(data, registration.library_id)
        if identity.git_mode != "system":
            raise ValueError("library history is unavailable: no-git")
        history = library_history(catalog_root, name, project_dir=project_dir)
        if not history["available"]:
            raise ValueError(f"library history is unavailable: {history['reason']}")
        version = resolve_history_version(history["versions"], expected_restore_hash)
        if version["commit"] != expected_commit:
            raise ValueError("historical version changed since rollback preview")
        files = tuple(git_tree_files(registration.layout.root, registration.layout.skill_root(name), expected_commit))
        quarantine_path: Path | None = None
        rollback_temp_root = project_dir.resolve() / ".skillager-quarantine"
        if rollback_temp_root.is_symlink():
            raise ValueError("refusing symlinked project quarantine directory")
        rollback_temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(
            prefix="skillager-rollback-",
            dir=rollback_temp_root,
        ) as tmp:
            temp_root = Path(tmp)
            candidate = temp_root / target.name
            _materialize_tree(files, candidate)
            if content_hash(candidate) != expected_restore_hash:
                raise ValueError("historical exposure version no longer reproduces its content hash")
            dirty = current_hash is not None and current_hash != data.get("materialized_hash")
            if dirty:
                assert current_hash is not None
                quarantine_path, data = quarantine_target_locked(
                    project_dir,
                    target,
                    data,
                    current_hash=current_hash,
                    block_hash=True,
                )
            updated = dict(data)
            updated.update(
                {
                    "source_hash": expected_restore_hash,
                    "materialized_hash": expected_restore_hash,
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
            write_reconciled_sidecar(candidate / "skillager.materialized.yaml", updated)
            displaced = temp_root / f"{target.name}.previous"
            if target.exists():
                os.replace(target, displaced)
            try:
                os.replace(candidate, target)
            except Exception:
                if displaced.exists():
                    os.replace(displaced, target)
                raise
        return {
            "schema": RECONCILE_ROLLBACK_SCHEMA,
            "status": "rolled-back",
            "action": "rollback",
            "will_write": True,
            "exposure": {
                "skill_id": record["skill_id"],
                "target": str(target),
                "source_hash": expected_restore_hash,
                "materialized_hash": expected_restore_hash,
            },
            "restored_version": version,
            "quarantine_path": str(quarantine_path) if quarantine_path else None,
        }


def _require_library_native(record: dict[str, Any], *, action: str) -> None:
    if record.get("mode") != "native" or record.get("ownership") != "library":
        raise ValueError(f"{action} is available only for native personal-library exposures")
    if not str(record.get("skill_id") or "").startswith(f"{LIBRARY_NAMESPACE}/"):
        raise ValueError("library exposure source identity is invalid")


def _require_external_native(record: dict[str, Any], *, action: str) -> None:
    if record.get("mode") != "native" or record.get("ownership") != "external":
        raise ValueError(f"reconcile {action} is available only for edited external native exposures")


def _rollback_unavailable(record: dict[str, Any], reason: str, next_action: str) -> dict[str, Any]:
    return {
        "schema": RECONCILE_ROLLBACK_SCHEMA,
        "status": "unavailable",
        "action": "rollback",
        "will_write": False,
        "can_apply": False,
        "reason": reason,
        "next_action": next_action,
        "exposure": record,
    }


def _next_command(action: str, record: dict[str, Any], *, destination: str | None = None) -> str:
    command = f"skillager reconcile {action} {record['skill_id']}"
    if destination is not None:
        command += f" --as {destination}"
    return f"{command} --agent {record['agent']} --yes"


def _require_matching_library(data: dict[str, Any], library_id: str) -> None:
    recorded = data.get("source_library_id")
    if recorded is not None and recorded != library_id:
        raise ValueError("exposure belongs to a different personal library identity")


def _load_sidecar(target: Path) -> dict[str, Any]:
    path = target / "skillager.materialized.yaml"
    if path.is_symlink() or not path.is_file():
        raise ValueError("managed exposure sidecar is missing or unsafe")
    data = load_mapping(path)
    if not isinstance(data.get("materialized_hash"), str):
        raise ValueError("managed exposure sidecar is missing materialized hash")
    return data


def _candidate_preview(target: Path, layout, library_id: str, name: str) -> dict[str, Any]:
    expected_hash = content_hash(target)
    with tempfile.TemporaryDirectory(prefix="skillager-reconcile-preview-") as tmp:
        candidate = Path(tmp) / name
        copy_content_tree(target, candidate)
        if content_hash(target) != expected_hash:
            raise ValueError("exposure changed during reconciliation preview; retry")
        entry = index_library_candidate(candidate, layout, library_id, name)
        if entry["content_hash"] != expected_hash:
            raise ValueError("filtered exposure does not reproduce its current content hash")
        return entry


def _mark_exposure_promoted(target: Path, data: dict[str, Any], promoted_hash: str) -> None:
    updated = dict(data)
    updated.update(
        {
            "source_hash": promoted_hash,
            "materialized_hash": promoted_hash,
            "materialized_fingerprint": content_tree_fingerprint(target),
            "source_trust": "reviewed",
            "materialized_at": _now_iso(),
            "customized": False,
        }
    )
    for key in ("customization_decision", "customized_hash", "customized_fingerprint", "customized_at"):
        updated.pop(key, None)
    write_reconciled_sidecar(target / "skillager.materialized.yaml", updated)


def _requires_override(entry: dict[str, Any]) -> bool:
    return _compact_lint(entry.get("lint"))["blocking_count"] > 0 or _compact_scan(entry.get("scan"))["risk"] == "high"


def _compact_library(where: dict[str, Any]) -> dict[str, Any]:
    return {
        key: where.get(key)
        for key in ("id", "name", "path", "working_hash", "accepted_hash", "head_hash", "acceptance", "status", "history")
    }


def _promotion_changes(root: Path, library_target: Path, exposure: Path, base_hash: str, where: dict[str, Any]) -> dict[str, Any]:
    if where.get("working_hash") == base_hash:
        base_to_exposure = _tree_difference(library_target, exposure)
        base_to_library = _empty_difference()
        return {"base_to_exposure": base_to_exposure, "base_to_library": base_to_library}
    history = library_history_from_where(root, library_target, where, base_hash)
    if history is None:
        return {
            "base_to_exposure": {"available": False, "reason": "base-history-unavailable"},
            "base_to_library": {"available": False, "reason": "base-history-unavailable"},
        }
    with tempfile.TemporaryDirectory(prefix="skillager-promote-diff-") as tmp:
        base = Path(tmp) / "base"
        _materialize_tree(tuple(history), base)
        return {
            "base_to_exposure": _tree_difference(base, exposure),
            "base_to_library": _tree_difference(base, library_target),
        }


def library_history_from_where(root: Path, target: Path, where: dict[str, Any], base_hash: str):
    try:
        # Path history is resolved through the public content-hash history verifier.
        from .versioning import _verified_history_versions

        versions = _verified_history_versions(root, target, where)
        version = resolve_history_version(versions, base_hash)
        return git_tree_files(root, target, str(version["commit"]))
    except (ValueError, LibraryGitError):
        return None


def _tree_difference(before: Path, after: Path) -> dict[str, Any]:
    first = content_tree_manifest(before)
    second = content_tree_manifest(after)
    return {
        "available": True,
        "added": sorted(set(second) - set(first)),
        "deleted": sorted(set(first) - set(second)),
        "changed": sorted(path for path in set(first) & set(second) if first[path] != second[path]),
    }


def _empty_difference() -> dict[str, Any]:
    return {"available": True, "added": [], "deleted": [], "changed": []}


def _historical_difference(root: Path, target: Path, version: dict[str, Any], current: Path) -> dict[str, Any]:
    if not (current / "SKILL.md").is_file():
        return {"available": True, "added": [], "deleted": ["SKILL.md"], "changed": []}
    with tempfile.TemporaryDirectory(prefix="skillager-rollback-diff-") as tmp:
        historical = Path(tmp) / "historical"
        _materialize_tree(tuple(git_tree_files(root, target, str(version["commit"]))), historical)
        return _tree_difference(current, historical)


def _exposure_provenance(data: dict[str, Any], base_hash: str) -> dict[str, Any]:
    source_id = str(data.get("source_id") or data.get("id"))
    source_type = str(data.get("source_type") or "unknown")
    source_key = _exposure_source_key(data, source_id, base_hash)
    return {
        "artifact_kind": "skill",
        "imported_from": {
            "source_key": source_key,
            "skill_id": source_id,
            "content_hash": base_hash,
            "source_type": source_type,
        },
    }


def _exposure_source_key(data: dict[str, Any], source_id: str, base_hash: str) -> str:
    entrypoint = data.get("source_entrypoint")
    entrypoint_path = Path(str(entrypoint)).expanduser() if entrypoint else None
    root = entrypoint_path.parent if entrypoint_path and entrypoint_path.name == "SKILL.md" else entrypoint_path
    source = {
        "type": data.get("source_type"),
        "package": data.get("source_package"),
    }
    key = approval_key_for(source_id, root, source, entrypoint=entrypoint_path)
    return key or f"exposure:{source_type_label(data)}:{source_id}#{base_hash}"


def source_type_label(data: dict[str, Any]) -> str:
    return str(data.get("source_type") or "unknown").replace(":", "-")


def _require_new_destination(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"library skill already exists: {LIBRARY_NAMESPACE}/{destination.name}; choose another --as name")


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"exposure sidecar is missing {key.replace('_', ' ')}")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "RECONCILE_IMPORT_SCHEMA",
    "RECONCILE_PROMOTE_SCHEMA",
    "RECONCILE_ROLLBACK_SCHEMA",
    "import_exposure",
    "import_exposure_preview",
    "promote_exposure",
    "promote_preview",
    "rollback_exposure",
    "rollback_preview",
]
