from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..statefiles import read_user_json, write_user_json
from .model import LIBRARY_SCHEMA, LibraryIdentity, LibraryLayout


LIBRARY_PROVENANCE_SCHEMA = "skillager.library-provenance.v1"


def new_library_identity(*, git_mode: str) -> LibraryIdentity:
    return LibraryIdentity(
        library_id=str(uuid4()),
        git_mode=git_mode,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def load_library_identity(layout: LibraryLayout) -> LibraryIdentity | None:
    if not layout.identity_path.exists() and not layout.identity_path.is_symlink():
        return None
    return LibraryIdentity.from_mapping(read_user_json(layout.identity_path, {}))


def write_library_identity(layout: LibraryLayout, identity: LibraryIdentity) -> None:
    if identity.schema != LIBRARY_SCHEMA:
        raise ValueError(f"unsupported library schema: {identity.schema!r}")
    write_user_json(layout.identity_path, identity.to_mapping())


def empty_provenance() -> dict[str, Any]:
    return {"schema": LIBRARY_PROVENANCE_SCHEMA, "skills": {}}


def load_library_provenance(layout: LibraryLayout) -> dict[str, Any] | None:
    path = layout.provenance_path
    if not path.exists() and not path.is_symlink():
        return None
    data = read_user_json(path, {})
    if data.get("schema") != LIBRARY_PROVENANCE_SCHEMA or not isinstance(data.get("skills"), dict):
        raise ValueError(f"invalid library provenance metadata: {path}")
    return data


def write_empty_provenance(layout: LibraryLayout) -> None:
    write_user_json(layout.provenance_path, empty_provenance())


def write_library_provenance(layout: LibraryLayout, data: dict[str, Any]) -> None:
    if data.get("schema") != LIBRARY_PROVENANCE_SCHEMA or not isinstance(data.get("skills"), dict):
        raise ValueError("invalid library provenance metadata")
    write_user_json(layout.provenance_path, data)


def set_import_provenance(
    layout: LibraryLayout,
    name: str,
    *,
    source_key: str,
    source_skill: str,
    source_hash: str,
    source_type: str,
    imported_at: str,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = load_library_provenance(layout)
    if data is None:
        raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")
    if expected is not None and data != expected:
        raise ValueError("library provenance changed during import; review and retry")
    skills = data["skills"]
    if name in skills:
        raise ValueError(f"library provenance already exists for: {name}")
    entry = {
        "artifact_kind": "skill",
        "imported_from": {
            "source_key": source_key,
            "skill_id": source_skill,
            "content_hash": source_hash,
            "source_type": source_type,
        },
        "imported_at": imported_at,
    }
    skills[name] = entry
    write_library_provenance(layout, data)
    return entry


def set_fork_provenance(
    layout: LibraryLayout,
    name: str,
    *,
    source_skill: str,
    source_hash: str,
    created_at: str,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = load_library_provenance(layout)
    if data is None:
        raise ValueError(f"library provenance metadata is missing: {layout.provenance_path}")
    if expected is not None and data != expected:
        raise ValueError("library provenance changed during fork; review and retry")
    skills = data["skills"]
    if name in skills:
        raise ValueError(f"library provenance already exists for: {name}")
    entry = {
        "artifact_kind": "skill",
        "forked_from": {
            "skill": source_skill,
            "hash": source_hash,
        },
        "created_at": created_at,
    }
    skills[name] = entry
    write_library_provenance(layout, data)
    return entry


__all__ = [
    "LIBRARY_PROVENANCE_SCHEMA",
    "empty_provenance",
    "load_library_identity",
    "load_library_provenance",
    "new_library_identity",
    "set_fork_provenance",
    "set_import_provenance",
    "write_empty_provenance",
    "write_library_identity",
    "write_library_provenance",
]
