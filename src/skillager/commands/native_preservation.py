"""Selected-original metadata observation for the existing exposure plan API."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ..exposure.impl import _project_agent_bases
from ..exposure.plan_request import MAX_OUTPUT_BYTES, REQUEST_SCHEMA, PlanRefusal, encode_plan_output, public_refusal_reason
from ..exposure.plan_sources import PlanSources
from ..library.sync_lineage import canonical_target_state
from .context import catalog_root, current_project_dir, root


NATIVE_PRESERVATION_SCHEMA = "skillager.native-preservation.v1"


def cmd_native_preservation(args: argparse.Namespace) -> int:
    result: dict[str, Any] = {"schema": NATIVE_PRESERVATION_SCHEMA, "status": "unavailable",
                              "reason_code": None, "origin": None, "candidates": []}
    try:
        conflicts = [name for name in ("skill_ids", "mode", "tag", "all_reviewed", "all_agents", "force", "allow_incompatible",
                     "source", "collection", "audience", "package", "activation", "include_blocked", "list_exposures", "remove",
                     "exposure_id", "request_json", "dry_run", "yes", "confirmation_token") if getattr(args, name, None)]
        if conflicts or not args.json or args.scope != "project" or not args.agent or len(args.agent) != 1:
            raise PlanRefusal("invalid-options", "Native observation requires --json, one --agent, --scope project and no exposure action or filters")
        project, agent = current_project_dir().resolve(), args.agent[0]
        path = Path(args.observe_native)
        if (len(os.fsencode(path)) > 4096 or not path.is_absolute() or path != path.resolve()
                or path.name != "SKILL.md" or path.is_symlink() or not path.is_file()
                or path.parent.parent not in _project_agent_bases(project, agent)
                or not path.is_relative_to(project)):
            raise PlanRefusal("origin-mismatch", "Select the exact canonical instruction file of one native original in this project and agent")
        # Bound the selected tree before discovery can hash/scan its content.
        canonical_target_state(path.parent)
        sources = PlanSources(root(args), catalog_root(args), project, agent, selected_native_file=path)
        pairs = [(key, pair) for key, pair in sources.origins.items() if pair[1]["entrypoint"] == str(path)]
        if len(pairs) != 1:
            raise PlanRefusal("origin-unavailable", "The selected unmanaged native occurrence is no longer observed")
        origin_id, (_source, observed) = pairs[0]
        result.update(project=str(project), agent=agent, scope="project", library_id=sources.library_id, origin=observed)
        basis, candidates = sources.native_choices(origin_id)
        for skill_id in candidates:
            item: dict[str, Any] = {"source": {"library_id": sources.library_id, "skill_id": skill_id},
                                    "basis": basis,
                                    "status": "unavailable", "reason_code": None, "requests": None}
            try:
                sources.native(origin_id, skill_id)
                selected = sources.selected_origins[origin_id]
                item.update(status="available", proof_id=(selected["status"]["lineage_id"] if basis == "approved-sync" else selected["stored"]["proof_id"]),
                            requests={mode: {"schema": REQUEST_SCHEMA, "action": "adopt-native", "origin_id": origin_id,
                                             "source": item["source"], "mode": mode} for mode in ("native", "stub")})
            except (OSError, ValueError, KeyError) as error:
                item.update(reason_code=error.code if isinstance(error, PlanRefusal) else "origin-unpreserved", reason=public_refusal_reason(error))
            result["candidates"].append(item)
        available = sum(item["status"] == "available" for item in result["candidates"])
        result.update(status="ambiguous" if available > 1 else "available" if available else "unavailable",
                      reason_code="origin-ambiguous" if available > 1 else None if available else "origin-unpreserved")
        if len(encode_plan_output(result).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise PlanRefusal("output-limit", "Native observation exceeds the complete 4 MiB output limit")
    except (OSError, ValueError, KeyError) as error:
        result.update(status="unavailable", reason_code=error.code if isinstance(error, PlanRefusal) else "origin-unpreserved",
                      reason=public_refusal_reason(error), candidates=[])
    sys.stdout.write(encode_plan_output(result))
    return 0 if result["status"] in {"available", "ambiguous"} else 2
