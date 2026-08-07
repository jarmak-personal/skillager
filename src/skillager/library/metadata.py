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


__all__ = [
    "LIBRARY_PROVENANCE_SCHEMA",
    "empty_provenance",
    "load_library_identity",
    "load_library_provenance",
    "new_library_identity",
    "write_empty_provenance",
    "write_library_identity",
]
