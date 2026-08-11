from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import refresh_collection, select_collection_skills
from ..skills.index import build_index
from ..skills.tree import copy_content_tree
from ..state.locking import resource_locks
from ..state.statefiles import read_user_json
from ..trust import APPROVED_TRUST_STATES, approval_key_for, content_hash, set_trust
from .candidate import index_library_candidate
from .git import LibraryGitError, commit_paths, path_changes, repository_status
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


IMPORT_SCHEMA = "skillager.import.v1"


def import_preview(
    project_state: Path,
    catalog_root: Path,
    source_skill_id: str,
    *,
    destination_name: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration, _identity = _require_library_identity(catalog_root)
    source = _resolve_external_skill(project_state, catalog_root, source_skill_id)
    name = normalize_skill_name(destination_name or source_skill_id.rsplit("/", 1)[-1])
    target = registration.layout.skill_root(name)
    _require_import_destination(registration.layout.skills, target, source)
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
    return {
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
        "provenance": prospective_provenance,
        "owner_review_required": source.get("trust") not in APPROVED_TRUST_STATES,
        "blocked": blocked,
        "lint": lint,
        "scan": scan,
        "requires_override": requires_override,
        "project": str(project_dir.resolve()) if project_dir is not None else None,
    }


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
) -> dict[str, Any]:
    name = normalize_skill_name(destination_name)
    resources = [catalog_root / "library-mutation", catalog_root / f"library-skill-{name}"]
    with resource_locks(resources):
        registration, identity = _require_library_identity(catalog_root)
        layout = registration.layout
        source = _resolve_external_skill(project_state, catalog_root, source_skill_id)
        source_hash = str(source["content_hash"])
        source_key = _source_key(source)
        if source_hash != expected_hash or source_key != expected_source_key:
            raise ValueError("import source changed since preview; review the new preview and rerun `skillager import`")
        if source.get("trust") == "blocked":
            raise ValueError(f"source skill is blocked and cannot be imported: {source_skill_id}")
        target = layout.skill_root(name)
        _require_import_destination(layout.skills, target, source)
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
            copied_files = copy_content_tree(source_root, candidate)
            skill_file = candidate / "SKILL.md"
            if skill_file.is_symlink() or not skill_file.is_file():
                raise ValueError("import source does not contain a regular canonical SKILL.md")
            if content_hash(source_root) != expected_hash:
                raise ValueError("import source changed while it was being copied; no library files were written")
            candidate_entry = index_library_candidate(candidate, layout, registration.library_id, name)
            if candidate_entry["content_hash"] != expected_hash:
                raise ValueError("filtered import tree does not reproduce the reviewed source content hash")
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
            )
        except Exception as exc:
            pending_state = "committed but pending" if identity.git_mode == "system" else "copied but pending"
            raise ValueError(
                f"imported content is {pending_state} acceptance: {exc}; repair by previewing "
                f"`skillager library accept lib/{name} --json`"
            ) from exc
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        where = library_where(catalog_root, name, project_dir=project_dir)["skill"]
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


def _external_skill_candidates(project_state: Path, catalog_root: Path, skill_id: str) -> list[dict[str, Any]]:
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
    )
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for skill in [*local, *collections]:
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


def _resolve_external_skill(project_state: Path, catalog_root: Path, skill_id: str) -> dict[str, Any]:
    candidates = _external_skill_candidates(project_state, catalog_root, skill_id)
    if not candidates:
        if skill_id.startswith(f"{LIBRARY_NAMESPACE}/"):
            raise ValueError("library skills are already owned and cannot be imported again")
        raise ValueError(f"external skill not found in current discovery: {skill_id}")
    if len(candidates) > 1:
        paths = ", ".join(sorted(str(Path(skill["root"]).resolve()) for skill in candidates))
        raise ValueError(f"external skill ID is ambiguous across discovered sources: {skill_id} ({paths})")
    return candidates[0]


def _source_key(skill: dict[str, Any]) -> str:
    key = skill.get("approval_key") or approval_key_for(
        str(skill["id"]),
        skill.get("root"),
        skill.get("source") or {},
        entrypoint=skill.get("entrypoint"),
    )
    if isinstance(key, str) and key:
        return key
    return f"path:{Path(skill['root']).resolve().as_posix()}"


def _compact_source(skill: dict[str, Any]) -> dict[str, Any]:
    source = skill.get("source") or {}
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
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
