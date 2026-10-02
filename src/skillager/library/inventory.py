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


def library_inventory(catalog: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every owned skill, including drafts and quarantined metadata, without a project."""
    registration = load_library_registration(catalog)
    if registration is None:
        return [], {"catalog": str(catalog), "library": None, "skills": []}
    identity = load_library_identity(registration.layout)
    if identity is None or identity.library_id != registration.library_id:
        raise ValueError("registered library identity is missing or does not match the catalog registration")
    rows = []
    state = []
    with trust_snapshot([catalog]):
        skills = select_collection_skills(
            catalog, LIBRARY_NAMESPACE, trust_root=catalog, approval_root=catalog,
            include_blocked=True, include_lint_blocked=True,
        )
        for skill in sorted(skills, key=lambda item: item["id"]):
            approval = trust_record(catalog, "global_approvals", _library_approval_key(skill)) or {}
            accepted_hash = approval.get("content_hash")
            acceptance = _acceptance_state(skill, working_hash=skill["content_hash"], accepted_hash=accepted_hash)
            skill_file = Path(skill["root"]) / "SKILL.md"
            try:
                declared = skill_frontmatter_metadata(skill_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                declared = {}
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
        "catalog": str(catalog), "library": registration.to_mapping(), "skills": state,
    }
