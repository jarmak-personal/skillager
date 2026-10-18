from __future__ import annotations

from pathlib import Path
from typing import Any

from ..catalog.impl import collection_index_skills
from ..trust import trust_record, trust_snapshot
from .metadata import load_library_identity, load_library_provenance
from .browse import library_metadata_index
from .model import LIBRARY_NAMESPACE
from .paths import load_library_registration
from .service import _acceptance_state, _library_approval_key, _safe_metadata_summary


class LibraryInventoryUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("personal library inventory could not be read completely")


def library_inventory(catalog: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every owned skill, including drafts and quarantined metadata, without a project."""
    _skills, state = library_metadata_observation(catalog)
    return [{key: value for key, value in row.items() if key != "working_hash"} for row in state["skills"]], state


def library_metadata_observation(catalog: Path, *, require_complete: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Observe full library metadata and its hidden-row snapshot together."""
    registration = load_library_registration(catalog)
    if registration is None:
        return [], {"catalog": str(catalog), "library": None, "skills": [], "errors": []}
    identity = load_library_identity(registration.layout)
    if identity is None or identity.library_id != registration.library_id:
        raise ValueError("registered library identity is missing or does not match the catalog registration")
    state = []
    errors: list[dict[str, str]] = []
    with trust_snapshot([catalog]):
        try:
            data = library_metadata_index(catalog, LIBRARY_NAMESPACE, registration.to_mapping(), require_complete=require_complete)
            skills = collection_index_skills(catalog, data, trust_root=catalog, approval_root=catalog)
            errors.extend(data.get("errors", []))
            error_roots = {str(Path(error["path"]).resolve()) for error in errors}
            indexed_roots = {str(Path(skill["root"]).resolve()) for skill in skills} if error_roots else set()
        except (OSError, ValueError) as exc:
            raise LibraryInventoryUnavailable() from exc
        if require_complete and not error_roots <= indexed_roots:
            raise LibraryInventoryUnavailable()
        for skill in sorted(skills, key=lambda item: item["id"]):
            approval = trust_record(catalog, "global_approvals", _library_approval_key(skill)) or {}
            accepted_hash = approval.get("content_hash")
            acceptance = _acceptance_state(skill, working_hash=skill["content_hash"], accepted_hash=accepted_hash)
            skill_file = Path(skill["root"]) / "SKILL.md"
            declared = skill["_browse_declared"]
            row = {
                "id": skill["id"],
                "name": declared.get("name") or skill["id"].partition("/")[2],
                "description": _safe_metadata_summary(declared.get("description")),
                "status": "blocked" if acceptance in {"blocked", "lint_blocked"} else acceptance,
                "accepted_hash": accepted_hash,
                "skill_file": str(skill_file),
            }
            state.append({**row, "working_hash": skill["content_hash"]})
    return skills, {
        "catalog": str(catalog), "library": registration.to_mapping(), "skills": state, "errors": errors,
        "provenance": load_library_provenance(registration.layout),
    }
