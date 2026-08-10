from __future__ import annotations

import json
import os
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import refresh_collection
from ..skills.tree import copy_content_tree
from ..state.locking import resource_locks
from ..trust import approval_key_for, content_hash, set_trust
from .candidate import index_library_candidate
from .git import LibraryGitError, commit_paths, path_changes, repository_status
from .metadata import load_library_provenance, set_fork_provenance
from .model import LIBRARY_NAMESPACE, LibraryLayout, normalize_skill_name
from .service import (
    _acceptance_overrides,
    _compact_lint,
    _compact_scan,
    _require_library_identity,
    _require_safe_git_mutation,
    library_where,
)
from .versioning import _historical_endpoint, _materialize_tree, library_history, resolve_history_version


LIBRARY_FORK_SCHEMA = "skillager.library-fork.v1"


def fork_preview(
    catalog_root: Path,
    source_skill: str,
    *,
    destination_name: str,
    description: str,
    from_hash: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration, identity = _require_library_identity(catalog_root)
    source_name = normalize_skill_name(source_skill)
    destination = normalize_skill_name(destination_name)
    if source_name == destination:
        raise ValueError("fork destination must have a distinct library identity")
    target = registration.layout.skill_root(destination)
    _require_new_fork_destination(target)
    normalized_description = _normalize_description(description)
    selected = _select_source_version(
        catalog_root,
        source_name,
        from_hash=from_hash,
        project_dir=project_dir,
    )
    with tempfile.TemporaryDirectory(prefix="skillager-fork-preview-") as tmp:
        candidate = Path(tmp) / destination
        source_entry, candidate_entry = _build_fork_candidate(
            registration.layout.root,
            registration.layout.skill_root(source_name),
            registration.layout,
            registration.library_id,
            destination,
            normalized_description,
            selected,
            candidate,
        )
    if _same_description(str(source_entry.get("summary") or ""), normalized_description):
        raise ValueError("fork description must differ from the selected source version")
    lint = _compact_lint(candidate_entry.get("lint"))
    scan = _compact_scan(candidate_entry.get("scan"))
    requires_override = lint["blocking_count"] > 0 or scan["risk"] == "high"
    source_id = f"{LIBRARY_NAMESPACE}/{source_name}"
    next_command_argv = _fork_argv(
        source_id,
        destination,
        normalized_description,
        from_hash=str(selected["content_hash"]) if from_hash is not None else None,
        override=requires_override,
    )
    return {
        "schema": LIBRARY_FORK_SCHEMA,
        "status": "preview",
        "will_fork": False,
        "source": {
            "id": source_id,
            "name": source_entry.get("name"),
            "summary": source_entry.get("summary"),
            "content_hash": selected["content_hash"],
            "commit": selected.get("commit"),
            "kind": selected["kind"],
        },
        "destination": {
            "id": f"{LIBRARY_NAMESPACE}/{destination}",
            "name": candidate_entry.get("name"),
            "summary": candidate_entry.get("summary"),
            "path": str(target),
            "content_hash": candidate_entry["content_hash"],
            "exists": False,
        },
        "lineage": {
            "skill": source_id,
            "hash": selected["content_hash"],
        },
        "description_changed": True,
        "lint": lint,
        "scan": scan,
        "requires_override": requires_override,
        "git": {"mode": identity.git_mode},
        "next_command": shlex.join(next_command_argv),
        "next_command_argv": next_command_argv,
    }


def fork_library_skill(
    catalog_root: Path,
    source_skill: str,
    *,
    destination_name: str,
    description: str,
    expected_source_hash: str,
    expected_source_commit: str | None,
    expected_candidate_hash: str,
    from_hash: str | None = None,
    override_lint: bool = False,
    reason: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    source_name = normalize_skill_name(source_skill)
    destination = normalize_skill_name(destination_name)
    normalized_description = _normalize_description(description)
    resources = [
        catalog_root / "library-mutation",
        catalog_root / f"library-skill-{source_name}",
        catalog_root / f"library-skill-{destination}",
    ]
    with resource_locks(resources):
        registration, identity = _require_library_identity(catalog_root)
        layout = registration.layout
        target = layout.skill_root(destination)
        _require_new_fork_destination(target)
        selected = _select_source_version(
            catalog_root,
            source_name,
            from_hash=from_hash,
            project_dir=project_dir,
        )
        if (
            selected["content_hash"] != expected_source_hash
            or selected.get("commit") != expected_source_commit
        ):
            raise ValueError("fork source changed since preview; review the fork again")
        git = repository_status(layout.root, mode=identity.git_mode)
        _require_safe_git_mutation(git, allow_target_staged=False)
        if identity.git_mode == "system" and any(
            path_changes(git, layout.root, layout.provenance_path).values()
        ):
            raise ValueError("library provenance has uncommitted changes; commit or restore it before forking")
        previous_provenance = load_library_provenance(layout)
        if previous_provenance is None:
            raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")

        created_at = datetime.now(timezone.utc).isoformat()
        candidate_entry: dict[str, Any]
        source_entry: dict[str, Any]
        with tempfile.TemporaryDirectory(prefix="skillager-fork-", dir=layout.root.parent) as tmp:
            candidate = Path(tmp) / destination
            source_entry, candidate_entry = _build_fork_candidate(
                layout.root,
                layout.skill_root(source_name),
                layout,
                registration.library_id,
                destination,
                normalized_description,
                selected,
                candidate,
            )
            if _same_description(str(source_entry.get("summary") or ""), normalized_description):
                raise ValueError("fork description must differ from the selected source version")
            candidate_hash = str(candidate_entry["content_hash"])
            if candidate_hash != expected_candidate_hash:
                raise ValueError("fork candidate changed since preview; review the fork again")
            lint_override, risk_override = _acceptance_overrides(
                candidate_entry,
                override_lint=override_lint,
                reason=reason,
            )
            _require_new_fork_destination(target)
            os.replace(candidate, target)
            try:
                provenance = set_fork_provenance(
                    layout,
                    destination,
                    source_skill=f"{LIBRARY_NAMESPACE}/{source_name}",
                    source_hash=expected_source_hash,
                    created_at=created_at,
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
                    f"Fork library skill {source_name} as {destination}",
                )
            except LibraryGitError as exc:
                raise ValueError(
                    f"{exc}; forked content remains pending. Fix Git, then run "
                    f"`skillager library accept lib/{destination} --yes`"
                ) from exc

        approval_key = approval_key_for(
            f"{LIBRARY_NAMESPACE}/{destination}",
            target,
            candidate_entry["source"],
            entrypoint=target / "SKILL.md",
        )
        if not approval_key:
            raise ValueError("forked library skill is missing a stable approval key")
        try:
            approval = set_trust(
                catalog_root,
                f"{LIBRARY_NAMESPACE}/{destination}",
                "reviewed",
                expected_candidate_hash,
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
            pending_state = "committed but pending" if identity.git_mode == "system" else "created but pending"
            raise ValueError(
                f"forked content is {pending_state} acceptance: {exc}; repair with "
                f"`skillager library accept lib/{destination} --yes`"
            ) from exc
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        where = library_where(catalog_root, destination, project_dir=project_dir)["skill"]
        return {
            "schema": LIBRARY_FORK_SCHEMA,
            "status": "forked",
            "will_fork": True,
            "source": {
                "id": f"{LIBRARY_NAMESPACE}/{source_name}",
                "name": source_entry.get("name"),
                "summary": source_entry.get("summary"),
                "content_hash": expected_source_hash,
                "commit": expected_source_commit,
                "kind": selected["kind"],
            },
            "destination": where,
            "lineage": provenance["forked_from"],
            "provenance": provenance,
            "approval": {
                "state": approval["state"],
                "scope": approval["scope"],
                "content_hash": approval["content_hash"],
                "approval_key": approval_key,
            },
            "commit": commit,
        }


def _select_source_version(
    catalog_root: Path,
    source_name: str,
    *,
    from_hash: str | None,
    project_dir: Path | None,
) -> dict[str, Any]:
    where = library_where(catalog_root, source_name, project_dir=project_dir)["skill"]
    if from_hash is not None:
        history = library_history(catalog_root, source_name, project_dir=project_dir)
        if not history["available"]:
            raise ValueError(f"library history is unavailable: {history['reason']}")
        version = resolve_history_version(history["versions"], from_hash)
        return {
            "kind": "history",
            "content_hash": str(version["content_hash"]),
            "commit": str(version["commit"]),
            "version": version,
        }
    if where.get("acceptance") != "accepted":
        raise ValueError("current library source hash must be accepted before it can be forked")
    if where.get("status") not in {"clean", "no_git"}:
        raise ValueError(f"current library source must be clean before fork; status is {where.get('status')}")
    return {
        "kind": "head" if where.get("head_hash") else "working",
        "content_hash": str(where["working_hash"]),
        "commit": _head_version_commit(catalog_root, source_name, where, project_dir=project_dir),
        "version": None,
    }


def _head_version_commit(
    catalog_root: Path,
    source_name: str,
    where: dict[str, Any],
    *,
    project_dir: Path | None,
) -> str | None:
    if where.get("head_hash") is None:
        return None
    history = library_history(catalog_root, source_name, project_dir=project_dir)
    if not history["available"]:
        raise ValueError(f"library history is unavailable: {history['reason']}")
    version = resolve_history_version(history["versions"], str(where["head_hash"]))
    return str(version["commit"])


def _build_fork_candidate(
    library_root: Path,
    source_target: Path,
    layout: LibraryLayout,
    library_id: str,
    destination: str,
    description: str,
    selected: dict[str, Any],
    candidate: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if selected["kind"] == "history":
        endpoint = _historical_endpoint(library_root, source_target, selected["version"])
        _materialize_tree(endpoint.files, candidate)
    else:
        copy_content_tree(source_target, candidate)
        if content_hash(source_target) != selected["content_hash"]:
            raise ValueError("library source changed during fork inspection; retry the command")
    if content_hash(candidate) != selected["content_hash"]:
        raise ValueError("fork source copy does not reproduce the selected content hash")
    source_entry = index_library_candidate(candidate, layout, library_id, source_target.name)
    _write_variant_identity(candidate / "SKILL.md", destination, description)
    candidate_entry = index_library_candidate(candidate, layout, library_id, destination)
    if candidate_entry.get("summary") != description:
        raise ValueError("fork description could not be represented as agent selection metadata")
    if candidate_entry.get("name") == source_entry.get("name"):
        raise ValueError("fork name could not be made distinct from the selected source version")
    return source_entry, candidate_entry


def _write_variant_identity(path: Path, name: str, description: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("fork source does not contain a regular canonical SKILL.md")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    display_name = " ".join(part.capitalize() for part in name.split("-"))
    fields = {
        "name": json.dumps(display_name, ensure_ascii=False),
        "description": json.dumps(description, ensure_ascii=False),
    }
    if lines and lines[0].strip() == "---":
        closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is None:
            raise ValueError("fork source has unterminated SKILL.md frontmatter")
        found: set[str] = set()
        for index in range(1, closing):
            stripped = lines[index]
            for key, value in fields.items():
                if stripped.startswith(f"{key}:"):
                    lines[index] = f"{key}: {value}\n"
                    found.add(key)
        additions = [f"{key}: {value}\n" for key, value in fields.items() if key not in found]
        lines[closing:closing] = additions
        updated = "".join(lines)
    else:
        updated = (
            "---\n"
            f"name: {fields['name']}\n"
            f"description: {fields['description']}\n"
            "---\n\n"
            f"{text}"
        )
    path.write_text(updated, encoding="utf-8")


def _normalize_description(value: str) -> str:
    description = " ".join(value.split())
    if not description:
        raise ValueError("fork description must be non-empty")
    if len(description) > 500:
        raise ValueError("fork description must be 500 characters or fewer")
    return description


def _same_description(first: str, second: str) -> bool:
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def _require_new_fork_destination(target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise ValueError(f"library skill already exists: {LIBRARY_NAMESPACE}/{target.name}; choose a collision-free --as name")


def _fork_argv(
    source: str,
    destination: str,
    description: str,
    *,
    from_hash: str | None,
    override: bool,
) -> list[str]:
    command = ["skillager", "fork", source, "--as", destination, "--description", description]
    if from_hash is not None:
        command.extend(["--from", from_hash])
    command.append("--yes")
    if override:
        command.extend(["--override-lint", "--reason", "<why>"])
    return command


__all__ = [
    "LIBRARY_FORK_SCHEMA",
    "fork_library_skill",
    "fork_preview",
]
