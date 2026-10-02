"""Public standalone Full export; preparation never grants project exposure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..library.exporting import ExportRefusal, export_skill
from .context import personal_catalog_root


def add_export_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("export", help="Write one current accepted Full payload into a missing or empty directory.")
    parser.add_argument("skill_id", help="Canonical owned lib/<name> ID.")
    parser.add_argument("--version", required=True, help="Full accepted Skillager content hash; historical bytes are not restored.")
    parser.add_argument("--agent", choices=["codex", "claude"], required=True)
    parser.add_argument("--dest", type=Path, required=True, help="Missing or empty directory with an existing non-symlink parent.")
    parser.add_argument("--json", action="store_true", help="Emit file paths, numeric modes, sizes, and hashes; no skill bodies.")
    parser.set_defaults(func=cmd_export)


def cmd_export(args: argparse.Namespace) -> int:
    try:
        result = export_skill(personal_catalog_root(args), args.skill_id, version=args.version,
                              agent=args.agent, destination=args.dest)
    except (ExportRefusal, OSError, ValueError) as error:
        code = error.code if isinstance(error, ExportRefusal) else "library_unavailable"
        result = {"schema": "skillager.export.v1", "status": "refused", "files": [],
                  "error": {"code": code, "message": str(error) if isinstance(error, ExportRefusal) else "library or request metadata is unavailable"}}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["status"] == "exported":
        print(f"Exported {result['id']} ({result['content_hash']}) to {result['destination']}")
    else:
        print(f"Export refused: {result['error']['code']}: {result['error']['message']}")
    return 0 if result["status"] == "exported" else 2
