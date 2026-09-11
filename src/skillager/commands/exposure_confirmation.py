from __future__ import annotations

import argparse
from typing import Any, Callable

from ..exposure.preview import exposure_source_state


def bound_exposure_request(args: argparse.Namespace, agents: list[str], mode: str) -> bool:
    supported = (
        mode in {"native", "stub"}
        and args.scope == "project"
        and len(args.skill_ids) == 1
        and len(agents) == 1
        and not any(
            getattr(args, key, None)
            for key in ("tag", "all_reviewed", "all_agents", "force", "allow_incompatible",
                        "source", "collection", "audience", "package", "activation", "include_blocked")
        )
    )
    if args.yes or args.confirmation_token:
        if not supported:
            raise ValueError("bound exposure requires one explicit skill, one agent, project scope, native/stub mode, and no filters or overrides")
        if args.dry_run:
            raise ValueError("--dry-run cannot be combined with --yes or --confirmation-token")
        if not args.yes or not args.confirmation_token:
            raise ValueError("bound exposure requires --yes and the confirmation token from its current --dry-run preview")
    return supported and (args.dry_run or bool(args.confirmation_token))


def source_revalidator(
    inventory: Callable[[], list[dict[str, Any]]],
) -> Callable[[dict[str, Any]], None]:
    def revalidate(expected: dict[str, Any]) -> None:
        matches = [item for item in inventory() if item.get("id") == expected.get("id")]
        if len(matches) != 1 or exposure_source_state(matches[0]) != exposure_source_state(expected):
            raise ValueError("exposure source identity or approval changed; review the current preview again")
    return revalidate


def add_confirmation_commands(results: list[dict[str, Any]], *, json_output: bool) -> None:
    for result in results:
        preview = result.get("preview")
        if not preview or result.get("status") != "would_write":
            continue
        command = [
            "skillager", "expose", str(result["skill_id"]),
            "--mode", str(result["mode"]), "--agent", str(result["agent"]),
            "--scope", "project",
        ]
        if json_output:
            command.append("--json")
        command.extend(["--yes", "--confirmation-token", preview["confirmation_token"]])
        result["next_command_argv"] = command
