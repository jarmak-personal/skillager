from __future__ import annotations

import argparse
import json
import shlex
import sys
import textwrap
from pathlib import Path
from typing import Any

from ..library.confirmation import confirmation_token, require_confirmation_token
from ..library.service import (
    accept_library_skill,
    initialize_library,
    library_acceptance_preview,
    library_relocation_preview,
    library_status,
    new_library_skill,
    relocate_library,
)
from ..library.versioning import (
    library_diff,
    library_history,
    library_restore_preview,
    restore_library_skill,
)
from .context import catalog_root, current_project_dir


def add_library_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "library",
        help="Manage the canonical personal skill library.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "The personal library is the canonical home for skills you own. "
            "Initialization registers it as the reserved lib collection without approving or exposing any skill bodies."
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager library init
              skillager library init --path ~/skills/personal
              skillager library init --no-git
              skillager library relocate --path ~/skills/personal --yes
              skillager library status
              skillager library status lib/my-skill --json
              skillager library new my-skill
              skillager library accept lib/my-skill --json
              skillager library history lib/my-skill --json
              skillager library diff lib/my-skill --stat
              skillager library restore lib/my-skill --to <content-hash> --json
            """
        ),
    )
    library_sub = parser.add_subparsers(required=True)
    init = library_sub.add_parser("init", help="Initialize or register the personal library.")
    init.add_argument("--path", type=Path, help="Custom library root. Defaults to ~/.skillager/library.")
    init.add_argument("--no-git", action="store_true", help="Initialize without Git history support.")
    init.add_argument("--json", action="store_true", help="Emit versioned initialization metadata as JSON.")
    init.set_defaults(func=cmd_library_init)
    relocate = library_sub.add_parser("relocate", help="Re-register the same personal-library identity at a moved path.")
    relocate.add_argument("--path", type=Path, required=True, help="Existing moved library root with the registered UUID.")
    relocate.add_argument("--yes", action="store_true", help="Confirm the registration path update.")
    relocate.add_argument("--json", action="store_true", help="Emit versioned relocation preview or result JSON.")
    relocate.set_defaults(func=cmd_library_relocate)
    status = library_sub.add_parser("status", help="Inspect library identity and Git state without changing it.")
    status.add_argument("skill", nargs="?", help="Optional library skill name or lib/<name> ID.")
    status.add_argument("--json", action="store_true", help="Emit versioned status metadata as JSON.")
    status.set_defaults(func=cmd_library_status)
    new = library_sub.add_parser("new", help="Create a collision-free pending library skill draft.")
    new.add_argument("name", help="Flat skill name or lib/<name> ID.")
    new.add_argument("--json", action="store_true", help="Emit versioned draft metadata as JSON.")
    new.set_defaults(func=cmd_library_new)
    accept = library_sub.add_parser("accept", help="Accept the exact current hash after review.")
    accept.add_argument("skill", help="Library skill name or lib/<name> ID.")
    accept.add_argument(
        "--yes",
        action="store_true",
        help="Run a non-interactive acceptance using the token returned by its preview.",
    )
    accept.add_argument(
        "--override-lint",
        action="store_true",
        help="Accept lint-blocking or high-risk content with an audited --reason.",
    )
    accept.add_argument("--reason", help="Required audit reason with --override-lint.")
    accept.add_argument(
        "--confirmation-token",
        help="Opaque token from the current acceptance preview.",
    )
    accept.add_argument("--json", action="store_true", help="Emit versioned preview or acceptance metadata as JSON.")
    accept.set_defaults(func=cmd_library_accept)
    history = library_sub.add_parser("history", help="List verified content-addressed versions without bodies.")
    history.add_argument("skill", help="Library skill name or lib/<name> ID.")
    history.add_argument("--json", action="store_true", help="Emit versioned metadata-only history JSON.")
    history.set_defaults(func=cmd_library_history)
    diff = library_sub.add_parser("diff", help="Compare the working tree or two historical content hashes.")
    diff.add_argument("skill", help="Library skill name or lib/<name> ID.")
    diff.add_argument("--from", dest="from_hash", help="Historical Skillager content-hash prefix.")
    diff.add_argument("--to", dest="to_hash", help="Historical Skillager content-hash prefix; defaults to working.")
    diff.add_argument("--stat", action="store_true", help="Show metadata-only file and line counts, not content.")
    diff.add_argument("--json", action="store_true", help="Emit a versioned diff result; use --stat to omit bodies.")
    diff.set_defaults(func=cmd_library_diff)
    restore = library_sub.add_parser("restore", help="Restore a verified historical tree as a new commit.")
    restore.add_argument("skill", help="Library skill name or lib/<name> ID.")
    restore.add_argument("--to", dest="to_hash", required=True, help="Historical Skillager content-hash prefix.")
    restore.add_argument(
        "--yes",
        action="store_true",
        help="Run a non-interactive restore using the token returned by its preview.",
    )
    restore.add_argument(
        "--override-lint",
        action="store_true",
        help="Restore lint-blocking or high-risk content with an audited --reason.",
    )
    restore.add_argument("--reason", help="Required audit reason with --override-lint.")
    restore.add_argument(
        "--confirmation-token",
        help="Opaque token from the current restore preview.",
    )
    restore.add_argument("--json", action="store_true", help="Emit versioned restore preview or result JSON.")
    restore.set_defaults(func=cmd_library_restore)

def cmd_library_init(args: argparse.Namespace) -> int:
    result = initialize_library(catalog_root(args), path=args.path, no_git=args.no_git)
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    library = result["library"]
    print(f"Personal skill library: {result['status']}")
    print(f"Path: {library['root']}")
    print(f"Identity: {library['library_id']}")
    print(f"Git: {_git_summary(result['git'])}")
    print(f"History: {_history_summary(result['history'])}")
    indexed = int(result["indexed"])
    print(f"Indexed: {indexed} skill{'s' if indexed != 1 else ''}; none were approved or exposed")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    for advisory in result["advisories"]:
        print(f"Note: {advisory}")
    return 0


def cmd_library_relocate(args: argparse.Namespace) -> int:
    if args.yes:
        result = relocate_library(catalog_root(args), args.path)
    else:
        result = library_relocation_preview(catalog_root(args), args.path)
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    if result["status"] == "preview":
        print("Personal library relocation preview (no files will be moved):")
        print(f"From: {result['from_path']}")
        print(f"To: {result['to_path']}")
        print(f"Identity: {result['library_id']}")
        print(f"Next: {shlex.join(result['next_command_argv'])}")
        return 0
    print("Personal skill library: relocated")
    print(f"Path: {result['to_path']}")
    print(f"Identity: {result['library_id']}")
    print(f"Indexed: {result['indexed']} skill{'s' if result['indexed'] != 1 else ''}")
    return 0


def cmd_library_status(args: argparse.Namespace) -> int:
    result = library_status(catalog_root(args), skill_name=args.skill, project_dir=current_project_dir())
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    if not result["initialized"]:
        print("Personal skill library is not initialized.")
        print(f"Next: {shlex.join(result['next_command_argv'])}")
        return 0
    library = result["library"]
    print(f"Personal skill library: {result['status']}")
    print(f"Path: {library['root']}")
    print(f"Identity: {library['library_id']}")
    print(f"Skills: {result['counts']['skills']}")
    print(f"Git: {_git_summary(result['git'])}")
    print(f"History: {_history_summary(result['history'])}")
    if result["skill"] is not None:
        skill = result["skill"]
        print(f"Skill: {skill['id']}")
        print(f"Skill path: {skill['path']}")
        print(f"Working hash: {skill['working_hash']}")
        print(f"Accepted hash: {skill['accepted_hash'] or '-'}")
        print(f"HEAD hash: {skill['head_hash'] or '-'}")
        print(f"Acceptance: {skill['acceptance']}")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    for advisory in result["advisories"]:
        print(f"Note: {advisory}")
    if result.get("next_command_argv"):
        print(f"Next: {' '.join(result['next_command_argv'])}")
    return 0


def cmd_library_new(args: argparse.Namespace) -> int:
    result = new_library_skill(catalog_root(args), args.name)
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    skill = result["skill"]
    print(f"Created pending library skill: {skill['id']}")
    print(f"SKILL.md: {skill['skill_file']}")
    print(f"Working hash: {skill['working_hash']}")
    print("Edit the file above, then review and accept its exact hash.")
    print(f"Then: {shlex.join(result['next_command_argv'])}")
    return 0


def cmd_library_accept(args: argparse.Namespace) -> int:
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")
    preview = library_acceptance_preview(catalog_root(args), args.skill)
    token = confirmation_token(
        "library-accept",
        skill_id=preview["skill"]["id"],
        working_hash=preview["skill"]["working_hash"],
        override_lint=args.override_lint,
        reason=(args.reason or "").strip() or None,
    )
    preview = dict(preview)
    if preview["requires_override"] and not args.override_lint:
        preview["required_arguments"] = ["--override-lint", "--reason"]
    else:
        preview["next_command_argv"] = _accept_argv(
            preview["skill"]["id"],
            token,
            override_lint=args.override_lint,
            reason=args.reason,
    )
    if not args.yes:
        if args.json:
            print(json.dumps(_public_payload(preview), indent=2, sort_keys=True))
            return 0
        if not sys.stdin.isatty():
            _print_acceptance_preview(preview)
            print("Preview only; no changes were made.")
            _print_preview_next(preview)
            return 0
        _print_acceptance_preview(preview)
        if preview["requires_override"] and not args.override_lint:
            print("Rerun the preview with --override-lint and a real --reason to continue.")
            return 0
        response = input("Accept this exact library skill hash? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Library acceptance cancelled.")
            return 1
    if preview["requires_override"] and not args.override_lint:
        raise ValueError("this skill requires --override-lint --reason with an audited explanation")
    if args.yes:
        require_confirmation_token(args.confirmation_token, token, operation="library acceptance")
    result = accept_library_skill(
        catalog_root(args),
        args.skill,
        expected_hash=str(preview["skill"]["working_hash"]),
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    skill = result["skill"]
    print(f"Accepted: {skill['id']}")
    print(f"Content hash: {skill['working_hash']}")
    print(f"Status: {skill['status']}")
    return 0


def cmd_library_history(args: argparse.Namespace) -> int:
    result = library_history(catalog_root(args), args.skill, project_dir=current_project_dir())
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    print(f"Library history: {result['skill']['id']}")
    if not result["available"]:
        print(f"Unavailable: {result['reason']}")
        return 0
    if not result["versions"]:
        print("No recoverable versions.")
        return 0
    for version in result["versions"]:
        markers = [name for name in ("head", "current", "accepted") if version[name]]
        marker_text = f" ({', '.join(markers)})" if markers else ""
        operation = f" {version['operation']}" if version["operation"] else ""
        print(
            f"{version['short_hash']}  {version['committed_at']}  "
            f"{version['commit'][:12]}{operation}{marker_text}"
        )
    return 0


def cmd_library_diff(args: argparse.Namespace) -> int:
    result = library_diff(
        catalog_root(args),
        args.skill,
        from_hash=args.from_hash,
        to_hash=args.to_hash,
        stat_only=args.stat,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    print(f"Library diff: {result['skill']['id']}")
    print(f"From: {result['from']['label']} ({result['from']['content_hash'] or 'empty'})")
    print(f"To: {result['to']['label']} ({result['to']['content_hash'] or 'empty'})")
    if args.stat:
        print("Content-bearing: no (--stat)")
        _print_diff_stat(result["stat"])
        return 0
    print("Content-bearing: yes")
    if result["diff"]:
        print(result["diff"], end="" if result["diff"].endswith("\n") else "\n")
    else:
        print("No content changes.")
    return 0


def cmd_library_restore(args: argparse.Namespace) -> int:
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")
    preview = library_restore_preview(
        catalog_root(args),
        args.skill,
        args.to_hash,
        project_dir=current_project_dir(),
    )
    if preview["status"] == "already-current":
        if args.json:
            print(json.dumps(_public_payload(preview), indent=2, sort_keys=True))
        else:
            print(f"Already current: {preview['skill']['id']} at {preview['selected_version']['short_hash']}")
        return 0
    token = confirmation_token(
        "library-restore",
        skill_id=preview["skill"]["id"],
        selected_hash=preview["selected_version"]["content_hash"],
        selected_commit=preview["selected_version"]["commit"],
        current_hash=preview["current_hash"],
        current_tree_fingerprint=preview["_current_tree_fingerprint"],
        override_lint=args.override_lint,
        reason=(args.reason or "").strip() or None,
    )
    preview = dict(preview)
    if preview["requires_override"] and not args.override_lint:
        preview["required_arguments"] = ["--override-lint", "--reason"]
    else:
        preview["next_command_argv"] = _restore_argv(
            preview["skill"]["id"],
            preview["selected_version"]["short_hash"],
            token,
            override_lint=args.override_lint,
            reason=args.reason,
    )
    if not args.yes:
        if args.json:
            print(json.dumps(_public_payload(preview), indent=2, sort_keys=True))
            return 0
        if not sys.stdin.isatty():
            _print_restore_preview(preview)
            print("Preview only; no files were changed.")
            _print_preview_next(preview)
            return 0
        _print_restore_preview(preview)
        if preview["requires_override"] and not args.override_lint:
            print("Rerun the preview with --override-lint and a real --reason to continue.")
            return 0
        response = input("Restore this exact historical tree as a new commit? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Library restore cancelled; no files were changed.")
            return 1
    if preview["requires_override"] and not args.override_lint:
        raise ValueError("this historical version requires --override-lint --reason with an audited explanation")
    if args.yes:
        require_confirmation_token(args.confirmation_token, token, operation="library restore")
    version = preview["selected_version"]
    result = restore_library_skill(
        catalog_root(args),
        args.skill,
        expected_hash=str(version["content_hash"]),
        expected_commit=str(version["commit"]),
        expected_current_hash=str(preview["current_hash"]),
        expected_current_fingerprint=str(preview["_current_tree_fingerprint"]),
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(_public_payload(result), indent=2, sort_keys=True))
        return 0
    print(f"Restored: {result['skill']['id']} -> {result['restored_version']['short_hash']}")
    print(f"Content hash: {result['skill']['working_hash']}")
    print(f"Status: {result['skill']['status']} ({result['skill']['acceptance']})")
    return 0


def _print_acceptance_preview(preview: dict[str, Any]) -> None:
    skill = preview["skill"]
    print(f"Library skill: {skill['id']}")
    print(f"Path: {skill['path']}")
    print(f"Exact content hash: {skill['working_hash']}")
    print(f"Scanner risk: {preview['scan']['risk']}")
    print(f"Lint: {preview['lint']['status']} ({preview['lint']['blocking_count']} blocking)")
    print(f"Git mode: {preview['git']['mode']}")
    if preview["requires_override"]:
        print("This hash requires --override-lint --reason <text> before acceptance.")


def _print_restore_preview(preview: dict[str, Any]) -> None:
    version = preview["selected_version"]
    print(f"Library skill: {preview['skill']['id']}")
    print(f"Current hash: {preview['current_hash']}")
    print(f"Restore hash: {version['content_hash']}")
    print(f"Historical commit: {version['commit']}")
    print(f"Scanner risk: {preview['scan']['risk']}")
    print(f"Lint: {preview['lint']['status']} ({preview['lint']['blocking_count']} blocking)")
    _print_diff_stat(preview["stat"])
    if preview["requires_override"]:
        print("This version requires --override-lint --reason <text> before restoration.")


def _print_diff_stat(stat: dict[str, Any]) -> None:
    print(
        f"Files changed: {stat['files_changed']}; "
        f"additions: {stat['additions']}; deletions: {stat['deletions']}"
    )
    for file in stat["files"]:
        counts = "binary" if file["binary"] else f"+{file['additions']} -{file['deletions']}"
        print(f"{file['status']}: {file['path']} ({counts})")


def _accept_argv(
    skill_id: str,
    token: str,
    *,
    override_lint: bool,
    reason: str | None,
) -> list[str]:
    command = [
        "skillager",
        "library",
        "accept",
        skill_id,
        "--yes",
        "--confirmation-token",
        token,
    ]
    return _with_override(command, override_lint=override_lint, reason=reason)


def _restore_argv(
    skill_id: str,
    selected_hash: str,
    token: str,
    *,
    override_lint: bool,
    reason: str | None,
) -> list[str]:
    command = [
        "skillager",
        "library",
        "restore",
        skill_id,
        "--to",
        selected_hash,
        "--yes",
        "--confirmation-token",
        token,
    ]
    return _with_override(command, override_lint=override_lint, reason=reason)


def _with_override(command: list[str], *, override_lint: bool, reason: str | None) -> list[str]:
    if override_lint:
        command.extend(["--override-lint", "--reason", (reason or "").strip()])
    return command


def _print_preview_next(preview: dict[str, Any]) -> None:
    command = preview.get("next_command_argv")
    if command:
        print(f"Next: {shlex.join(command)}")
    else:
        print("Next: provide --override-lint with a real --reason and preview again.")


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_payload(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return value


def _git_summary(git: dict[str, Any] | None) -> str:
    if git is None:
        return "unavailable"
    if git["mode"] == "disabled":
        return "disabled (--no-git)"
    if not git["available"]:
        return "unavailable (git executable not found)"
    if not git["repository"]:
        return "degraded (repository missing)"
    state = "clean" if git["clean"] else "changes present"
    head = git["head"][:12] if git.get("head") else "no commits"
    return f"{state}, HEAD {head}"


def _history_summary(history: dict[str, Any]) -> str:
    return "available" if history["available"] else f"unavailable ({history['reason']})"


__all__ = [
    "add_library_parser",
    "cmd_library_accept",
    "cmd_library_diff",
    "cmd_library_history",
    "cmd_library_init",
    "cmd_library_new",
    "cmd_library_restore",
    "cmd_library_status",
]
