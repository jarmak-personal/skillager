"""Exact new-import preservation evidence, stored only with canonical acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..exposure.target_state import target_state_manifest
from ..state.trust import content_hash
from .source_identity import origin, source_identity
from .sync_lineage import canonical_target_state


IMPORT_PRESERVATION_SCHEMA = "skillager.import-preservation.v1"


def import_preservation(source: dict[str, Any], project: Path | None, target: Path,
                        library_id: str, library_root: Path, skill_id: str,
                        accepted_hash: str) -> dict[str, Any] | None:
    """Filtered copies remain useful, but uncovered originals get no proof.

    Use the existing complete-state limits and owners. Root modes are bound
    independently: adoption retains the deployment root, not the library root.
    """
    native = source.get("native") or {}
    if project is None or native.get("scope") != "project" or native.get("managed"):
        return None
    original = Path(source["root"])
    if original.is_symlink() or not original.resolve().is_relative_to(project.resolve()):
        return None
    try:
        source_state = canonical_target_state(original)
        target_state = canonical_target_state(target)
        if source_state["tree"] != target_state["tree"]:
            return None
        # Explicitly compare complete manifests as well as their stable hashes.
        if target_state_manifest(original, max_entries=512, max_bytes=32 * 1024 * 1024) != target_state_manifest(target, max_entries=512, max_bytes=32 * 1024 * 1024):
            return None
        if content_hash(original) != accepted_hash or content_hash(target) != accepted_hash:
            return None
        if canonical_target_state(original) != source_state or canonical_target_state(target) != target_state:
            return None
    except (OSError, ValueError):
        return None
    source_id = source_identity(source)
    occurrence = origin(source, project, source_id=source_id)
    # Inventory display IDs can gain collision suffixes. Physical identity,
    # provider, project and provenance remain exact and cannot be inferred by name.
    occurrence.pop("skill_id")
    payload = {
        "schema": IMPORT_PRESERVATION_SCHEMA, "source_identity": source_id,
        "origin": occurrence, "source_state": source_state,
        "canonical": {"library_id": library_id, "library_root": str(library_root.resolve()),
                      "skill_id": skill_id, "path": str(target.resolve()), "accepted_hash": accepted_hash,
                      "target_state": target_state},
    }
    digest = hashlib.sha256(b"skillager.import-preservation.v1\0")
    digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return {**payload, "proof_id": digest.hexdigest()}
