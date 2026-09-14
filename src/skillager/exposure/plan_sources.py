"""Selected source authority and native preservation for an exposure plan."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ..compatibility import compatibility_problem
from ..library.importing import import_inventory
from ..library.metadata import load_library_provenance
from ..library.sync import _group_sources, library_binding
from ..library.sync_lineage import canonical_approval_key, canonical_target_state, lineage_status, origin
from ..state import approvals
from ..state.library_approval import approval_witness, public_approval
from ..state.trust import APPROVED_TRUST_STATES, content_hash
from .plan_request import PlanRefusal
from .impl import _project_agent_bases
from .preview import exposure_source_state
from .target_state import MATERIALIZED_SIDECAR, target_state_manifest
from .plan_targets import bounded_metadata


class PlanSources:
    """Resolve one inventory/map; final checks reread only selected bytes/decisions."""

    def __init__(self, state: Path, catalog: Path, project: Path, agent: str) -> None:
        self.state, self.catalog = state.resolve(), catalog.resolve()
        self.project, self.agent = project.resolve(), agent
        self.binding = library_binding(self.catalog, None, None)
        if self.binding is None:
            raise PlanRefusal("library-unavailable", "Connect an existing personal library before exposure changes")
        self.registration = self.binding[0]
        self.library_id = self.registration.library_id
        self.layout = self.registration.layout
        inventory = import_inventory(self.state, self.catalog)
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
                                            "skill": skill, "witness": witness, "status": status}
        return skill, target

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
                                 for value in self.selected_origins.values() if value["skill"]["id"] == skill_id]
            bounded_metadata(item)
            result.append(item)
        return result

    def revalidate(self, records: dict[Path, dict[str, Any]] | None = None) -> None:
        library_binding(self.catalog, self.library_id, self.layout.root)
        current = records if records is not None else self.read_records()
        provenance = (load_library_provenance(self.layout) or {"skills": {}})
        for value in self.selected.values():
            skill, target = value["skill"], Path(value["skill"]["root"])
            if content_hash(target) != skill["content_hash"] or canonical_target_state(target) != value["target_state"] or approval_witness(skill, self.state, self.catalog, current) != value["witness"]:
                raise PlanRefusal("source-changed", "Canonical content, identity or approval changed after preview")
        for value in self.selected_origins.values():
            source, skill = value["source"], value["skill"]
            stored = (provenance.get("skills", {}).get(skill["id"][4:]) or {}).get("sync")
            if stored != value["stored"] or approval_witness(source, self.state, self.catalog, current) != value["witness"] or content_hash(Path(source["root"])) != source["content_hash"]:
                raise PlanRefusal("origin-changed", "Native origin identity, lineage or approval changed after preview")
            if self._lineage(stored, skill, source, current) != value["status"]:
                raise PlanRefusal("origin-changed", "Native preservation changed after preview")
            self._preserved_tree(Path(source["root"]), Path(skill["root"]))
