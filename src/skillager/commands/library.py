from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from ..library.service import (
    accept_library_skill,
    initialize_library,
    library_acceptance_preview,
    library_skill_path,
    library_status,
    library_where,
    new_library_skill,
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
              skillager library status
              skillager library status lib/my-skill --json
              skillager library new my-skill
              skillager edit lib/my-skill
              skillager library accept lib/my-skill --yes
              skillager where lib/my-skill --json
            """
        ),
    )
    library_sub = parser.add_subparsers(required=True)
    init = library_sub.add_parser("init", help="Initialize or register the personal library.")
    init.add_argument("--path", type=Path, help="Custom library root. Defaults to ~/.skillager/library.")
    init.add_argument("--no-git", action="store_true", help="Initialize without Git history support.")
    init.add_argument("--json", action="store_true", help="Emit versioned initialization metadata as JSON.")
    init.set_defaults(func=cmd_library_init)
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
    accept.add_argument("--yes", action="store_true", help="Confirm non-interactive exact-hash acceptance.")
    accept.add_argument(
        "--override-lint",
        action="store_true",
        help="Accept lint-blocking or high-risk content with an audited --reason.",
    )
    accept.add_argument("--reason", help="Required audit reason with --override-lint.")
    accept.add_argument("--json", action="store_true", help="Emit versioned preview or acceptance metadata as JSON.")
    accept.set_defaults(func=cmd_library_accept)

    where = sub.add_parser("where", help="Show canonical library ownership, hashes, Git state, and project exposures.")
    where.add_argument("skill", help="Library skill name or lib/<name> ID.")
    where.add_argument("--json", action="store_true", help="Emit versioned location metadata as JSON.")
    where.set_defaults(func=cmd_where)

    edit = sub.add_parser("edit", help="Print or open a canonical library skill path.")
    edit.add_argument("skill", help="Library skill name or lib/<name> ID.")
    edit.add_argument("--open", action="store_true", dest="open_editor", help="Open SKILL.md with $EDITOR.")
    edit.set_defaults(func=cmd_edit)


def cmd_library_init(args: argparse.Namespace) -> int:
    result = initialize_library(catalog_root(args), path=args.path, no_git=args.no_git)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    library = result["library"]
    print(f"Personal skill library: {result['status']}")
    print(f"Path: {library['root']}")
    print(f"Identity: {library['library_id']}")
    print(f"Git: {_git_summary(result['git'])}")
    print(f"Indexed: {result['indexed']} skill(s); none were approved or exposed")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    return 0


def cmd_library_status(args: argparse.Namespace) -> int:
    result = library_status(catalog_root(args), skill_name=args.skill, project_dir=current_project_dir())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if not result["initialized"]:
        print("Personal skill library is not initialized.")
        print("Next: skillager library init")
        return 0
    library = result["library"]
    print(f"Personal skill library: {result['status']}")
    print(f"Path: {library['root']}")
    print(f"Identity: {library['library_id']}")
    print(f"Skills: {result['counts']['skills']}")
    print(f"Git: {_git_summary(result['git'])}")
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
    return 0


def cmd_library_new(args: argparse.Namespace) -> int:
    result = new_library_skill(catalog_root(args), args.name)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    skill = result["skill"]
    print(f"Created pending library skill: {skill['id']}")
    print(f"Path: {skill['path']}")
    print(f"Working hash: {skill['working_hash']}")
    print(f"Next: {result['next_commands'][0]}")
    print(f"Then: {result['next_commands'][1]}")
    return 0


def cmd_library_accept(args: argparse.Namespace) -> int:
    if args.override_lint and not (args.reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    if args.reason and not args.override_lint:
        raise ValueError("--reason can only be used with --override-lint")
    preview = library_acceptance_preview(catalog_root(args), args.skill, project_dir=current_project_dir())
    if not args.yes:
        if not sys.stdin.isatty():
            preview = dict(preview)
            preview["status"] = "confirmation-required"
            preview["next_command"] = f"skillager library accept {preview['skill']['id']} --yes"
            if args.json:
                print(json.dumps(preview, indent=2, sort_keys=True))
                return 1
            raise ValueError("non-interactive library acceptance requires --yes")
        _print_acceptance_preview(preview)
        response = input("Accept this exact library skill hash? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Library acceptance cancelled.")
            return 1
    result = accept_library_skill(
        catalog_root(args),
        args.skill,
        expected_hash=str(preview["skill"]["working_hash"]),
        override_lint=args.override_lint,
        reason=args.reason,
        project_dir=current_project_dir(),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    skill = result["skill"]
    print(f"Accepted: {skill['id']}")
    print(f"Content hash: {skill['working_hash']}")
    print(f"Approval key: {skill['approval_key']}")
    print(f"Status: {skill['status']}")
    return 0


def cmd_where(args: argparse.Namespace) -> int:
    result = library_where(catalog_root(args), args.skill, project_dir=current_project_dir())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    skill = result["skill"]
    print(f"Skill: {skill['id']}")
    print(f"Ownership: {skill['ownership']}")
    print(f"Path: {skill['path']}")
    print(f"Working hash: {skill['working_hash']}")
    print(f"Accepted hash: {skill['accepted_hash'] or '-'}")
    print(f"HEAD hash: {skill['head_hash'] or '-'}")
    print(f"Status: {skill['status']} ({skill['acceptance']})")
    print(f"Project exposures: {len(skill['exposures'])}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    path = library_skill_path(catalog_root(args), args.skill)
    if not args.open_editor:
        print(path)
        return 0
    editor = (os.environ.get("EDITOR") or "").strip()
    if not editor:
        raise ValueError("$EDITOR is not set; run plain `skillager edit <skill>` to print the path")
    command = shlex.split(editor)
    if not command:
        raise ValueError("$EDITOR does not contain an executable")
    completed = subprocess.run([*command, str(path)], check=False)
    if completed.returncode != 0:
        raise ValueError(f"editor exited with status {completed.returncode}")
    status = library_where(catalog_root(args), args.skill, project_dir=current_project_dir())["skill"]
    print(f"Edited: {status['id']}")
    print(f"Working hash: {status['working_hash']}")
    print(f"Acceptance: {status['acceptance']}")
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


__all__ = [
    "add_library_parser",
    "cmd_edit",
    "cmd_library_accept",
    "cmd_library_init",
    "cmd_library_new",
    "cmd_library_status",
    "cmd_where",
]
