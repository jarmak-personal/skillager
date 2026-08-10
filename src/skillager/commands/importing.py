from __future__ import annotations

import argparse
import json
import shlex
import sys
import textwrap
from typing import Any

from ..library.importing import import_library_skill, import_preview
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
            """
        ),
    )
    parser.add_argument("skill", help="External discovered skill ID.")
    parser.add_argument("--as", dest="destination_name", help="Collision-free library skill name.")
    parser.add_argument("--yes", action="store_true", help="Confirm the exact reviewed source hash for import.")
    parser.add_argument(
        "--override-lint",
        action="store_true",
        help="Import lint-blocking or high-risk content with an audited --reason.",
    )
    parser.add_argument("--reason", help="Required audit reason with --override-lint.")
    parser.add_argument("--json", action="store_true", help="Emit versioned import metadata as JSON.")
    parser.set_defaults(func=cmd_import)


def cmd_import(args: argparse.Namespace) -> int:
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
            print(json.dumps(_public_payload(preview), indent=2, sort_keys=True))
            return 0
        _print_preview(preview)
        if not sys.stdin.isatty():
            print(f"Next: {shlex.join(preview['next_command_argv'])}")
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
        expected_source_key=str(preview["_source_key"]),
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    destination = result["destination"]
    print(f"Imported: {result['source']['id']} -> {destination['id']}")
    print(f"Path: {destination['path']}")
    print(f"Content hash: {destination['working_hash']}")
    print(f"Status: {destination['status']} ({destination['acceptance']})")
    return 0


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


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_payload(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return value


__all__ = ["add_import_parser", "cmd_import"]
