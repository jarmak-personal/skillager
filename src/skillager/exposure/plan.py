"""Select and preview one closed local exposure action, without project writes."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .. import project_tags
from ..library.confirmation import confirmation_token
from ..state.statefiles import write_user_json
from ..trust import content_hash
from .impl import (_collision_safe_target, _direct_projection_identity, _exposure_decisions,
                   _project_agent_bases, _require_safe_project_projection_target,
                   _router_projection_identity, prepare_direct_candidate, prepare_router_candidate, slugify)
from .management import _exposure_records
from .identity import recorded_source_keys, source_key
from .plan_request import MAX_EFFECTS, MAX_MEMBERS, MAX_OUTPUT_BYTES, MAX_REASON_BYTES, MAX_STAGED_BYTES, MAX_TARGETS, PLAN_SCHEMA, PlanRefusal, canonical_json, encode_plan_output, library_skill
from .plan_sources import PlanSources
from .plan_targets import PlanTarget, bounded_metadata, directory_state, file_state, managed_data, require_managed_metadata, result_identity, tree_state
from .target_state import MATERIALIZED_SIDECAR


class ExposurePlan:
    def __init__(self, request: dict[str, Any], *, state: Path, catalog: Path, project: Path, agent: str, scratch: Path) -> None:
        self.request, self.project, self.agent, self.scratch = request, project.resolve(), agent, scratch
        self.require_staging_clear()
        self.sources = PlanSources(state, catalog, self.project, agent)
        self.records = _exposure_records(self.project, agents=["codex", "claude"], scope="project")
        self.targets: list[PlanTarget] = []
        self.group: dict[str, Any] | None = None
        self.tag_state = project_tags.load_tags(self.project)
        bounded_metadata(self.tag_state)
        self.tag_read_state = file_state(project_tags.tags_path(self.project))
        self.shared_tag: str | None = None
        self.staged_bytes = 0
        self._select()
        self._parents()
        self.staging = self._staging_budget()
        self.sources.revalidate()
        self.revalidate_targets()
        self.payload = {"schema": PLAN_SCHEMA, "request": request, "project": str(self.project), "agent": agent,
                        "scope": "project", "library_id": self.sources.library_id, "sources": self.sources.public(),
                        "group": self.group, "staging": self.staging, "targets": [target.public() for target in self.targets]}
        if sum(len(target["file_effects"]) for target in self.payload["targets"]) > MAX_EFFECTS:
            raise PlanRefusal("effect-limit", "Exposure plan exceeds 2,048 complete file effects")
        self.token = confirmation_token("exposure-plan", **self.payload)
        # Admission uses the exact public serialization, with a complete worst-case
        # result envelope including retained recovery paths, not compact-size guesses.
        worst_results = [{**result_identity(target), "status": "recovery_required", "reason_code": "x" * 64,
                          "observed_state_hash": "f" * 64,
                          "recovery_path": str(Path(target["path"]).parent / (".skillager-exposure-plan-" + "x" * 16) / "interrupted-current")}
                         for target in self.payload["targets"]]
        worst_apply = {**self.payload, "status": "partial", "plan_hash": self.token, "reason_code": "x" * 64, "results": worst_results}
        worst_refusal = {**self.payload, "status": "refused", "reason_code": "x" * 64, "reason": "x" * MAX_REASON_BYTES,
                         "results": [{"target_id": target["target_id"], "path": target["path"], "status": "refused", "reason_code": "x" * 64}
                                     for target in self.payload["targets"]]}
        if max(len(encode_plan_output(value).encode("utf-8")) for value in (self.preview(), worst_apply, worst_refusal)) > MAX_OUTPUT_BYTES:
            raise PlanRefusal("output-limit", "Exposure plan exceeds the complete 4 MiB output limit")

    def require_staging_clear(self) -> None:
        # Existing retained stages are an explicit stop, not an unbounded extra
        # allocation or an implicit cleanup/recovery service on the next action.
        parents = {self.project / ".skillager"}
        for agent in ("codex", "claude"):
            parents.update(_project_agent_bases(self.project, agent))
        for parent in sorted(parents):
            if parent.is_symlink() or not parent.is_dir():
                continue
            retained = next(parent.glob(".skillager-exposure-plan-*"), None)
            if retained is not None:
                raise PlanRefusal("staging-present", f"Inspect the existing exposure staging/recovery location before another action: {retained}")

    def preview(self) -> dict[str, Any]:
        return {**self.payload, "status": "would_apply", "confirmation_token": self.token,
                "next_command_argv": ["skillager", "expose", "--request-json", canonical_json(self.request),
                                      "--agent", self.agent, "--scope", "project", "--json", "--yes", "--confirmation-token", self.token]}

    def _select(self) -> None:
        action = self.request["action"]
        if action in {"adopt-native", "remove-native"}:
            self.sources.require_library(self.request["source"]["library_id"])
            skill, path = self.sources.native(self.request["origin_id"], self.request["source"]["skill_id"])
            if action == "remove-native":
                self._add(PlanTarget(path, "native-origin", tree_state(path), identity={"skill_id": skill["id"], "origin_id": self.request["origin_id"], "mode": None}))
            else:
                self._direct(skill, self.request["mode"], target=path, adopted_origin=self.request["origin_id"])
            return
        if action == "group":
            self.sources.require_library(self.request["library_id"])
            tag = project_tags.normalize_tag(self.request["name"])
            if tag in self.tag_state["tags"] or any(item.get("tag") == tag for item in self.records):
                raise PlanRefusal("name-conflict", "This project tag/router name already exists; choose a new name")
            members = sorted(self.request["members"])
            skills = [self.sources.skill(skill_id) for skill_id in members]
            path = self._default_base() / f"skillager-{tag}"
            if path.exists() or path.is_symlink():
                raise PlanRefusal("name-conflict", "The named router destination already exists")
            self.group = {"tag": tag, "before_members": [], "after_members": members, "tag_policy": "create", "before_tag_members": [], "after_tag_members": members}
            self._router(path, tag, skills)
            self._replacements(set(members))
            self._tag(tag, members)
            return
        router, data = self._managed(self.request["router_id"], router=True)
        old = data.get("skill_ids")
        if not isinstance(old, list) or not old or len(old) > MAX_MEMBERS or not all(isinstance(member, str) for member in old) or len(set(old)) != len(old):
            raise PlanRefusal("unsupported-router", "Router membership is missing, ambiguous or exceeds 64 members")
        for member in old:
            library_skill(member)
        tag = str(data["tag"]) if data.get("tag") else ""
        before_tag = list((self.tag_state["tags"].get(tag) or {}).get("skills", [])) if tag else []
        if action == "ungroup":
            self._require_member_sources(data, old)
            self.group = {"tag": tag, "before_members": sorted(old), "after_members": [], "tag_policy": "retained", "before_tag_members": before_tag, "after_tag_members": before_tag}
            for member in sorted(old):
                self._direct(self.sources.skill(member), self.request["mode"])
            self._remove_managed(router, data)
            self._retain_tag()
            return
        self.sources.require_library(self.request["library_id"])
        if data.get("router_kind") != "tag" or not isinstance(tag, str):
            raise PlanRefusal("unsupported-router-kind", "Only named tag routers support changing membership; legacy routers can be ungrouped or removed")
        members = sorted(self.request["members"])
        departures = {item["skill_id"]: item["mode"] for item in self.request["departures"]}
        if set(departures) != set(old) - set(members):
            raise PlanRefusal("incomplete-departures", "Every departing router member must explicitly select Full, Stub or Remove")
        if sorted(before_tag) != sorted(old):
            raise PlanRefusal("tag-changed", "Project tag curation differs from this router; preserve that curation before changing membership")
        self.shared_tag = tag
        self._require_unshared(router)
        self._require_member_sources(data, [member for member in old if member in members or departures[member] != "remove"])
        skills = [self.sources.skill(member) for member in members]
        self.group = {"tag": tag, "before_members": sorted(old), "after_members": members, "tag_policy": "update", "before_tag_members": before_tag, "after_tag_members": members}
        if members:
            self._router(router, tag, skills, data=data)
        else:
            self._remove_managed(router, data)
        for member, mode in sorted(departures.items()):
            if mode != "remove":
                self._direct(self.sources.skill(member), mode)
        self._replacements(set(members))
        self._tag(tag, members)

    def _require_member_sources(self, data: dict[str, Any], members: list[str]) -> None:
        keys = recorded_source_keys(data)
        if any(keys.get(member) != source_key(member, self.sources.library_id) for member in members):
            raise PlanRefusal("source-identity", "Recorded router member library identity is missing or differs from the connected library; remove without restoring instead")

    def _default_base(self) -> Path:
        return _project_agent_bases(self.project, self.agent)[0]

    def _managed(self, exposure_id: str, *, router: bool = False) -> tuple[Path, dict[str, Any]]:
        matches = [item for item in self.records if item["exposure_id"] == exposure_id and item["agent"] == self.agent and item["scope"] == "project"]
        if len(matches) != 1:
            raise PlanRefusal("target-ambiguous" if matches else "target-missing", "Select one existing managed exposure in this exact project and agent")
        item = matches[0]
        path = Path(item["target"])
        self._safe(path)
        data = managed_data(path, agent=self.agent)
        if bool(data.get("source_type") == "skillager-router") != router:
            raise PlanRefusal("target-identity", "The selected exposure has a different kind")
        return path, data

    def _safe(self, path: Path) -> None:
        if path.parent not in _project_agent_bases(self.project, self.agent):
            raise PlanRefusal("unsafe-target", "The selected target is not a direct project skill directory")
        _require_safe_project_projection_target(self.project, base=path.parent, target=path)
        current = path.parent
        while current != self.project:
            directory_state(current)
            current = current.parent

    def _direct(self, skill: dict[str, Any], mode: str, *, target: Path | None = None, adopted_origin: str | None = None) -> None:
        identity = _direct_projection_identity(skill)
        restoring = target is None
        if target is None:
            # Restorations select the normal destination, preserving any other existing copy.
            target = _collision_safe_target(self._default_base() / slugify(skill["id"]), identity, fallback_key=skill["id"], require_exact=False)
        self._safe(target)
        before = tree_state(target)
        data: dict[str, Any] = {}
        if before is not None and adopted_origin is None:
            data = managed_data(target, agent=self.agent)
            require_managed_metadata(before, data)
            if data.get("projection_kind") != "direct" or data.get("projection_identity") != identity:
                raise PlanRefusal("target-identity", "Direct destination belongs to another source")
            existing_mode = "stub" if data.get("source_type") == "skillager-stub" else "native"
            if restoring and (data.get("source_hash") != skill["content_hash"] or existing_mode != mode):
                raise PlanRefusal("standalone-conflict", "An unselected standalone copy has a different version or mode; change that exact copy separately")
        decisions = _exposure_decisions(target / MATERIALIZED_SIDECAR) if data else {}
        if adopted_origin:
            decisions["adopted_native_origin"] = adopted_origin
        if data and data.get("source_hash") == skill["content_hash"] and ("stub" if data.get("source_type") == "skillager-stub" else "native") == mode:
            if content_hash(target) in decisions.get("exposure_blocked_hashes", []):
                raise PlanRefusal("blocked-exposure", "This exact exposure hash is blocked by prior project policy")
            self._add(PlanTarget(target, "direct", before, identity={"exposure_id": target.name, "skill_id": skill["id"], "mode": mode}, keep=True))
            return
        candidate = self._candidate()
        self._reserve_source(skill, mode)
        metadata = prepare_direct_candidate(skill, candidate=candidate, target=target, agent=self.agent, scope="project", mode=mode,
                                            decisions=decisions, root_mode=before["mode"] if before else 0o755)
        if content_hash(candidate) in decisions.get("exposure_blocked_hashes", []):
            raise PlanRefusal("blocked-exposure", "This exact exposure hash is blocked by prior project policy")
        item = PlanTarget(target, "direct", before, candidate, metadata, {"exposure_id": target.name, "skill_id": skill["id"], "mode": mode})
        self._add(item)

    def _router(self, path: Path, tag: str, skills: list[dict[str, Any]], *, data: dict[str, Any] | None = None) -> None:
        self._safe(path)
        before = tree_state(path)
        identity = _router_projection_identity(tag=tag, router_slug=None, router_kind="tag")
        if data:
            require_managed_metadata(before, data)
        if data and data.get("projection_identity") != identity:
            raise PlanRefusal("target-identity", "The named router has a different managed identity")
        decisions = _exposure_decisions(path / MATERIALIZED_SIDECAR) if data else {}
        candidate = self._candidate()
        metadata = prepare_router_candidate(tag, skills, candidate=candidate, target=path, agent=self.agent, scope="project", router_slug=None,
                                            router_kind="tag", decisions=decisions, root_mode=before["mode"] if before else 0o755)
        if content_hash(candidate) in decisions.get("exposure_blocked_hashes", []):
            raise PlanRefusal("blocked-exposure", "This exact router hash is blocked by prior project policy")
        self._add(PlanTarget(path, "router", before, candidate, metadata, {"exposure_id": path.name, "mode": "router", "skill_ids": [skill["id"] for skill in skills], "tag": tag}))

    def _remove_managed(self, path: Path, data: dict[str, Any]) -> None:
        before = tree_state(path)
        require_managed_metadata(before, data)
        self._add(PlanTarget(path, "router" if data.get("source_type") == "skillager-router" else "direct", before,
                             identity={"exposure_id": path.name, "skill_id": data.get("source_id"), "mode": None}))

    def _replacements(self, members: set[str]) -> None:
        for selected in self.request["replace"]:
            if "origin_id" in selected:
                skill, path = self.sources.native(selected["origin_id"])
                if skill["id"] not in members:
                    raise PlanRefusal("replacement-mismatch", "A replaced native source must be a desired router member")
                self._add(PlanTarget(path, "native-origin", tree_state(path), identity={"origin_id": selected["origin_id"], "skill_id": skill["id"], "mode": None}))
            else:
                path, data = self._managed(selected["exposure_id"])
                member = data.get("source_id")
                if member not in members or data.get("projection_identity") != _direct_projection_identity(self.sources.skill(member)):
                    raise PlanRefusal("replacement-mismatch", "A replaced direct exposure must belong to a desired canonical member")
                self._remove_managed(path, data)

    def _tag(self, tag: str, members: list[str]) -> None:
        metadata = deepcopy(self.tag_state)
        entry = metadata.setdefault("tags", {}).setdefault(tag, {})
        entry["skills"] = members
        entry.pop("updated_at", None)
        bounded_metadata(metadata)
        candidate = self._candidate()
        current = deepcopy(metadata)
        project_tags._touch_tag(current["tags"][tag])
        write_user_json(candidate, current)
        candidate.chmod(self.tag_read_state["mode"] if self.tag_read_state else 0o600)
        self._add(PlanTarget(project_tags.tags_path(self.project), "tags", self.tag_read_state, candidate, metadata, {"tag": tag}))

    def _retain_tag(self) -> None:
        if self.tag_read_state is not None:
            self._add(PlanTarget(project_tags.tags_path(self.project), "tags", self.tag_read_state, keep=True))

    def _require_unshared(self, selected: Path) -> None:
        if any(item.get("tag") == self.shared_tag and Path(item["target"]) != selected for item in _exposure_records(self.project, agents=["codex", "claude"], scope="project")):
            raise PlanRefusal("shared-tag-conflict", "Another router uses this project tag; choose a separate named group")

    def _candidate(self) -> Path:
        return self.scratch / f"candidate-{len(self.targets)}"

    def _reserve_source(self, skill: dict[str, Any], mode: str) -> None:
        if mode == "native":
            state = tree_state(Path(skill["root"]))
            assert state is not None
            size = sum(item.get("size", 0) for item in state["entries"].values())
            if self.staged_bytes + size > MAX_STAGED_BYTES:
                raise PlanRefusal("payload-limit", "Exposure plan exceeds 128 MiB of staged content")

    def _add(self, target: PlanTarget) -> None:
        if any(item.path == target.path for item in self.targets):
            raise PlanRefusal("target-conflict", "Two selections affect the same destination")
        if target.candidate is not None:
            state = target.observe(target.candidate)
            assert state is not None
            target.prepared_state = deepcopy(state)
            self.staged_bytes += state["size"] if target.kind == "tags" else sum(item.get("size", 0) for item in state["entries"].values())
            if self.staged_bytes > MAX_STAGED_BYTES:
                raise PlanRefusal("payload-limit", "Exposure plan exceeds 128 MiB including generated metadata")
        self.targets.append(target)
        if len(self.targets) > MAX_TARGETS:
            raise PlanRefusal("target-limit", "Exposure plan exceeds 128 targets")

    def _staging_budget(self) -> dict[str, int]:
        originals, transfer_reserve = 0, 0
        for target in self.targets:
            if target.keep or target.kind == "parent":
                continue
            if target.before is not None:
                originals += target.before["size"] if target.kind == "tags" else sum(item.get("size", 0) for item in target.before["entries"].values())
            if target.candidate is not None:
                ancestor = target.path.parent
                while not ancestor.exists():
                    ancestor = ancestor.parent
                if ancestor.stat().st_dev != target.candidate.stat().st_dev:
                    state = target.prepared_state
                    assert state is not None
                    size = state["size"] if target.kind == "tags" else sum(item.get("size", 0) for item in state["entries"].values())
                    transfer_reserve = max(transfer_reserve, size)
        peak = self.staged_bytes + originals + transfer_reserve
        if peak > MAX_STAGED_BYTES:
            raise PlanRefusal("payload-limit", "Exposure exceeds 128 MiB peak staging, including retained originals and cross-filesystem transfer")
        return {"candidate_bytes": self.staged_bytes, "retained_original_bytes": originals,
                "transfer_reserve_bytes": transfer_reserve, "peak_bytes": peak, "limit_bytes": MAX_STAGED_BYTES}

    def _parents(self) -> None:
        parents = set()
        for target in self.targets:
            current = target.path.parent
            while current != self.project:
                parents.add(current)
                current = current.parent
        for parent in sorted(parents, key=lambda path: (len(path.parts), str(path))):
            before = directory_state(parent)
            self._add(PlanTarget(parent, "parent", before, keep=before is not None, parent_mode=0o700 if parent.name == ".skillager" else 0o755))
        self.targets.sort(key=lambda target: (target.kind != "parent", len(target.path.parts), str(target.path)))

    def revalidate_targets(self) -> None:
        for target in self.targets:
            target.revalidate()
        if file_state(project_tags.tags_path(self.project)) != self.tag_read_state or project_tags.load_tags(self.project) != self.tag_state:
            raise PlanRefusal("tag-changed", "Project tag state changed; request a fresh preview")
        if self.shared_tag:
            selected = next(target.path for target in self.targets if target.kind == "router")
            self._require_unshared(selected)
