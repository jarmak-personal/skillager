from __future__ import annotations

from pathlib import Path
from typing import Any

from ..lint import lint_skill
from ..review_gates import apply_review_metadata
from ..scan import scan_path
from ..schema import QuarantinedSkill, SchemaError, load_skill_from_dir, quarantine_skill_from_dir
from ..trust import content_hash
from .model import LIBRARY_NAMESPACE, LibraryLayout


def index_library_candidate(
    candidate: Path,
    layout: LibraryLayout,
    library_id: str,
    name: str,
) -> dict[str, Any]:
    """Validate a candidate tree with canonical library source provenance."""

    source = {
        "type": "collection",
        "collection": LIBRARY_NAMESPACE,
        "path": str(layout.skills),
        "ownership": "library",
        "library_id": library_id,
        "library_root": str(layout.root),
        "library_skill": name,
    }
    try:
        skill = load_skill_from_dir(candidate, source)
    except (SchemaError, OSError, ValueError) as exc:
        quarantined = quarantine_skill_from_dir(candidate, source, exc)
        if quarantined is None:
            raise ValueError(f"library candidate is not a valid skill: {exc}") from exc
        skill = quarantined
    digest = content_hash(candidate)
    scan = scan_path(candidate, allow_tools=False)
    lint = skill.lint if isinstance(skill, QuarantinedSkill) else lint_skill(skill)
    entry = skill.to_index(digest, scan, "discovered")
    entry["id"] = f"{LIBRARY_NAMESPACE}/{name}"
    entry["source"] = source
    entry["lint"] = lint
    apply_review_metadata(entry)
    return entry


__all__ = ["index_library_candidate"]
