from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import refresh_collection, select_collection_skills
from ..skills.index import build_index
from ..state.locking import resource_locks
from ..state.statefiles import read_user_json
from ..trust import APPROVED_TRUST_STATES, approval_key_for, set_trust
from .candidate import prepare_library_candidate
from .source_identity import import_source_relation, source_key as _source_key
from .source_groups import source_group
from .import_preservation import import_preservation
from .review import REVIEW_LIMITS, ReviewRefusal, capture_review_tree
from .git import LibraryGitError, commit_paths, path_changes, repository_status, verified_version_reference
from .metadata import load_library_provenance, set_import_provenance
from .model import LIBRARY_NAMESPACE, normalize_skill_name
from .service import (
    _acceptance_overrides,
    _compact_lint,
    _compact_scan,
    _library_first_use_plan,
    _require_library_identity,
    _require_safe_git_mutation,
    _safe_metadata_summary,
    library_where,
)


IMPORT_SCHEMA = "skillager.import.v1"
IMPORT_REVIEW_SCHEMA = "skillager.import-review-manifest.v1"


def import_preview(
    project_state: Path,
    catalog_root: Path,
    source_skill_id: str,
    *,
    destination_name: str | None = None,
    project_dir: Path | None = None,
    source_file: Path | None = None,
    review_manifest: bool = False,
    project_native_root: Path | None = None,
) -> dict[str, Any]:
    layout, library, library_binding = _library_first_use_plan(catalog_root)
    source = _resolve_external_skill(project_state, catalog_root, source_skill_id, source_file=source_file, project_native_root=project_native_root)
    name = normalize_skill_name(destination_name or source_skill_id.rsplit("/", 1)[-1])
    target = layout.skill_root(name)
    _require_import_destination(layout.skills, target, source)
    source_key = _source_key(source)
    blocked = source.get("trust") == "blocked"
    lint = _compact_lint(source.get("lint"))
    scan = _compact_scan(source.get("scan"))
    requires_override = lint["blocking_count"] > 0 or scan["risk"] == "high"
    prospective_provenance = {
        "imported_from": {
            "skill_id": source_skill_id,
            "content_hash": source["content_hash"],
            "source_type": str((source.get("source") or {}).get("type") or "unknown"),
        },
    }
    result = {
        "schema": IMPORT_SCHEMA,
        "status": "preview",
        "source": _compact_source(source),
        "destination": {
            "id": f"{LIBRARY_NAMESPACE}/{name}",
            "slug": name,
            "name": name,
            "display_name": source.get("name") or name,
            "path": str(target),
            "exists": False,
        },
        "source_hash": source["content_hash"],
        "_source_key": source_key,
        "_library_binding": library_binding,
        "library": library,
        "provenance": prospective_provenance,
        "owner_review_required": source.get("trust") not in APPROVED_TRUST_STATES,
        "blocked": blocked,
        "lint": lint,
        "scan": scan,
        "requires_override": requires_override,
        "project": str(project_dir.resolve()) if project_dir is not None else None,
    }
    if source_file is not None or review_manifest:
        result["source"]["file"] = str(Path(source["entrypoint"]).resolve())
    if review_manifest:
        captured = _capture_import_review(source)
        result["review_manifest"] = {
            "schema": IMPORT_REVIEW_SCHEMA,
            "source_id": source["id"], "source_root": result["source"]["path"],
            "source_file": result["source"]["file"], "source_hash": source["content_hash"],
            "source_provenance": _review_source_provenance(source),
            "destination_id": result["destination"]["id"], "destination_path": str(target),
            "library": {**library, "library_id": library_binding["library_id"]},
            "limits": dict(REVIEW_LIMITS), **captured,
        }
    return result


def import_library_skill(
    project_state: Path,
    catalog_root: Path,
    source_skill_id: str,
    *,
    destination_name: str,
    expected_hash: str,
    expected_source_key: str,
    override_lint: bool = False,
    reason: str | None = None,
    project_dir: Path | None = None,
    source_file: Path | None = None,
    expected_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(destination_name)
    resources = [catalog_root / "library-mutation", catalog_root / f"library-skill-{name}"]
    with resource_locks(resources):
        return import_library_skill_locked(
            project_state, catalog_root, source_skill_id, destination_name=name,
            expected_hash=expected_hash, expected_source_key=expected_source_key,
            override_lint=override_lint, reason=reason, project_dir=project_dir,
            source_file=source_file, expected_review=expected_review,
        )


def import_library_skill_locked(
    project_state: Path, catalog_root: Path, source_skill_id: str, *,
    destination_name: str, expected_hash: str, expected_source_key: str,
    override_lint: bool = False, reason: str | None = None,
    project_dir: Path | None = None, source_file: Path | None = None,
    expected_review: dict[str, Any] | None = None,
    project_native_root: Path | None = None,
    refresh_catalog: bool = True,
) -> dict[str, Any]:
    """Import while the caller holds library-mutation and this skill's resource lock."""
    name = normalize_skill_name(destination_name)
    registration, identity = _require_library_identity(catalog_root)
    layout = registration.layout
    source = _resolve_external_skill(project_state, catalog_root, source_skill_id, source_file=source_file, project_native_root=project_native_root)
    source_hash = str(source["content_hash"])
    source_key = _source_key(source)
    if source_hash != expected_hash or source_key != expected_source_key:
        raise ValueError("import source changed since preview; review the new preview and rerun `skillager import`")
    if source.get("trust") == "blocked":
        raise ValueError(f"source skill is blocked and cannot be imported: {source_skill_id}")
    target = layout.skill_root(name)
    _require_import_destination(layout.skills, target, source)
    if expected_review is not None:
        captured = _capture_import_review(source)
        if (str(Path(source["root"]).resolve()) != expected_review["source_root"]
                or str(Path(source["entrypoint"]).resolve()) != expected_review["source_file"]
                or _review_source_provenance(source) != expected_review["source_provenance"]
                or str(target) != expected_review["destination_path"]
                or str(layout.root) != expected_review["library"]["root"]
                or expected_review["library"]["library_id"] != identity.library_id
                or expected_review["library"]["git_mode"] != identity.git_mode
                or any(captured[key] != expected_review[key] for key in captured)):
            raise ReviewRefusal("review_changed", "the selected import source or destination changed; preview again")
    _acceptance_overrides(source, override_lint=override_lint, reason=reason)
    git = repository_status(layout.root, mode=identity.git_mode)
    _require_safe_git_mutation(git, allow_target_staged=False)
    if identity.git_mode == "system" and any(
        path_changes(git, layout.root, layout.provenance_path).values()
    ):
        raise ValueError("library provenance has uncommitted changes; commit or restore it before importing")

    source_root = Path(source["root"]).resolve()
    previous_provenance = load_library_provenance(layout)
    if previous_provenance is None:
        raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")
    imported_at = datetime.now(timezone.utc).isoformat()
    copied_files: list[str]
    candidate_entry: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="skillager-import-", dir=layout.root.parent) as tmp:
        candidate = Path(tmp) / name
        copied_files, candidate_entry = prepare_library_candidate(
            source_root, candidate, layout, registration.library_id, name, expected_hash,
        )
        lint_override, risk_override = _acceptance_overrides(
            candidate_entry,
            override_lint=override_lint,
            reason=reason,
        )
        _require_import_destination(layout.skills, target, source)
        os.replace(candidate, target)
        try:
            provenance = set_import_provenance(
                layout,
                name,
                source_skill=source_skill_id,
                source_hash=expected_hash,
                source_type=str((source.get("source") or {}).get("type") or "unknown"),
                imported_at=imported_at,
                source_group=source_group(source, project_dir),
                expected=previous_provenance,
            )
        except Exception:
            if target.exists() and not candidate.exists():
                os.replace(target, candidate)
            raise

    commit = None
    if identity.git_mode == "system":
        try:
            commit = commit_paths(
                layout.root,
                [target, layout.provenance_path],
                f"Import library skill {name}",
            )
        except LibraryGitError as exc:
            raise ValueError(
                f"{exc}; imported content remains pending. Fix Git, then preview "
                f"`skillager library accept lib/{name} --json`"
            ) from exc

    approval_key = approval_key_for(
        f"{LIBRARY_NAMESPACE}/{name}",
        target,
        candidate_entry["source"],
        entrypoint=target / "SKILL.md",
    )
    if not approval_key:
        raise ValueError("imported library skill is missing a stable approval key")
    try:
        version = verified_version_reference(layout.root, target, source_key=approval_key, expected_hash=expected_hash, mode=identity.git_mode)
        preservation = import_preservation(source, project_dir, target, identity.library_id,
                                           layout.root, f"{LIBRARY_NAMESPACE}/{name}", expected_hash)
        record = set_trust(
            catalog_root,
            f"{LIBRARY_NAMESPACE}/{name}",
            "reviewed",
            expected_hash,
            candidate_entry["source"],
            lint=candidate_entry.get("lint"),
            lint_override=lint_override,
            risk_override=risk_override,
            reason=(reason or "").strip() or None,
            approval_key=approval_key,
            approval_root=catalog_root,
            global_scope=True,
            version=version,
            import_preservation=preservation,
            import_source=import_source_relation(source, project_dir, target, identity.library_id,
                                                 layout.root, f"{LIBRARY_NAMESPACE}/{name}", expected_hash),
        )
    except Exception as exc:
        pending_state = "committed but pending" if identity.git_mode == "system" else "copied but pending"
        raise ValueError(
            f"imported content is {pending_state} acceptance: {exc}; repair by previewing "
            f"`skillager library accept lib/{name} --json`"
        ) from exc
    if refresh_catalog:
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        where = library_where(catalog_root, name, project_dir=project_dir)["skill"]
    else:
        # Batch imports already have an exact accepted candidate. Catalog indexes
        # remain derived state; do not rehash every unrelated owned skill per item.
        where = {"id": f"{LIBRARY_NAMESPACE}/{name}", "path": str(target),
                 "working_hash": expected_hash, "accepted_hash": expected_hash,
                 "status": "clean", "acceptance": "accepted", "name": candidate_entry.get("name")}
    destination = {
        **where,
        "slug": name,
        "name": name,
        "display_name": where.get("name") or name,
    }
    return {
        "schema": IMPORT_SCHEMA,
        "status": "imported",
        "source": _compact_source(source),
        "destination": destination,
        "provenance": provenance,
        "approval": {
            "state": record["state"],
            "scope": record["scope"],
            "content_hash": record["content_hash"],
            "lint_override": record.get("lint_override"),
            "risk_override": record.get("risk_override"),
        },
        "copied_file_count": len(copied_files),
        "commit": commit,
    }


def import_inventory(project_state: Path, catalog_root: Path, *, extra_paths: list[Path] | None = None) -> dict[str, Any]:
    """Resolve the effective import inventory once, retaining discovery failures."""
    local = build_index(
        project_state,
        include_packages=True,
        approval_root=catalog_root,
        extra_paths=list(dict.fromkeys([*(_saved_setup_paths(project_state) or []), *(extra_paths or [])])),
        persist=False,
    )
    errors = list(local.get("errors", []))
    collections = select_collection_skills(
        catalog_root,
        trust_root=project_state,
        approval_root=catalog_root,
        include_blocked=True,
        include_lint_blocked=True,
        discovery_errors=errors,
    )
    return {"skills": [*local.get("skills", []), *collections], "errors": errors}


def _external_skill_candidates(project_state: Path, catalog_root: Path, skill_id: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for skill in import_inventory(project_state, catalog_root)["skills"]:
        if skill.get("id") != skill_id and skill.get("_claimed_id") != skill_id:
            continue
        if (skill.get("source") or {}).get("ownership") == "library":
            continue
        key = (str(skill.get("id")), str(Path(skill["root"]).resolve()))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(skill)
    return candidates


def _resolve_external_skill(project_state: Path, catalog_root: Path, skill_id: str,
                            *, source_file: Path | None = None,
                            project_native_root: Path | None = None) -> dict[str, Any]:
    if source_file is not None and (len(os.fsencode(source_file)) > 4096 or not source_file.is_absolute() or source_file != source_file.resolve()
                                    or not source_file.is_file()):
        raise ValueError("--source-file requires an existing canonical absolute instruction-file path")
    if project_native_root is None:
        candidates = _external_skill_candidates(project_state, catalog_root, skill_id)
    else:
        if source_file is None:
            raise ValueError("project import requires an exact instruction occurrence")
        observed = build_index(project_state, approval_root=catalog_root, include_packages=False, persist=False,
                               project_native_root=project_native_root, project_inventory=True,
                               selected_native_file=source_file)
        candidates = [skill for skill in observed["skills"]
                      if skill.get("id") == skill_id or skill.get("_claimed_id") == skill_id]
    if source_file is not None:
        candidates = [skill for skill in candidates if skill.get("entrypoint")
                      and Path(skill["entrypoint"]).resolve() == source_file]
    if not candidates:
        if skill_id.startswith(f"{LIBRARY_NAMESPACE}/"):
            raise ValueError("library skills are already owned and cannot be imported again")
        raise ValueError(f"external skill not found in current discovery: {skill_id}")
    if len(candidates) > 1:
        paths = ", ".join(sorted(str(Path(skill["root"]).resolve()) for skill in candidates))
        raise ValueError(f"external skill ID is ambiguous across discovered sources: {skill_id} ({paths})")
    return candidates[0]


def _capture_import_review(source: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(source["root"]).resolve()
    instruction = source_root / "SKILL.md"
    if instruction.is_symlink() or not instruction.is_file():
        raise ReviewRefusal("review_changed", "import requires a regular canonical SKILL.md; repair the source and preview again")
    captured, _ = capture_review_tree(source_root)
    if captured["working_hash"] != source["content_hash"] or capture_review_tree(source_root)[0] != captured:
        raise ReviewRefusal("review_changed", "the external source changed during review capture; preview again")
    return captured


def _review_source_provenance(source: dict[str, Any]) -> dict[str, Any]:
    metadata = _compact_source(source)
    return {key: metadata[key] for key in ("type", "collection", "package", "version", "editable")}


def _compact_source(skill: dict[str, Any]) -> dict[str, Any]:
    source = skill.get("source") or {}
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": _safe_metadata_summary(skill.get("summary")),
        "path": str(Path(skill["root"]).resolve()),
        "type": source.get("type"),
        "collection": source.get("collection"),
        "package": source.get("package"),
        "version": source.get("version"),
        "editable": source.get("editable") == "true",
        "trust": skill.get("trust"),
        "content_hash": skill.get("content_hash"),
    }


def _require_import_destination(library_skills: Path, target: Path, source: dict[str, Any]) -> None:
    if target.exists() or target.is_symlink():
        raise ValueError(f"library skill already exists: {LIBRARY_NAMESPACE}/{target.name}; choose a collision-free --as name")
    source_root = Path(source["root"]).resolve()
    try:
        source_root.relative_to(library_skills.resolve())
    except ValueError:
        return
    raise ValueError("library skills cannot be imported from the library")


def _saved_setup_paths(project_state: Path) -> list[Path] | None:
    data = read_user_json(project_state / "status_scope.json", {})
    paths = []
    for raw in data.get("paths") or []:
        if not isinstance(raw, str):
            continue
        path = Path(raw).expanduser()
        if path.exists():
            paths.append(path)
    return paths or None


__all__ = [
    "IMPORT_SCHEMA",
    "import_library_skill",
    "import_preview",
]
