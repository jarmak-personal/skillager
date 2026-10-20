"""Public admission for an explicitly combined save/accept and project placement."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from ..exposure.plan_request import PlanRefusal, public_refusal_reason
from ..library.import_placement import IMPORT_PLACEMENT_SCHEMA, apply_import_placement, encode_result, prepare_import_placement
from ..library.review import ReviewRefusal
from .context import catalog_root, current_project_dir, root


def cmd_import_placement(args: argparse.Namespace) -> int:
    value = None
    directory = None
    try:
        if (not args.json or not args.review_manifest or args.source_file is None
                or args.agent not in {"codex", "claude"} or args.override_lint or args.reason
                or (not args.yes and args.confirmation_token)):
            raise PlanRefusal("invalid-options", "Save and use requires --json --review-manifest --source-file and one --agent; overrides are unavailable")
        with tempfile.TemporaryDirectory(prefix="skillager-save-use-") as directory:
            scratch = Path(directory)
            plan = prepare_import_placement(root(args), catalog_root(args), current_project_dir(), args.skill,
                                           args.source_file, args.destination_name, args.agent, args.project_mode, scratch / "preview")
            if args.yes:
                value, code = apply_import_placement(plan, args.confirmation_token, scratch / "apply")
            else:
                value, code = plan.preview(), 0
    except (OSError, ValueError, KeyError) as error:
        reason = public_refusal_reason(PlanRefusal("unavailable", str(error)))
        if value is None or value.get("status") == "preview":
            value = {"schema": IMPORT_PLACEMENT_SCHEMA, "status": "refused",
                     "save": {"status": "not-started"}, "placement": {"status": "not-started"}}
        else:
            value["status"] = "partial"
            value["cleanup"] = {"status": "incomplete", "path": directory}
        value["error"] = {"code": error.code if isinstance(error, (PlanRefusal, ReviewRefusal)) else "unavailable", "message": reason}
        code = 2
    # A final encoding/output error is a missing response, never a preflight
    # refusal. In particular, it cannot relabel completed mutations not-started.
    sys.stdout.write(encode_result(value))
    return code
