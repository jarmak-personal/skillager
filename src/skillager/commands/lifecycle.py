from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from ..library.syncing import pin_exposure, sync_preview, sync_project, unpin_exposure
from ..library.variants import fork_library_skill, fork_preview
from .context import catalog_root, current_project_dir


def add_lifecycle_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    fork = sub.add_parser(
        "fork",
        help="Create an accepted personal-library variant with exact lineage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fork a current or historical personal-library skill into a distinct identity. "
            "A new agent-facing description is mandatory, and mutation requires confirmation or --yes."
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager fork lib/pandas --as pandas-2 --description "Patterns for pandas 2.x projects"
              skillager fork lib/pandas --as pandas-2 --description "Patterns for pandas 2.x projects" --from <hash> --yes
            """
        ),
    )
    fork.add_argument("skill", help="Source personal-library skill ID.")
    fork.add_argument("--as", dest="destination_name", required=True, help="Collision-free variant name.")
    fork.add_argument("--description", required=True, help="Distinct agent-facing variant description.")
    fork.add_argument("--from", dest="from_hash", help="Historical Skillager content-hash prefix.")
    fork.add_argument("--yes", action="store_true", help="Confirm the exact previewed fork mutation.")
    fork.add_argument(
        "--override-lint",
        action="store_true",
        help="Fork lint-blocking or high-risk content with an audited --reason.",
    )
    fork.add_argument("--reason", help="Required audit reason with --override-lint.")
    fork.add_argument("--json", action="store_true", help="Emit versioned metadata-only fork JSON.")
    fork.set_defaults(func=cmd_fork)

    sync = sub.add_parser(
        "sync",
        help="Preview or apply clean personal-library exposure updates in this project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Inspect only the current project's managed exposures. Bare sync is read-only; "
            "--apply updates clean, unpinned native and stub exposures from accepted library heads."
        ),
        epilog="Examples:\n  skillager sync --json\n  skillager sync --agent codex --apply --json",
    )
    sync.add_argument("--agent", choices=["codex", "claude"], help="Select one current-project agent exposure root.")
    sync.add_argument("--apply", action="store_true", help="Apply the exact clean updates found by the preview.")
    sync.add_argument("--json", action="store_true", help="Emit versioned metadata-only sync JSON.")
    sync.set_defaults(func=cmd_sync)

    pin = sub.add_parser("pin", help="Freeze one clean current-project library exposure at its exact source hash.")
    pin.add_argument("skill", help="Personal-library exposure skill ID.")
    pin.add_argument("--to", dest="to_hash", help="Hash prefix that must identify the exposure's current source hash.")
    pin.add_argument("--agent", choices=["codex", "claude"], help="Select one current-project agent exposure root.")
    pin.add_argument("--json", action="store_true", help="Emit versioned metadata-only pin JSON.")
    pin.set_defaults(func=cmd_pin)

    unpin = sub.add_parser("unpin", help="Allow one current-project library exposure to sync again.")
    unpin.add_argument("skill", help="Personal-library exposure skill ID.")
    unpin.add_argument("--agent", choices=["codex", "claude"], help="Select one current-project agent exposure root.")
    unpin.add_argument("--json", action="store_true", help="Emit versioned metadata-only unpin JSON.")
    unpin.set_defaults(func=cmd_unpin)


def cmd_fork(args: argparse.Namespace) -> int:
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")
    project = current_project_dir()
    preview = fork_preview(
        catalog_root(args),
        args.skill,
        destination_name=args.destination_name,
        description=args.description,
        from_hash=args.from_hash,
        project_dir=project,
    )
    if not args.yes:
        if args.json:
            _emit(preview, as_json=True)
            return 0
        _print_fork(preview)
        if not sys.stdin.isatty():
            print(f"Next: {preview['next_command']}")
            return 0
        response = input("Create and accept this exact library fork? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Fork cancelled; no library files were written.")
            return 1
    result = fork_library_skill(
        catalog_root(args),
        args.skill,
        destination_name=args.destination_name,
        description=args.description,
        expected_source_hash=str(preview["source"]["content_hash"]),
        expected_source_commit=preview["source"].get("commit"),
        expected_candidate_hash=str(preview["destination"]["content_hash"]),
        from_hash=args.from_hash,
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=project,
    )
    if args.json:
        _emit(result, as_json=True)
        return 0
    destination = result["destination"]
    print(f"Forked: {result['source']['id']} -> {destination['id']}")
    print(f"Lineage hash: {result['lineage']['hash']}")
    print(f"Content hash: {destination['working_hash']}")
    print(f"Status: {destination['status']} ({destination['acceptance']})")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    project = current_project_dir()
    result = (
        sync_project(catalog_root(args), project, agent=args.agent)
        if args.apply
        else sync_preview(catalog_root(args), project, agent=args.agent)
    )
    if args.json:
        _emit(result, as_json=True)
        return 0
    mode = "applied" if args.apply else "preview"
    print(f"Project sync {mode}: {result['update_count']} update(s)")
    for item in result["items"]:
        label = item["status"]
        reason = f" ({item['reason']})" if item.get("reason") else ""
        print(f"- {item['skill_id']} [{item['agent']}] {label}{reason}")
    if not args.apply and result["update_count"]:
        print("Next: skillager sync --apply")
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    result = pin_exposure(
        catalog_root(args),
        current_project_dir(),
        args.skill,
        to_hash=args.to_hash,
        agent=args.agent,
    )
    if args.json:
        _emit(result, as_json=True)
        return 0
    print(f"Pinned: {result['skill_id']} [{result['agent']}] at {result['pin_hash']}")
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    result = unpin_exposure(
        catalog_root(args),
        current_project_dir(),
        args.skill,
        agent=args.agent,
    )
    if args.json:
        _emit(result, as_json=True)
        return 0
    print(f"Unpinned: {result['skill_id']} [{result['agent']}]")
    return 0


def _print_fork(preview: dict[str, Any]) -> None:
    print(f"Fork source: {preview['source']['id']} at {preview['source']['content_hash']}")
    print(f"Destination: {preview['destination']['id']}")
    print(f"Name: {preview['destination']['name']}")
    print(f"Description: {preview['destination']['summary']}")
    print(f"Candidate hash: {preview['destination']['content_hash']}")
    print(f"Scanner risk: {preview['scan']['risk']}")
    print(f"Lint: {preview['lint']['status']} ({preview['lint']['blocking_count']} blocking)")
    if preview["requires_override"]:
        print("This fork requires --override-lint --reason <text>.")


def _emit(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))


__all__ = [
    "add_lifecycle_parsers",
    "cmd_fork",
    "cmd_pin",
    "cmd_sync",
    "cmd_unpin",
]
