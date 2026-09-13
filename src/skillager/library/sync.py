from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import time
import json
from typing import Any

from ..catalog.impl import refresh_collection
from ..state import approvals
from ..state.library_approval import approval_witness
from ..state.trust import _record_trust_info
from ..exposure.target_state import MATERIALIZED_SIDECAR, matches_materialized_target
from ..simple_yaml import load_mapping
from .importing import import_inventory
from .metadata import load_library_provenance
from .model import normalize_skill_name
from .paths import load_library_registration
from .service import _library_first_use_plan, _require_library_identity, initialize_library
from .sync_lineage import canonical_approval_key, lineage_status, origin, projected_metadata_limits, source_identity


SYNC_SCHEMA = "skillager.library-sync.v1"
SYNC_STATUS_SCHEMA = "skillager.library-sync-status.v1"
SYNC_INVENTORY_LIMIT = 10_000
SYNC_RESULT_BYTES = 32 * 1024 * 1024
OUTCOMES = ("created", "updated", "unchanged", "conflict", "skipped", "failed", "uncertain")
SOFT_DEADLINE_SECONDS = 25.0


def sync_refusal(status_only: bool, reason: str = "sync-unavailable") -> dict[str, Any]:
    return {"schema": SYNC_STATUS_SCHEMA if status_only else SYNC_SCHEMA, "status": "refused", "reason_code": reason,
            "library": None, "context": None, "coverage": {"discovered_origins": 0, "approved_origins": 0,
            "selected_sources": 0, "processed_sources": 0, "complete": False, "discovery_error_count": 1},
            **({"lineages": [], "candidates": []} if status_only else {"counts": {name: 0 for name in OUTCOMES}, "items": []})}


def _managed_projection(skill: dict[str, Any]) -> bool:
    if not (skill.get("native") or {}).get("managed"):
        return False
    target = Path(skill["root"])
    try:
        return matches_materialized_target(target, load_mapping(target / MATERIALIZED_SIDECAR))
    except (OSError, ValueError):
        return False


def library_binding(catalog: Path, expected_id: str | None, expected_root: Path | None):
    if bool(expected_id) != (expected_root is not None):
        raise ValueError("expected library ID and root must be supplied together")
    registration = load_library_registration(catalog)
    if expected_id and (registration is None or registration.library_id != expected_id or registration.layout.root != expected_root):
        raise ValueError("sync library selection changed")
    if registration is None:
        return None
    registration, library_identity = _require_library_identity(catalog)
    return registration, library_identity


def _library(binding) -> dict[str, Any] | None:
    if binding is None:
        return None
    registration, library_identity = binding
    return {"library_id": registration.library_id, "root": str(registration.layout.root), "git_mode": library_identity.git_mode}


def _group_sources(skills: list[dict[str, Any]], binding) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for skill in skills:
        if skill.get("source", {}).get("ownership") == "library":
            continue
        if binding:
            try:
                Path(skill["root"]).resolve().relative_to(binding[0].layout.skills)
                continue
            except ValueError:
                pass
        key = source_identity(skill)
        occurrence = (key, str(Path(skill["root"]).resolve()))
        if occurrence not in seen:
            seen.add(occurrence)
            groups[key].append(skill)
    return dict(sorted(groups.items()))


def _load_state(project: Path, catalog: Path, binding, skills: list[dict[str, Any]]):
    records = {root: approvals.load(root) for root in {project.resolve(), catalog.resolve()}}
    provenance = load_library_provenance(binding[0].layout) if binding else None
    entries = {skill["id"]: skill for skill in skills if skill.get("source", {}).get("ownership") == "library"}
    mapping = {}
    for name, entry in (provenance or {}).get("skills", {}).items():
        sync = entry.get("sync")
        if sync:
            if sync.get("schema") != "skillager.library-sync-lineage.v1" or not isinstance(sync.get("source_identity"), str):
                raise ValueError("unsupported library sync provenance")
            key = sync["source_identity"]
            if key in mapping:
                raise ValueError("ambiguous library sync provenance")
            mapping[key] = name
    return records, provenance, entries, mapping


def _item(key: str, skills: list[dict[str, Any]], project: Path | None) -> dict[str, Any]:
    return {"source_identity": key, "origin_ids": [origin(skill, project, source_id=key)["origin_id"] for skill in skills],
            "lineage_id": None, "canonical_skill_id": None, "outcome": "skipped", "phase": "not-started",
            "accepted_hash": None, "reason_code": None, "repair": "none"}


def _plan(groups, binding, records, provenance, entries, mapping, project, catalog, project_dir):
    items, selected, lineages = [], [], []
    library = _library(binding)
    seen_lineages = set()
    for key, skills in groups.items():
        item = _item(key, skills, project_dir)
        items.append(item)
        witnesses = [(skill, approval_witness(skill, project, catalog, records)) for skill in skills]
        approved = [(skill, witness) for skill, witness in witnesses if witness is not None]
        name = mapping.get(key)
        status = None
        if name and binding:
            assert library is not None
            stored = provenance["skills"][name]["sync"]
            entry = entries.get(f"lib/{name}", {})
            record = records[catalog].get("global_approvals", {}).get(canonical_approval_key(binding[0].library_id, name))
            status = lineage_status(stored, library, binding[0].layout.skill_root(name), record,
                                    entry.get("content_hash"), skills, records, project, catalog, project_dir, entry.get("lint"))
            lineages.append(status)
            seen_lineages.add(key)
            item.update(lineage_id=stored["lineage_id"], canonical_skill_id=f"lib/{name}", accepted_hash=status["canonical"]["accepted_hash"])
        if not approved:
            item["reason_code"] = "source-not-approved"
            continue
        if len({skill["content_hash"] for skill, _ in approved}) != 1:
            item.update(outcome="conflict", reason_code="source-version-conflict", repair="resolve-conflict")
            continue
        skill, witness = approved[0]
        if _managed_projection(skill):
            # Managed projections are not authoritative copies of their original source.
            item.update(reason_code="managed-origin-requires-source")
            continue
        if name:
            protections = [records[authority].get("skills", {}).get(f"lib/{name}") for authority in {project, catalog}]
            if any((_record_trust_info(record, (status or {}).get("canonical", {}).get("working_hash") or "",
                       lint=None, scope="project") or {}).get("state") in {"blocked", "pinned"} for record in protections):
                item.update(outcome="conflict", reason_code="canonical-protected", repair="resolve-conflict")
                continue
        if status and status["preservation"] != "verified":
            item.update(outcome="conflict", reason_code=status["reason_code"], repair="accept-pending" if status["preservation"] == "pending" else "resolve-conflict")
            continue
        if status and status["canonical"]["working_hash"] == skill["content_hash"]:
            item.update(outcome="unchanged", phase="accepted")
            continue
        if status and status["canonical"]["trust"] == "pinned":
            item.update(outcome="conflict", reason_code="canonical-pinned", repair="resolve-conflict")
            continue
        if not name:
            try:
                slug = normalize_skill_name(str(skill["id"]).rsplit("/", 1)[-1][:48])
            except ValueError:
                slug = "skill"
            name = f"{slug[:47]}-{key[:16]}"
            if binding and (binding[0].layout.skill_root(name).exists() or name in provenance["skills"]):
                item.update(outcome="conflict", reason_code="destination-collision", repair="resolve-conflict")
                continue
        item.update(canonical_skill_id=f"lib/{name}", reason_code=None)
        previous_origins = provenance["skills"][name]["sync"]["origins"] if status else []
        preserved_origins = {item["origin_id"]: item for item in previous_origins}
        for value, _ in approved:
            occurrence = origin(value, project_dir, source_id=key)
            preserved_origins[occurrence["origin_id"]] = occurrence
        selected.append({"source": skill, "witness": witness, "name": name, "item": item,
                         "origins": list(preserved_origins.values()), "previous": status})
    if binding:
        assert library is not None
        for key, name in mapping.items():
            if key in seen_lineages:
                continue
            stored = provenance["skills"][name]["sync"]
            entry = entries.get(f"lib/{name}", {})
            record = records[catalog].get("global_approvals", {}).get(canonical_approval_key(binding[0].library_id, name))
            lineages.append(lineage_status(stored, library, binding[0].layout.skill_root(name), record,
                            entry.get("content_hash"), [], records, project, catalog, project_dir, entry.get("lint")))
    return items, selected, lineages


def sync_approved(
    project: Path, catalog: Path, *, status_only: bool = False, project_dir: Path | None = None,
    skills: list[dict[str, Any]] | None = None, expected_library_id: str | None = None,
    expected_library_root: Path | None = None, inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One explicit approved-source batch; observers never enter mutation owners."""
    deadline = time.monotonic() + SOFT_DEADLINE_SECONDS
    project, catalog = project.resolve(), catalog.resolve()
    binding = library_binding(catalog, expected_library_id, expected_library_root)
    inventory = inventory if inventory is not None else import_inventory(project, catalog)
    groups = _group_sources(inventory["skills"], binding)
    admitted_origins = sum(map(len, groups.values()))
    records, provenance, entries, mapping = _load_state(project, catalog, binding, inventory["skills"])
    if admitted_origins + len(entries) > SYNC_INVENTORY_LIMIT:
        refused = sync_refusal(status_only, "inventory-limit")
        refused["library"] = _library(binding)
        refused["coverage"].update(discovered_origins=admitted_origins, discovery_error_count=len(inventory["errors"]))
        return refused
    admitted_approved = sum(approval_witness(skill, project, catalog, records) is not None for values in groups.values() for skill in values)
    observed_origin_ids = {origin(skill, project_dir, source_id=key)["origin_id"] for key, values in groups.items() for skill in values}
    missing: dict[str, list[dict[str, Any]]] = {}
    if skills is not None:
        requested = _group_sources(skills, binding)
        wanted = {(skill["id"], str(Path(skill["root"]).resolve())) for values in requested.values() for skill in values}
        groups = {key: values for key, values in groups.items()
                  if any((skill["id"], str(Path(skill["root"]).resolve())) in wanted for skill in values)}
        missing = {key: values for key, values in requested.items() if key not in groups}
    items, selected, lineages = _plan(groups, binding, records, provenance, entries, mapping, project, catalog, project_dir)
    for key, values in missing.items():
        items.append({**_item(key, values, project_dir), "outcome": "failed", "reason_code": "source-missing", "repair": "observe"})
    coverage = {"discovered_origins": admitted_origins,
                "approved_origins": admitted_approved,
                "selected_sources": len(items), "processed_sources": len(items) - len(selected),
                "complete": not inventory["errors"], "discovery_error_count": len(inventory["errors"])}
    context = {"project_root": str(project_dir.resolve()) if project_dir else None, "discovery": "effective-local"}
    creates = [value for value in selected if value["previous"] is None]
    capacity_refused = admitted_origins + len(entries) + len(creates) > SYNC_INVENTORY_LIMIT
    library_root = binding[0].layout.root if binding else _library_first_use_plan(catalog)[0].root
    metadata_refusal = projected_metadata_limits(lineages, [value for value in selected if not capacity_refused or value["previous"]], items, library_root, observed_origin_ids, len(entries) + (0 if capacity_refused else len(creates)), SYNC_INVENTORY_LIMIT, SYNC_RESULT_BYTES)
    if metadata_refusal:
        refused = sync_refusal(status_only, metadata_refusal)
        refused.update(library=_library(binding), context=context)
        refused["coverage"].update(coverage, complete=False, processed_sources=0)
        return refused
    if status_only:
        coverage["processed_sources"] = len(items)
        candidate_states = {id(value["item"]): "eligible-update" if value["previous"] else "unavailable" if capacity_refused else "eligible-create" for value in selected}
        result = {"schema": SYNC_STATUS_SCHEMA, "library": _library(binding), "context": context, "coverage": coverage,
                "lineages": lineages, "candidates": [{"source_identity": item["source_identity"],
                    "canonical_skill_id": item["canonical_skill_id"], "state": candidate_states.get(id(item), "current" if item["outcome"] == "unchanged" else item["outcome"]),
                    "reason_code": "inventory-limit" if capacity_refused and candidate_states.get(id(item)) == "unavailable" else item["reason_code"]} for item in items]}
        return result if len(json.dumps(result).encode("utf-8")) <= SYNC_RESULT_BYTES else sync_refusal(True, "result-limit")
    if capacity_refused:
        for value in creates:
            value["item"].update(reason_code="inventory-limit", repair="observe")
        selected = [value for value in selected if value["previous"] is not None]
        coverage["complete"] = False
    if selected:
        if time.monotonic() >= deadline:
            for value in selected:
                value["item"]["reason_code"] = "time-limit"
            selected = []
    if selected:
        if binding is None:
            # Bound callers already refused in library_binding; only standalone approval can initialize.
            first_layout, _, _ = _library_first_use_plan(catalog)
            try:
                initialized = initialize_library(catalog, path=first_layout.root)
                binding = library_binding(catalog, initialized["library"]["library_id"], first_layout.root)
                assert binding is not None
                provenance = load_library_provenance(binding[0].layout)
            except Exception:
                # Initialization may have published metadata before an I/O failure.
                for value in selected:
                    value["item"].update(outcome="uncertain", phase="unknown", reason_code="initialization-incomplete", repair="observe")
                counts = Counter(item["outcome"] for item in items)
                coverage.update(complete=False, processed_sources=0)
                return {"schema": SYNC_SCHEMA, "status": "uncertain", "library": None, "context": context,
                        "coverage": coverage, "counts": {name: counts[name] for name in OUTCOMES}, "items": items}
        from .sync_mutation import apply_sync_chunks
        try:
            apply_sync_chunks(selected, project, catalog, binding, deepcopy(provenance), records,
                              expected_library_id=expected_library_id or binding[0].library_id,
                              expected_library_root=expected_library_root or binding[0].layout.root, deadline=deadline)
        except Exception:
            # Preserve earlier accepted outcomes when a later chunk cannot start.
            coverage.update(complete=False, discovery_error_count=coverage["discovery_error_count"] + 1)
            for value in selected:
                item = value["item"]
                if item["outcome"] == "skipped" and item["reason_code"] is None:
                    item.update(outcome="failed", reason_code="sync-incomplete", repair="observe")
        try:
            library_binding(catalog, binding[0].library_id, binding[0].layout.root)
            if time.monotonic() < deadline:
                refresh_collection(catalog, "lib")
        except Exception:
            coverage.update(complete=False, discovery_error_count=coverage["discovery_error_count"] + 1)
    counts = Counter(item["outcome"] for item in items)
    coverage["processed_sources"] = sum(item["reason_code"] not in {"time-limit", "inventory-limit", "recovery-required"} for item in items)
    coverage["complete"] = bool(coverage["complete"] and coverage["processed_sources"] == len(items))
    failed = any(counts[name] for name in ("conflict", "failed", "uncertain"))
    return {"schema": SYNC_SCHEMA, "status": "uncertain" if counts["uncertain"] else "refused" if capacity_refused and not any(counts[name] for name in ("created", "updated")) else "partial" if failed or not coverage["complete"] else "completed",
            "library": _library(binding), "context": context, "coverage": coverage,
            "counts": {name: counts[name] for name in OUTCOMES}, "items": items}
