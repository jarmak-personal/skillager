from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.impl import (
    load_collections,
    refresh_collection,
    register_library_collection,
    relocate_library_collection,
    select_collection_skills,
)
from ..lint import blocking_findings
from ..simple_yaml import load_mapping
from ..skills.tree import require_canonical_content_tree
from ..state.locking import resource_lock, resource_locks
from ..trust import approval_key_for, load_trust, make_lint_override, set_trust
from .git import commit_paths, git_available, head_content_hash, initialize_repository, path_changes, repository_status
from .metadata import (
    load_library_identity,
    load_library_provenance,
    new_library_identity,
    write_empty_provenance,
    write_library_identity,
)
from .model import LIBRARY_COLLECTION_KIND, LIBRARY_NAMESPACE, LibraryIdentity, LibraryLayout, LibraryRegistration, normalize_skill_name
from .paths import default_library_root, load_library_registration


LIBRARY_INIT_SCHEMA = "skillager.library-init.v1"
LIBRARY_STATUS_SCHEMA = "skillager.library-status.v1"
LIBRARY_NEW_SCHEMA = "skillager.library-new.v1"
LIBRARY_ACCEPT_SCHEMA = "skillager.library-accept.v1"
LIBRARY_WHERE_SCHEMA = "skillager.where.v1"
LIBRARY_RELOCATE_SCHEMA = "skillager.library-relocate.v1"


def initialize_library(catalog_root: Path, *, path: Path | None = None, no_git: bool = False) -> dict[str, Any]:
    created = False
    git_repository_created = False
    commit: dict[str, Any] | None = None
    with resource_lock(catalog_root / "library-init"):
        registration = _registered_library_or_conflict(catalog_root)
        if registration is not None:
            if path is not None and LibraryLayout.from_root(path).root != registration.layout.root:
                raise ValueError(
                    f"a personal skill library is already registered at {registration.layout.root}; relocation is not implicit"
                )
            layout = registration.layout
        else:
            layout = LibraryLayout.from_root(path or default_library_root())
        identity = load_library_identity(layout)
        if registration is not None:
            _validate_registered_library(registration, identity)
            assert identity is not None
            if load_library_provenance(layout) is None:
                raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")
        elif identity is not None:
            _require_library_layout(layout)
            if load_library_provenance(layout) is None:
                raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")
            _validate_identity_git(identity, layout)
        else:
            _preflight_new_library(layout, no_git=no_git)
            layout.root.mkdir(parents=True, exist_ok=True)
            git_mode = "disabled" if no_git else "system"
            if git_mode == "system":
                git_repository_created = initialize_repository(layout.root)
            layout.skills.mkdir(exist_ok=True)
            layout.metadata.mkdir()
            identity = new_library_identity(git_mode=git_mode)
            write_library_identity(layout, identity)
            write_empty_provenance(layout)
            commit_targets = [layout.identity_path, layout.provenance_path]
            if not any(layout.skills.iterdir()):
                keep_path = layout.skills / ".gitkeep"
                keep_path.touch(exist_ok=False)
                commit_targets.append(keep_path)
            if git_mode == "system":
                commit = commit_paths(
                    layout.root,
                    commit_targets,
                    "Initialize Skillager personal library",
                )
            created = True
        register_library_collection(catalog_root, layout.root, identity.library_id)
        index = refresh_collection(catalog_root, LIBRARY_NAMESPACE)
    status = library_status(catalog_root)
    return {
        "schema": LIBRARY_INIT_SCHEMA,
        "status": "initialized" if created else "already-initialized",
        "created": created,
        "git_repository_created": git_repository_created,
        "commit": commit,
        "indexed": len(index.get("skills", [])),
        "errors": index.get("errors", []),
        "library": status["library"],
        "git": status["git"],
        "history": status["history"],
        "warnings": status["warnings"],
        "advisories": status["advisories"],
    }


def library_relocation_preview(catalog_root: Path, path: Path) -> dict[str, Any]:
    registration = _registered_library_or_conflict(catalog_root)
    if registration is None:
        raise ValueError("personal skill library is not initialized; run `skillager library init`")
    candidate = LibraryLayout.from_root(path)
    if candidate.root == registration.layout.root:
        raise ValueError("relocation path is already the registered personal library")
    _require_library_layout(candidate)
    identity = load_library_identity(candidate)
    if identity is None or identity.library_id != registration.library_id:
        raise ValueError("relocation candidate does not have the registered personal-library identity")
    if load_library_provenance(candidate) is None:
        raise ValueError(f"library provenance metadata is missing: {candidate.provenance_path}")
    if identity.git_mode == "system":
        git = repository_status(candidate.root, mode=identity.git_mode)
        if not git.get("available") or not git.get("repository"):
            raise ValueError("Git-backed relocation candidate is not its own Git working tree")
    return {
        "schema": LIBRARY_RELOCATE_SCHEMA,
        "status": "preview",
        "library_id": registration.library_id,
        "from_path": str(registration.layout.root),
        "to_path": str(candidate.root),
        "next_command_argv": ["skillager", "library", "relocate", "--path", str(candidate.root), "--yes"],
    }


def relocate_library(catalog_root: Path, path: Path) -> dict[str, Any]:
    with resource_lock(catalog_root / "library-init"):
        preview = library_relocation_preview(catalog_root, path)
        relocate_library_collection(catalog_root, Path(preview["to_path"]), str(preview["library_id"]))
        index = refresh_collection(catalog_root, LIBRARY_NAMESPACE)
    status = library_status(catalog_root)
    preview.pop("next_command_argv", None)
    return {
        **preview,
        "status": "relocated",
        "indexed": len(index.get("skills", [])),
        "errors": index.get("errors", []),
        "library": status["library"],
    }


def new_library_skill(catalog_root: Path, name: str) -> dict[str, Any]:
    normalized = normalize_skill_name(name)
    with resource_locks([catalog_root / "library-mutation", catalog_root / f"library-skill-{normalized}"]):
        registration, identity = _require_library_identity(catalog_root)
        layout = registration.layout
        target = layout.skill_root(normalized)
        if target.exists() or target.is_symlink():
            raise ValueError(f"library skill already exists: {LIBRARY_NAMESPACE}/{normalized}")
        git = repository_status(layout.root, mode=identity.git_mode)
        _require_safe_git_mutation(git, allow_target_staged=False)
        with tempfile.TemporaryDirectory(prefix="skillager-library-new-", dir=layout.root.parent) as tmp:
            candidate = Path(tmp) / normalized
            candidate.mkdir()
            skill_path = candidate / "SKILL.md"
            skill_path.write_text(_new_skill_template(normalized), encoding="utf-8")
            os.replace(candidate, target)
        index = refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        skill = _library_skill_entry(catalog_root, normalized)
        return {
            "schema": LIBRARY_NEW_SCHEMA,
            "status": "pending",
            "skill": _compact_library_skill(skill),
            "indexed": len(index.get("skills", [])),
            "next_command_argv": ["skillager", "library", "accept", f"{LIBRARY_NAMESPACE}/{normalized}"],
        }


def library_acceptance_preview(catalog_root: Path, skill_name: str) -> dict[str, Any]:
    registration, identity = _require_library_identity(catalog_root)
    normalized = normalize_skill_name(skill_name)
    require_canonical_content_tree(registration.layout.skill_root(normalized), action="library acceptance")
    skill = _library_skill_entry(catalog_root, skill_name)
    git = repository_status(registration.layout.root, mode=identity.git_mode)
    changes = path_changes(git, registration.layout.root, Path(skill["root"])) if identity.git_mode == "system" else _empty_path_changes()
    lint_blocked = bool(blocking_findings(skill.get("lint")))
    high_risk = skill.get("scan", {}).get("risk") == "high"
    return {
        "schema": LIBRARY_ACCEPT_SCHEMA,
        "status": "preview",
        "skill": _compact_library_skill(skill),
        "lint": _compact_lint(skill.get("lint")),
        "scan": _compact_scan(skill.get("scan")),
        "requires_override": lint_blocked or high_risk,
        "git": {
            "mode": git["mode"],
            "head": git.get("head"),
            "operation": git.get("operation"),
            **changes,
        },
    }


def accept_library_skill(
    catalog_root: Path,
    skill_name: str,
    *,
    expected_hash: str,
    override_lint: bool = False,
    reason: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_skill_name(skill_name)
    resources = [catalog_root / "library-mutation", catalog_root / f"library-skill-{normalized}"]
    with resource_locks(resources):
        registration, identity = _require_library_identity(catalog_root)
        layout = registration.layout
        require_canonical_content_tree(layout.skill_root(normalized), action="library acceptance")
        skill = _library_skill_entry(catalog_root, normalized)
        working_hash = str(skill["content_hash"])
        if working_hash != expected_hash:
            raise ValueError("library skill changed since the acceptance preview; review it again and rerun `library accept`")
        lint_override, risk_override = _acceptance_overrides(
            skill,
            override_lint=override_lint,
            reason=reason,
        )
        approval_key = _library_approval_key(skill)
        commit = None
        head_hash = None
        if identity.git_mode == "system":
            git = repository_status(layout.root, mode=identity.git_mode)
            target = Path(skill["root"])
            commit_targets = [target]
            provenance = load_library_provenance(layout)
            if isinstance(provenance, dict) and normalized in provenance.get("skills", {}):
                commit_targets.append(layout.provenance_path)
            changes = _merge_path_changes(
                *(path_changes(git, layout.root, path) for path in commit_targets)
            )
            _require_safe_git_mutation(git, allow_target_staged=True, target_changes=changes)
            head_hash = head_content_hash(layout.root, target)
            if any(changes.values()) or head_hash != working_hash:
                commit = commit_paths(
                    layout.root,
                    commit_targets,
                    f"Accept library skill {normalized}",
                    allow_staged_paths=True,
                )
                head_hash = head_content_hash(layout.root, target)
            if head_hash != working_hash:
                raise ValueError("library Git HEAD does not reproduce the accepted Skillager content hash")
        record = set_trust(
            catalog_root,
            skill["id"],
            "reviewed",
            working_hash,
            skill["source"],
            lint=skill.get("lint"),
            lint_override=lint_override,
            risk_override=risk_override,
            reason=(reason or "").strip() or None,
            approval_key=approval_key,
            approval_root=catalog_root,
            global_scope=True,
        )
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        where = library_where(catalog_root, normalized, project_dir=project_dir)["skill"]
        return {
            "schema": LIBRARY_ACCEPT_SCHEMA,
            "status": "accepted",
            "skill": where,
            "approval": {
                "state": record["state"],
                "scope": record["scope"],
                "content_hash": record["content_hash"],
                "lint_override": record.get("lint_override"),
                "risk_override": record.get("risk_override"),
            },
            "commit": commit,
        }


def library_where(catalog_root: Path, skill_name: str, *, project_dir: Path | None = None) -> dict[str, Any]:
    registration, identity = _require_library_identity(catalog_root)
    git = repository_status(registration.layout.root, mode=identity.git_mode)
    skill = _skill_status(
        catalog_root,
        registration,
        identity,
        skill_name,
        git,
        project_dir=project_dir,
    )
    return {"schema": LIBRARY_WHERE_SCHEMA, "skill": skill}


def library_status(
    catalog_root: Path,
    *,
    skill_name: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration = _registered_library_or_conflict(catalog_root)
    if registration is None:
        return {
            "schema": LIBRARY_STATUS_SCHEMA,
            "status": "not-initialized",
            "initialized": False,
            "library": None,
            "git": None,
            "history": {"available": False, "reason": "not-initialized"},
            "counts": {"skills": 0},
            "skill": None,
            "warnings": [],
            "advisories": [],
            "next_command_argv": ["skillager", "library", "init"],
        }

    layout = registration.layout
    warnings: list[str] = []
    identity: LibraryIdentity | None = None
    if not layout.root.is_dir():
        warnings.append(f"registered library path is missing: {layout.root}")
    else:
        try:
            identity = load_library_identity(layout)
        except ValueError as exc:
            warnings.append(str(exc))
        if identity is None:
            warnings.append(f"library identity is missing: {layout.identity_path}")
        elif identity.library_id != registration.library_id:
            warnings.append("library identity does not match the catalog registration")
        if layout.skills.is_symlink() or not layout.skills.is_dir():
            warnings.append(f"library skills path is missing or unsafe: {layout.skills}")
        try:
            if load_library_provenance(layout) is None:
                warnings.append(f"library provenance metadata is missing: {layout.provenance_path}")
        except ValueError as exc:
            warnings.append(str(exc))

    git_mode = identity.git_mode if identity is not None else "disabled"
    git = repository_status(layout.root, mode=git_mode) if layout.root.is_dir() else _missing_git_status(git_mode)
    history = _history_availability(identity, git) if identity is not None else {"available": False, "reason": "identity-missing"}
    advisories: list[str] = []
    if git.get("error"):
        warnings.append(str(git["error"]))
    if git.get("conflicts"):
        warnings.append("library Git repository has unresolved conflicts")
    if git.get("operation"):
        warnings.append(f"library Git repository has an in-progress {git['operation']} operation")
    if identity is not None and identity.git_mode == "disabled":
        advisories.append("library history is disabled (--no-git); ordinary ownership remains available")
    elif identity is not None and git.get("repository"):
        if not git.get("remote"):
            advisories.append("library has no Git remote; consider a private backup remote")
        if any(git.get(key) for key in ("staged", "unstaged", "untracked")):
            advisories.append("library Git repository has uncommitted changes")
    names, path_warnings = _library_skill_names(layout)
    warnings.extend(path_warnings)
    selected = (
        _skill_status(catalog_root, registration, identity, skill_name, git, project_dir=project_dir)
        if skill_name is not None and identity is not None
        else None
    )
    result = {
        "schema": LIBRARY_STATUS_SCHEMA,
        "status": "ready" if not warnings else "degraded",
        "initialized": True,
        "library": {
            "schema": identity.schema if identity is not None else None,
            "library_id": registration.library_id,
            "namespace": LIBRARY_NAMESPACE,
            "root": str(layout.root),
            "skills_path": str(layout.skills),
            "created_at": identity.created_at if identity is not None else None,
            "registration": "valid" if identity is not None and identity.library_id == registration.library_id else "mismatch",
        },
        "git": git,
        "history": history,
        "counts": {"skills": len(names)},
        "skill": selected,
        "warnings": warnings,
        "advisories": advisories,
    }
    if not layout.root.is_dir():
        result["next_command_argv"] = [
            "skillager",
            "library",
            "relocate",
            "--path",
            "<moved-library-path>",
        ]
    return result


def _registered_library_or_conflict(catalog_root: Path) -> LibraryRegistration | None:
    value = load_collections(catalog_root).get("collections", {}).get(LIBRARY_NAMESPACE)
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") != LIBRARY_COLLECTION_KIND:
        raise ValueError(
            "collection name 'lib' is already in use; remove or rename it before initializing the personal library"
        )
    return load_library_registration(catalog_root)


def _require_library_identity(catalog_root: Path) -> tuple[LibraryRegistration, LibraryIdentity]:
    registration = _registered_library_or_conflict(catalog_root)
    if registration is None:
        raise ValueError("personal skill library is not initialized; run `skillager library init`")
    _require_library_layout(registration.layout)
    identity = load_library_identity(registration.layout)
    if identity is None:
        raise ValueError(f"library identity is missing: {registration.layout.identity_path}")
    if identity.library_id != registration.library_id:
        raise ValueError("library identity does not match the catalog registration")
    if load_library_provenance(registration.layout) is None:
        raise ValueError(f"library provenance metadata is missing: {registration.layout.provenance_path}")
    return registration, identity


def _validate_registered_library(registration: LibraryRegistration, identity: LibraryIdentity | None) -> None:
    _require_library_layout(registration.layout)
    if identity is None:
        raise ValueError(
            f"registered library identity is missing: {registration.layout.identity_path}; run `skillager doctor` for repair guidance"
        )
    if identity.library_id != registration.library_id:
        raise ValueError("registered library identity does not match the catalog registration")
    _validate_identity_git(identity, registration.layout)


def _validate_identity_git(identity: LibraryIdentity, layout: LibraryLayout) -> None:
    if identity.git_mode == "system":
        if not git_available():
            raise ValueError("git executable is unavailable for this Git-backed library")
        status = repository_status(layout.root, mode=identity.git_mode)
        if not status["repository"]:
            raise ValueError("Git-backed library path is not its own Git working tree")
        if status["conflicts"]:
            raise ValueError("library Git repository has unresolved conflicts")
        if status["operation"]:
            raise ValueError(f"library Git repository has an in-progress {status['operation']} operation")
        if status["staged"]:
            raise ValueError("library Git repository has staged changes; commit or unstage them before initializing")


def _preflight_new_library(layout: LibraryLayout, *, no_git: bool) -> None:
    if layout.root.exists() and not layout.root.is_dir():
        raise ValueError(f"library root is not a directory: {layout.root}")
    if layout.root.is_dir():
        if layout.metadata.exists() or layout.metadata.is_symlink():
            raise ValueError(f"library metadata path already exists without a valid identity: {layout.metadata}")
        if layout.skills.is_symlink() or (layout.skills.exists() and not layout.skills.is_dir()):
            raise ValueError(f"library skills path must be a non-symlinked directory: {layout.skills}")
    if not no_git and not git_available():
        raise ValueError("git executable is unavailable; install Git or rerun with --no-git")


def _require_library_layout(layout: LibraryLayout) -> None:
    if not layout.root.is_dir():
        raise ValueError(f"registered library path is missing: {layout.root}")
    if layout.skills.is_symlink() or not layout.skills.is_dir():
        raise ValueError(f"library skills path must be a non-symlinked directory: {layout.skills}")


def _library_skill_names(layout: LibraryLayout) -> tuple[list[str], list[str]]:
    if layout.skills.is_symlink() or not layout.skills.is_dir():
        return [], []
    names: list[str] = []
    warnings: list[str] = []
    try:
        entries = sorted(layout.skills.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        return [], [f"could not read library skills: {exc}"]
    for entry in entries:
        if entry.is_symlink():
            warnings.append(f"ignoring symlinked library skill path: {entry}")
            continue
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            try:
                normalized = normalize_skill_name(entry.name)
            except ValueError as exc:
                warnings.append(f"invalid library skill path {entry}: {exc}")
                continue
            if normalized != entry.name:
                warnings.append(f"library skill directory must use its canonical slug: {entry}")
                continue
            names.append(normalized)
    return names, warnings


def _skill_status(
    catalog_root: Path,
    registration: LibraryRegistration,
    identity: LibraryIdentity,
    value: str,
    git: dict[str, Any],
    *,
    project_dir: Path | None,
) -> dict[str, Any]:
    skill = _library_skill_entry(catalog_root, value)
    root = Path(skill["root"])
    approval_key = _library_approval_key(skill)
    approval = load_trust(catalog_root).get("global_approvals", {}).get(approval_key)
    accepted_hash = approval.get("content_hash") if isinstance(approval, dict) else None
    working_hash = str(skill["content_hash"])
    head_hash = head_content_hash(registration.layout.root, root) if identity.git_mode == "system" and git.get("repository") else None
    git_paths = path_changes(git, registration.layout.root, root) if identity.git_mode == "system" else _empty_path_changes()
    acceptance = _acceptance_state(skill, working_hash=working_hash, accepted_hash=accepted_hash)
    if git.get("operation") or git_paths["conflicts"]:
        state = "conflicted"
    elif identity.git_mode == "disabled":
        state = "no_git"
    elif acceptance != "accepted":
        state = "pending"
    elif head_hash != working_hash or any(git_paths.values()):
        state = "accepted_uncommitted"
    else:
        state = "clean"
    result = {
        "id": skill["id"],
        "name": skill.get("name") or skill["id"],
        "summary": skill.get("summary"),
        "path": str(root),
        "entrypoint": skill.get("entrypoint"),
        "status": state,
        "acceptance": acceptance,
        "working_hash": working_hash,
        "accepted_hash": accepted_hash,
        "head_hash": head_hash,
        "lint": _compact_lint(skill.get("lint")),
        "scan": _compact_scan(skill.get("scan")),
        "git": git_paths,
        "history": _history_availability(identity, git),
        "exposures": _library_exposures(project_dir, str(skill["id"])),
    }
    if skill.get("imported_from"):
        result["imported_from"] = skill["imported_from"]
    return result


def _history_availability(identity: LibraryIdentity, git: dict[str, Any]) -> dict[str, Any]:
    if identity.git_mode == "disabled":
        return {"available": False, "reason": "no-git"}
    if not git.get("available"):
        return {"available": False, "reason": "git-unavailable"}
    if not git.get("repository"):
        return {"available": False, "reason": "repository-unavailable"}
    if git.get("conflicts"):
        return {"available": False, "reason": "conflicted"}
    if git.get("operation"):
        return {"available": False, "reason": f"operation-in-progress:{git['operation']}"}
    if not git.get("head"):
        return {"available": False, "reason": "no-commits"}
    return {"available": True, "reason": None}


def _missing_git_status(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "available": git_available(),
        "repository": False,
        "clean": None,
        "branch": None,
        "head": None,
        "operation": None,
        "conflicts": [],
        "staged": [],
        "unstaged": [],
        "untracked": [],
        "remote": None,
        "commit_identity": None,
    }


def _library_skill_entry(catalog_root: Path, value: str) -> dict[str, Any]:
    name = normalize_skill_name(value)
    skill_id = f"{LIBRARY_NAMESPACE}/{name}"
    skills = select_collection_skills(
        catalog_root,
        LIBRARY_NAMESPACE,
        trust_root=catalog_root,
        approval_root=catalog_root,
        include_blocked=True,
        include_lint_blocked=True,
    )
    for skill in skills:
        if skill.get("id") == skill_id:
            result = dict(skill)
            registration = load_library_registration(catalog_root)
            provenance = load_library_provenance(registration.layout) if registration is not None else None
            record = (provenance or {}).get("skills", {}).get(name)
            if isinstance(record, dict):
                if isinstance(record.get("imported_from"), dict):
                    result["imported_from"] = dict(record["imported_from"])
            return result
    raise ValueError(f"library skill not found: {skill_id}")


def _library_approval_key(skill: dict[str, Any]) -> str:
    key = skill.get("approval_key") or approval_key_for(
        str(skill["id"]),
        skill.get("root"),
        skill.get("source"),
        entrypoint=skill.get("entrypoint"),
    )
    if not isinstance(key, str) or not key.startswith("library:"):
        raise ValueError(f"library skill is missing a stable approval key: {skill.get('id')}")
    return key


def _compact_library_skill(skill: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
        "path": skill.get("root"),
        "skill_file": skill.get("entrypoint"),
        "working_hash": skill.get("content_hash"),
        "trust": skill.get("trust"),
    }
    if skill.get("imported_from"):
        result["imported_from"] = skill["imported_from"]
    return result


def _compact_lint(lint: dict[str, Any] | None) -> dict[str, Any]:
    lint = lint or {"status": "ok", "findings": []}
    return {
        "status": lint.get("status", "ok"),
        "blocking_count": len(blocking_findings(lint)),
        "findings": [
            {key: item.get(key) for key in ("code", "severity", "field", "detail", "rule_key") if item.get(key) is not None}
            for item in lint.get("findings", [])
            if isinstance(item, dict)
        ],
    }


def _compact_scan(scan: dict[str, Any] | None) -> dict[str, Any]:
    scan = scan or {"risk": "unknown", "findings": []}
    return {
        "risk": scan.get("risk", "unknown"),
        "finding_count": len(scan.get("findings", [])),
        "findings": [
            {key: item.get(key) for key in ("code", "severity", "path", "line", "message") if item.get(key) is not None}
            for item in scan.get("findings", [])
            if isinstance(item, dict)
        ],
    }


def _acceptance_overrides(
    skill: dict[str, Any],
    *,
    override_lint: bool,
    reason: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    blocking = blocking_findings(skill.get("lint"))
    high_risk = skill.get("scan", {}).get("risk") == "high"
    if override_lint and not (reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if (blocking or high_risk) and not override_lint:
        causes = []
        if blocking:
            causes.append("lint-blocking findings")
        if high_risk:
            causes.append("high-risk scanner findings")
        raise ValueError(f"library acceptance has {' and '.join(causes)}; use --override-lint --reason <text>")
    lint_override = make_lint_override(reason or "", skill.get("lint") or {}) if blocking else None
    risk_override = None
    if high_risk:
        risk_override = {
            "reason": (reason or "").strip(),
            "at": datetime.now(timezone.utc).isoformat(),
            "findings": _compact_scan(skill.get("scan"))["findings"],
        }
    return lint_override, risk_override


def _require_safe_git_mutation(
    git: dict[str, Any],
    *,
    allow_target_staged: bool,
    target_changes: dict[str, list[str]] | None = None,
) -> None:
    if git.get("mode") == "disabled":
        return
    if not git.get("available") or not git.get("repository"):
        raise ValueError(git.get("error") or "Git-backed library repository is unavailable")
    if git.get("conflicts"):
        raise ValueError("library Git repository has unresolved conflicts")
    if git.get("operation"):
        raise ValueError(f"library Git repository has an in-progress {git['operation']} operation")
    staged = list(git.get("staged") or [])
    if staged:
        target_staged = set((target_changes or {}).get("staged", []))
        if not allow_target_staged or any(path not in target_staged for path in staged):
            raise ValueError("library Git repository has unrelated staged changes; commit or unstage them first")


def _acceptance_state(skill: dict[str, Any], *, working_hash: str, accepted_hash: object) -> str:
    if skill.get("trust") == "blocked":
        return "blocked"
    if blocking_findings(skill.get("lint")) and skill.get("trust") == "lint_blocked":
        return "lint_blocked"
    return "accepted" if accepted_hash == working_hash and skill.get("trust") in {"reviewed", "trusted", "pinned"} else "pending"


def _empty_path_changes() -> dict[str, list[str]]:
    return {"conflicts": [], "staged": [], "unstaged": [], "untracked": []}


def _merge_path_changes(*changes: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: sorted({path for change in changes for path in change.get(key, [])})
        for key in ("conflicts", "staged", "unstaged", "untracked")
    }


def _new_skill_template(name: str) -> str:
    title = " ".join(part.capitalize() for part in name.split("-"))
    return "\n".join(
        [
            f"# {title}",
            "",
            "Use this skill when the task clearly matches this workflow.",
            "",
            "## Instructions",
            "",
            "- Replace this placeholder with the workflow, constraints, and examples.",
            "- Keep activation guidance specific enough that agents know when not to use it.",
            "",
        ]
    )


def _library_exposures(project_dir: Path | None, skill_id: str) -> list[dict[str, Any]]:
    if project_dir is None:
        return []
    project = project_dir.resolve()
    roots = {
        "codex": [project / ".agents" / "skills", project / ".agents" / "codex" / "skills", project / ".codex" / "skills"],
        "claude": [project / ".claude" / "skills", project / ".agents" / "claude" / "skills"],
    }
    exposures: list[dict[str, Any]] = []
    for default_agent, bases in roots.items():
        for base in bases:
            if not base.is_dir():
                continue
            for sidecar in base.glob("*/skillager.materialized.yaml"):
                try:
                    data = load_mapping(sidecar)
                except Exception:
                    continue
                source_type = data.get("source_type")
                is_router_member = source_type == "skillager-router" and skill_id in set(data.get("skill_ids") or [])
                is_direct = (data.get("source_id") or data.get("id")) == skill_id
                if not (is_router_member or is_direct):
                    continue
                kind = "router" if is_router_member else "stub" if source_type == "skillager-stub" else "native"
                exposures.append(
                    {
                        "agent": data.get("agent") or default_agent,
                        "scope": data.get("scope") or "project",
                        "kind": kind,
                        "path": str(sidecar.parent.resolve()),
                        "source_hash": None if is_router_member else data.get("source_hash"),
                        "router": data.get("router_slug") if is_router_member else None,
                    }
                )
    return sorted(exposures, key=lambda item: (str(item["agent"]), str(item["kind"]), str(item["path"])))


__all__ = [
    "LIBRARY_INIT_SCHEMA",
    "LIBRARY_ACCEPT_SCHEMA",
    "LIBRARY_NEW_SCHEMA",
    "LIBRARY_RELOCATE_SCHEMA",
    "LIBRARY_STATUS_SCHEMA",
    "LIBRARY_WHERE_SCHEMA",
    "accept_library_skill",
    "initialize_library",
    "library_acceptance_preview",
    "library_relocation_preview",
    "library_status",
    "library_where",
    "new_library_skill",
    "relocate_library",
]
