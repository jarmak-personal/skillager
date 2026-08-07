from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from ..library.importing import import_library_skill, import_preview, import_refresh_preview
from .context import catalog_root, current_project_dir, root


def add_import_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "import",
        help="Adopt one discovered external skill into the personal library.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Preview and import one discovered external skill into the canonical personal library. "
            "The origin is never modified, and no candidate content enters the library before review."
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager import project/orbital-review --json
              skillager import community/brainstorm --as brainstorm --yes
              skillager import demo-package/release-check --yes
              skillager import --refresh lib/brainstorm --json
            """
        ),
    )
    parser.add_argument("skill", nargs="?", help="External discovered skill ID.")
    parser.add_argument("--as", dest="destination_name", help="Collision-free library skill name.")
    parser.add_argument("--refresh", metavar="LIBRARY_SKILL", help="Preview current upstream drift for an imported skill.")
    parser.add_argument("--yes", action="store_true", help="Confirm the exact reviewed source hash for import.")
    parser.add_argument(
        "--override-lint",
        action="store_true",
        help="Import lint-blocking or high-risk content with an audited --reason.",
    )
    parser.add_argument("--reason", help="Required audit reason with --override-lint.")
    parser.add_argument("--json", action="store_true", help="Emit versioned import or refresh metadata as JSON.")
    parser.set_defaults(func=cmd_import)


def cmd_import(args: argparse.Namespace) -> int:
    if args.refresh:
        _validate_refresh_args(args)
        result = import_refresh_preview(
            root(args),
            catalog_root(args),
            args.refresh,
            project_dir=current_project_dir(),
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            _print_refresh(result)
        return 0
    if not args.skill:
        raise ValueError("provide an external skill ID or --refresh lib/<name>")
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")

    preview = import_preview(
        root(args),
        catalog_root(args),
        args.skill,
        destination_name=args.destination_name,
        project_dir=current_project_dir(),
    )
    if not args.yes:
        if args.json:
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 0
        _print_preview(preview)
        if not sys.stdin.isatty():
            print(f"Next: {preview['next_command']}")
            return 0
        response = input("Import and accept this exact external skill hash? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Import cancelled; no library files were written.")
            return 1

    result = import_library_skill(
        root(args),
        catalog_root(args),
        args.skill,
        destination_name=str(preview["destination"]["name"]),
        expected_hash=str(preview["source_hash"]),
        expected_source_key=str(preview["source"]["source_key"]),
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    destination = result["destination"]
    print(f"Imported: {result['source']['id']} -> {destination['id']}")
    print(f"Path: {destination['path']}")
    print(f"Content hash: {destination['working_hash']}")
    print(f"Approval key: {destination['approval_key']}")
    print(f"Status: {destination['status']} ({destination['acceptance']})")
    return 0


def _validate_refresh_args(args: argparse.Namespace) -> None:
    conflicts = []
    if args.skill:
        conflicts.append("external skill ID")
    if args.destination_name:
        conflicts.append("--as")
    if args.yes:
        conflicts.append("--yes")
    if args.override_lint:
        conflicts.append("--override-lint")
    if args.reason:
        conflicts.append("--reason")
    if conflicts:
        raise ValueError(f"--refresh cannot be combined with {', '.join(conflicts)}")


def _print_preview(preview: dict[str, Any]) -> None:
    source = preview["source"]
    destination = preview["destination"]
    print(f"External skill: {source['id']}")
    print(f"Source: {source['type']} at {source['path']}")
    print(f"Exact source hash: {preview['source_hash']}")
    print(f"Destination: {destination['id']} at {destination['path']}")
    print(f"Owner review required: {'yes' if preview['owner_review_required'] else 'no'}")
    print(f"Scanner risk: {preview['scan']['risk']}")
    print(f"Lint: {preview['lint']['status']} ({preview['lint']['blocking_count']} blocking)")
    if preview["blocked"]:
        print("Blocked: this source must be unblocked before import.")
    elif preview["requires_override"]:
        print("This hash requires --override-lint --reason <text> before import.")


def _print_refresh(result: dict[str, Any]) -> None:
    print(f"Imported skill: {result['library']['id']}")
    print(f"Refresh status: {result['status']}")
    if result.get("reason"):
        print(f"Reason: {result['reason']}")
        return
    print(f"Imported base: {result['base_hash']}")
    print(f"Current upstream: {result['upstream_hash']}")
    print(f"Current library: {result['library']['working_hash']}")
    print("Preview only: no files were changed.")


__all__ = ["add_import_parser", "cmd_import"]
