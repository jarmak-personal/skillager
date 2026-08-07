from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any

from ..library.service import initialize_library, library_status
from .context import catalog_root


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
    result = library_status(catalog_root(args), skill_name=args.skill)
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
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    return 0


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


__all__ = ["add_library_parser", "cmd_library_init", "cmd_library_status"]
