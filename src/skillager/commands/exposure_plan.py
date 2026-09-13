"""Public CLI admission and response boundary for local exposure lifecycle plans."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..exposure.plan import ExposurePlan
from ..exposure.plan_apply import apply_plan
from ..exposure.plan_request import PLAN_SCHEMA, PlanRefusal, encode_plan_output, parse_request, public_refusal_reason
from .context import catalog_root, current_project_dir, root


def cmd_exposure_plan(args: argparse.Namespace) -> int:
    plan: ExposurePlan | None = None
    try:
        conflicts = [name for name in ("skill_ids", "mode", "tag", "all_reviewed", "all_agents", "force", "allow_incompatible",
                     "source", "collection", "audience", "package", "activation", "include_blocked", "list_exposures", "remove", "exposure_id")
                     if getattr(args, name, None)]
        if conflicts or (not args.agent or len(args.agent) != 1 or args.agent[0] not in {"codex", "claude"}) or args.scope != "project" or not args.json:
            raise PlanRefusal("invalid-options", "Lifecycle requests require --json, one --agent codex|claude, --scope project and no selection filters or overrides")
        if args.dry_run:
            if args.yes or args.confirmation_token:
                raise PlanRefusal("invalid-options", "Preview cannot confirm an exposure plan")
        elif not args.yes or not args.confirmation_token:
            raise PlanRefusal("confirmation-required", "Request a complete --dry-run --json preview, then apply its exact token with --yes")
        request = parse_request(args.request_json)
        with tempfile.TemporaryDirectory(prefix="skillager-exposure-preview-") as scratch:
            plan = ExposurePlan(request, state=root(args), catalog=catalog_root(args), project=current_project_dir(), agent=args.agent[0], scratch=Path(scratch))
            if args.dry_run:
                result, code = plan.preview(), 0
            else:
                result, code = apply_plan(plan, args.confirmation_token)
        sys.stdout.write(encode_plan_output(result))
        return code
    except (OSError, ValueError, KeyError) as error:
        reason_code = error.code if isinstance(error, PlanRefusal) else "state-changed" if args.confirmation_token else "unavailable"
        refused: dict[str, Any] = {"schema": PLAN_SCHEMA, "status": "refused", "reason_code": reason_code, "reason": public_refusal_reason(error), "results": []}
        if plan is not None:
            refused.update(plan.payload)
            refused["results"] = [{"target_id": target["target_id"], "path": target["path"], "status": "refused", "reason_code": reason_code} for target in plan.payload["targets"]]
        sys.stdout.write(encode_plan_output(refused))
        return 2
