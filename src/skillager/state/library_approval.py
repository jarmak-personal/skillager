from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..lint import valid_lint_override
from . import approvals
from .trust import APPROVED_TRUST_STATES, _record_trust_info, approval_key_for, content_hash


def approval_witness(
    skill: dict[str, Any], project: Path, catalog: Path,
    records: dict[Path, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve the actual effective decision using the existing trust precedence."""
    skill_id = str(skill["id"])
    candidates = [(project, "skills", skill_id, "project")]
    # A live project decision already wins; avoid rewalking its repository for a
    # global key that cannot affect this effective witness.
    direct = records[project.resolve()].get("skills", {}).get(skill_id)
    if not _record_trust_info(direct, skill["content_hash"], lint=skill.get("lint"), scope="project"):
        key = skill.get("approval_key") or approval_key_for(
            skill_id, skill["root"], skill.get("source"), entrypoint=skill.get("entrypoint"),
        )
        if key:
            candidates.append((catalog, "global_approvals", key, "global"))
        alias = skill.get("_decision_skill_id")
        if isinstance(alias, str) and alias != skill_id:
            candidates.append((project, "skills", alias, "project"))
    for authority, namespace, record_key, scope in candidates:
        record = records[authority.resolve()].get(namespace, {}).get(record_key)
        info = _record_trust_info(record, skill["content_hash"], lint=skill.get("lint"), scope=scope)
        if not info:
            continue
        if info["state"] not in APPROVED_TRUST_STATES:
            return None
        witness = {
            "authority_root": str(authority.resolve()), "record_scope": namespace,
            "record_key": record_key, "scope": scope,
            "decision_skill_id": record.get("skill_id") or record_key,
            "record": record,
        }
        digest = hashlib.sha256(b"skillager.approval-evidence.v1\0")
        digest.update(json.dumps(witness, sort_keys=True, separators=(",", ":")).encode())
        return {**witness, "evidence_id": digest.hexdigest()}
    return None


def public_approval(witness: dict[str, Any]) -> dict[str, Any]:
    record = witness["record"]
    return {
        "evidence_id": witness["evidence_id"], "decision_skill_id": witness["decision_skill_id"],
        "scope": witness["scope"], "state": record["state"], "content_hash": record["content_hash"],
        "lint_override": bool(record.get("lint_override")), "risk_override": bool(record.get("risk_override")),
    }


def derive_library_approvals(project: Path, catalog: Path, candidates: list[dict[str, Any]], publish: Callable[[], None]) -> None:
    """Carry existing source approval to identical verified canonical bytes only."""
    keys: dict[Path, set[tuple[str, str]]] = {root.resolve(): set() for root in (project, catalog)}
    for item in candidates:
        source = item["source"]
        keys[project.resolve()].add(("skills", source["id"]))
        if source.get("_decision_skill_id"):
            keys[project.resolve()].add(("skills", source["_decision_skill_id"]))
        source_key = source.get("approval_key")
        if not source_key and item["witness"]["scope"] != "project":
            source_key = approval_key_for(source["id"], source["root"], source.get("source"), entrypoint=source.get("entrypoint"))
        if source_key:
            keys[catalog.resolve()].add(("global_approvals", source_key))
        keys[catalog.resolve()].add(("global_approvals", item["approval_key"]))
        for authority in {project.resolve(), catalog.resolve()}:
            keys[authority].add(("skills", item["candidate"]["id"]))
    def derive(records: dict[Path, dict[str, Any]]) -> list[tuple[str, dict[str, Any], dict[str, str]]]:
        # Keep source/canonical decisions locked from pre-publication validation
        # through append-only derivation. Newly entered pins/blocks protect files.
        for item in candidates:
            source, candidate, expected = item["source"], item["candidate"], item["witness"]
            digest = expected["record"]["content_hash"]
            if content_hash(Path(source["root"])) != digest or approval_witness(source, project, catalog, records) != expected:
                raise ValueError("sync source approval changed before publication")
            if content_hash(item["candidate_path"]) != digest or not valid_lint_override(expected["record"], candidate.get("lint")):
                raise ValueError("sync candidate differs from approved content/findings")
            before = records[catalog.resolve()].get("global_approvals", {}).get(item["approval_key"])
            if before != item["previous_approval"]:
                raise ValueError("sync canonical approval changed before publication")
            for authority, previous in item["previous_protections"].items():
                protection = records[authority].get("skills", {}).get(candidate["id"])
                if protection != previous or (protection or {}).get("state") in {"blocked", "pinned"}:
                    raise ValueError("sync canonical protection changed before publication")
            if before and (before["state"] == "blocked" or (before["state"] == "pinned" and before["content_hash"] != digest)):
                raise ValueError("sync preserves blocked and pinned canonical decisions")
        publish()
        changes = []
        for item in candidates:
            source, candidate, expected = item["source"], item["candidate"], item["witness"]
            digest = expected["record"]["content_hash"]
            if content_hash(Path(source["root"])) != digest or approval_witness(source, project, catalog, records) != expected:
                raise ValueError("sync source changed before acceptance")
            if content_hash(item["target"]) != digest:
                raise ValueError("sync canonical content changed before acceptance")
            record = expected["record"]
            key = item["approval_key"]
            before = records[catalog.resolve()].get("global_approvals", {}).get(key)
            state = "pinned" if before and before["state"] == "pinned" else record["state"]
            result = {
                "state": state, "content_hash": digest, "source": candidate["source"],
                "scope": "global", "approval_key": key, "skill_id": candidate["id"],
                "derived_from": item["lineage"],
                "version": item["version"],
            }
            for name in ("lint_override", "risk_override", "reason"):
                if name in record:
                    result[name] = record[name]
            changes.append((key, result, item["version"]))
        return changes
    approvals.derive_library_records(catalog, project, keys, derive)
