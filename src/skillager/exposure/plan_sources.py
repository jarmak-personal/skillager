"""Selected source authority and native preservation for an exposure plan."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ..compatibility import compatibility_problem
from ..catalog.impl import collection_index_skills
from ..library.candidate import index_library_candidate
from ..library.importing import import_inventory
from ..library.import_preservation import import_preservation
from ..library.metadata import load_library_provenance
from ..library.sync import _group_sources, library_binding
from ..library.sync_lineage import canonical_approval_key, canonical_target_state, lineage_status, origin
from ..state import approvals
from ..state.library_approval import approval_witness, public_approval
from ..state.trust import APPROVED_TRUST_STATES, content_hash
from ..state.trust import _record_trust_info
from ..skills.index import build_index
from .plan_request import MAX_MEMBERS, PlanRefusal, library_skill
from .impl import _project_agent_bases
from .preview import exposure_source_state
from .target_state import MATERIALIZED_SIDECAR, target_state_manifest
from .plan_targets import bounded_metadata, digest


class PlanSources:
    """Resolve one inventory/map; final checks reread only selected bytes/decisions."""

    def __init__(self, state: Path, catalog: Path, project: Path, agent: str, *, selected_native_file: Path | None = None) -> None:
        self.state, self.catalog = state.resolve(), catalog.resolve()
        self.project, self.agent = project.resolve(), agent
        self.binding = library_binding(self.catalog, None, None)
        if self.binding is None:
            raise PlanRefusal("library-unavailable", "Connect an existing personal library before exposure changes")
        self.registration = self.binding[0]
        self.library_id = self.registration.library_id
        self.layout = self.registration.layout
        self.selected_native_file = selected_native_file
        inventory = (build_index(self.state, approval_root=self.catalog, project_native_root=self.project,
                                 include_packages=False, persist=False, project_inventory=True,
                                 selected_native_file=selected_native_file) if selected_native_file is not None
                     else import_inventory(self.state, self.catalog))
        if inventory["errors"]:
            raise PlanRefusal("inventory-incomplete", "Skillager could not resolve the complete selected source inventory")
        self.groups = _group_sources(inventory["skills"], self.binding)
        self.records = self.read_records()
        self.provenance = (load_library_provenance(self.layout) or {"skills": {}})
        self.canonical: dict[str, dict[str, Any]] = {}
        for skill in inventory["skills"]:
            if (skill.get("source") or {}).get("ownership") != "library":
                continue
            previous = self.canonical.get(skill["id"])
            if previous is not None and exposure_source_state(previous) != exposure_source_state(skill):
                raise PlanRefusal("source-ambiguous", "Canonical skill identity is ambiguous")
            self.canonical[skill["id"]] = skill
        self.origins: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.origin_sources: dict[str, str] = {}
        for key, skills in self.groups.items():
            for skill in skills:
                observed = origin(skill, self.project, source_id=key)
                if observed["origin_id"] in self.origins:
                    raise PlanRefusal("source-ambiguous", "Native source occurrence is ambiguous")
                self.origins[observed["origin_id"]] = (skill, observed)
                self.origin_sources[observed["origin_id"]] = key
        self.selected: dict[str, dict[str, Any]] = {}
        self.selected_origins: dict[str, dict[str, Any]] = {}

    def read_records(self) -> dict[Path, dict[str, Any]]:
        return {root: approvals.load(root) for root in {self.state, self.catalog}}

    def require_library(self, selected_id: str) -> None:
        if selected_id != self.library_id:
            raise PlanRefusal("library-changed", "The requested canonical library is not the registered library")

    def skill(self, skill_id: str) -> dict[str, Any]:
        if skill_id in self.selected:
            return self.selected[skill_id]["skill"]
        skill = self.canonical.get(skill_id)
        if skill is None and self.selected_native_file is not None:
            library_skill(skill_id)
            target = self.layout.skill_root(skill_id[4:])
            canonical_target_state(target)
            entry = index_library_candidate(target, self.layout, self.library_id, skill_id[4:])
            skill = collection_index_skills(self.catalog, {"skills": [entry]}, trust_root=self.state, approval_root=self.catalog)[0]
            self.canonical[skill_id] = skill
        if skill is None or (skill.get("source") or {}).get("library_id") != self.library_id:
            raise PlanRefusal("source-unavailable", "The selected canonical skill is unavailable")
        target = self.layout.skill_root(skill_id[4:])
        if Path(skill["root"]).resolve() != target or Path(skill["entrypoint"]).resolve() != target / "SKILL.md":
            raise PlanRefusal("source-ambiguous", "Canonical source location does not match its library identity")
        witness = approval_witness(skill, self.state, self.catalog, self.records)
        if witness is None or skill.get("trust") not in APPROVED_TRUST_STATES:
            raise PlanRefusal("source-unapproved", "The selected canonical version is not currently approved")
        problem = compatibility_problem(skill, self.agent)
        if problem:
            raise PlanRefusal("source-incompatible", problem)
        if content_hash(target) != skill["content_hash"]:
            raise PlanRefusal("source-changed", "The selected canonical bytes changed during planning")
        state = canonical_target_state(target)
        self.selected[skill_id] = {"skill": skill, "witness": witness, "target_state": state}
        return skill

    def native(self, origin_id: str, expected_skill_id: str | None = None) -> tuple[dict[str, Any], Path]:
        pair = self.origins.get(origin_id)
        if pair is None:
            raise PlanRefusal("origin-unavailable", "The selected native occurrence is no longer observed")
        source, observed = pair
        native = observed["native"]
        if not native or native["agent"] != self.agent or native["scope"] != "project" or native["project_root"] != str(self.project):
            raise PlanRefusal("origin-mismatch", "The selected occurrence is not native to this project and agent")
        target = Path(observed["path"])
        if target.parent not in _project_agent_bases(self.project, self.agent) or not target.is_relative_to(self.project) or target.is_symlink() or (target / MATERIALIZED_SIDECAR).exists():
            raise PlanRefusal("origin-mismatch", "Native adoption requires one unchanged unmanaged project directory")
        matching = []
        for name, metadata in self.provenance.get("skills", {}).items():
            stored = (metadata or {}).get("sync")
            if stored and stored.get("source_identity") == self.origin_sources[origin_id]:
                matching.append((name, stored))
        if not matching:
            return self._imported_native(source, observed, expected_skill_id)
        if len(matching) != 1:
            raise PlanRefusal("origin-unpreserved", "The native origin has no unique preserved canonical lineage")
        name, stored = matching[0]
        skill_id = f"lib/{name}"
        if expected_skill_id is not None and expected_skill_id != skill_id:
            raise PlanRefusal("origin-mismatch", "Native origin belongs to a different canonical skill")
        skill = self.skill(skill_id)
        status = self._lineage(stored, skill, source, self.records)
        selected_origin = next((item for item in status["origins"] if item["origin_id"] == origin_id), None)
        if status["preservation"] != "verified" or selected_origin is None or selected_origin["observation"]["status"] != "current":
            raise PlanRefusal("origin-unpreserved", "The current native origin is not preserved as this accepted canonical version")
        witness = approval_witness(source, self.state, self.catalog, self.records)
        if witness is None or content_hash(target) != source["content_hash"] or source["content_hash"] != skill["content_hash"]:
            raise PlanRefusal("origin-unapproved", "The exact native version is no longer approved and preserved")
        self._preserved_tree(target, Path(skill["root"]))
        self.selected_origins[origin_id] = {"source": source, "observed": observed, "stored": deepcopy(stored),
                                            "skill": skill, "witness": witness, "status": status, "basis": "approved-sync"}
        return skill, target

    def import_candidates(self, origin_id: str) -> list[str]:
        """Select trusted decision metadata before reading any candidate body."""
        candidates = []
        for key, record in self.records[self.catalog].get("global_approvals", {}).items():
            proof = record.get("import_preservation")
            if not isinstance(proof, dict) or not isinstance(proof.get("origin"), dict) or proof["origin"].get("origin_id") != origin_id:
                continue
            bounded_metadata(proof)
            canonical = proof.get("canonical")
            if not isinstance(canonical, dict):
                raise PlanRefusal("origin-unpreserved", "Import preservation metadata is malformed")
            skill_id = library_skill(canonical.get("skill_id"))
            if key != canonical_approval_key(self.library_id, skill_id[4:]):
                raise PlanRefusal("origin-unpreserved", "Import preservation is not bound to this library decision")
            candidates.append(skill_id)
        if len(candidates) > MAX_MEMBERS:
            raise PlanRefusal("request-limit", "Native preservation choices exceed 64 complete candidates")
        bounded_metadata(candidates)
        return sorted(candidates)

    def _imported_native(self, source: dict[str, Any], observed: dict[str, Any], expected_skill_id: str | None) -> tuple[dict[str, Any], Path]:
        candidates = self.import_candidates(observed["origin_id"])
        if expected_skill_id is None and len(candidates) > 1:
            raise PlanRefusal("origin-ambiguous", "Choose one exact accepted import from the selected native observation")
        skill_id = expected_skill_id if expected_skill_id in candidates else candidates[0] if len(candidates) == 1 else None
        if skill_id is None:
            raise PlanRefusal("origin-unpreserved", "No accepted import preservation proof exists for this exact original")
        if expected_skill_id is not None and expected_skill_id != skill_id:
            raise PlanRefusal("origin-mismatch", "Native original belongs to a different canonical import")
        skill = self.skill(skill_id)
        record = self.records[self.catalog].get("global_approvals", {}).get(canonical_approval_key(self.library_id, skill_id[4:])) or {}
        proof = self._import_proof(source, skill, record, self.records)
        witness = self.selected[skill_id]["witness"]
        self.selected_origins[observed["origin_id"]] = {"source": source, "observed": observed,
            "stored": deepcopy(proof), "skill": skill, "witness": witness, "basis": "accepted-import",
            "source_decision_state": self._import_decision_state(source, self.records)}
        return skill, Path(observed["path"])

    def _import_decision_state(self, source: dict[str, Any], records: dict[Path, dict[str, Any]]) -> str:
        return digest([records[self.state].get("skills", {}).get(source["id"]),
                       records[self.catalog].get("global_approvals", {}).get(source.get("approval_key")),
                       records[self.state].get("skills", {}).get(source.get("_decision_skill_id"))])

    def _import_proof(self, source: dict[str, Any], skill: dict[str, Any], record: dict[str, Any], records: dict[Path, dict[str, Any]]) -> dict[str, Any]:
        source_witness = approval_witness(source, self.state, self.catalog, records)
        # Missing source approval is the ordinary import journey. Explicit block
        # decisions still win; acceptance never approves or clears this source.
        for root, namespace, key in ((self.state, "skills", source["id"]),
                                     (self.catalog, "global_approvals", source.get("approval_key")),
                                     (self.state, "skills", source.get("_decision_skill_id"))):
            decision = records[root].get(namespace, {}).get(key) or {}
            info = _record_trust_info(decision, source["content_hash"], lint=source.get("lint"), scope="project")
            if source_witness is None and info and info.get("state") == "blocked":
                raise PlanRefusal("origin-blocked", "The selected original is explicitly blocked")
            if source_witness is None and decision.get("state") == "pinned" and decision.get("content_hash") != source["content_hash"]:
                raise PlanRefusal("origin-pinned", "The original source is pinned to another version")
        problem = compatibility_problem(source, self.agent)
        if problem:
            raise PlanRefusal("source-incompatible", problem)
        info = _record_trust_info(record, skill["content_hash"], lint=skill.get("lint"), scope="global") or {}
        if record.get("derived_from") is not None or info.get("state") not in APPROVED_TRUST_STATES or record.get("skill_id") != skill["id"] or record.get("approval_key") != canonical_approval_key(self.library_id, skill["id"][4:]):
            raise PlanRefusal("origin-unpreserved", "Import preservation requires its current canonical acceptance")
        proof = import_preservation(source, self.project, Path(skill["root"]), self.library_id, self.layout.root, skill["id"], skill["content_hash"])
        if proof is None or proof != record.get("import_preservation"):
            raise PlanRefusal("origin-unpreserved", "The original or canonical bytes, modes, identity or import decision no longer match the accepted preservation proof")
        return proof

    def _lineage(self, stored: dict[str, Any], skill: dict[str, Any], source: dict[str, Any], records: dict[Path, dict[str, Any]]) -> dict[str, Any]:
        record = records[self.catalog].get("global_approvals", {}).get(canonical_approval_key(self.library_id, skill["id"][4:]))
        return lineage_status(stored, {"library_id": self.library_id}, Path(skill["root"]), record,
                              skill["content_hash"], [source], records, self.state, self.catalog, self.project, skill.get("lint"))

    @staticmethod
    def _preserved_tree(target: Path, canonical: Path) -> None:
        original, preserved = target_state_manifest(target), target_state_manifest(canonical)
        if any(entry["type"] not in {"file", "directory"} for entry in original.values()) or original != preserved:
            raise PlanRefusal("unpreserved-material", "Native directory contains files, modes or extra entries not preserved in Your library; use Files for recoverable removal")

    def public(self) -> list[dict[str, Any]]:
        result = []
        for skill_id, selected in sorted(self.selected.items()):
            item = {**exposure_source_state(selected["skill"]), "approval": public_approval(selected["witness"]),
                    "target_state": selected["target_state"]}
            item["lineages"] = [{"lineage_id": value["status"]["lineage_id"], "source_identity": value["status"]["source_identity"],
                                  "canonical": value["status"]["canonical"], "preservation": value["status"]["preservation"],
                                  "origin": next(origin for origin in value["status"]["origins"] if origin["origin_id"] == value["observed"]["origin_id"]),
                                  "source_approval": value["status"]["source_approval"], "approval": public_approval(value["witness"])}
                                 for value in self.selected_origins.values() if value["skill"]["id"] == skill_id and value["basis"] == "approved-sync"]
            imports = [{"basis": "accepted-import", **value["stored"], "approval": public_approval(value["witness"]),
                        "source_decision_state": value["source_decision_state"]}
                       for value in self.selected_origins.values() if value["skill"]["id"] == skill_id and value["basis"] == "accepted-import"]
            if imports:
                item["import_preservations"] = imports
            bounded_metadata(item)
            result.append(item)
        return result

    def revalidate_canonical(self, records: dict[Path, dict[str, Any]], *, skill_id: str | None = None) -> None:
        """Retain canonical preservation even after native originals are detached."""
        library_binding(self.catalog, self.library_id, self.layout.root)
        selected = self.selected.values() if skill_id is None else [self.selected[skill_id]]
        for value in selected:
            skill, target = value["skill"], Path(value["skill"]["root"])
            if content_hash(target) != skill["content_hash"] or canonical_target_state(target) != value["target_state"] or approval_witness(skill, self.state, self.catalog, records) != value["witness"]:
                raise PlanRefusal("source-changed", "Canonical content, identity or approval changed after preview")

    def revalidate(self, records: dict[Path, dict[str, Any]] | None = None) -> None:
        current = records if records is not None else self.read_records()
        self.revalidate_canonical(current)
        provenance = (load_library_provenance(self.layout) or {"skills": {}})
        for value in self.selected_origins.values():
            source, skill = value["source"], value["skill"]
            if value["basis"] == "accepted-import":
                record = current[self.catalog].get("global_approvals", {}).get(canonical_approval_key(self.library_id, skill["id"][4:])) or {}
                if self._import_decision_state(source, current) != value["source_decision_state"] or any(
                    isinstance(entry, dict) and isinstance(entry.get("sync"), dict)
                    and entry["sync"].get("source_identity") == value["stored"]["source_identity"]
                    for entry in provenance.get("skills", {}).values()
                ):
                    raise PlanRefusal("origin-changed", "Original source decision or synchronization changed after preview")
                if self._import_proof(source, skill, record, current) != value["stored"]:
                    raise PlanRefusal("origin-changed", "Import preservation changed after preview")
                continue
            stored = (provenance.get("skills", {}).get(skill["id"][4:]) or {}).get("sync")
            if stored != value["stored"] or approval_witness(source, self.state, self.catalog, current) != value["witness"] or content_hash(Path(source["root"])) != source["content_hash"]:
                raise PlanRefusal("origin-changed", "Native origin identity, lineage or approval changed after preview")
            if self._lineage(stored, skill, source, current) != value["status"]:
                raise PlanRefusal("origin-changed", "Native preservation changed after preview")
            self._preserved_tree(Path(source["root"]), Path(skill["root"]))
