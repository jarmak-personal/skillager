"""One explicit save/accept and native replacement decision, with distinct outcomes."""
from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from ..compatibility import compatibility_problem
from ..exposure.impl import _project_agent_bases, _require_safe_project_projection_target, prepare_direct_candidate
from ..exposure.plan import ExposurePlan, require_staging_clear
from ..exposure.plan_apply import apply_plan_locked
from ..exposure.plan_request import MAX_EFFECTS, MAX_REASON_BYTES, PlanRefusal, public_refusal_reason
from ..exposure.plan_sources import PlanSources, require_import_source
from ..exposure.plan_targets import PlanTarget, digest, parent_targets, staging_budget, tree_state
from ..exposure.target_state import MATERIALIZED_SIDECAR
from ..state import approvals
from ..state.locking import resource_locks
from .candidate import prepare_library_candidate
from .confirmation import confirmation_token, require_confirmation_token
from .importing import _resolve_external_skill, import_library_skill_locked, import_preview
from .model import LIBRARY_NAMESPACE, LibraryLayout
from .review import REVIEW_LIMITS, limit_refusal
from .service import _acceptance_overrides, initialize_library_locked
from .source_identity import origin, source_identity
from .sync_lineage import canonical_target_state


IMPORT_PLACEMENT_SCHEMA = "skillager.import-placement.v1"
_IDENTITY_NAMESPACE = UUID("a9f63aad-d5bf-41ec-aeb0-3c0794fe3e3c")


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: public_payload(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    return value


def encode_result(value: dict[str, Any]) -> str:
    encoded = json.dumps(public_payload(value), indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > REVIEW_LIMITS["response_bytes"]:
        raise limit_refusal()
    return encoded


class ImportPlacement:
    """A prospective file plan is not an accepted source or an exposure authority."""

    def __init__(self, state: Path, catalog: Path, project: Path, source_id: str,
                 source_file: Path, name: str | None, agent: str, mode: str, scratch: Path):
        self.state, self.catalog, self.project = state.resolve(), catalog.resolve(), project.resolve()
        self.agent, self.mode, self.source_file = agent, mode, source_file
        if agent not in {"codex", "claude"} or mode not in {"native", "stub"}:
            raise PlanRefusal("invalid-options", "Choose one project agent and Full or Stub")
        if (not source_file.is_absolute() or source_file != source_file.resolve()
                or source_file.name != "SKILL.md" or source_file.is_symlink()
                or source_file.parent.parent not in _project_agent_bases(self.project, agent)):
            raise PlanRefusal("origin-mismatch", "Choose an exact native project SKILL.md for this agent")
        target = source_file.parent
        _require_safe_project_projection_target(self.project, base=target.parent, target=target)
        canonical_target_state(target)
        before = tree_state(target)
        if before is None or (target / MATERIALIZED_SIDECAR).exists():
            raise PlanRefusal("origin-mismatch", "Choose an unmanaged native project original")
        require_staging_clear(self.project)
        self.import_plan = import_preview(self.state, self.catalog, source_id, destination_name=name,
                                          project_dir=self.project, source_file=source_file, review_manifest=True)
        source = _resolve_external_skill(self.state, self.catalog, source_id, source_file=source_file)
        native = source.get("native") or {}
        if native.get("agent") != agent or native.get("scope") != "project" or native.get("managed"):
            raise PlanRefusal("origin-mismatch", "The discovered occurrence is not this agent's native project original")
        records = {root: approvals.load(root) for root in {self.state, self.catalog}}
        PlanSources._validate_records(records)
        require_import_source(source, self.state, self.catalog, agent, records)
        _acceptance_overrides(source, override_lint=False, reason=None)
        if self.import_plan["blocked"]:
            raise PlanRefusal("origin-blocked", "Unblock this source before saving and using it")
        self.observed_origin = origin(source, self.project, source_id=source_identity(source))
        manifest = self.import_plan["review_manifest"]
        self.library_id = manifest["library"]["library_id"]
        if self.library_id is None:
            # Deterministic planned identity makes repeated previews stable. It is
            # explicitly future state, created only by confirmed initialization.
            identity_seed = digest({"catalog": str(self.catalog), "import": public_payload(self.import_plan)})
            self.library_id = str(uuid5(_IDENTITY_NAMESPACE, identity_seed))
        elif manifest["library"]["status"] == "ready":
            existing = PlanSources(self.state, self.catalog, self.project, agent, selected_native_file=source_file)
            _, choices = existing.native_choices(self.observed_origin["origin_id"])
            if choices:
                raise PlanRefusal("already-preserved", "This original already has a library relation; use its existing native adoption plan")
        layout = LibraryLayout.from_root(Path(manifest["library"]["root"]))
        candidate = scratch / "canonical"
        _, entry = prepare_library_candidate(target, candidate, layout, self.library_id,
                                               self.import_plan["destination"]["name"], manifest["source_hash"])
        _acceptance_overrides(entry, override_lint=False, reason=None)
        problem = compatibility_problem(entry, agent)
        if problem:
            raise PlanRefusal("source-incompatible", problem)
        PlanSources._preserved_tree(target, candidate)
        if tree_state(target) != before:
            raise PlanRefusal("target-changed", "The original changed while preparing the combined review")
        # Only output rendering uses prospective canonical metadata. No approval
        # record or PlanSources acceptance is synthesized during this preview.
        projected = {**entry, "root": manifest["destination_path"], "package": LIBRARY_NAMESPACE,
                     "entrypoint": str(Path(manifest["destination_path"]) / "SKILL.md"), "trust": "reviewed"}
        replacement = scratch / "replacement"
        metadata = prepare_direct_candidate(
            projected, candidate=replacement, content_root=candidate, target=target,
            agent=agent, scope="project", mode=mode,
            decisions={"adopted_native_origin": self.observed_origin["origin_id"]}, root_mode=before["mode"],
        )
        item = PlanTarget(target, "direct", before, replacement, metadata,
                          {"exposure_id": target.name, "skill_id": entry["id"], "mode": mode})
        item.prepared_state = tree_state(replacement)
        targets = [item, *parent_targets(self.project, [item])]
        targets.sort(key=lambda target: (target.kind != "parent", len(target.path.parts), str(target.path)))
        self.targets = [target.public() for target in targets]
        if sum(len(target["file_effects"]) for target in self.targets) > MAX_EFFECTS:
            raise PlanRefusal("effect-limit", "Combined replacement exceeds complete file-effect limits")
        self.request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native",
                        "origin_id": self.observed_origin["origin_id"],
                        "source": {"library_id": self.library_id, "skill_id": entry["id"]}, "mode": mode}
        self.payload = {
            "schema": IMPORT_PLACEMENT_SCHEMA, "project": str(self.project), "agent": agent, "mode": mode,
            "import": public_payload(self.import_plan),
            "library_identity": {"status": "will-create" if manifest["library"]["library_id"] is None else "existing",
                                 "library_id": self.library_id},
            "original": self.observed_origin,
            "source_decision_state": digest([records[self.state].get("skills", {}).get(source["id"]),
                                            records[self.catalog].get("global_approvals", {}).get(source.get("approval_key")),
                                            records[self.state].get("skills", {}).get(source.get("_decision_skill_id"))]),
            "replacement": {"request": self.request, "targets": self.targets, "staging": staging_budget(targets)},
        }
        self.token = confirmation_token("import-placement", **self.payload)
        # Admit the complete review and reserve stage metadata, bounded error copy
        # and one recovery path per actual target before any mutation.
        reserve = MAX_REASON_BYTES + 8192 + sum(len(json.dumps(target["path"]).encode()) * 3 + 2048 for target in self.targets)
        if len(encode_result(self.preview()).encode("utf-8")) > REVIEW_LIMITS["response_bytes"] - reserve:
            raise limit_refusal()

    def preview(self) -> dict[str, Any]:
        return {**self.payload, "status": "preview", "confirmation_token": self.token,
                "next_command_argv": ["skillager", "import", self.import_plan["source"]["id"],
                                      "--as", self.import_plan["destination"]["name"],
                                      "--source-file", str(self.source_file), "--review-manifest",
                                      "--project-mode", self.mode, "--agent", self.agent, "--json",
                                      "--yes", "--confirmation-token", self.token]}


def prepare_import_placement(state: Path, catalog: Path, project: Path, source_id: str,
                             source_file: Path, name: str | None, agent: str, mode: str,
                             scratch: Path) -> ImportPlacement:
    """Retain only complete metadata; discard prospective bytes before later stages."""
    scratch.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="skillager-placement-preview-", dir=scratch) as directory:
        return ImportPlacement(state, catalog, project, source_id, source_file, name, agent, mode, Path(directory))


def apply_import_placement(plan: ImportPlacement, token: str, scratch: Path) -> tuple[dict[str, Any], int]:
    result = None
    try:
        require_confirmation_token(token, plan.token, operation="save and use")
        name = plan.import_plan["destination"]["name"]
        with resource_locks([plan.catalog / "library-mutation", plan.catalog / f"library-skill-{name}"]):
            # Recompute under the same mutation locks used by ordinary library owners.
            current = prepare_import_placement(plan.state, plan.catalog, plan.project, plan.import_plan["source"]["id"],
                                              plan.source_file, name, plan.agent, plan.mode, scratch / "recheck")
            require_confirmation_token(token, current.token, operation="save and use")
            result, code = _apply_locked(current, scratch / "exposure")
        return result, code
    except (OSError, ValueError, KeyError) as error:
        if result is None:
            result = {**plan.payload, "status": "refused", "save": {"status": "not-started"},
                      "placement": {"status": "not-started"}}
        else:
            result["status"] = "partial"
        result["error"] = {"code": error.code if isinstance(error, PlanRefusal) else "unavailable",
                           "message": public_refusal_reason(PlanRefusal("unavailable", str(error)))}
        return result, 2


def _apply_locked(plan: ImportPlacement, scratch: Path) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {**plan.payload, "plan_hash": plan.token, "status": "uncertain",
                              "save": {"status": "not-started"}, "placement": {"status": "not-started"},
                              "initialization": None}
    manifest = deepcopy(plan.import_plan["review_manifest"])
    try:
        if manifest["library"]["status"] == "will-initialize":
            result["initialization"] = {"status": "uncertain", "root": manifest["library"]["root"]}
            initialization = initialize_library_locked(
                plan.catalog, path=Path(manifest["library"]["root"]), expected_first_use=manifest["library"],
                planned_library_id=plan.library_id if manifest["library"]["library_id"] is None else None,
            )
            result["initialization"] = {"status": initialization["status"], "root": initialization["library"]["root"],
                                         "library_id": initialization["library"]["library_id"]}
            manifest["library"]["library_id"] = initialization["library"]["library_id"]
        result["save"] = {"status": "uncertain", "destination": plan.import_plan["destination"]}
        imported = import_library_skill_locked(
            plan.state, plan.catalog, plan.import_plan["source"]["id"],
            destination_name=plan.import_plan["destination"]["name"], expected_hash=manifest["source_hash"],
            expected_source_key=plan.import_plan["_source_key"], project_dir=plan.project,
            source_file=plan.source_file, expected_review=manifest,
        )
        destination = {key: imported["destination"][key] for key in
                       ("id", "path", "working_hash", "accepted_hash", "acceptance")}
        result["save"] = {"status": "accepted", "destination": destination,
                          "approval": imported["approval"], "copied_file_count": imported["copied_file_count"]}
        scratch.mkdir(parents=True)
        actual = ExposurePlan(plan.request, state=plan.state, catalog=plan.catalog, project=plan.project,
                              agent=plan.agent, scratch=scratch)
        selected = actual.sources.selected_origins[plan.observed_origin["origin_id"]]
        if selected.get("basis") != "accepted-import" or selected.get("source_decision_state") != plan.payload["source_decision_state"]:
            raise PlanRefusal("origin-changed", "Saved in your library; the original's decisions changed before replacement")
        if actual.payload["targets"] != plan.targets or actual.payload["staging"] != plan.payload["replacement"]["staging"]:
            raise PlanRefusal("replacement-changed", "Saved in your library; the project replacement changed and was not applied")
        # This actual plan proves accepted preservation; the prospective plan
        # never enters the publisher or supplies an approval witness.
        result["placement"] = {"status": "uncertain"}
        applied, code = apply_plan_locked(actual, actual.token)
        result["placement"] = {"status": applied["status"], "results": applied["results"],
                               "reason_code": applied.get("reason_code")}
        result["status"] = "saved-and-replaced" if code == 0 else "saved-not-replaced" if applied["status"] == "refused" else "partial"
        return result, code
    except (OSError, ValueError, KeyError) as error:
        if result["save"]["status"] == "accepted" and result["placement"]["status"] != "uncertain":
            result["status"] = "saved-not-replaced"
            result["placement"] = {"status": "not-applied"}
        result["error"] = {"code": error.code if isinstance(error, PlanRefusal) else "stage-failed",
                           "message": public_refusal_reason(PlanRefusal("stage-failed", str(error)))}
        return result, 2
