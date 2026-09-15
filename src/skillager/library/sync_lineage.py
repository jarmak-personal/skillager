from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from ..exposure.target_state import target_state_hash, target_state_manifest
from ..skills.tree import require_canonical_content_tree
from ..state.library_approval import approval_witness, public_approval, public_approval_evidence
from ..state.trust import APPROVED_TRUST_STATES, _record_trust_info
from .importing import _source_key


LINEAGE_SCHEMA = "skillager.library-lineage.v1"


def identity(kind: str, *parts: str) -> str:
    digest = hashlib.sha256(f"skillager.sync-{kind}.v1\0".encode())
    digest.update(json.dumps(parts, separators=(",", ":")).encode())
    return digest.hexdigest()


def source_identity(skill: dict[str, Any]) -> str:
    return identity("source", _source_key(skill))


def origin(skill: dict[str, Any], project: Path | None, *, source_id: str) -> dict[str, Any]:
    source = skill.get("source") or {}
    root = str(Path(skill["root"]).resolve())
    entrypoint = str(Path(skill["entrypoint"]).resolve())
    native = skill.get("native")
    return {
        "origin_id": identity("origin", source_id, root, entrypoint),
        "skill_id": skill["id"], "source_type": source.get("type", "unknown"),
        "path": root, "entrypoint": entrypoint,
        "native": ({"agent": native["agent"], "scope": native["scope"],
                    "project_root": str(project.resolve()) if project and native["scope"] == "project" else None} if native else None),
        "provenance": {name: (source[name] in {True, "true", "True"} if name == "editable" else source[name])
                       for name in ("collection", "package", "version", "editable") if name in source},
    }


def canonical_target_state(target: Path) -> dict[str, Any]:
    """Preserve complete destination state separately from approval hashing."""
    require_canonical_content_tree(target, action="library sync")
    entries = target_state_manifest(target)
    if len(entries) > 512 or any(item["type"] not in {"file", "directory"} for item in entries.values()):
        raise ValueError("sync destination has unsupported or excessive entries")
    if any(item.get("size", 0) > 8 * 1024 * 1024 for item in entries.values()):
        raise ValueError("sync destination exceeds file limits")
    if sum(item.get("size", 0) for item in entries.values()) > 32 * 1024 * 1024:
        raise ValueError("sync destination exceeds tree limits")
    return {"tree": target_state_hash(target), "mode": stat.S_IMODE(target.stat().st_mode)}


def canonical_approval_key(library_id: str, name: str) -> str:
    return f"library:{library_id}#{name}"


def lineage_is_bound(stored: dict[str, Any], approval: dict[str, Any] | None) -> bool:
    """An editable provenance record alone does not establish an approved derivation."""
    derivation = (approval or {}).get("derived_from") or {}
    if not isinstance(derivation, dict):
        return False
    private_witness = derivation.get("source_approval")
    try:
        return (derivation.get("lineage") == stored and isinstance(private_witness, dict)
                and public_approval(private_witness) == stored["source_approval"])
    except (KeyError, TypeError):
        return False


def lineage_status(
    stored: dict[str, Any], library: dict[str, Any], target: Path,
    approval: dict[str, Any] | None, working_hash: str | None,
    observed: list[dict[str, Any]], records: dict[Path, dict[str, Any]],
    project_state: Path, catalog: Path, project_dir: Path | None, lint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = stored["source_approval"]
    bound = lineage_is_bound(stored, approval)
    accepted = approval.get("content_hash") if approval else None
    trust = _record_trust_info(approval, working_hash or "", lint=lint, scope="global") or {}
    preservation = "pending"
    reason: str | None = "canonical-pending"
    if working_hash is None:
        preservation, reason = "unavailable", "canonical-missing"
    elif approval and bound and accepted == working_hash == evidence["content_hash"] and trust.get("state") in APPROVED_TRUST_STATES:
        try:
            if canonical_target_state(target) == stored["target_state"]:
                preservation, reason = "verified", None
            else:
                preservation, reason = "conflict", "canonical-customized"
        except (OSError, ValueError):
            preservation, reason = "conflict", "canonical-customized"
    elif approval:
        preservation, reason = "conflict", "canonical-approval-changed"
    origins = {item["origin_id"]: {**item, "observation": {"status": "not-observed", "content_hash": None,
               "trust": None, "approval_evidence_id": None}} for item in stored["origins"]}
    for skill in observed:
        item = origin(skill, project_dir, source_id=stored["source_identity"])
        current = approval_witness(skill, project_state, catalog, records)
        state = "current" if current else "blocked" if skill.get("trust") == "blocked" else "unapproved"
        if current and skill["content_hash"] != evidence["content_hash"]:
            state = "changed"
        item["observation"] = {"status": state, "content_hash": skill["content_hash"],
                               "trust": current["record"]["state"] if current else skill.get("trust"),
                               "approval_evidence_id": current["evidence_id"] if current else None}
        origins[item["origin_id"]] = item
    return {
        "schema": LINEAGE_SCHEMA, "lineage_id": stored["lineage_id"], "source_identity": stored["source_identity"],
        "source_approval": public_approval_evidence(evidence),
        "canonical": {"library_id": library["library_id"], "skill_id": f"lib/{target.name}", "path": str(target),
                      "accepted_hash": accepted, "working_hash": working_hash,
                      "acceptance": "accepted" if accepted == working_hash and trust.get("state") in APPROVED_TRUST_STATES else "pending" if working_hash else "missing",
                      "trust": trust.get("state", "discovered"), "reuse": "all-projects", "git_commit": (approval or {}).get("version", {}).get("git_commit")},
        "origins": list(origins.values()), "preservation": preservation, "reason_code": reason,
    }


def projected_metadata_limits(lineages, selected, items, library_root: Path, observed_origin_ids: set[str], canonical_count: int, inventory_limit: int, result_limit: int) -> str | None:
    """Admit the complete retained origin graph and reserve fixed public result fields."""
    origins = {value["source_identity"]: {item["origin_id"] for item in value["origins"]} for value in lineages}
    for value in selected:
        origins.setdefault(value["item"]["source_identity"], set()).update(item["origin_id"] for item in value["origins"])
    origin_count = len(observed_origin_ids.union(*(values for values in origins.values())))
    if origin_count + canonical_count > inventory_limit:
        return "inventory-limit"
    projection = {"lineages": lineages, "items": items,
                  "selected": [{"origins": value["origins"], "source_approval": public_approval(value["witness"])} for value in selected]}
    # Reserve canonical paths/recovery paths plus 1 KiB of fixed schema/hash/state
    # fields per relation, origin and result, beyond their exact variable metadata.
    reserve = (origin_count + len(items) + len(origins) + 1) * (1024 + len(json.dumps(str(library_root)).encode("utf-8")))
    if len(json.dumps(projection).encode("utf-8")) + reserve > result_limit:
        return "result-limit"
    return None
