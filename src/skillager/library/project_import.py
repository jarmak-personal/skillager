"""One bounded decision to save and accept selected exact project originals."""
from __future__ import annotations

import json
import re
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..skills.project_inventory import observe_project
from ..state import approvals
from ..state.locking import resource_locks
from ..state.trust import APPROVED_TRUST_STATES, _record_trust_info, content_hash
from .candidate import prepare_library_candidate
from .confirmation import confirmation_token, require_confirmation_token
from .git import path_changes, repository_status
from .import_preservation import import_preservation
from .importing import _capture_import_review, _review_source_provenance, import_library_skill_locked, import_preview
from .metadata import load_library_identity, load_library_provenance
from .model import normalize_skill_name
from .review import REVIEW_LIMITS, ReviewRefusal
from .service import _acceptance_overrides, _history_availability, _library_first_use_plan, _require_safe_git_mutation, initialize_library_locked
from .source_identity import import_source_relation, origin, source_identity
from .sync_lineage import canonical_approval_key, canonical_target_state, lineage_is_bound


INVENTORY_SCHEMA = "skillager.project-import-inventory.v1"
IMPORT_SCHEMA = "skillager.project-import.v1"
LIMITS = {**REVIEW_LIMITS, "selection_items": 32, "selection_bytes": 4096, "inventory_items": 10000,
          "page_items": 100, "argv_bytes": 8192, "status_request_bytes": 6144}
ELIGIBILITIES = ("eligible", "already_saved", "managed", "duplicate", "conflict", "blocked")


def encode(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > LIMITS["response_bytes"]:
        raise ReviewRefusal("result-limit", "Complete response exceeds limits; select fewer skills or request a smaller inventory page")
    return encoded


def parse_selection(value: str, *, ordered: bool = False) -> list[str]:
    if len(value.encode()) > LIMITS["selection_bytes"]:
        raise ReviewRefusal("selection-limit", "Select at most 32 occurrences within 4096 UTF-8 bytes")
    try:
        selected = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise ReviewRefusal("invalid-selection", "Selection must be a JSON array of exact occurrence IDs") from exc
    if (not isinstance(selected, list) or not selected or len(selected) > LIMITS["selection_items"]
            or any(not isinstance(item, str) or not re.fullmatch(r"project:[0-9a-f]{64}", item) for item in selected)
            or len(set(selected)) != len(selected)):
        raise ReviewRefusal("invalid-selection", "Select between 1 and 32 distinct project occurrence IDs")
    return selected if ordered else sorted(selected)


def _location(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path != path.resolve():
        raise ReviewRefusal("path-changed", "Review paths must remain canonical and unaliased")
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


def _canonical(source, name, layout, library_id, records, provenance, project):
    """Read only a related destination; a name collision is never a preservation proof."""
    target = layout.skill_root(name)
    record = records.get(canonical_approval_key(library_id, name), {})
    if target.is_dir():
        canonical_target_state(target)
    working = content_hash(target) if target.is_dir() else None
    info = _record_trust_info(record, working or "", lint=None, scope="global") or {}
    accepted = bool(working and record.get("content_hash") == working and info.get("state") in APPROVED_TRUST_STATES
                    and record.get("skill_id") == f"lib/{name}"
                    and record.get("approval_key") == canonical_approval_key(library_id, name))
    facts = {"skill_id": f"lib/{name}", "path": str(target), "working_hash": working,
             "accepted_hash": record.get("content_hash"),
             "acceptance": "accepted" if accepted else "pending" if working else "missing"}
    preserved = False
    if accepted and working is not None and source is not None and working == source["content_hash"]:
        relation = record.get("import_source")
        if relation is not None:
            preserved = relation == import_source_relation(source, project, target, library_id, layout.root, f"lib/{name}", working)
        proof = record.get("import_preservation")
        if proof is not None:
            preserved = preserved or proof == import_preservation(source, project, target, library_id, layout.root, f"lib/{name}", working)
        lineage = provenance.get("skills", {}).get(name, {}).get("sync")
        if lineage and lineage_is_bound(lineage, record):
            observed = origin(source, project, source_id=source_identity(source))
            preserved = preserved or (observed in lineage.get("origins", [])
                                     and lineage.get("source_approval", {}).get("content_hash") == working
                                     and canonical_target_state(target) == lineage.get("target_state"))
    return facts, preserved


def project_import_inventory(state: Path, catalog: Path, project: Path) -> dict[str, Any]:
    """Project-native coverage, without collection indexes or unrelated library hashing."""
    observed = observe_project(state, catalog, project)
    layout, library, binding = _library_first_use_plan(catalog)
    library = {**library, "library_id": binding["library_id"]}
    records = approvals.load(catalog).get("global_approvals", {})
    provenance = load_library_provenance(layout) or {"skills": {}}
    by_origin: dict[str, set[str]] = defaultdict(set)
    for key, record in records.items():
        for field in ("import_source", "import_preservation"):
            relation = record.get(field) or {}
            canonical = relation.get("canonical") or {}
            skill_id = canonical.get("skill_id", "")
            if (canonical.get("library_id") == library["library_id"] and skill_id.startswith("lib/")
                    and key == canonical_approval_key(library["library_id"], skill_id[4:])):
                by_origin[(relation.get("origin") or {}).get("origin_id", "")].add(skill_id[4:])
    for name, metadata in provenance["skills"].items():
        for occurrence in (metadata.get("sync") or {}).get("origins", []):
            by_origin[occurrence["origin_id"]].add(name)
    sources = {str(Path(skill["entrypoint"]).resolve()): skill for skill in observed["skills"]}
    rows = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in observed["rows"]:
        row = {key: item[key] for key in ("occurrence_id", "id", "name", "description", "skill_file")}
        row.update(source_hash=None, source_identity=None, eligibility="managed", reason_code="managed-copy",
                   destination_id=None, duplicate_of=None, canonical=None)
        rows.append(row)
        if item["native"]["managed"]:
            continue
        source = sources[item["skill_file"]]
        source_id = source_identity(source)
        row.update(source_hash=source["content_hash"], source_identity=source_id, eligibility="eligible", reason_code=None)
        try:
            name = normalize_skill_name(Path(source["root"]).name)
            row["destination_id"] = f"lib/{name}"
            row["_version"] = {"hash": source["content_hash"], "provenance": _review_source_provenance(source),
                               "tree": _capture_import_review(source)}
            _acceptance_overrides(source, override_lint=False, reason=None)
            if source.get("trust") == "blocked":
                raise ReviewRefusal("source-blocked", "Source is blocked")
            occurrence = origin(source, project, source_id=source_id)
            related = by_origin.get(occurrence["origin_id"], set())
            if len(related) > 1:
                row.update(eligibility="conflict", reason_code="ambiguous-library-relation")
            elif related or layout.skill_root(name).exists() or name in provenance["skills"]:
                destination = next(iter(related)) if related else name
                try:
                    facts, preserved = _canonical(source, destination, layout, library["library_id"], records, provenance, project)
                    row.update(canonical=facts, destination_id=facts["skill_id"],
                               eligibility="already_saved" if preserved else "conflict",
                               reason_code="accepted-preservation" if preserved else "destination-conflict")
                except (OSError, ValueError):
                    row.update(eligibility="conflict", reason_code="canonical-unavailable")
            groups[name].append(row)
        except (OSError, ValueError, KeyError):
            row.update(eligibility="blocked", reason_code="source-blocked" if source.get("trust") == "blocked" else "source-unavailable")
    for same_name in groups.values():
        versions = {confirmation_token("project-import-version", **row["_version"]) for row in same_name}
        if len(versions) > 1:
            for row in same_name:
                if row["eligibility"] == "eligible":
                    row.update(eligibility="conflict", reason_code="source-version-conflict")
            continue
        # Prefer an existing exact accepted preservation, then stable path order.
        representative = min(same_name, key=lambda row: (row["eligibility"] != "already_saved", row["skill_file"]))
        for row in same_name:
            if row is not representative and row["eligibility"] in {"eligible", "conflict"}:
                row.update(eligibility="duplicate", reason_code="identical-project-original",
                           duplicate_of=representative["occurrence_id"], canonical=representative["canonical"])
    counts = Counter(row["eligibility"] for row in rows)
    selected = sorted(row["occurrence_id"] for row in rows if row["eligibility"] == "eligible")
    default = selected if len(selected) <= LIMITS["selection_items"] else None
    public_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
    return {"schema": INVENTORY_SCHEMA, "scope": "project", "project_root": str(project), "library": library,
            "observation": observed["observation"], "summary": {"total": len(rows), **{key: counts[key] for key in ELIGIBILITIES}},
            "items": public_rows, "default_selection": default, "limits": dict(LIMITS),
            "_sources": sources, "_snapshot": observed["snapshot"], "_provenance": provenance}


def prepare_project_import(state: Path, catalog: Path, project: Path, selection: list[str]) -> dict[str, Any]:
    current = project_import_inventory(state, catalog, project)
    by_id = {row["occurrence_id"]: row for row in current["items"]}
    if any(item not in by_id or by_id[item]["eligibility"] != "eligible" for item in selection):
        raise ReviewRefusal("selection-changed", "A selected occurrence is missing or ineligible; observe and choose again")
    layout, library, binding = _library_first_use_plan(catalog)
    identity = load_library_identity(layout)
    git = repository_status(layout.root, mode=library["git_mode"]) if layout.root.exists() else {}
    if identity:
        _require_safe_git_mutation(git, allow_target_staged=False)
        if identity.git_mode == "system" and any(path_changes(git, layout.root, layout.provenance_path).values()):
            raise ReviewRefusal("library-changed", "Commit or restore pending library provenance before saving")
    library = {**library, "library_id": binding["library_id"],
               "history": _history_availability(identity, git, layout) if identity else {"available": False, "reason": "will-initialize"}}
    items = []
    with tempfile.TemporaryDirectory(prefix="skillager-project-import-preview-") as temporary:
        for occurrence_id in selection:
            row = by_id[occurrence_id]
            source_file = Path(row["skill_file"])
            plan = import_preview(state, catalog, row["id"], destination_name=row["destination_id"][4:],
                                  project_dir=project, source_file=source_file, review_manifest=True, project_native_root=project)
            if plan["blocked"] or plan["requires_override"] or plan["source_hash"] != row["source_hash"]:
                raise ReviewRefusal("source-changed", "Selected source changed or requires individual review")
            _, candidate = prepare_library_candidate(source_file.parent, Path(temporary) / occurrence_id[8:],
                                                     layout, binding["library_id"] or "00000000-0000-0000-0000-000000000000", plan["destination"]["name"], plan["source_hash"])
            _acceptance_overrides(candidate, override_lint=False, reason=None)
            items.append({"occurrence_id": occurrence_id, "source_identity": row["source_identity"],
                          "source": {**plan["source"], "name": row["name"], "summary": row["description"]},
                          "source_location": _location(source_file.parent), "source_file_identity": _location(source_file),
                          "source_hash": plan["source_hash"], "destination": {**plan["destination"], "display_name": row["name"]},
                          "lint": {key: plan["lint"][key] for key in ("status", "blocking_count")},
                          "scan": {key: plan["scan"][key] for key in ("risk", "finding_count")},
                          "review_manifest": plan["review_manifest"], "_source_key": plan["_source_key"]})
    if sum(item["review_manifest"]["file_count"] for item in items) > LIMITS["files"] or sum(
            item["review_manifest"]["total_bytes"] for item in items) > LIMITS["tree_bytes"]:
        raise ReviewRefusal("review_limit_exceeded", "The complete batch exceeds tree limits; select fewer skills")
    payload = {"schema": IMPORT_SCHEMA, "status": "preview", "project_root": str(project), "selection": selection,
               "library": library, "observation": current["observation"], "items": public(items), "limits": dict(LIMITS),
               "library_state": confirmation_token("project-import-library", binding=binding, provenance=current["_provenance"],
                                                   location=_location(layout.root) if layout.root.exists() else None, git=git),
               "project_identity": _location(project)}
    token = confirmation_token("project-import", state=str(state), catalog=str(catalog), review=payload)
    payload.update(confirmation_token=token, next_command_argv=["skillager", "import-project", "--json", "--review-manifest",
                   "--select", json.dumps(selection, separators=(",", ":")), "--yes", "--confirmation-token", token])
    if len(json.dumps(payload["next_command_argv"]).encode()) > LIMITS["argv_bytes"]:
        raise ReviewRefusal("selection-limit", "The complete command exceeds argument limits; select fewer skills")
    encode(payload)  # A complete response must fit before its token can escape.
    return {**payload, "_items": items}


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def apply_project_import(state: Path, catalog: Path, project: Path, selection: list[str], token: str | None):
    plan = prepare_project_import(state, catalog, project, selection)
    require_confirmation_token(token, plan["confirmation_token"], operation="project import")
    resources = [catalog / "library-mutation", *[catalog / f"library-skill-{item['destination']['name']}" for item in plan["items"]]]
    with resource_locks(resources):
        plan = prepare_project_import(state, catalog, project, selection)
        require_confirmation_token(token, plan["confirmation_token"], operation="project import")
        return _apply_locked(state, catalog, project, plan)


def _apply_locked(state, catalog, project, plan):
    result = {"schema": IMPORT_SCHEMA, "status": "uncertain", "project_root": str(project), "selection": plan["selection"],
              "plan_hash": plan["confirmation_token"], "library": plan["library"], "initialization": None,
              "items": [{"occurrence_id": item["occurrence_id"], "source_id": item["source"]["id"],
                         "source_file": item["source"]["file"], "source_hash": item["source_hash"],
                         "destination_id": item["destination"]["id"], "destination_path": item["destination"]["path"],
                         "status": "skipped", "accepted_hash": None, "reason_code": "batch-stopped"} for item in plan["items"]]}
    try:
        if plan["library"]["status"] == "will-initialize":
            result["initialization"] = {"status": "uncertain", "root": plan["library"]["root"]}
            initialized = initialize_library_locked(catalog, path=Path(plan["library"]["root"]), expected_first_use=plan["items"][0]["review_manifest"]["library"])
            result["initialization"] = {"status": initialized["status"], "root": initialized["library"]["root"],
                                         "library_id": initialized["library"]["library_id"]}
            result["library"] = {**result["library"], "status": "ready", "library_id": initialized["library"]["library_id"], "history": initialized["history"]}
        for item, outcome in zip(plan["_items"], result["items"]):
            manifest = deepcopy(item["review_manifest"])
            manifest["library"]["library_id"] = result["library"]["library_id"]
            outcome.update(status="uncertain", reason_code="save-incomplete")
            saved = import_library_skill_locked(state, catalog, item["source"]["id"],
                destination_name=item["destination"]["name"], expected_hash=item["source_hash"],
                expected_source_key=item["_source_key"], project_dir=project, source_file=Path(item["source"]["file"]),
                expected_review=manifest, project_native_root=project, refresh_catalog=False)
            outcome.update(status="accepted", accepted_hash=saved["destination"]["accepted_hash"], reason_code=None)
        result["status"] = "completed"
    except Exception:
        # The existing owner may have copied, committed or accepted before an
        # error. Never convert missing completion evidence into a safe retry.
        result["status"] = "partial" if any(item["status"] == "accepted" for item in result["items"]) else "uncertain"
        result["error"] = {"code": "save-incomplete", "message": "Saving stopped; observe current item facts before deciding further work"}
    counts = Counter(item["status"] for item in result["items"])
    result["counts"] = {key: counts[key] for key in ("accepted", "failed", "skipped", "uncertain")}
    return result, 0 if result["status"] == "completed" else 2


def project_import_status(state: Path, catalog: Path, project: Path, selection_json: str, destinations_json: str) -> dict[str, Any]:
    """Observe requested destinations even after their originals disappear.

    This is current state, never evidence that a previous invocation completed.
    The reviewed caller supplies the occurrence-to-destination association.
    """
    selected = parse_selection(selection_json, ordered=True)
    if len(selection_json.encode()) + len(destinations_json.encode()) > LIMITS["status_request_bytes"]:
        raise ReviewRefusal("selection-limit", "Status request exceeds 6144 UTF-8 bytes; select fewer occurrences")
    try:
        destinations = json.loads(destinations_json)
    except (ValueError, RecursionError) as exc:
        raise ReviewRefusal("invalid-selection", "Destinations must be a parallel array of library skill IDs") from exc
    if (not isinstance(destinations, list) or len(destinations) != len(selected)
            or any(not isinstance(item, str) or not item.startswith("lib/")
                   or item != f"lib/{normalize_skill_name(item)}" for item in destinations)
            or len(set(destinations)) != len(destinations)):
        raise ReviewRefusal("invalid-selection", "Destinations must be distinct canonical library skill IDs parallel to selection")
    observed = observe_project(state, catalog, project)
    rows = {row["occurrence_id"]: row for row in observed["rows"]}
    layout, library, binding = _library_first_use_plan(catalog)
    records = approvals.load(catalog).get("global_approvals", {})
    items = []
    for occurrence_id, destination in sorted(zip(selected, destinations)):
        facts, _ = _canonical(None, destination[4:], layout, binding["library_id"], records, {}, project)
        row = rows.get(occurrence_id)
        source = {key: row[key] for key in ("id", "skill_file", "name", "description")} if row else None
        items.append({"occurrence_id": occurrence_id, "destination_id": destination, "source": source, "canonical": facts})
    return {"schema": "skillager.project-import-status.v1", "project_root": str(project),
            "library": {**library, "library_id": binding["library_id"]}, "observation": observed["observation"],
            "items": items, "limits": dict(LIMITS)}
