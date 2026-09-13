from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..library.model import normalize_library_id
from ..library.sync import LibraryBindingError, OUTCOMES, sync_approved, sync_refusal
from .context import catalog_root, current_project_dir, root


def add_sync_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("sync", help="Preserve approved sources in your reusable personal library.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--approved", action="store_true", help="Sync approved effective sources; preserve originals and protected library copies.")
    action.add_argument("--status", action="store_true", help="Observe sync eligibility and lineage without changing the library or approvals.")
    parser.add_argument("--expected-library-id", help="Require this exact registered library UUID; never initialize or select another library.")
    parser.add_argument("--expected-library-root", type=Path, help="Require this exact absolute registered library root, paired with its UUID.")
    parser.add_argument("--json", action="store_true", help="Emit versioned metadata-only sync results.")
    parser.set_defaults(func=cmd_library_sync)


def cmd_library_sync(args: argparse.Namespace) -> int:
    expected_id, expected_root = args.expected_library_id, args.expected_library_root
    if bool(expected_id) != (expected_root is not None):
        raise ValueError("--expected-library-id and --expected-library-root must be supplied together")
    if expected_root is not None and (not expected_root.is_absolute() or ".." in expected_root.parts):
        raise ValueError("expected library root must be an absolute canonical path")
    if expected_id:
        expected_id = normalize_library_id(expected_id)
    try:
        result = sync_approved(root(args), catalog_root(args), status_only=args.status,
                              project_dir=current_project_dir(), expected_library_id=expected_id,
                              expected_library_root=expected_root)
    except LibraryBindingError:
        result = sync_refusal(args.status, "library-changed")
    except (OSError, ValueError):
        result = sync_refusal(args.status)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.status:
        print(f"Your library: {len(result.get('lineages', []))} synchronized sources; observation only.")
    else:
        print_sync_result(result)
    return 0 if result.get("status", "completed") == "completed" else 2


def print_sync_result(result: dict[str, Any]) -> None:
    counts = result.get("counts", {})
    print("Your library: " + ", ".join(f"{counts.get(name, 0)} {name}" for name in OUTCOMES if counts.get(name)))
    if result.get("status") != "completed":
        print("Some skills were not synchronized. Use `skillager library sync --status --json` to inspect the current state.")
