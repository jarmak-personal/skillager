from __future__ import annotations

from pathlib import Path
from typing import Any

from ..catalog.impl import select_collection_skills
from ..schema import skill_frontmatter_metadata
from ..trust import trust_record, trust_snapshot
from .metadata import load_library_identity
from .model import LIBRARY_NAMESPACE
from .paths import load_library_registration
from .service import _acceptance_state, _library_approval_key, _safe_metadata_summary


class LibraryInventoryUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("personal library inventory could not be read completely")


def library_inventory(catalog: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every owned skill, including drafts and quarantined metadata, without a project."""
    registration = load_library_registration(catalog)
    if registration is None:
        return [], {"catalog": str(catalog), "library": None, "skills": [], "errors": []}
    identity = load_library_identity(registration.layout)
    if identity is None or identity.library_id != registration.library_id:
        raise ValueError("registered library identity is missing or does not match the catalog registration")
    rows = []
    state = []
    errors: list[dict[str, str]] = []
    with trust_snapshot([catalog]):
        try:
            skills = select_collection_skills(
                catalog, LIBRARY_NAMESPACE, trust_root=catalog, approval_root=catalog,
                include_blocked=True, include_lint_blocked=True,
                discovery_errors=errors, require_complete_library=True,
            )
            indexed_roots = {str(Path(skill["root"]).resolve()) for skill in skills}
            error_roots = {str(Path(error["path"]).resolve()) for error in errors}
        except (OSError, ValueError) as exc:
            raise LibraryInventoryUnavailable() from exc
        if not error_roots <= indexed_roots:
            raise LibraryInventoryUnavailable()
        for skill in sorted(skills, key=lambda item: item["id"]):
            approval = trust_record(catalog, "global_approvals", _library_approval_key(skill)) or {}
            accepted_hash = approval.get("content_hash")
            acceptance = _acceptance_state(skill, working_hash=skill["content_hash"], accepted_hash=accepted_hash)
            skill_file = Path(skill["root"]) / "SKILL.md"
            try:
                declared = skill_frontmatter_metadata(skill_file.read_text(encoding="utf-8"))
            except OSError as exc:
                raise LibraryInventoryUnavailable() from exc
            except UnicodeError as exc:
                declared = {}
                errors.append({"path": str(skill["root"]), "error": str(exc)})
            row = {
                "id": skill["id"],
                "name": declared.get("name") or skill["id"].partition("/")[2],
                "description": _safe_metadata_summary(declared.get("description")),
                "status": "blocked" if acceptance in {"blocked", "lint_blocked"} else acceptance,
                "accepted_hash": accepted_hash,
                "skill_file": str(skill_file),
            }
            rows.append(row)
            state.append({**row, "working_hash": skill["content_hash"]})
    return rows, {
        "catalog": str(catalog), "library": registration.to_mapping(), "skills": state, "errors": errors,
    }
