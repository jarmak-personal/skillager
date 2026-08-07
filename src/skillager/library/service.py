from __future__ import annotations

from pathlib import Path
from typing import Any

from ..catalog.impl import load_collections, refresh_collection, register_library_collection
from ..schema import load_skill_from_dir
from ..state.locking import resource_lock
from ..trust import content_hash
from .git import commit_paths, git_available, initialize_repository, repository_status
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
        "warnings": status["warnings"],
    }


def library_status(catalog_root: Path, *, skill_name: str | None = None) -> dict[str, Any]:
    registration = _registered_library_or_conflict(catalog_root)
    if registration is None:
        return {
            "schema": LIBRARY_STATUS_SCHEMA,
            "status": "not-initialized",
            "initialized": False,
            "library": None,
            "git": None,
            "counts": {"skills": 0},
            "skill": None,
            "warnings": [],
            "next_command": "skillager library init",
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
    if git.get("error"):
        warnings.append(str(git["error"]))
    if git.get("conflicts"):
        warnings.append("library Git repository has unresolved conflicts")
    names, path_warnings = _library_skill_names(layout)
    warnings.extend(path_warnings)
    selected = _skill_status(layout, skill_name, git) if skill_name is not None else None
    return {
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
        "counts": {"skills": len(names)},
        "skill": selected,
        "warnings": warnings,
        "next_command": None,
    }


def _registered_library_or_conflict(catalog_root: Path) -> LibraryRegistration | None:
    value = load_collections(catalog_root).get("collections", {}).get(LIBRARY_NAMESPACE)
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") != LIBRARY_COLLECTION_KIND:
        raise ValueError(
            "collection name 'lib' is already in use; remove or rename it before initializing the personal library"
        )
    return load_library_registration(catalog_root)


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


def _skill_status(layout: LibraryLayout, value: str, git: dict[str, Any]) -> dict[str, Any]:
    name = normalize_skill_name(value)
    root = layout.skill_root(name)
    if root.is_symlink() or not root.is_dir() or not (root / "SKILL.md").is_file():
        raise ValueError(f"library skill not found: {LIBRARY_NAMESPACE}/{name}")
    source = {
        "type": "library",
        "collection": LIBRARY_NAMESPACE,
        "library_root": str(layout.root),
    }
    skill = load_skill_from_dir(root, source)
    prefix = f"skills/{name}/"
    git_paths = {
        key: [path for path in git.get(key, []) if path == f"skills/{name}" or path.startswith(prefix)]
        for key in ("conflicts", "staged", "unstaged", "untracked")
    }
    return {
        "id": f"{LIBRARY_NAMESPACE}/{name}",
        "name": skill.name,
        "summary": skill.summary,
        "path": str(root),
        "working_hash": content_hash(root),
        "git": git_paths,
    }


def _missing_git_status(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "available": git_available(),
        "repository": False,
        "clean": None,
        "branch": None,
        "head": None,
        "conflicts": [],
        "staged": [],
        "unstaged": [],
        "untracked": [],
        "remote": None,
        "commit_identity": None,
    }


__all__ = [
    "LIBRARY_INIT_SCHEMA",
    "LIBRARY_STATUS_SCHEMA",
    "initialize_library",
    "library_status",
]
