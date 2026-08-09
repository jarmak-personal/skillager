from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from ..exposure.reconcile import (
    keep_local,
    keep_local_preview,
    quarantine,
    quarantine_preview,
    reconcile_inventory,
    repair_generated,
    repair_preview,
)
from ..library.reconciliation import (
    import_exposure,
    import_exposure_preview,
    promote_exposure,
    promote_preview,
    rollback_exposure,
    rollback_preview,
)
from .context import catalog_root, current_project_dir, root


RECONCILE_ACTIONS = {"keep-local", "quarantine", "repair", "promote", "rollback", "import"}
_STATUS_LABELS = {
    "current": "current",
    "kept_local": "intentionally kept local",
    "local_edit": "locally edited",
    "target_missing": "partially missing",
    "blocked": "blocked for this exposure",
    "sidecar_error": "malformed management metadata",
    "unmanaged": "not managed by Skillager",
}
_ACTION_DESCRIPTIONS = {
    "keep-local": "acknowledge this exact project edit and leave the source unchanged",
    "quarantine": "preserve the target outside agent-visible roots and block its exact hash",
    "repair": "preserve local bytes, then regenerate this stub or router",
    "promote": "make this edited native copy the new accepted library head",
    "rollback": "restore this native copy from verified library history",
    "import": "adopt this edited external native copy as a new library skill",
}
_CHANGE_LABELS = {
    "base_to_exposure": "Project edit",
    "base_to_library": "Library changes since exposure",
}


def add_reconcile_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "reconcile",
        help="Inspect and safely reconcile current-project exposure changes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Without an action, report metadata-only current-project exposure state. "
            "Mutating actions preview first and require interactive confirmation or --yes."
        ),
        epilog=textwrap.dedent(
            """\
            Actions:
              keep-local  Acknowledge this exact project edit without changing the source.
              quarantine  Preserve the target outside agent-visible roots and block its exact hash.
              repair      Preserve then regenerate an edited or incomplete stub/router.
              promote     Make an edited native library exposure the new accepted library head.
              rollback    Restore a native library exposure from verified library history.
              import      Adopt an edited external native exposure as a new library skill.

            Examples:
              skillager reconcile --json
              skillager reconcile lib/brainstorm --json
              skillager reconcile keep-local lib/brainstorm --yes
              skillager reconcile quarantine lib/brainstorm --yes
              skillager reconcile repair community/brainstorm --yes
              skillager reconcile promote lib/brainstorm --yes
              skillager reconcile rollback lib/brainstorm --yes
              skillager reconcile import community/brainstorm --as brainstorm-local --yes
            """
        ),
    )
    parser.add_argument(
        "arguments",
        nargs="*",
        metavar="ACTION_OR_SKILL",
        help="Optional exposure skill ID, or an action (keep-local, quarantine, repair, promote, rollback, import) followed by one skill ID.",
    )
    parser.add_argument("--agent", choices=["codex", "claude"], help="Select one current-project agent exposure root.")
    parser.add_argument("--as", dest="destination_name", help="Collision-free library name for reconcile import.")
    parser.add_argument("--yes", action="store_true", help="Confirm the exact previewed reconciliation mutation.")
    parser.add_argument(
        "--override-lint",
        action="store_true",
        help="Promote or import lint-blocking/high-risk content with an audited --reason.",
    )
    parser.add_argument("--reason", help="Required audit reason with --override-lint.")
    parser.add_argument("--json", action="store_true", help="Emit versioned metadata-only JSON.")
    parser.set_defaults(func=cmd_reconcile)


def cmd_reconcile(args: argparse.Namespace) -> int:
    action, skill_id = _parse_reconcile_arguments(args.arguments)
    _validate_options(args, action)
    project_dir = current_project_dir()
    if action is None:
        result = reconcile_inventory(
            root(args),
            catalog_root(args),
            project_dir,
            skill_id=skill_id,
            agent=args.agent,
        )
        _emit_inventory(result, as_json=args.json)
        return 0

    preview = _preview(args, action, str(skill_id), project_dir)
    if not _preview_can_apply(preview):
        _emit_action(preview, as_json=args.json)
        if args.yes and preview.get("status") not in {"already-current", "already-kept", "already-quarantined"}:
            raise ValueError(f"reconcile {action} cannot apply: {preview.get('status')}")
        return 0
    if not args.yes:
        if args.json:
            _emit_action(preview, as_json=True)
            return 0
        _emit_action(preview, as_json=False)
        if not sys.stdin.isatty():
            if preview.get("next_command"):
                print(f"Next: {preview['next_command']}")
            return 0
        response = input(f"Apply reconcile {action} to this exact exposure state? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Reconciliation cancelled; no files were changed.")
            return 1

    result = _apply(args, action, str(skill_id), project_dir, preview)
    _emit_action(result, as_json=args.json)
    return 0


def _parse_reconcile_arguments(arguments: list[str]) -> tuple[str | None, str | None]:
    if not arguments:
        return None, None
    if arguments[0] in RECONCILE_ACTIONS:
        if len(arguments) != 2:
            raise ValueError(f"reconcile {arguments[0]} requires exactly one exposure skill ID")
        return arguments[0], arguments[1]
    if len(arguments) != 1:
        raise ValueError("read-only reconcile accepts at most one exposure skill ID")
    return None, arguments[0]


def _validate_options(args: argparse.Namespace, action: str | None) -> None:
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")
    if action not in {"promote", "import"} and (args.override_lint or args.reason):
        raise ValueError("--override-lint and --reason apply only to reconcile promote/import")
    if action == "import" and not args.destination_name:
        raise ValueError("reconcile import requires --as <library-name>")
    if action != "import" and args.destination_name:
        raise ValueError("--as applies only to reconcile import")
    if action is None and args.yes:
        raise ValueError("--yes requires an explicit reconcile action")


def _preview(args: argparse.Namespace, action: str, skill_id: str, project_dir) -> dict[str, Any]:
    catalog = catalog_root(args)
    if action == "keep-local":
        return keep_local_preview(project_dir, catalog, skill_id, agent=args.agent)
    if action == "quarantine":
        return quarantine_preview(project_dir, catalog, skill_id, agent=args.agent)
    if action == "repair":
        return repair_preview(root(args), catalog, project_dir, skill_id, agent=args.agent)
    if action == "promote":
        return promote_preview(root(args), catalog, project_dir, skill_id, agent=args.agent)
    if action == "rollback":
        return rollback_preview(catalog, project_dir, skill_id, agent=args.agent)
    if action == "import":
        return import_exposure_preview(
            catalog,
            project_dir,
            skill_id,
            destination_name=str(args.destination_name),
            agent=args.agent,
        )
    raise ValueError(f"unsupported reconcile action: {action}")


def _preview_can_apply(preview: dict[str, Any]) -> bool:
    if "can_apply" in preview:
        return preview["can_apply"] is True
    return preview.get("status") == "preview"


def _apply(
    args: argparse.Namespace,
    action: str,
    skill_id: str,
    project_dir,
    preview: dict[str, Any],
) -> dict[str, Any]:
    catalog = catalog_root(args)
    if action == "keep-local":
        return keep_local(
            project_dir,
            catalog,
            skill_id,
            expected_hash=str(preview["expected_current_hash"]),
            agent=args.agent,
        )
    if action == "quarantine":
        return quarantine(
            project_dir,
            catalog,
            skill_id,
            expected_hash=str(preview["expected_current_hash"]),
            agent=args.agent,
        )
    if action == "repair":
        return repair_generated(
            root(args),
            catalog,
            project_dir,
            skill_id,
            expected_hash=preview.get("expected_current_hash"),
            agent=args.agent,
        )
    if action == "promote":
        return promote_exposure(
            catalog,
            project_dir,
            skill_id,
            expected_target=str(preview["expected_target"]),
            expected_base_hash=str(preview["base_hash"]),
            expected_exposure_hash=str(preview["promoted_hash"]),
            agent=args.agent,
            override_lint=args.override_lint,
            reason=args.reason,
        )
    if action == "rollback":
        return rollback_exposure(
            catalog,
            project_dir,
            skill_id,
            expected_target=str(preview["expected_target"]),
            expected_current_hash=preview.get("expected_current_hash"),
            expected_restore_hash=str(preview["restore_hash"]),
            expected_commit=str(preview["selected_version"]["commit"]),
            agent=args.agent,
        )
    if action == "import":
        return import_exposure(
            catalog,
            project_dir,
            skill_id,
            destination_name=str(args.destination_name),
            expected_target=str(preview["expected_target"]),
            expected_hash=str(preview["imported_hash"]),
            expected_source_hash=str(preview["source_hash"]),
            agent=args.agent,
            override_lint=args.override_lint,
            reason=args.reason,
        )
    raise ValueError(f"unsupported reconcile action: {action}")


def _emit_inventory(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    items = result["items"]
    if not items:
        print("No current-project exposures matched.")
        return
    exposure_count = len(items)
    print(f"Reconcile inventory: {exposure_count} exposure{'s' if exposure_count != 1 else ''}")
    for item in items:
        status = _STATUS_LABELS.get(str(item["status"]), str(item["status"]).replace("_", " "))
        print(f"- {item['skill_id']} [{item['agent']}] {status} ({item['ownership']}/{item['mode']})")
        if item["actions"]:
            print("  choices:")
            for action in item["actions"]:
                print(f"    {action}: {_ACTION_DESCRIPTIONS[action]}")
        else:
            print("  choices: none")


def _emit_action(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"Reconcile {result['action']}: {result['status']}")
    exposure = result.get("exposure") or result.get("source") or {}
    if exposure.get("skill_id"):
        print(f"Exposure: {exposure['skill_id']}")
    if exposure.get("target"):
        print(f"Target: {exposure['target']}")
    if result.get("base_hash"):
        print(f"Base hash: {result['base_hash']}")
    if result.get("promoted_hash"):
        label = "Promoted hash" if result.get("status") == "promoted" else "Edited exposure hash"
        print(f"{label}: {result['promoted_hash']}")
    if result.get("imported_hash"):
        print(f"Imported hash: {result['imported_hash']}")
    if result.get("restore_hash"):
        print(f"Restore hash: {result['restore_hash']}")
    if result.get("quarantine_path"):
        print(f"Recoverable quarantine: {result['quarantine_path']}")
    if result.get("requires_override"):
        print("This content requires --override-lint --reason <text>.")
    changes = result.get("changes")
    if isinstance(changes, dict):
        _print_changes(changes)


def _print_changes(changes: dict[str, Any]) -> None:
    if _is_tree_difference(changes):
        _print_tree_difference("Files to restore", changes)
        return
    for label, difference in changes.items():
        if not isinstance(difference, dict):
            continue
        _print_tree_difference(_CHANGE_LABELS.get(label, label.replace("_", " ").title()), difference)


def _is_tree_difference(value: dict[str, Any]) -> bool:
    return bool({"available", "added", "deleted", "changed"} & value.keys())


def _print_tree_difference(label: str, difference: dict[str, Any]) -> None:
    if difference.get("available") is False:
        print(f"{label}: unavailable ({difference.get('reason')})")
        return
    changed = [*difference.get("added", []), *difference.get("deleted", []), *difference.get("changed", [])]
    file_count = len(changed)
    print(f"{label}: {file_count} file{'s' if file_count != 1 else ''} changed")


__all__ = ["RECONCILE_ACTIONS", "add_reconcile_parser", "cmd_reconcile"]
