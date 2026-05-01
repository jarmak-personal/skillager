from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .compatibility import compatibility_problem, compatibility_warnings
from .collections import (
    add_collection,
    add_tag_skill,
    attach_project_tag,
    attached_tag_skills,
    collection_skills,
    create_tag,
    detach_project_tag,
    load_collections,
    load_project_tags,
    load_tags,
    refresh_collection,
    remove_collection,
    remove_tag_skill,
    search_collection,
    set_tag_skills,
    tag_skills,
)
from .index import build_index, find_skill, load_index
from .lookback import build_lookback, record_feedback, render_lookback
from .materialize import TRUSTED_STATES, agent_note_paths, materialize_skills, materialize_working_skill
from .materialize import materialize_router
from .onboard import onboard_path
from .paths import cache_root, catalog_state_root, state_root
from .render import render_skill
from .review import apply_review_action, review_summary, selected_skills, setup_environment
from .scan import scan_path
from .search import search as search_index
from .session import (
    current_session,
    end_session,
    prune_sessions,
    read_events,
    record_materialize_events,
    record_search_event,
    record_skill_event,
    redact_session,
    start_session,
)
from .simple_yaml import load_mapping
from .trust import set_trust
from .update_check import check_for_update


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 1
    except Exception as exc:
        print(f"skillager: error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Skillager is a pure CLI registry and materialization tool for agent skills.

            Agent-safe default workflow:
              1. skillager status
              2. skillager setup
              3. Ask the user what they plan to do in the repo
              4. Inspect approved metadata with search/list/show
              5. Materialize the narrow native skills or router tags needed
              6. skillager lookback --agent codex --external-session-id <id>

            Important rules:
              - Do not activate or materialize unreviewed skills unless the user explicitly asks.
              - Agents should start with `skillager status`; it is metadata-only and safe.
              - Agents should ask the user to run `skillager setup` when status reports review needed.
              - Prefer project scope inside repos so users can inspect and customize local copies.
              - Use --json when another program or agent needs stable machine-readable output.

            Agent command contract:
              status/search/list/show without --content are safe metadata commands.
              setup/review/trust/block/materialize change state and need user intent.
              activate/show --content reveal skill bodies and require prior approval.
            """
        ),
        epilog="Run `skillager <command> --help` for command examples.",
    )
    parser.add_argument("--version", action="version", version=f"skillager {__version__}")
    parser.add_argument("--state-dir", type=Path, help="Override Skillager state directory.")
    parser.add_argument("--catalog-state-dir", type=Path, help="Override reusable collection/tag catalog state directory.")
    sub = parser.add_subparsers(required=True)

    add_setup_parser(sub)
    add_status_parser(sub)
    add_collection_parser(sub)
    add_tag_parser(sub)
    add_project_parser(sub)

    p = sub.add_parser(
        "index",
        help="Discover and index skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Discover skills and rebuild the local index. Prefer `setup` in a new environment.",
        epilog="Examples:\n  skillager index\n  skillager index --no-packages\n  skillager index examples --no-packages --json",
    )
    p.add_argument("paths", nargs="*", type=Path, help="Optional skill roots or directories to scan instead of default discovery roots.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--json", action="store_true", help="Emit index data as JSON.")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser(
        "list",
        help="List effective project skill metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List effective project skills, including attached collection-tag skills. Blocked skills are hidden unless requested.",
        epilog="Examples:\n  skillager list\n  skillager list --no-packages --json\n  skillager list --include-global\n  skillager list --trust reviewed\n  skillager list --source python-package --json",
    )
    p.add_argument("--source")
    p.add_argument("--trust")
    p.add_argument("--activation")
    p.add_argument("--audience")
    p.add_argument("--package")
    p.add_argument("--no-packages", action="store_true", help="Hide installed package skills from the listing.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global native skills. Defaults to local/project/package inventory.")
    p.add_argument("--include-blocked", action="store_true", help="Include blocked skills in output.")
    p.add_argument("--json", action="store_true", help="Emit listed skills as JSON.")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "search",
        help="Search effective project skill metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Search compact effective project skill metadata. Search does not activate or materialize skills.",
        epilog=(
            "Examples:\n"
            "  skillager search dataframe\n"
            "  skillager search testing --trusted-only\n"
            "  skillager search pandas --json\n"
            "  skillager search pandas --json --limit 20\n"
            "  skillager search pandas --json --full-json"
        ),
    )
    p.add_argument("query")
    p.add_argument("--tag", help="Search skills in a curated tag.")
    p.add_argument("--include-blocked", action="store_true", help="Include blocked skills in search results.")
    p.add_argument("--include-global", action="store_true", help="Include global native skills. Defaults to project/environment/package and attached collection skills.")
    p.add_argument("--trusted-only", action="store_true", help="Return only reviewed/trusted/pinned skills.")
    p.add_argument("--approved-only", action="store_true", help="Alias for --trusted-only; when used with --tag, requires the tag to be attached to this project.")
    p.add_argument("--agent", choices=["codex", "claude"], help="Include compatibility warnings for this agent.")
    p.add_argument("--compatible-only", action="store_true", help="Hide skills explicitly marked incompatible with --agent. Skills without metadata are assumed compatible.")
    p.add_argument("--limit", type=int, default=10, help="Maximum search results to return. Use 0 for no limit.")
    p.add_argument("--no-session-record", action="store_true", help="Do not record this search in the compact local session log.")
    p.add_argument("--json", action="store_true", help="Emit search results as JSON.")
    p.add_argument("--full-json", action="store_true", help="Emit full indexed metadata instead of compact agent-facing search results.")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "show",
        help="Show skill metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Show one effective project skill's metadata. Use --content only when the skill has already been reviewed or the user asks.",
        epilog="Examples:\n  skillager show pandas/data-cleaning\n  skillager show pandas/data-cleaning --json\n  skillager show pandas/data-cleaning --content",
    )
    p.add_argument("skill_id")
    p.add_argument("--content", action="store_true", help="Show full SKILL.md content. Avoid for unapproved skills.")
    p.add_argument("--activate", action="store_true", help="Record this show as an activation event.")
    p.add_argument("--json", action="store_true", help="Emit skill metadata/content as JSON.")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "activate",
        help="Emit full skill content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Emit full skill content to stdout. Activation requires reviewed/trusted/pinned status unless --force is used.",
        epilog="Examples:\n  skillager activate pandas/data-cleaning\n  skillager activate pandas/data-cleaning --from-stub pandas-data-cleaning\n  skillager activate pandas/data-cleaning --format codex\n  skillager activate pandas/data-cleaning --agent codex --external-session-id <id>",
    )
    p.add_argument("skill_id")
    p.add_argument("--format", choices=["markdown", "codex", "claude", "json"], default="markdown")
    p.add_argument("--force", action="store_true", help="Allow activation despite unreviewed/high-risk status. Use only with explicit user approval.")
    p.add_argument("--allow-incompatible", action="store_true", help="Allow activation even when skill metadata explicitly excludes this agent.")
    p.add_argument("--from-router", help="Router skill slug, e.g. skillager-gis. Refuses skills outside the attached router tag.")
    p.add_argument("--from-stub", help="Stub skill slug, e.g. pandas-data-cleaning. Refuses activation unless that stub is materialized in this project.")
    p.add_argument("--agent", help="Agent name for session tracking, e.g. codex or claude.")
    p.add_argument("--external-session-id", help="Codex/Claude session ID for lookback tracking.")
    p.add_argument("--no-session-record", action="store_true", help="Do not record this activation in the current Skillager session.")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser(
        "scan",
        help="Scan one path or all indexed skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run the static safety scanner over a file, skill directory, skill ID, or all indexed skills.",
        epilog="Examples:\n  skillager scan pandas/data-cleaning\n  skillager scan path/to/SKILL.md\n  skillager scan --all --json",
    )
    p.add_argument("target", nargs="?")
    p.add_argument("--all", action="store_true", help="Scan all indexed skills.")
    p.add_argument("--json", action="store_true", help="Emit scan findings as JSON.")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser(
        "trust",
        help="Trust a skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Mark one skill reviewed/trusted/pinned by recording its current directory hash.",
        epilog="Examples:\n  skillager trust pandas/data-cleaning\n  skillager trust pandas/data-cleaning --state pinned",
    )
    p.add_argument("skill_id")
    p.add_argument("--state", choices=["reviewed", "trusted", "pinned"], default="reviewed")
    p.set_defaults(func=cmd_trust)

    p = sub.add_parser(
        "block",
        help="Block a skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Block one skill so it is hidden from default list/search/materialize flows.",
        epilog="Example:\n  skillager block suspicious/skill",
    )
    p.add_argument("skill_id")
    p.set_defaults(func=cmd_block)

    add_review_parser(sub)
    add_materialize_parser(sub)

    p = sub.add_parser(
        "onboard",
        help="Create Skillager metadata for existing SKILL.md files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate sidecar skillager.yaml metadata for existing skill directories.",
        epilog="Examples:\n  skillager onboard ~/.codex/skills\n  skillager onboard ~/.claude/skills --dry-run --json",
    )
    p.add_argument("path", type=Path)
    p.add_argument("--dry-run", action="store_true", help="Report sidecar files that would be written without writing them.")
    p.add_argument("--json", action="store_true", help="Emit onboarding results as JSON.")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser(
        "session",
        help="Manage Skillager sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Track Skillager usage against a Codex or Claude session ID for later lookback.",
        epilog="Examples:\n  skillager session start --agent codex --external-session-id <id>\n  skillager session current --json\n  skillager session events\n  skillager session prune",
    )
    session_sub = p.add_subparsers(required=True)
    start = session_sub.add_parser("start")
    start.add_argument("--agent", default="unknown", help="Agent name, usually codex or claude.")
    start.add_argument("--external-session-id", help="Codex/Claude session ID.")
    start.add_argument("--external-conversation-id", help="Optional secondary conversation ID.")
    start.set_defaults(func=cmd_session_start)
    end = session_sub.add_parser("end")
    end.add_argument("--agent", help="Require the current session to match this agent before ending.")
    end.add_argument("--external-session-id", help="Require the current session to match this external session ID before ending.")
    end.set_defaults(func=cmd_session_end)
    current = session_sub.add_parser("current")
    current.add_argument("--json", action="store_true", help="Emit current session metadata as JSON.")
    current.set_defaults(func=cmd_session_current)
    events = session_sub.add_parser("events")
    events.add_argument("session_id", nargs="?")
    events.add_argument("--json", action="store_true", help="Emit session events as JSON.")
    events.set_defaults(func=cmd_session_events)
    redact = session_sub.add_parser("redact")
    redact.add_argument("session_id")
    redact.set_defaults(func=cmd_session_redact)
    prune = session_sub.add_parser("prune")
    prune.add_argument("--days", type=int, help="Delete sessions whose last event is older than this many days.")
    prune.add_argument("--max-mb", type=int, help="Maximum total session event storage in MB.")
    prune.add_argument("--max-events-per-session", type=int, help="Maximum events retained in each session file.")
    prune.add_argument("--json", action="store_true", help="Emit prune result as JSON.")
    prune.set_defaults(func=cmd_session_prune)

    p = sub.add_parser(
        "lookback",
        help="Generate session lookback or record feedback.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Summarize what skills were used in a session and record explicit feedback.",
        epilog="Examples:\n  skillager lookback --agent codex --external-session-id <id>\n  skillager lookback --recent 20 --json\n  skillager lookback --feedback useful --skill-id pandas/data-cleaning\n  skillager lookback --feedback route-only --skill-id pandas/data-cleaning\n  skillager lookback --json",
    )
    p.add_argument("--session-id")
    p.add_argument("--agent")
    p.add_argument("--external-session-id")
    p.add_argument("--recent", type=int, default=10, help="Include this many most-recent sessions when computing promotion/demotion recommendations.")
    p.add_argument("--no-active", action="store_true", help="Do not add currently active sessions outside the recent window to recommendation evidence.")
    p.add_argument("--feedback", choices=["useful", "not_useful", "harmful", "materialize", "route-only", "block"])
    p.add_argument("--skill-id")
    p.add_argument("--note")
    p.add_argument("--json", action="store_true", help="Emit lookback report as JSON.")
    p.set_defaults(func=cmd_lookback)

    return parser


def add_setup_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "setup",
        help="First-run environment review: discover, select, scan, and optionally trust skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Discover skills in the current project/environment and present a review summary.

            This is the first command to run in a new environment. It scans discovered
            skills and shows risk/trust buckets. It does not trust anything unless an
            explicit action flag is provided. In interactive mode, after review is
            complete, setup asks which agent target you use and installs Skillager
            Working plus any narrow native project skills you choose one by one.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager setup
              skillager setup --fresh
              skillager setup --source project --accept-low
              skillager setup --include-global
              skillager setup --package pandas --trust-selected reviewed
              skillager setup --block-high
              skillager setup --details
              skillager setup --non-interactive
              skillager setup --json
              skillager setup --summary-json

            Next step after trust changes:
              Restart the chosen agent in this project, then tell it what you plan
              to do; Skillager Working will guide narrow router/native exposure.
            """
        ),
    )
    p.add_argument("paths", nargs="*", type=Path, help="Optional skill roots or directories to scan instead of default discovery roots.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills in setup review. Defaults to local/environment/package skills only.")
    p.add_argument("--fresh", action="store_true", help="Clear prior trust decisions for the selected setup scope before review. Does not delete materialized skill files.")
    add_review_filters(p)
    add_review_actions(p)
    p.add_argument("--details", action="store_true", help="Print every selected skill. Default output is compact.")
    p.add_argument("--non-interactive", action="store_true", help="Print report only; do not prompt for choices.")
    p.add_argument("--json", action="store_true", help="Emit setup report as JSON.")
    p.add_argument("--summary-json", action="store_true", help="Emit compact setup JSON without per-skill metadata bodies.")
    p.set_defaults(func=cmd_setup)


def add_status_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "status",
        help="Agent-safe check for new or unreviewed skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Check whether the current project/environment has new or unreviewed
            skills. This command rebuilds compact metadata and scanner results,
            but does not activate skills, emit skill bodies, approve anything, or
            materialize anything.

            Agents can run this at session start from a project with a Skillager
            handoff note. If review is needed, ask the user to run `skillager setup`.
            If lookback is pending, ask before running `skillager lookback`.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager status
              skillager status --json
              skillager status --json --full-json
              skillager status --quiet
              skillager status --exit-code
            """
        ),
    )
    p.add_argument("paths", nargs="*", type=Path, help="Optional skill roots or directories to scan instead of default discovery roots.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills. Defaults to local/environment/package skills only.")
    p.add_argument("--all", action="store_true", help="Ignore the saved setup scope and report all selected skills.")
    p.add_argument("--quiet", action="store_true", help="Print one concise line.")
    p.add_argument("--exit-code", action="store_true", help="Exit 10 when review is needed. Default exit code is 0.")
    p.add_argument("--json", action="store_true", help="Emit status as JSON.")
    p.add_argument("--full-json", action="store_true", help="Include verbose scope baseline details in JSON output.")
    p.set_defaults(func=cmd_status)


def add_collection_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "collection",
        help="Manage indexed skill collections.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Collections are local skill inventory. Adding a collection does not expose skills to agents.",
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager collection add ~/skills/community --name community
              skillager collection enable community
              skillager collection list
              skillager collection refresh community
              skillager collection search community gis
              skillager collection show community/gis-domain
              skillager collection remove community
            """
        ),
    )
    collection_sub = p.add_subparsers(required=True)
    add = collection_sub.add_parser("add")
    add.add_argument("path", type=Path)
    add.add_argument("--name", required=True)
    add.add_argument("--json", action="store_true")
    add.set_defaults(func=cmd_collection_add)
    list_cmd = collection_sub.add_parser("list")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_collection_list)
    refresh = collection_sub.add_parser("refresh")
    refresh.add_argument("name")
    refresh.add_argument("--json", action="store_true")
    refresh.set_defaults(func=cmd_collection_refresh)
    enable = collection_sub.add_parser("enable")
    enable.add_argument("name")
    enable.add_argument("--tag", help="Project tag to create/update. Defaults to the collection name.")
    enable.add_argument("--sync", action="store_true", help="Replace the tag contents with the collection's current skills instead of merging.")
    enable.add_argument("--json", action="store_true")
    enable.set_defaults(func=cmd_collection_enable)
    search = collection_sub.add_parser("search")
    search.add_argument("name")
    search.add_argument("query")
    search.add_argument("--include-blocked", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_collection_search)
    show = collection_sub.add_parser("show")
    show.add_argument("skill_id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_collection_show)
    remove = collection_sub.add_parser("remove")
    remove.add_argument("name")
    remove.set_defaults(func=cmd_collection_remove)


def add_tag_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "tag",
        help="Manage curated skill tags.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Tags are curated sets of collection skill IDs. Tags do not expose skills until attached to a project.",
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager tag create gis
              skillager tag add gis community/gis-domain community/topology
              skillager tag add community --from-collection community
              skillager tag add community --from-collection community --sync
              skillager tag show gis
              skillager tag remove gis community/gis-domain
            """
        ),
    )
    tag_sub = p.add_subparsers(required=True)
    create = tag_sub.add_parser("create")
    create.add_argument("tag")
    create.set_defaults(func=cmd_tag_create)
    add = tag_sub.add_parser("add")
    add.add_argument("tag")
    add.add_argument("skill_ids", nargs="*")
    add.add_argument("--from-collection", help="Add every skill from a registered collection.")
    add.add_argument("--all", action="store_true", help="Add every registered collection skill.")
    add.add_argument("--sync", action="store_true", help="Replace the tag contents with the selected skills.")
    add.set_defaults(func=cmd_tag_add)
    remove = tag_sub.add_parser("remove")
    remove.add_argument("tag")
    remove.add_argument("skill_ids", nargs="+")
    remove.set_defaults(func=cmd_tag_remove)
    list_cmd = tag_sub.add_parser("list")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_tag_list)
    show = tag_sub.add_parser("show")
    show.add_argument("tag")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_tag_show)


def add_project_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "project",
        help="Manage project Skillager settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Attach curated tags to the current project. Attached tag skills become setup/status candidates, not agent-visible skills.",
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager project attach-tag gis
              skillager project tags
              skillager project detach-tag gis
            """
        ),
    )
    project_sub = p.add_subparsers(required=True)
    attach = project_sub.add_parser("attach-tag")
    attach.add_argument("tag")
    attach.set_defaults(func=cmd_project_attach_tag)
    detach = project_sub.add_parser("detach-tag")
    detach.add_argument("tag")
    detach.set_defaults(func=cmd_project_detach_tag)
    tags = project_sub.add_parser("tags")
    tags.add_argument("--json", action="store_true")
    tags.set_defaults(func=cmd_project_tags)


def add_review_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "review",
        help="Review indexed skills before trusting or enabling them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Review already-indexed skills. Use this after setup, after package changes,
            or when deciding whether to trust/block/materialize a selected subset.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager review --summary
              skillager review --source project
              skillager review --include-global --summary
              skillager review pandas/data-cleaning --trust-selected reviewed
              skillager review --source collection --trust-all
              skillager review --block-high
              skillager review --json
            """
        ),
    )
    p.add_argument("skill_ids", nargs="*")
    add_review_filters(p)
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills in review. Defaults to local/environment/package skills only.")
    add_review_actions(p)
    p.add_argument("--summary", action="store_true", help="Show compact buckets without per-skill details.")
    p.add_argument("--json", action="store_true", help="Emit review result as JSON.")
    p.set_defaults(func=cmd_review)


def add_materialize_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "materialize",
        help="Copy reviewed skills into agent-native skill directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Copy reviewed/trusted/pinned skill directories into Codex or Claude native
            skill directories. This is how Skillager presents skills to agents.

            Native materialization copies the full skill directory and writes
            provenance. Stub materialization writes a tiny native handle that
            tells the agent how to activate the full reviewed body through
            Skillager on demand. Project materialization also installs or refreshes Skillager Working,
            writes a one-line handoff note to existing AGENTS.md/CLAUDE.md files,
            or creates the right default file for the selected agent. Customized
            local copies are not overwritten unless --force is used.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager materialize --agent codex --scope project
              skillager materialize --agent claude --scope project
              skillager materialize --all-agents --scope project
              skillager materialize pandas/data-cleaning --agent codex
              skillager materialize --tag gis --mode index --agent codex
              skillager materialize pandas/data-cleaning --mode stub --agent codex
              skillager materialize --dry-run --json
            """
        ),
    )
    p.add_argument("skill_ids", nargs="*")
    p.add_argument("--tag", help="Materialize skills from a curated tag.")
    p.add_argument("--mode", choices=["native", "index", "stub"], default="native", help="native copies each skill; stub writes tiny activation handles; index creates one router skill for --tag.")
    p.add_argument("--agent", action="append", choices=["codex", "claude"], help="Agent target. Repeat to target multiple agents. Defaults to codex.")
    p.add_argument("--all-agents", action="store_true", help="Target both codex and claude.")
    p.add_argument("--scope", choices=["project", "global"], default="project", help="Materialize into project .agents or global agent skill directory.")
    p.add_argument("--include-unreviewed", action="store_true", help="Allow discovered skills to be materialized.")
    p.add_argument("--allow-incompatible", action="store_true", help="Allow native/stub materialization even when skill metadata explicitly excludes the selected agent.")
    p.add_argument("--dry-run", action="store_true", help="Report target paths without writing files.")
    p.add_argument("--force", action="store_true", help="Overwrite existing Skillager-managed customized targets.")
    add_review_filters(p)
    p.add_argument("--json", action="store_true", help="Emit materialization results as JSON.")
    p.set_defaults(func=cmd_materialize)


def add_review_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", help="Filter by source type, e.g. project, global, environment, python-package.")
    parser.add_argument("--audience", help="Filter by audience.")
    parser.add_argument("--package", help="Filter by package name.")
    parser.add_argument("--activation", help="Filter by activation mode.")
    parser.add_argument("--include-blocked", action="store_true", help="Include blocked skills in the selection.")


def add_review_actions(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--accept-low", action="store_true", help="Mark selected low-risk skills as reviewed.")
    parser.add_argument("--yolo", action="store_true", help="Mark all selected skills reviewed, including high-risk findings. Same behavior as --trust-all; use only for fully trusted sources.")
    parser.add_argument("--trust-all", action="store_true", help="Mark all selected skills reviewed, including high-risk findings. Use only for fully trusted sources.")
    parser.add_argument("--trust-selected", choices=["reviewed", "trusted", "pinned"], help="Trust selected skills after review.")
    parser.add_argument("--block-high", action="store_true", help="Block selected high-risk skills.")


def root(args: argparse.Namespace) -> Path:
    return args.state_dir.resolve() if args.state_dir else state_root()


def catalog_root(args: argparse.Namespace) -> Path:
    if getattr(args, "catalog_state_dir", None):
        return args.catalog_state_dir.resolve()
    stored = load_project_tags(root(args)).get("catalog_state_dir")
    if stored:
        return Path(stored).expanduser().resolve()
    return args.state_dir.resolve() if args.state_dir else catalog_state_root()


def cmd_collection_add(args: argparse.Namespace) -> int:
    result = add_collection(catalog_root(args), args.name, args.path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['collection']['name']}: indexed {result['indexed']} skill(s)")
        if result.get("errors"):
            print(f"errors: {len(result['errors'])}")
        print("No skills were exposed to agents. Add skills to tags and attach tags to a project to use them.")
    return 0


def cmd_collection_list(args: argparse.Namespace) -> int:
    data = load_collections(catalog_root(args))
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for name, item in sorted(data.get("collections", {}).items()):
            print(f"{name}\t{item['path']}")
    return 0


def cmd_collection_refresh(args: argparse.Namespace) -> int:
    data = refresh_collection(catalog_root(args), args.name)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"{data['name']}: indexed {len(data.get('skills', []))} skill(s)")
        if data.get("errors"):
            print(f"errors: {len(data['errors'])}")
    return 0


def cmd_collection_enable(args: argparse.Namespace) -> int:
    data = refresh_collection(catalog_root(args), args.name)
    tag = args.tag or data["name"]
    skill_ids = [skill["id"] for skill in data.get("skills", [])]
    tag_data = set_tag_skills(catalog_root(args), tag, skill_ids, sync=args.sync, source_collection=data["name"])
    project = attach_project_tag(root(args), tag, catalog_root=catalog_root(args))
    result = {
        "collection": data["name"],
        "tag": tag_data["tag"],
        "skills": len(tag_data["skills"]),
        "attached_tags": project.get("attached_tags", []),
        "errors": data.get("errors", []),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{data['name']}: enabled {len(tag_data['skills'])} skill(s) as catalog tag {tag_data['tag']} and attached it to this project")
        if data.get("errors"):
            print(f"errors: {len(data['errors'])}")
        print("Next: run `skillager setup --source collection` to review and trust collection skills.")
    return 0


def cmd_collection_search(args: argparse.Namespace) -> int:
    results = search_collection(catalog_root(args), args.name, args.query, include_blocked=args.include_blocked)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for skill in results:
            print(f"{skill['score']}\t{skill['id']}\t{skill['trust']}\t{skill['summary']}")
    return 0


def cmd_collection_show(args: argparse.Namespace) -> int:
    skills = collection_skills(catalog_root(args))
    skill = next((item for item in skills if item.get("id") == args.skill_id), None)
    if skill is None:
        raise KeyError(f"collection skill not found: {args.skill_id}")
    if args.json:
        print(json.dumps(skill, indent=2, sort_keys=True))
    else:
        print(f"{skill['id']}")
        print(f"  name: {skill['name']}")
        print(f"  trust: {skill['trust']}")
        print(f"  risk: {skill.get('scan', {}).get('risk')}")
        print(f"  file: {skill['entrypoint']}")
        if skill.get("summary"):
            _print_wrapped("  used for: ", skill["summary"], width=_output_width(), max_chars=260)
    return 0


def cmd_collection_remove(args: argparse.Namespace) -> int:
    removed = remove_collection(catalog_root(args), args.name)
    print(f"{args.name}: {'removed' if removed else 'not found'}")
    return 0


def cmd_tag_create(args: argparse.Namespace) -> int:
    tag = create_tag(catalog_root(args), args.tag)
    print(f"{tag['tag']}: created")
    return 0


def cmd_tag_add(args: argparse.Namespace) -> int:
    skill_ids = list(args.skill_ids)
    source_collection = None
    if args.from_collection:
        source_collection = args.from_collection
        skill_ids.extend(skill["id"] for skill in collection_skills(catalog_root(args), args.from_collection, trust_root=root(args)))
    if args.all:
        skill_ids.extend(skill["id"] for skill in collection_skills(catalog_root(args), trust_root=root(args)))
    if args.sync or args.from_collection or args.all:
        tag = set_tag_skills(catalog_root(args), args.tag, skill_ids, sync=args.sync, source_collection=source_collection)
        print(f"{tag['tag']}: {len(tag['skills'])} skill(s)")
        return 0
    tag = None
    for skill_id in skill_ids:
        tag = add_tag_skill(catalog_root(args), args.tag, skill_id)
    if tag is None:
        raise ValueError("provide at least one skill id")
    print(f"{tag['tag']}: {len(tag['skills'])} skill(s)")
    return 0


def cmd_tag_remove(args: argparse.Namespace) -> int:
    tag = None
    for skill_id in args.skill_ids:
        tag = remove_tag_skill(catalog_root(args), args.tag, skill_id)
    if tag is None:
        raise ValueError("provide at least one skill id")
    print(f"{tag['tag']}: {len(tag['skills'])} skill(s)")
    return 0


def cmd_tag_list(args: argparse.Namespace) -> int:
    data = load_tags(catalog_root(args))
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for tag, skills in sorted(data.get("tags", {}).items()):
            print(f"{tag}\t{len(skills)} skill(s)")
    return 0


def cmd_tag_show(args: argparse.Namespace) -> int:
    skills = tag_skills(catalog_root(args), args.tag, trust_root=root(args))
    if args.json:
        print(json.dumps({"tag": args.tag, "skills": skills}, indent=2, sort_keys=True))
    else:
        for skill in skills:
            print(f"{skill['id']}\t{skill['trust']}\t{skill['summary']}")
    return 0


def cmd_project_attach_tag(args: argparse.Namespace) -> int:
    data = attach_project_tag(root(args), args.tag, catalog_root=catalog_root(args))
    print(f"{args.tag}: attached")
    print(f"attached tags: {', '.join(data.get('attached_tags', [])) or '-'}")
    return 0


def cmd_project_detach_tag(args: argparse.Namespace) -> int:
    data = detach_project_tag(root(args), args.tag)
    print(f"{args.tag}: detached")
    print(f"attached tags: {', '.join(data.get('attached_tags', [])) or '-'}")
    return 0


def cmd_project_tags(args: argparse.Namespace) -> int:
    data = load_project_tags(root(args))
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for tag in data.get("attached_tags", []):
            print(tag)
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    args.yolo = bool(args.yolo or args.trust_all)
    if args.json and args.summary_json:
        raise ValueError("--json and --summary-json cannot be combined")
    audience = args.audience
    if _should_prompt_setup_audience(args):
        audience = _prompt_setup_audience(root(args), args, catalog_root=catalog_root(args))
        if audience == "__cancel__":
            print("Setup canceled.")
            return 1
    report = setup_environment(
        root(args),
        paths=args.paths or None,
        include_packages=not args.no_packages,
        extra_skills=_review_extra_skills(args),
        source=args.source,
        audience=audience,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_global=args.include_global,
        fresh=args.fresh,
        accept_low=args.accept_low,
        yolo=args.yolo,
        trust_state=args.trust_selected,
        block_high=args.block_high,
    )
    if args.summary_json:
        print(json.dumps(_compact_setup_report(report), indent=2, sort_keys=True))
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Indexed {report['indexed']} skills")
        if report.get("skipped_global"):
            print(f"Skipped {report['skipped_global']} global skill(s) already installed; use --include-global to review them.")
        if report.get("fresh_reset"):
            print(f"Reset {report['fresh_reset']} prior trust decision(s) for fresh review.")
        if report.get("errors"):
            print(f"Errors: {len(report['errors'])}")
        _print_review_report(report["selected"], report["summary"], report["action"], compact=not args.details)
        _print_out_of_scope_collections(root(args), catalog_root(args), action_requested=bool(args.yolo or args.accept_low or args.trust_selected or args.block_high))
        action_requested = any((args.accept_low, args.yolo, args.trust_selected, args.block_high))
        if not action_requested and not args.non_interactive:
            _interactive_setup(root(args), report["selected"], audience=audience, include_global=args.include_global, catalog_root=catalog_root(args))
        elif not action_requested:
            print()
            _print_setup_next_steps(report["selected"])
        elif report["action"].get("changed"):
            print()
            print("Next step: tell your agent what you plan to do; it can inspect approved metadata with `skillager search --trusted-only --json` and materialize the right router or native skills.")
    return 0


def _compact_setup_report(report: dict[str, Any]) -> dict[str, Any]:
    selected = report.get("selected", [])
    review_needed = [skill for skill in selected if skill.get("trust") == "discovered"]
    approved = [skill for skill in selected if skill.get("trust") in {"reviewed", "trusted", "pinned"}]
    return {
        "indexed": report.get("indexed", 0),
        "selected": len(selected),
        "approved": len(approved),
        "review_needed": len(review_needed),
        "skipped_global": report.get("skipped_global", 0),
        "fresh_reset": report.get("fresh_reset", 0),
        "errors": len(report.get("errors", [])),
        "summary": report.get("summary", {}),
        "action": report.get("action", {}),
        "selected_ids": [skill.get("id") for skill in selected],
        "review_needed_ids": [skill.get("id") for skill in review_needed],
    }


def cmd_status(args: argparse.Namespace) -> int:
    data = build_index(root(args), args.paths or None, include_packages=not args.no_packages)
    extra_skills = attached_tag_skills(root(args), catalog_root=catalog_root(args))
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    saved_scope = _load_status_scope(root(args)) if not args.all else None
    scope_audience = saved_scope.get("audience") if saved_scope else None
    include_global = args.include_global
    if saved_scope:
        include_global = include_global or bool(saved_scope.get("include_global"))
    skills = selected_skills(data.get("skills", []), audience=scope_audience, include_global=include_global)
    _save_native_inventory(root(args), _effective_project_skills(root(args), catalog_root=catalog_root(args)))
    summary = review_summary(skills)
    materialized = _materialized_project_counts(Path.cwd())
    exposure = _project_exposure(Path.cwd())
    review_needed = _status_review_needed(skills, saved_scope=saved_scope)
    approved = [skill for skill in skills if skill.get("trust") in {"reviewed", "trusted", "pinned"}]
    user_installed = [skill for skill in skills if skill.get("trust_reason") == "user-installed"]
    high_risk_user_installed = [skill for skill in user_installed if skill.get("scan", {}).get("risk") == "high"]
    high_risk_user_installed_ids = [skill["id"] for skill in high_risk_user_installed]
    blocked = [skill for skill in data.get("skills", []) if skill.get("trust") == "blocked"]
    lookback_summary = _status_lookback_summary(root(args))
    collection_summary = _status_collection_summary(root(args), catalog_root(args))
    update = check_for_update(cache_root(), current_version=__version__)
    status = {
        "indexed": len(data.get("skills", [])),
        "selected": len(skills),
        "review_needed": len(review_needed),
        "approved": len(approved),
        "user_installed": len(user_installed),
        "high_risk_user_installed": len(high_risk_user_installed),
        "high_risk_user_installed_ids": high_risk_user_installed_ids,
        "blocked": len(blocked),
        "skipped_global": sum(1 for skill in data.get("skills", []) if skill.get("source", {}).get("type") == "global") if not args.include_global else 0,
        "summary": summary,
        "materialized": materialized,
        "reviewed_scope_count": saved_scope.get("selected_count") if saved_scope else None,
        "exposure_count": len([skill_id for skill_id in exposure if skill_id != "skillager/working"]),
        "needs_setup": bool(review_needed),
        "lookback_pending": lookback_summary["pending"],
        "lookback_summary": lookback_summary,
        "collections": collection_summary,
        "update": update,
        "scope": saved_scope or None,
        "message": _status_message(review_needed, lookback_summary=lookback_summary, collection_summary=collection_summary),
    }
    if args.json:
        payload = status if args.full_json else _compact_status(status)
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.quiet:
        print(status["message"])
    else:
        _print_status(status)
    return 10 if args.exit_code and review_needed else 0


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    scope = payload.get("scope")
    if isinstance(scope, dict):
        payload["scope"] = {key: value for key, value in scope.items() if key != "baseline"}
    return payload


def _status_lookback_summary(state_root: Path) -> dict[str, Any]:
    try:
        report = build_lookback(state_root)
    except Exception:
        return {
            "pending": False,
            "recommendations": 0,
            "observed_overlaps": 0,
            "candidate_sessions": 0,
            "active_candidate_sessions": 0,
        }
    recommendations = report.get("recommendations") or []
    overlaps = report.get("observed_overlaps") or []
    actions: dict[str, int] = {}
    for rec in recommendations:
        action = str(rec.get("action") or "unknown")
        actions[action] = actions.get(action, 0) + 1
    return {
        "pending": bool(recommendations or overlaps),
        "recommendations": len(recommendations),
        "observed_overlaps": len(overlaps),
        "candidate_sessions": report.get("candidate_session_count", 0),
        "active_candidate_sessions": report.get("active_candidate_sessions", 0),
        "actions": actions,
    }


def _status_collection_summary(state_root: Path, catalog_root: Path) -> dict[str, Any]:
    collections = load_collections(catalog_root).get("collections", {})
    tag_data = load_tags(catalog_root)
    tags = tag_data.get("tags", {})
    tag_metadata = tag_data.get("tag_metadata", {})
    attached = set(load_project_tags(state_root).get("attached_tags", []))
    items = []
    total_skills = 0
    attached_count = 0
    for name, item in sorted(collections.items()):
        try:
            collection_ids = {skill["id"] for skill in collection_skills(catalog_root, name, trust_root=state_root)}
            count = len(collection_ids)
        except Exception:
            collection_ids = set()
            count = 0
        matching_tags = [
            tag
            for tag, skill_ids in tags.items()
            if name in set((tag_metadata.get(tag) or {}).get("source_collections") or [])
            or (tag == name and tag in attached and collection_ids.intersection(skill_ids))
        ]
        tag_attached = any(tag in attached for tag in matching_tags)
        tag_exists = bool(matching_tags)
        total_skills += count
        if tag_attached:
            attached_count += 1
        items.append(
            {
                "name": name,
                "path": item.get("path"),
                "skills": count,
                "tag_exists": tag_exists,
                "attached": tag_attached,
                "tags": matching_tags,
                "attached_tags": [tag for tag in matching_tags if tag in attached],
            }
        )
    return {
        "count": len(items),
        "skill_count": total_skills,
        "attached_count": attached_count,
        "unattached_count": len(items) - attached_count,
        "items": items,
    }


def _print_out_of_scope_collections(state_root: Path, catalog_root: Path, *, action_requested: bool) -> None:
    summary = _status_collection_summary(state_root, catalog_root)
    if not summary.get("unattached_count"):
        return
    names = ", ".join(f"{item['name']}={item['skills']}" for item in summary.get("items", []) if not item.get("attached"))
    prefix = "Not in setup scope" if action_requested else "Available collections"
    print()
    print(f"{prefix}: {summary['unattached_count']} unattached collection(s) ({names})")
    print("  run `skillager collection enable <name>` to include one in project setup")


def _status_message(review_needed: list[dict[str, Any]], *, lookback_summary: dict[str, Any] | None = None, collection_summary: dict[str, Any] | None = None) -> str:
    if review_needed:
        return f"Skillager: {len(review_needed)} new or changed unreviewed skill(s) available. Ask the user to run `skillager setup`."
    if collection_summary and collection_summary.get("unattached_count"):
        return "Skillager: registered collections are not attached to this project. Run `skillager collection enable <name>` before setup."
    if lookback_summary and lookback_summary.get("pending"):
        recs = lookback_summary.get("recommendations", 0)
        overlaps = lookback_summary.get("observed_overlaps", 0)
        return f"Skillager: no new unreviewed skills found. Lookback available: {recs} recommendation(s), {overlaps} overlap hint(s) (behavioral signals, not decisions). Run `skillager lookback`."
    return "Skillager: no new unreviewed skills found. Use only approved materialized skills."


def _print_status(status: dict[str, Any]) -> None:
    print(_style("Skillager status", "bold"))
    print(f"  - selected skills: {status['selected']}")
    print(f"  - approved: {status['approved']}")
    if status.get("user_installed"):
        print(f"  - user-installed native skills: {status['user_installed']}")
    if status.get("high_risk_user_installed"):
        print(f"  - high-risk user-installed skills: {status['high_risk_user_installed']} (review warnings recommended)")
        for skill_id in status.get("high_risk_user_installed_ids", []):
            print(f"    - {skill_id}")
    print(f"  - review needed: {status['review_needed']}")
    print(f"  - blocked: {status['blocked']}")
    if status["skipped_global"]:
        print(f"  - skipped global: {status['skipped_global']} (use --include-global to include)")
    collections = status.get("collections") or {}
    if collections.get("count"):
        names = ", ".join(f"{item['name']}={item['skills']}" for item in collections.get("items", [])[:5])
        if collections.get("count", 0) > 5:
            names += f", ... {collections['count'] - 5} more"
        print(
            "  - registered collections: "
            f"{collections['count']} ({names}) - "
            f"{collections.get('attached_count', 0)} attached"
        )
        if collections.get("unattached_count"):
            print("    run `skillager collection enable <name>` to onboard a collection")
    scope = status.get("scope")
    if scope:
        scope_bits = []
        if scope.get("audience"):
            scope_bits.append(f"audience={scope['audience']}")
        if scope.get("selected_count") is not None:
            scope_bits.append(f"reviewed-scope={scope['selected_count']}")
        if scope_bits:
            print(f"  - setup scope: {', '.join(scope_bits)}")
    materialized = status.get("materialized", {})
    if materialized:
        parts = [f"{agent}={count}" for agent, count in sorted(materialized.items())]
        print(f"  - materialized project skills: {', '.join(parts)}")
    if status.get("exposure_count"):
        print(f"  - exposed approved skills: {status['exposure_count']}")
    lookback = status.get("lookback_summary") or {}
    if lookback.get("pending"):
        print(
            "  - lookback pending: "
            f"{lookback.get('recommendations', 0)} recommendation(s), "
            f"{lookback.get('observed_overlaps', 0)} overlap hint(s) (behavioral signals, not decisions)"
        )
    update = status.get("update") or {}
    if update.get("available"):
        print(f"  - update available: skillager {update.get('latest_version')} (run `{update.get('command')}`)")
    by_risk = status.get("summary", {}).get("by_risk", {})
    if by_risk:
        risk_bits = ", ".join(_risk_count(risk, count) for risk, count in sorted(by_risk.items()))
        print(f"  - risk: {risk_bits}")
    print()
    print(status["message"])


def _status_review_needed(skills: list[dict[str, Any]], *, saved_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    baseline = saved_scope.get("baseline", {}) if saved_scope else {}
    review_needed = []
    for skill in skills:
        if skill.get("trust") != "discovered":
            continue
        if baseline.get(skill["id"]) == skill.get("content_hash"):
            continue
        review_needed.append(skill)
    return review_needed


def _status_scope_path(state_root: Path) -> Path:
    return state_root / "status_scope.json"


def _native_inventory_path(state_root: Path) -> Path:
    return state_root / "native_inventory.json"


def _save_native_inventory(state_root: Path, skills: list[dict[str, Any]]) -> None:
    entries = []
    for skill in skills:
        for target in skill.get("materialized_targets", []):
            if target.get("kind") in {"native", "stub"}:
                entries.append(
                    {
                        "skill_id": skill.get("id"),
                        "family_key": skill.get("family_key") or _native_candidate_key(skill),
                        **target,
                    }
                )
    state_root.mkdir(parents=True, exist_ok=True)
    _native_inventory_path(state_root).write_text(json.dumps({"schema": "skillager.native-inventory.v1", "skills": entries}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_status_scope(state_root: Path) -> dict[str, Any] | None:
    path = _status_scope_path(state_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save_status_scope(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    audience: str | None,
    include_global: bool,
    agents: list[str],
) -> None:
    data = {
        "schema": "skillager.status-scope.v1",
        "audience": audience,
        "include_global": include_global,
        "agents": agents,
        "selected_count": len(skills),
        "baseline": {skill["id"]: skill.get("content_hash") for skill in skills if skill.get("id") and skill.get("content_hash")},
    }
    state_root.mkdir(parents=True, exist_ok=True)
    _status_scope_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _materialized_project_counts(project: Path) -> dict[str, int]:
    roots = _project_skill_roots(project)
    counts: dict[str, int] = {}
    seen: set[Path] = set()
    for agent, agent_roots in roots.items():
        count = 0
        for root_path in agent_roots:
            if not root_path.is_dir():
                continue
            for sidecar in root_path.glob("*/skillager.materialized.yaml"):
                skill_dir = sidecar.parent.resolve()
                if skill_dir in seen:
                    continue
                seen.add(skill_dir)
                count += 1
        if count:
            counts[agent] = count
    return counts


def _should_prompt_setup_audience(args: argparse.Namespace) -> bool:
    return (
        not args.audience
        and not args.json
        and not args.non_interactive
        and not any((args.accept_low, args.trust_selected, args.block_high))
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _prompt_setup_audience(state_root: Path, args: argparse.Namespace, *, catalog_root: Path | None = None) -> str | None:
    data = build_index(state_root, args.paths or None, include_packages=not args.no_packages)
    extra_skills = attached_tag_skills(state_root, catalog_root=catalog_root)
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    skills = selected_skills(
        data.get("skills", []),
        source=args.source,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_global=args.include_global,
    )
    counts: dict[str, int] = {}
    for skill in skills:
        audience = skill.get("audience_guess", {}).get("audience") or "unknown"
        counts[audience] = counts.get(audience, 0) + 1
    if len([count for count in counts.values() if count]) <= 1:
        return None

    print(_style("Audience scope", "bold"))
    print("  This setup selection spans multiple audiences.")
    for audience, count in sorted(counts.items()):
        print(f"    - {audience}: {count}")
    while True:
        print("  1. User-facing library usage")
        print("  2. Dev/maintainer workflows")
        print("  3. Both user and dev")
        if counts.get("unknown"):
            print("  4. Unknown only")
            print("  5. Cancel setup")
        else:
            print("  4. Cancel setup")
        choice = _interactive_input("> ").strip().lower()
        if choice == "1" or choice == "user":
            return "user"
        if choice == "2" or choice in {"dev", "developer", "maintainer"}:
            return "dev"
        if choice == "3" or choice in {"both", "all"}:
            return None
        if counts.get("unknown") and (choice == "4" or choice == "unknown"):
            return "unknown"
        if (counts.get("unknown") and choice == "5") or (not counts.get("unknown") and choice == "4") or choice in {"q", "quit", "exit", "cancel"}:
            return "__cancel__"
        print("Enter a listed number, user, dev, both, or cancel.")


def cmd_index(args: argparse.Namespace) -> int:
    data = build_index(root(args), args.paths or None, include_packages=not args.no_packages)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Indexed {len(data['skills'])} skills")
        if data.get("errors"):
            print(f"Errors: {len(data['errors'])}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    skills = _effective_project_skills(root(args), catalog_root=catalog_root(args))
    if not args.include_global and not args.source:
        skills = [skill for skill in skills if skill.get("source", {}).get("type") != "global"]
    if args.no_packages and not args.source:
        skills = [skill for skill in skills if skill.get("source", {}).get("type") != "python-package"]
    skills = [_skill for _skill in skills if _matches_filters(_skill, args)]
    if args.json:
        print(json.dumps(skills, indent=2, sort_keys=True))
    else:
        for skill in skills:
            print(f"{skill['id']}\t{skill['activation']}\t{skill['trust']}\t{skill['source'].get('type')}\t{skill['summary']}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if args.tag:
        attached = args.tag in load_project_tags(root(args)).get("attached_tags", [])
        if args.approved_only and not attached:
            raise ValueError(f"tag is not attached to this project: {args.tag}")
        exposure = _project_exposure(Path.cwd())
        skills = []
        for skill in tag_skills(catalog_root(args), args.tag, trust_root=root(args)):
            item = _with_project_inventory_fields(skill, exposure)
            availability = set(item.get("availability", []))
            if attached:
                availability.add("attached-tag")
            item["availability"] = sorted(availability)
            item["tags"] = sorted(set(item.get("tags", [])) | {args.tag})
            skills.append(item)
    else:
        skills = _effective_project_skills(root(args), catalog_root=catalog_root(args))
        if not args.include_global:
            skills = [skill for skill in skills if skill.get("source", {}).get("type") != "global"]
    results = search_index(
        skills,
        args.query,
        include_blocked=args.include_blocked,
        include_untrusted=not (args.trusted_only or args.approved_only),
    )
    if args.compatible_only:
        if not args.agent:
            raise ValueError("--compatible-only requires --agent")
        results = [skill for skill in results if compatibility_problem(skill, args.agent) is None]
    if args.limit < 0:
        raise ValueError("--limit must be 0 or greater")
    if args.limit:
        results = results[: args.limit]
    record_search_event(
        root(args),
        query=args.query,
        results=results,
        agent=args.agent,
        tag=args.tag,
        trusted_only=bool(args.trusted_only or args.approved_only),
        limit=args.limit,
        no_record=args.no_session_record,
    )
    if args.json:
        payload = results if args.full_json else [_compact_search_result(skill, agent=args.agent) for skill in results]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for skill in results:
            print(f"{skill['score']}\t{skill['id']}\t{skill['trust']}\t{skill['summary']}")
    return 0


def _compact_search_result(skill: dict[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    scan = skill.get("scan") or {}
    compatibility = skill.get("compatibility") or {}
    problem = compatibility_problem(skill, agent)
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
        "score": skill.get("score"),
        "reasons": skill.get("reasons", []),
        "trust": skill.get("trust"),
        "trust_reason": skill.get("trust_reason"),
        "risk": scan.get("risk"),
        "source": skill.get("source", {}),
        "availability": skill.get("availability", []),
        "exposure": skill.get("exposure", "hidden"),
        "materialized_targets": skill.get("materialized_targets", []),
        "tags": skill.get("tags", []),
        "entrypoint": skill.get("entrypoint"),
        "compatibility": {
            "exclusive_to": compatibility.get("exclusive_to"),
            "incompatible_with": compatibility.get("incompatible_with", []),
            "assumptions": compatibility.get("assumptions", {}),
            "warnings": compatibility.get("warnings", {}),
            "agent": agent,
            "problem": problem,
            "activation_warnings": compatibility_warnings(skill, agent),
        },
    }


def cmd_show(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args))
    if args.content and skill.get("trust") not in {"reviewed", "trusted", "pinned"}:
        raise ValueError(f"skill content is not available until reviewed or trusted: {args.skill_id}")
    if args.activate:
        record_skill_event(root(args), "skill_activated", skill)
    elif args.content:
        record_skill_event(root(args), "skill_shown", skill)
    if args.json:
        payload: dict[str, Any] = {"skill": skill}
        if args.content:
            payload["content"] = Path(skill["entrypoint"]).read_text(encoding="utf-8", errors="replace")
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_skill(skill))
        if args.content:
            print()
            print(Path(skill["entrypoint"]).read_text(encoding="utf-8", errors="replace"))
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    if args.from_router and args.from_stub:
        raise ValueError("--from-router and --from-stub cannot be combined")
    if (args.from_router or args.from_stub) and args.force:
        raise ValueError("--from-router/--from-stub cannot be combined with --force")
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args), include_collection_inventory=bool(args.from_router))
    if args.from_router:
        _validate_router_activation(root(args), catalog_root(args), args.from_router, skill)
    if args.from_stub:
        _validate_stub_activation(skill, args.from_stub)
    if skill.get("trust") == "blocked" and not args.force:
        raise ValueError(f"skill is blocked: {args.skill_id}")
    if skill.get("trust") == "discovered" and not args.force:
        raise ValueError(f"skill is not reviewed or trusted: {args.skill_id}; run skillager setup/review or use --force")
    activation_agent = _activation_agent(args, skill)
    problem = compatibility_problem(skill, activation_agent)
    if problem and not args.allow_incompatible:
        raise ValueError(f"skill is {problem}; use --allow-incompatible only with explicit user approval")
    for warning in compatibility_warnings(skill, activation_agent):
        print(f"warning: {warning}", file=sys.stderr)
    if skill.get("scan", {}).get("risk") == "high" and not args.force:
        print(f"warning: skill has high-risk scan findings: {args.skill_id}", file=sys.stderr)
    record_skill_event(
        root(args),
        "skill_activated",
        skill,
        agent=args.agent,
        external_session_id=args.external_session_id,
        no_record=args.no_session_record,
    )
    print(render_skill(skill, fmt=args.format))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    reports = []
    if args.all:
        for skill in load_index(root(args)).get("skills", []):
            report = scan_path(Path(skill["root"]), allow_tools=bool(skill.get("safety", {}).get("allow_tools", False)))
            reports.append({"skill_id": skill["id"], **report})
    elif args.target:
        target = Path(args.target)
        if target.exists():
            reports.append({"path": str(target), **scan_path(target)})
        else:
            skill = find_skill(root(args), args.target)
            reports.append({"skill_id": skill["id"], **scan_path(Path(skill["root"]))})
    else:
        raise ValueError("provide a target or --all")
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
    else:
        for report in reports:
            label = report.get("skill_id") or report.get("path")
            print(f"{label}: risk={report['risk']} findings={len(report['findings'])}")
            for finding in report["findings"]:
                print(f"  line {finding['line']} {finding['severity']} {finding['code']}: {finding['message']}")
    return 0


def _effective_project_skills(state_root: Path, *, catalog_root: Path | None = None) -> list[dict[str, Any]]:
    catalog_root = catalog_root or state_root
    exposure = _project_exposure(Path.cwd())
    data = load_index(state_root)
    by_id: dict[str, dict[str, Any]] = {}
    for skill in data.get("skills", []):
        item = _with_project_inventory_fields(skill, exposure)
        by_id[item["id"]] = item
    for skill in attached_tag_skills(state_root, catalog_root=catalog_root):
        item = _with_project_inventory_fields(skill, exposure)
        availability = set(item.get("availability", []))
        availability.add("attached-tag")
        if item["id"] in by_id:
            existing = dict(by_id[item["id"]])
            existing["availability"] = sorted(set(existing.get("availability", [])) | availability)
            existing["tags"] = sorted(set(existing.get("tags", [])) | set(item.get("tags", [])))
            existing["trust"] = item.get("trust", existing.get("trust"))
            by_id[item["id"]] = existing
        else:
            item["availability"] = sorted(availability)
            item.setdefault("exposure", "hidden")
            by_id[item["id"]] = item
    return [by_id[skill_id] for skill_id in sorted(by_id)]


def _with_project_inventory_fields(skill: dict[str, Any], exposure: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    item = dict(skill)
    item["availability"] = _skill_availability(item)
    targets = list(exposure.get(item["id"], []))
    unmanaged = _unmanaged_native_target(item, Path.cwd())
    if unmanaged and not any(target.get("path") == unmanaged["path"] for target in targets):
        targets.append(unmanaged)
    item["materialized_targets"] = targets
    target_types = {target.get("kind") for target in targets}
    if len(target_types & {"native", "router", "stub"}) > 1:
        item["exposure"] = "multiple"
    elif "native" in target_types:
        item["exposure"] = "native"
    elif "router" in target_types:
        item["exposure"] = "router"
    elif "stub" in target_types:
        item["exposure"] = "stub"
    else:
        item["exposure"] = "hidden"
    return item


def _skill_availability(skill: dict[str, Any]) -> list[str]:
    source_type = skill.get("source", {}).get("type") or "unknown"
    if source_type == "python-package":
        return ["package"]
    if source_type == "collection":
        return ["collection"]
    return [source_type]


def _project_exposure(project: Path) -> dict[str, list[dict[str, Any]]]:
    exposure: dict[str, list[dict[str, Any]]] = {}
    roots = _project_skill_roots(project)
    for agent, root_paths in roots.items():
        for root_path in root_paths:
            if not root_path.is_dir():
                continue
            for sidecar in root_path.glob("*/skillager.materialized.yaml"):
                try:
                    data = load_mapping(sidecar)
                except Exception:
                    continue
                target = {
                    "agent": data.get("agent") or agent,
                    "scope": data.get("scope") or "project",
                    "path": str(sidecar.parent),
                    "status": "materialized",
                    "managed": True,
                }
                if data.get("source_type") == "skillager-router":
                    target["kind"] = "router"
                    target["router"] = data.get("id") or data.get("source_id")
                    for skill_id in data.get("skill_ids") or []:
                        exposure.setdefault(str(skill_id), []).append(dict(target))
                elif data.get("source_type") == "skillager-stub":
                    target["kind"] = "stub"
                    skill_id = data.get("source_id") or data.get("id")
                    if skill_id:
                        exposure.setdefault(str(skill_id), []).append(target)
                else:
                    target["kind"] = "native"
                    skill_id = data.get("source_id") or data.get("id")
                    if skill_id:
                        exposure.setdefault(str(skill_id), []).append(target)
    return exposure


def _unmanaged_native_target(skill: dict[str, Any], project: Path) -> dict[str, Any] | None:
    root_value = skill.get("root")
    if not root_value:
        return None
    try:
        root = Path(root_value).resolve()
    except OSError:
        return None
    for agent, roots in _project_skill_roots(project).items():
        for base in roots:
            try:
                root.relative_to(base)
            except ValueError:
                continue
            if (root / "SKILL.md").exists():
                return {
                    "agent": agent,
                    "scope": "project",
                    "path": str(root),
                    "status": "existing",
                    "managed": (root / "skillager.materialized.yaml").exists(),
                    "kind": "native",
                }
    return None


def _project_skill_roots(project: Path) -> dict[str, list[Path]]:
    project = project.resolve()
    return {
        "codex": [project / ".agents" / "skills", project / ".agents" / "codex" / "skills", project / ".codex" / "skills"],
        "claude": [project / ".claude" / "skills", project / ".agents" / "claude" / "skills"],
    }


def _find_project_skill(
    state_root: Path,
    skill_id: str,
    *,
    catalog_root: Path | None = None,
    include_collection_inventory: bool = False,
) -> dict[str, Any]:
    catalog_root = catalog_root or state_root
    skills = _effective_project_skills(state_root, catalog_root=catalog_root)
    if include_collection_inventory:
        skills.extend(collection_skills(catalog_root, trust_root=state_root))
    for skill in skills:
        if skill.get("id") == skill_id:
            return skill
    raise KeyError(f"skill not found: {skill_id}")


def _require_attached_tag(state_root: Path, tag: str) -> None:
    if tag not in load_project_tags(state_root).get("attached_tags", []):
        raise ValueError(f"tag is not attached to this project: {tag}")


def _validate_router_activation(state_root: Path, catalog_root: Path, router: str, skill: dict[str, Any]) -> None:
    tag = _tag_from_router(router)
    _require_attached_tag(state_root, tag)
    allowed = {item["id"] for item in tag_skills(catalog_root, tag, trust_root=state_root) if item.get("trust") in {"reviewed", "trusted", "pinned"}}
    if skill["id"] not in allowed:
        raise ValueError(f"skill {skill['id']} is not listed by router {router}")


def _validate_stub_activation(skill: dict[str, Any], stub: str) -> None:
    expected = _slug(stub)
    for target in skill.get("materialized_targets", []):
        if target.get("kind") != "stub":
            continue
        path = target.get("path")
        if path and Path(path).name == expected:
            return
    raise ValueError(f"skill {skill['id']} is not exposed by stub {stub}")


def _activation_agent(args: argparse.Namespace, skill: dict[str, Any]) -> str | None:
    if args.agent:
        return str(args.agent).lower()
    if args.format in {"codex", "claude"}:
        return str(args.format)
    for target in skill.get("materialized_targets", []):
        if args.from_stub and target.get("kind") == "stub":
            return target.get("agent")
        if args.from_router and target.get("kind") == "router":
            return target.get("agent")
    return None


def _tag_from_router(router: str) -> str:
    value = router.strip().lower()
    if value.startswith("skillager-"):
        value = value.removeprefix("skillager-")
    elif value.startswith("skillager/"):
        value = value.removeprefix("skillager/")
    if not value:
        raise ValueError("router must name a skillager router, e.g. skillager-gis")
    return value


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def cmd_trust(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args))
    if skill.get("scan", {}).get("risk") == "high":
        print(f"warning: trusting high-risk skill {args.skill_id}", file=sys.stderr)
    record = set_trust(root(args), args.skill_id, args.state, skill["content_hash"], skill["source"])
    record_skill_event(root(args), "skill_trusted", skill)
    print(f"{args.skill_id}: {record['state']}")
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args))
    record = set_trust(root(args), args.skill_id, "blocked", skill["content_hash"], skill["source"])
    record_skill_event(root(args), "skill_blocked", skill)
    print(f"{args.skill_id}: {record['state']}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    args.yolo = bool(args.yolo or args.trust_all)
    data = load_index(root(args))
    extra_skills = _review_extra_skills(args)
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    skills = selected_skills(
        data.get("skills", []),
        skill_ids=args.skill_ids,
        source=args.source,
        audience=args.audience,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_global=args.include_global,
    )
    summary = review_summary(skills)
    action = apply_review_action(
        root(args),
        skills,
        accept_low=args.accept_low,
        yolo=args.yolo,
        trust_state=args.trust_selected,
        block_high=args.block_high,
        preserve_user_installed=not bool(args.skill_ids),
    )
    if action["changed"]:
        data = load_index(root(args))
        extra_skills = _review_extra_skills(args)
        if extra_skills:
            data["skills"] = [*data.get("skills", []), *extra_skills]
        skills = selected_skills(
            data.get("skills", []),
            skill_ids=args.skill_ids,
            source=args.source,
            audience=args.audience,
            package=args.package,
            activation=args.activation,
            include_blocked=args.include_blocked or args.block_high,
            include_global=args.include_global,
        )
        summary = review_summary(skills)
    if args.json:
        print(json.dumps({"selected": skills, "summary": summary, "action": action}, indent=2, sort_keys=True))
    else:
        _print_review_report(skills, summary, action, compact=args.summary)
    return 0


def _review_extra_skills(args: argparse.Namespace) -> list[dict[str, Any]]:
    return attached_tag_skills(root(args), catalog_root=catalog_root(args))


def cmd_materialize(args: argparse.Namespace) -> int:
    data = load_index(root(args))
    extra_skills = attached_tag_skills(root(args), catalog_root=catalog_root(args))
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    agents = ["codex", "claude"] if args.all_agents else args.agent or ["codex"]
    agent_notes_ready_before = _agent_notes_ready(Path.cwd(), agents=agents) if args.scope == "project" else False
    materialized_targets_before = _materialized_target_paths(Path.cwd(), agents=agents) if args.scope == "project" else set()
    if args.tag and args.mode == "index":
        _require_attached_tag(root(args), args.tag)
        skills = tag_skills(catalog_root(args), args.tag, trust_root=root(args))
        results = materialize_router(
            args.tag,
            skills,
            agents=agents,
            scope=args.scope,
            dry_run=args.dry_run,
            force=args.force,
            project_dir=Path.cwd(),
        )
    else:
        if args.mode == "index":
            raise ValueError("--mode index requires --tag")
        if args.tag:
            _require_attached_tag(root(args), args.tag)
        tag_skill_ids = {skill["id"] for skill in tag_skills(catalog_root(args), args.tag, trust_root=root(args))} if args.tag else None
        skills = selected_skills(
            data.get("skills", []),
            skill_ids=args.skill_ids,
            source=args.source,
            audience=args.audience,
            package=args.package,
            activation=args.activation,
            include_blocked=args.include_blocked,
        )
        if tag_skill_ids is not None:
            skills = [skill for skill in skills if skill["id"] in tag_skill_ids]
        results = materialize_skills(
            skills,
            agents=agents,
            scope=args.scope,
            mode=args.mode,
            dry_run=args.dry_run,
            force=args.force,
            reviewed_only=not args.include_unreviewed,
            project_dir=Path.cwd(),
            allow_incompatible=args.allow_incompatible,
        )
    if not args.dry_run:
        record_materialize_events(root(args), results)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        _print_materialize_results(results)
        if args.scope == "project" and not args.dry_run and any(item.get("status") == "materialized" for item in results):
            if _load_status_scope(root(args)) is None:
                _save_status_scope(
                    root(args),
                    skills,
                    audience=args.audience or _common_audience(skills),
                    include_global=False,
                    agents=_materialized_agents(results),
                )
            if _should_print_agent_next_steps(
                results,
                agent_notes_ready_before=agent_notes_ready_before,
                materialized_targets_before=materialized_targets_before,
            ):
                _print_agent_next_steps(results)
    return 0


def _print_materialize_results(results: list[dict[str, Any]]) -> None:
    for item in results:
        if item.get("skill_id") == "skillager/working" and item.get("status") == "materialized":
            continue
        line = f"{item['skill_id']}: {item['status']}"
        if item.get("target"):
            line += f" {item['target']}"
        if item.get("reason"):
            line += f" ({item['reason']})"
        print(line)


def _agent_notes_ready(project_dir: Path, *, agents: list[str]) -> bool:
    notes = agent_note_paths(project_dir, agents=agents)
    if not notes:
        return False
    for note in notes:
        if not note.exists():
            return False
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            return False
        if "## Skillager" not in text:
            return False
    return True


def _materialized_target_paths(project_dir: Path, *, agents: list[str]) -> set[Path]:
    roots = _project_skill_roots(project_dir)
    paths: set[Path] = set()
    for agent in agents:
        for root_path in roots.get(agent, []):
            if not root_path.is_dir():
                continue
            for sidecar in root_path.glob("*/skillager.materialized.yaml"):
                with contextlib.suppress(OSError):
                    paths.add(sidecar.parent.resolve())
    return paths


def _should_print_agent_next_steps(
    results: list[dict[str, Any]],
    *,
    agent_notes_ready_before: bool = False,
    materialized_targets_before: set[Path] | None = None,
) -> bool:
    changed = [item for item in results if item.get("status") == "materialized" and item.get("skill_id") != "skillager/working"]
    if not changed:
        return False
    if not agent_notes_ready_before:
        return True
    materialized_targets_before = materialized_targets_before or set()
    for item in changed:
        target = item.get("target")
        if not target:
            return True
        try:
            if Path(target).resolve() not in materialized_targets_before:
                return True
        except OSError:
            return True
    return False


def cmd_onboard(args: argparse.Namespace) -> int:
    results = onboard_path(args.path, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for item in results:
            action = "would write" if args.dry_run else "wrote"
            print(f"{item['skill_id']}: {action} {item['manifest']} risk={item['scan']['risk']}")
    return 0


def cmd_session_start(args: argparse.Namespace) -> int:
    meta = start_session(
        root(args),
        agent=args.agent,
        external_session_id=args.external_session_id,
        external_conversation_id=args.external_conversation_id,
    )
    print(meta["session_id"])
    return 0


def cmd_session_end(args: argparse.Namespace) -> int:
    meta = end_session(root(args), agent=args.agent, external_session_id=args.external_session_id)
    print(meta["session_id"])
    return 0


def cmd_session_current(args: argparse.Namespace) -> int:
    meta = current_session(root(args))
    if args.json:
        print(json.dumps(meta, indent=2, sort_keys=True))
    elif meta:
        print(f"{meta['session_id']}\t{meta.get('agent')}\t{meta.get('external_session_id') or ''}")
    else:
        print("no current session")
    return 0


def cmd_session_events(args: argparse.Namespace) -> int:
    session = args.session_id or (current_session(root(args)) or {}).get("session_id")
    if not session:
        raise ValueError("no session id provided and no current session")
    events = read_events(root(args), session)
    if args.json:
        print(json.dumps(events, indent=2, sort_keys=True))
    else:
        for event in events:
            label = event.get("skill_id") or ""
            print(f"{event['timestamp']}\t{event['event']}\t{label}")
    return 0


def cmd_session_redact(args: argparse.Namespace) -> int:
    redact_session(root(args), args.session_id)
    print(f"redacted {args.session_id}")
    return 0


def cmd_session_prune(args: argparse.Namespace) -> int:
    result = prune_sessions(
        root(args),
        days=args.days,
        max_mb=args.max_mb,
        max_events_per_session=args.max_events_per_session,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "Pruned sessions: "
            f"deleted={result['deleted_sessions']} "
            f"trimmed={result['trimmed_sessions']} "
            f"bytes={result['bytes_after']}"
        )
    return 0


def cmd_lookback(args: argparse.Namespace) -> int:
    if args.feedback:
        if not args.skill_id:
            raise ValueError("--skill-id is required with --feedback")
        event = record_feedback(root(args), args.skill_id, args.feedback, note=args.note)
        print(f"recorded {event['event']} for {args.skill_id}")
        return 0
    report = build_lookback(
        root(args),
        session_id=args.session_id,
        agent=args.agent,
        external_session_id=args.external_session_id,
        recent=args.recent,
        include_active=not args.no_active,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_lookback(report), end="")
    return 0


def _matches_filters(skill: dict[str, Any], args: argparse.Namespace) -> bool:
    if skill.get("trust") == "blocked" and not args.include_blocked:
        return False
    if args.source and skill.get("source", {}).get("type") != args.source:
        return False
    if args.trust and skill.get("trust") != args.trust:
        return False
    if args.activation and skill.get("activation") != args.activation:
        return False
    if args.audience and args.audience not in skill.get("audience", []):
        return False
    if args.package and skill.get("package") != args.package:
        return False
    return True


def _format_skill(skill: dict[str, Any]) -> str:
    lines = [
        f"id: {skill['id']}",
        f"name: {skill['name']}",
        f"summary: {skill['summary']}",
        f"source: {skill['source'].get('type')}",
        f"availability: {', '.join(skill.get('availability', [])) or '-'}",
        f"activation: {skill['activation']}",
        f"trust: {skill['trust']}",
        f"trust_reason: {skill.get('trust_reason', '-')}",
        f"exposure: {skill.get('exposure', 'hidden')}",
        f"scan: {skill.get('scan', {}).get('risk')}",
        f"entrypoint: {skill['entrypoint']}",
    ]
    compatibility = skill.get("compatibility") or {}
    if compatibility.get("exclusive_to"):
        lines.append(f"exclusive_to: {compatibility['exclusive_to']}")
    if compatibility.get("incompatible_with"):
        lines.append(f"incompatible_with: {', '.join(compatibility['incompatible_with'])}")
    warnings = compatibility_warnings(skill)
    if warnings:
        lines.append("compatibility_warnings:")
        lines.extend(f"  - {warning}" for warning in warnings)
    return "\n".join(lines)


def _print_review_report(
    skills: list[dict[str, Any]],
    summary: dict[str, Any],
    action: dict[str, Any],
    *,
    compact: bool = False,
) -> None:
    if _use_rich():
        _print_review_report_rich(skills, summary, action, compact=compact)
        return
    print(_style("Review summary", "bold"))
    print(f"  - selected: {_style(str(summary['total']), 'bold')}")
    families = summary.get("families") or {}
    if families:
        print(f"  - families: total={families.get('total', 0)}, variants={families.get('with_variants', 0)}")
    for source, counts in summary.get("by_source", {}).items():
        risk_bits = ", ".join(_risk_count(risk, count) for risk, count in sorted(counts.items()))
        print(f"  - {source}: {risk_bits}")
    if summary.get("by_audience"):
        audience_bits = ", ".join(f"{audience}={_style(str(count), 'bold')}" for audience, count in sorted(summary["by_audience"].items()))
        print(f"  - audience: {audience_bits}")
    if summary.get("by_trust"):
        trust_bits = ", ".join(_trust_count(state, count) for state, count in sorted(summary["by_trust"].items()))
        print(f"  - trust: {trust_bits}")
    if action.get("changed"):
        print(_style("Changed:", "bold"))
        for item in action["changed"]:
            print(f"  - {item['skill_id']}: {_trust_label(item['state'])}")
    if action.get("skipped"):
        print(_style("Skipped:", "bold"))
        for item in action["skipped"]:
            print(f"  - {item['skill_id']}: {item['reason']}")
    if compact:
        _print_needs_review(skills)
        _print_ready_for_approval(skills)
        return
    print(_style("Skills:", "bold"))
    for skill in skills:
        risk = skill.get("scan", {}).get("risk")
        source = skill.get("source", {}).get("type")
        print(f"  - {_style(skill['id'], 'bold')} [{_risk_label(risk)}] {_trust_label(skill['trust'])} {source}/{skill['activation']} - {skill['summary']}")
        print(f"    audience: {_audience_label(skill)}")
        findings = skill.get("scan", {}).get("findings", [])
        for finding in findings[:3]:
            print(f"    {_finding_location(finding)} {_risk_label(finding['severity'])} {finding['code']}: {finding['message']}")
        if len(findings) > 3:
            print(f"    ... {len(findings) - 3} more findings")


def _print_review_report_rich(
    skills: list[dict[str, Any]],
    summary: dict[str, Any],
    action: dict[str, Any],
    *,
    compact: bool = False,
) -> None:
    console = _console()
    table = Table(title="Review summary", box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Bucket", style="bold")
    table.add_column("Value")
    table.add_row("selected", str(summary["total"]))
    families = summary.get("families") or {}
    if families:
        table.add_row("families", f"total={families.get('total', 0)}, variants={families.get('with_variants', 0)}")
    for source, counts in summary.get("by_source", {}).items():
        table.add_row(source, ", ".join(_risk_count(risk, count) for risk, count in sorted(counts.items())))
    if summary.get("by_audience"):
        table.add_row("audience", ", ".join(f"{audience}={count}" for audience, count in sorted(summary["by_audience"].items())))
    if summary.get("by_trust"):
        table.add_row("trust", ", ".join(_trust_count(state, count) for state, count in sorted(summary["by_trust"].items())))
    console.print(table)
    if action.get("changed") or action.get("skipped"):
        lines = []
        for item in action.get("changed", []):
            lines.append(f"[green]{item['skill_id']}[/green]: {item['state']}")
        for item in action.get("skipped", []):
            lines.append(f"[yellow]{item['skill_id']}[/yellow]: skipped ({item['reason']})")
        console.print(Panel("\n".join(lines), title="Review action", border_style="cyan"))
    if compact:
        _print_needs_review(skills)
        _print_ready_for_approval(skills)
        return
    skill_table = Table(title="Selected skills", box=box.SIMPLE, show_header=True, header_style="bold")
    skill_table.add_column("Skill")
    skill_table.add_column("Risk")
    skill_table.add_column("Trust")
    skill_table.add_column("Source")
    skill_table.add_column("Used for", overflow="fold")
    for skill in skills:
        skill_table.add_row(
            skill["id"],
            _risk_label(skill.get("scan", {}).get("risk")),
            _trust_label(skill.get("trust")),
            f"{skill.get('source', {}).get('type')}/{skill.get('activation')}",
            _first_sentence(skill.get("summary") or ""),
        )
    console.print(skill_table)


def _print_needs_review(skills: list[dict[str, Any]]) -> None:
    risky = [
        skill
        for skill in skills
        if skill.get("trust") == "discovered" and skill.get("scan", {}).get("risk") in {"medium", "high"}
    ]
    if not risky:
        return
    if _use_rich():
        _print_needs_review_rich(risky)
        return
    print()
    print(_style("Needs review", "bold"))
    first_group = True
    for risk in ("high", "medium"):
        group = [skill for skill in risky if skill.get("scan", {}).get("risk") == risk]
        if not group:
            continue
        if not first_group:
            print()
        first_group = False
        print(f"{_risk_label(risk)} risk ({len(group)})")
        for index, skill in enumerate(group):
            if index:
                print()
            findings = skill.get("scan", {}).get("findings", [])
            print(f"  - {_style(skill['id'], 'bold')} ({len(findings)} finding(s))")
            print(f"    audience: {_audience_label(skill)}")
            if skill.get("summary"):
                _print_wrapped("    used for: ", skill["summary"], width=_output_width(), max_chars=220)
            for finding in findings[:2]:
                _print_wrapped("    at: ", _finding_location(finding), width=_output_width(), break_long_words=True)
                _print_wrapped("        ", _finding_detail(finding, group_risk=risk), width=_output_width())
            if len(findings) > 2:
                print(f"    ... {len(findings) - 2} more findings")


def _print_needs_review_rich(skills: list[dict[str, Any]]) -> None:
    console = _console()
    for risk in ("high", "medium"):
        group = [skill for skill in skills if skill.get("scan", {}).get("risk") == risk]
        if not group:
            continue
        rows = []
        for skill in group:
            findings = skill.get("scan", {}).get("findings", [])
            detail_lines = [
                f"[bold]{skill['id']}[/bold] ({len(findings)} finding(s))",
                f"audience: {_audience_label(skill)}",
            ]
            if skill.get("summary"):
                detail_lines.append(f"used for: {_truncate(_first_sentence(skill['summary']), 180)}")
            for finding in findings[:2]:
                detail_lines.append(f"{_finding_location(finding)}")
                detail_lines.append(f"  {_finding_detail(finding, group_risk=risk)}")
            if len(findings) > 2:
                detail_lines.append(f"... {len(findings) - 2} more findings")
            rows.append("\n".join(detail_lines))
        border = "red" if risk == "high" else "yellow"
        console.print(Panel("\n\n".join(rows), title=f"{risk.upper()} risk ({len(group)})", border_style=border))


def _print_ready_for_approval(skills: list[dict[str, Any]], *, limit: int = 12) -> None:
    ready = [
        skill
        for skill in skills
        if skill.get("trust") == "discovered" and skill.get("scan", {}).get("risk") == "low"
    ]
    if not ready:
        return
    if _use_rich():
        _print_ready_for_approval_rich(ready, limit=limit)
        return
    print()
    print(_style(f"Ready for approval ({len(ready)} low-risk)", "bold"))
    for index, skill in enumerate(ready[:limit]):
        if index:
            print()
        print(f"  - {_style(skill['id'], 'bold')}")
        print(f"    audience: {_audience_label(skill)}")
        if skill.get("summary"):
            _print_wrapped("    used for: ", _first_sentence(skill["summary"]), width=_output_width(), max_chars=140)
        _print_wrapped("    file: ", skill.get("entrypoint", "<unknown>"), width=_output_width(), break_long_words=True)
    if len(ready) > limit:
        print(f"  ... {len(ready) - limit} more low-risk skill(s); choose option 1 or run skillager setup --details to inspect all.")


def _print_ready_for_approval_rich(skills: list[dict[str, Any]], *, limit: int = 12) -> None:
    console = _console()
    table = Table(title=f"Ready for approval ({len(skills)} low-risk)", box=box.SIMPLE, show_header=True, header_style="bold green")
    table.add_column("Skill")
    table.add_column("Audience")
    table.add_column("Used for", overflow="fold")
    table.add_column("File", overflow="fold")
    for skill in skills[:limit]:
        table.add_row(
            skill["id"],
            _audience_label(skill),
            _truncate(_first_sentence(skill.get("summary") or ""), 140),
            skill.get("entrypoint", "<unknown>"),
        )
    console.print(table)
    if len(skills) > limit:
        console.print(f"[dim]... {len(skills) - limit} more low-risk skill(s); choose option 1 or run skillager setup --details to inspect all.[/dim]")


def _print_setup_next_steps(skills: list[dict[str, Any]]) -> None:
    by_source: dict[str, int] = {}
    by_package: dict[str, int] = {}
    unreviewed = _unreviewed_skills(skills)
    approved = _approved_skills(skills)
    for skill in skills:
        source = skill.get("source", {}).get("type") or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
        package = skill.get("package") or skill.get("source", {}).get("package")
        if package:
            by_package[package] = by_package.get(package, 0) + 1
    print(_style("Suggested next steps", "bold"))
    if approved and not unreviewed:
        print("  - Run interactive setup to install Skillager Working for your agent target: skillager setup")
        print("  - Or materialize a specific approved skill/tag when it is clearly relevant.")
        print("  - Show full list: skillager setup --details")
        return
    print("  - Inspect candidates: skillager review --summary")
    if by_source:
        source = max(by_source, key=by_source.get)
        print(f"  - Narrow by source: skillager review --source {source}")
    if by_package:
        package = max(by_package, key=by_package.get)
        print(f"  - Narrow by package: skillager review --package {package}")
    print("  - Approve one skill: skillager review <skill-id> --trust-selected reviewed")
    print("  - Show full list: skillager setup --details")


def _interactive_setup(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    audience: str | None,
    include_global: bool,
    catalog_root: Path | None = None,
) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print()
        _print_setup_next_steps(skills)
        return
    selected_ids = [skill["id"] for skill in skills]
    if not selected_ids:
        print()
        print("No skills selected.")
        return
    decided_ids: set[str] = set()
    while True:
        selected = _current_selected_skills(state_root, selected_ids, catalog_root=catalog_root)
        candidates = [skill for skill in _unreviewed_skills(selected) if skill["id"] not in decided_ids]
        if not candidates:
            approved = _approved_skills(selected)
            if not approved:
                print()
                print("No approved skills in this setup selection.")
                return
            print()
            results = _materialize_reviewed_for_project(approved, state_root=state_root, catalog_root=catalog_root, prompt_prefix="Review complete. ")
            if results is not None:
                _save_status_scope(state_root, selected, audience=audience, include_global=include_global, agents=_materialized_agents(results))
                print("Setup complete.")
                _print_setup_completion_summary(selected, results, agents=_materialized_agents(results))
                _print_agent_next_steps(results)
            else:
                print("Setup complete; no skills materialized.")
            return
        print()
        print(_style("Choose an action", "bold"))
        print(f"  {_style('1', 'cyan')}. Review unapproved skills one by one")
        print(f"  {_style('2', 'green')}. Approve all low-risk selected skills")
        print(f"  {_style('3', 'red')}. Block all high-risk selected skills")
        print(f"  {_style('4', 'cyan')}. Install Skillager working skill for project scope")
        print(f"  {_style('5', 'dim')}. Exit")
        choice = _interactive_input("> ").strip()
        if choice == "1":
            decided_ids.update(_interactive_review_skills(state_root, candidates))
        elif choice == "2":
            low = [skill for skill in candidates if skill.get("scan", {}).get("risk") == "low"]
            if not low:
                print("No unreviewed low-risk skills remain in this setup selection.")
                continue
            selected_low = _choose_low_risk_audience_group(low)
            if selected_low and _confirm(f"Approve {len(selected_low)} low-risk skill(s) as reviewed?"):
                _print_action_result(apply_review_action(state_root, selected_low, trust_state="reviewed"))
        elif choice == "3":
            high = [skill for skill in candidates if skill.get("scan", {}).get("risk") == "high"]
            if not high:
                print("No unreviewed high-risk skills remain in this setup selection.")
            elif _confirm(f"Block {len(high)} high-risk skill(s)?"):
                _print_action_result(apply_review_action(state_root, high, block_high=True))
        elif choice == "4":
            reviewed = _approved_skills(selected)
            results = _materialize_reviewed_for_project(reviewed, state_root=state_root, catalog_root=catalog_root)
            if results is not None:
                _save_status_scope(state_root, selected, audience=audience, include_global=include_global, agents=_materialized_agents(results))
                print("Setup complete.")
                _print_setup_completion_summary(selected, results, agents=_materialized_agents(results))
                _print_agent_next_steps(results)
                return
        elif choice == "5" or choice.lower() in {"q", "quit", "exit"}:
            return
        else:
            print("Enter 1, 2, 3, 4, or 5.")


def _current_selected_skills(state_root: Path, selected_ids: list[str], *, catalog_root: Path | None = None) -> list[dict[str, Any]]:
    by_id = {skill["id"]: skill for skill in load_index(state_root).get("skills", [])}
    for skill in attached_tag_skills(state_root, catalog_root=catalog_root):
        by_id[skill["id"]] = skill
    return [by_id[skill_id] for skill_id in selected_ids if skill_id in by_id]


def _unreviewed_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill for skill in skills if skill.get("trust") == "discovered"]


def _approved_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill for skill in skills if skill.get("trust") in {"reviewed", "trusted", "pinned"}]


def _choose_low_risk_audience_group(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        audience = skill.get("audience_guess", {}).get("audience") or "unknown"
        groups.setdefault(audience, []).append(skill)
    if len(groups) <= 1:
        return skills
    ordered = sorted(groups)
    print("Low-risk skills span multiple audiences:")
    for audience in ordered:
        print(f"  - {audience}: {len(groups[audience])}")
    answer = _interactive_input("Approve which audience? Enter user/dev/unknown/all, or blank to cancel: ").strip().lower()
    if not answer:
        return []
    if answer == "all":
        return skills
    if answer in groups:
        return groups[answer]
    print(f"Unknown audience choice: {answer}")
    return []


def _interactive_review_skills(state_root: Path, skills: list[dict[str, Any]]) -> set[str]:
    decided: set[str] = set()
    for index, skill in enumerate(skills, start=1):
        risk = skill.get("scan", {}).get("risk")
        source = skill.get("source", {}).get("type")
        package = skill.get("package") or skill.get("source", {}).get("package") or "-"
        print()
        print(_style(f"Review skill {index} of {len(skills)}", "bold"))
        print(f"  {_style(skill['id'], 'bold')} [{_risk_label(risk)}] {source}/{package} {_trust_label(skill['trust'])}")
        print(f"  audience: {_audience_label(skill)}")
        if skill.get("summary"):
            _print_wrapped("  used for: ", skill["summary"], width=_output_width(), max_chars=260)
        _print_wrapped("  file: ", skill.get("entrypoint", "<unknown>"), width=_output_width(), break_long_words=True)
        findings = skill.get("scan", {}).get("findings", [])
        for finding in findings[:3]:
            _print_wrapped("  at: ", _finding_location(finding), width=_output_width(), break_long_words=True)
            _print_wrapped("      ", _finding_detail(finding, group_risk=risk), width=_output_width())
        if len(findings) > 3:
            print(f"  ... {len(findings) - 3} more findings")
        choice = _interactive_input("Approve this skill as reviewed? [y/N/q] ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            return decided
        decided.add(skill["id"])
        if choice in {"y", "yes"}:
            _print_action_result(apply_review_action(state_root, [skill], trust_state="reviewed"))
        else:
            print(f"{skill['id']}: not approved")
    return decided


def _materialize_reviewed_for_project(
    skills: list[dict[str, Any]],
    *,
    state_root: Path,
    catalog_root: Path | None,
    prompt_prefix: str = "",
) -> list[dict[str, Any]] | None:
    if not skills:
        print("No reviewed/trusted/pinned skills are ready for project setup.")
        return None
    agents = _choose_materialize_agents()
    if not agents:
        return None
    target_label = " and ".join(agent.title() for agent in agents)
    if not _confirm(f"{prompt_prefix}Install Skillager working skill for {target_label} project scope?"):
        return None
    results = materialize_working_skill(agents=agents, scope="project", project_dir=Path.cwd())
    for item in results:
        line = f"{item['skill_id']}: {item['status']}"
        if item.get("target"):
            line += f" {item['target']}"
        if item.get("reason"):
            line += f" ({item['reason']})"
        print(line)
    if not any(item.get("status") == "materialized" for item in results):
        return None
    native = _choose_native_project_skills(skills, agents=agents)
    if native:
        native_results = materialize_skills(
            native,
            agents=agents,
            scope="project",
            project_dir=Path.cwd(),
            include_working=False,
        )
        for item in native_results:
            line = f"{item['skill_id']}: {item['status']}"
            if item.get("target"):
                line += f" {item['target']}"
            if item.get("reason"):
                line += f" ({item['reason']})"
            print(line)
        results.extend(native_results)
    _print_router_suggestions(state_root, catalog_root=catalog_root, agents=agents)
    return results


def _print_router_suggestions(state_root: Path, *, catalog_root: Path | None, agents: list[str]) -> None:
    catalog_root = catalog_root or state_root
    attached = load_project_tags(state_root).get("attached_tags", [])
    if not attached:
        return
    suggestions = []
    for tag in attached:
        reviewed = [skill for skill in tag_skills(catalog_root, tag, trust_root=state_root) if skill.get("trust") in {"reviewed", "trusted", "pinned"}]
        if reviewed:
            suggestions.append((tag, len(reviewed)))
    if not suggestions:
        return
    agent = agents[0] if len(agents) == 1 else "codex"
    print()
    print(_style("Router suggestions", "bold"))
    print("  Broad attached tags are best exposed as router skills when relevant to the task:")
    for tag, count in suggestions:
        print(f"  - {tag}: {count} approved skill(s)")
        print(f"    skillager materialize --tag {tag} --mode index --agent {agent} --scope project")


def _print_setup_completion_summary(
    skills: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    agents: list[str],
) -> None:
    approved = [skill for skill in skills if skill.get("trust") in TRUSTED_STATES]
    exposed_ids = {
        item.get("skill_id")
        for item in results
        if item.get("status") in {"materialized", "already_native"} and item.get("skill_id") != "skillager/working"
    }
    hidden = [skill for skill in approved if skill["id"] not in exposed_ids]
    if not approved:
        return
    agent = agents[0] if len(agents) == 1 else "codex"
    print()
    print(_style("Setup summary", "bold"))
    print(f"  - approved skills: {len(approved)}")
    print(f"  - exposed native skills: {len(exposed_ids)}")
    print(f"  - available through Skillager metadata: {len(hidden)}")
    if hidden:
        print()
        print("  Stub candidates")
        print("    These are approved but not loaded as native skills. Stub any that should be easy to invoke by name:")
        for index, skill in enumerate(hidden[:25], start=1):
            summary = _first_sentence(skill.get("summary", ""))
            print(f"    {index}. {skill['id']} [{_risk_label(skill.get('scan', {}).get('risk'))}]")
            if summary:
                _print_wrapped("       ", summary, width=_output_width(), max_chars=110)
        if len(hidden) > 25:
            print(f"    ... {len(hidden) - 25} more approved hidden skill(s)")
        print()
        print("    To stub specific skills:")
        print(f"    skillager materialize <skill-id> --mode stub --agent {agent} --scope project")
        print("    Or ask your agent: please stub 1, 5, 8 from the Skillager setup summary.")


def _choose_native_project_skills(skills: list[dict[str, Any]], *, agents: list[str]) -> list[dict[str, Any]]:
    skills = _native_setup_candidates(skills, agents=agents)
    if not skills:
        print("No narrow native project skill candidates found. Broad collection skills can be exposed later with router mode.")
        return []
    if not _confirm("Materialize a narrow always-relevant set of approved skills now?"):
        return []
    selected: list[dict[str, Any]] = []
    print()
    print(_style("Native skill selection", "bold"))
    print("  Choose skills that should be available in every agent session for this project.")
    print("  Leave task-specific or broad collection skills for Skillager Working to route later.")
    for index, skill in enumerate(skills, start=1):
        print()
        print(f"  {_style(f'Skill {index} of {len(skills)}', 'bold')}")
        print(f"  {_style(skill['id'], 'bold')} [{_risk_label(skill.get('scan', {}).get('risk'))}]")
        variants = skill.get("_family_variants") or []
        if len(variants) > 1:
            print(f"  family: {skill.get('family_key')} ({len(variants)} variants)")
            for variant in variants[:4]:
                marker = "selected" if variant.get("id") == skill.get("id") and variant.get("entrypoint") == skill.get("entrypoint") else "variant"
                print(f"    - {marker}: {variant.get('id')} {variant.get('agent_hint') or variant.get('source_type')} {variant.get('status')}")
            if len(variants) > 4:
                print(f"    ... {len(variants) - 4} more variants")
        if skill.get("cross_agent_source"):
            print("  note: no native variant exists for the selected agent; this is a cross-agent source")
        print(f"  audience: {_audience_label(skill)}")
        if skill.get("summary"):
            _print_wrapped("  used for: ", skill["summary"], width=_output_width(), max_chars=220)
        choice = _interactive_input("  Materialize this native skill? [y/N/q] ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            break
        if choice in {"y", "yes"}:
            selected.append(skill)
    if not selected:
        print("No native project skills selected.")
    return selected


def _native_setup_candidates(skills: list[dict[str, Any]], *, agents: list[str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        if skill.get("source", {}).get("type") == "collection":
            continue
        key = _native_candidate_key(skill)
        groups.setdefault(key, []).append(skill)
    result: list[dict[str, Any]] = []
    for key, group in groups.items():
        target_matches = [skill for skill in group if _skill_matches_agent_target(skill, agents)]
        selectable = target_matches or group
        exact_hashes = {skill.get("content_hash") for skill in group}
        representative = sorted(selectable, key=lambda skill: _native_candidate_sort_key(skill, agents))[0]
        variants = [_family_variant(skill, representative_hash=representative.get("content_hash")) for skill in group]
        item = dict(representative)
        item["family_key"] = key
        item["_family_variants"] = variants
        if not target_matches:
            item["cross_agent_source"] = True
        if len(group) > 1 and len(exact_hashes) > 1:
            item["family_status"] = "variants_differ"
        elif len(group) > 1:
            item["family_status"] = "duplicates_collapsed"
        else:
            item["family_status"] = "single"
        result.append(item)
    return sorted(result, key=lambda skill: _native_candidate_sort_key(skill, agents))


def _family_variant(skill: dict[str, Any], *, representative_hash: str | None) -> dict[str, Any]:
    return {
        "id": skill.get("id"),
        "content_hash": skill.get("content_hash"),
        "source_type": skill.get("source", {}).get("type"),
        "entrypoint": skill.get("entrypoint"),
        "agent_hint": _agent_hint(skill),
        "status": "selected-hash" if skill.get("content_hash") == representative_hash else "differs",
    }


def _native_candidate_sort_key(skill: dict[str, Any], agents: list[str]) -> tuple[int, int, int, str]:
    source_type = skill.get("source", {}).get("type")
    audience = skill.get("audience_guess", {}).get("audience") or "unknown"
    source_rank = {"project": 0, "python-package": 1, "environment": 2, "global": 3}.get(source_type, 4)
    audience_rank = {"user": 0, "unknown": 1, "dev": 2}.get(audience, 3)
    agent_hint = _agent_hint(skill)
    target_rank = 0 if agent_hint in set(agents) or agent_hint is None else 1
    return (source_rank, audience_rank, target_rank, skill["id"])


def _skill_matches_agent_target(skill: dict[str, Any], agents: list[str]) -> bool:
    entrypoint = str(skill.get("entrypoint") or "")
    if agents == ["codex"] and "/.claude/skills/" in entrypoint:
        return False
    if agents == ["claude"] and ("/.agents/skills/" in entrypoint or "/.agents/codex/skills/" in entrypoint or "/.codex/skills/" in entrypoint):
        return False
    return True


def _native_candidate_key(skill: dict[str, Any]) -> str:
    return skill["id"].rsplit("/", 1)[-1].removesuffix("-vibespatial-claude")


def _agent_hint(skill: dict[str, Any]) -> str | None:
    entrypoint = str(skill.get("entrypoint") or "")
    if "/.claude/skills/" in entrypoint or "/.agents/claude/skills/" in entrypoint:
        return "claude"
    if "/.agents/skills/" in entrypoint or "/.agents/codex/skills/" in entrypoint or "/.codex/skills/" in entrypoint:
        return "codex"
    return None


def _choose_materialize_agents() -> list[str]:
    print()
    print(_style("Project skill target", "bold"))
    print(f"  {_style('1', 'cyan')}. Codex")
    print(f"  {_style('2', 'cyan')}. Claude")
    print(f"  {_style('3', 'cyan')}. Both Codex and Claude")
    print(f"  {_style('4', 'dim')}. Cancel")
    choice = _interactive_input("> ").strip().lower()
    if choice in {"1", "codex"}:
        return ["codex"]
    if choice in {"2", "claude"}:
        return ["claude"]
    if choice in {"3", "both", "all"}:
        return ["codex", "claude"]
    return []


def _print_agent_next_steps(results: list[dict[str, Any]]) -> None:
    target_bases = _materialized_target_bases(results)
    project_dir = _materialized_project_dir_from_bases(target_bases)
    agents = sorted({item.get("agent") for item in results if item.get("status") == "materialized" and item.get("agent")})
    print()
    print(_style("Next step", "bold"))
    if len(target_bases) == 1:
        print(f"  - Skills were written to: {target_bases[0]}")
    elif target_bases:
        print("  - Skills were written to:")
        for target_base in target_bases:
            print(f"    - {target_base}")
    if project_dir:
        print(f"  - Restart {_agent_label(agents)} in this directory: {project_dir}")
        notes = agent_note_paths(project_dir, agents=agents)
        if len(notes) == 1:
            print(f"  - Project handoff note: {notes[0]}")
        else:
            print("  - Project handoff notes:")
            for note in notes:
                print(f"    - {note}")
    else:
        print(f"  - Restart {_agent_label(agents)} in the directory where you ran Skillager.")
    print("  - The agent will discover Skillager-managed skills from the project note and native skill directory.")


def _agent_label(agents: list[str]) -> str:
    if agents == ["claude"]:
        return "Claude"
    if agents == ["codex"]:
        return "Codex"
    if agents:
        return "Codex/Claude"
    return "the agent"


def _materialized_agents(results: list[dict[str, Any]]) -> list[str]:
    return sorted({item.get("agent") for item in results if item.get("status") == "materialized" and item.get("agent")})


def _common_audience(skills: list[dict[str, Any]]) -> str | None:
    audiences = {skill.get("audience_guess", {}).get("audience") for skill in skills}
    audiences.discard(None)
    audiences.discard("unknown")
    if len(audiences) == 1:
        return next(iter(audiences))
    return None


def _materialized_target_bases(results: list[dict[str, Any]]) -> list[Path]:
    targets = [Path(item["target"]) for item in results if item.get("target") and item.get("status") == "materialized"]
    if not targets:
        return []
    return sorted({target.parent for target in targets})


def _materialized_project_dir_from_bases(target_bases: list[Path]) -> Path | None:
    project_dirs = {_materialized_project_dir(target_base) for target_base in target_bases}
    project_dirs.discard(None)
    if len(project_dirs) == 1:
        return next(iter(project_dirs))
    return None


def _materialized_project_dir(target_base: Path | None) -> Path | None:
    if not target_base:
        return None
    parts = target_base.parts
    if len(parts) >= 2 and parts[-2:] in ((".codex", "skills"), (".claude", "skills")):
        return target_base.parents[1]
    if len(parts) >= 2 and parts[-2:] == (".agents", "skills"):
        return target_base.parents[1]
    if len(parts) >= 3 and parts[-3:] == (".agents", "codex", "skills"):
        return target_base.parents[2]
    return None


def _confirm(prompt: str) -> bool:
    answer = _interactive_input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _interactive_input(prompt: str) -> str:
    print()
    value = input(prompt)
    print()
    print()
    return value


def _print_action_result(action: dict[str, Any]) -> None:
    for item in action.get("changed", []):
        print(f"{item['skill_id']}: {_trust_label(item['state'])}")
    for item in action.get("skipped", []):
        print(f"{item['skill_id']}: skipped ({item['reason']})")


def _console() -> Console:
    return Console(width=_output_width(), highlight=False)


def _use_rich() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("SKILLAGER_PLAIN") != "1"


def _supports_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _style(text: str, style: str) -> str:
    if not _supports_color():
        return text
    codes = {
        "bold": "1",
        "dim": "2",
        "red": "31",
        "green": "32",
        "yellow": "33",
        "cyan": "36",
    }
    code = codes.get(style)
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def _risk_label(risk: str | None) -> str:
    value = risk or "unknown"
    if value == "high":
        return _style("HIGH", "red")
    if value == "medium":
        return _style("MED", "yellow")
    if value == "low":
        return _style("LOW", "green")
    return value.upper()


def _risk_count(risk: str, count: int) -> str:
    return f"{_risk_label(risk)}={_style(str(count), 'bold')}"


def _trust_label(state: str | None) -> str:
    value = state or "unknown"
    if value in {"reviewed", "trusted", "pinned"}:
        return _style(value, "green")
    if value == "blocked":
        return _style(value, "red")
    if value == "discovered":
        return _style(value, "yellow")
    return value


def _trust_count(state: str, count: int) -> str:
    return f"{_trust_label(state)}={_style(str(count), 'bold')}"


def _finding_location(finding: dict[str, Any]) -> str:
    path = finding.get("path") or "<unknown>"
    line = finding.get("line") or 1
    return _style(f"{path}:{line}", "cyan")


def _finding_detail(finding: dict[str, Any], *, group_risk: str | None = None) -> str:
    severity = finding.get("severity")
    text = f"{finding.get('code', 'finding')}: {finding.get('message', '')}"
    if finding.get("explanation"):
        text += f" - {finding['explanation']}"
    if finding.get("recommendation"):
        text += f" Recommendation: {finding['recommendation']}"
    if severity and severity != group_risk:
        return f"{_risk_label(severity)} {text}"
    return text


def _audience_label(skill: dict[str, Any]) -> str:
    guess = skill.get("audience_guess") or {}
    audience = guess.get("audience") or "unknown"
    confidence = guess.get("confidence") or "low"
    reasons = guess.get("reasons") or []
    label = f"{audience} ({confidence})"
    if reasons:
        label += " - " + "; ".join(str(reason) for reason in reasons[:2])
    return label


def _shorten(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _truncate(text: str, limit: int) -> str:
    return _shorten(text, limit)


def _first_sentence(text: str) -> str:
    clean = " ".join(str(text).split())
    if not clean:
        return clean
    for index, char in enumerate(clean):
        if char in {".", "!", "?"} and _is_sentence_boundary(clean, index):
            return clean[: index + 1]
    return clean


def _is_sentence_boundary(text: str, index: int) -> bool:
    next_index = index + 1
    if next_index >= len(text):
        return True
    return text[next_index].isspace()


def _output_width() -> int:
    raw = os.environ.get("SKILLAGER_WIDTH")
    if raw:
        try:
            return max(60, min(160, int(raw)))
        except ValueError:
            pass
    return max(60, min(100, shutil.get_terminal_size((100, 24)).columns))


def _print_wrapped(
    prefix: str,
    text: str,
    *,
    width: int,
    max_chars: int | None = None,
    break_long_words: bool = False,
) -> None:
    clean = " ".join(str(text).split())
    if max_chars is not None:
        clean = _shorten(clean, max_chars)
    available = max(20, width - len(prefix))
    lines = textwrap.wrap(
        clean,
        width=available,
        break_long_words=break_long_words,
        break_on_hyphens=break_long_words,
    ) or [""]
    print(prefix + lines[0])
    continuation = " " * len(prefix)
    for line in lines[1:]:
        print(continuation + line)


if __name__ == "__main__":
    raise SystemExit(main())
