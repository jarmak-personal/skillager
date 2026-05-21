from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import textwrap
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from .. import project_registry
from .. import project_tags
from ..authored import record_authored_skill
from ..audience import AUDIENCE_OTHER, audience_bucket, audience_bucket_label, declared_audiences
from ..compatibility import compatibility_problem, compatibility_warnings
from ..collections import (
    ack_collection_migrations,
    add_collection,
    apply_collection_trust_migrations,
    collection_migration_summary,
    load_collection_migrations,
    load_collections,
    load_tags,
    refresh_collection,
    remove_collection,
    search_collection,
    select_collection_skills,
)
from ..families import agent_variant_family_key, canonical_agent_variant_slug
from ..index import build_index, find_skill, load_index
from ..materialize import (
    AGENT_NOTE,
    TRUSTED_STATES,
    WORKING_REASON_LOCAL_CUSTOMIZATION,
    WORKING_REASON_UNMANAGED,
    WORKING_SKILL_ID,
    agent_note_paths,
    ensure_agent_notes,
    materialize_skills,
    materialize_working_skill,
)
from ..materialize import materialize_router
from ..materialize import target_dir, working_source_hash
from ..manifest import init_manifests
from ..paths import cache_root, catalog_state_root, find_project_root, legacy_project_state_root, project_state_root, state_root
from ..render import render_skill
from ..review import (
    annotate_duplicate_content,
    apply_review_action,
    duplicate_content_group_entries,
    duplicate_content_summary,
    review_summary,
    setup_environment,
)
from ..scan import scan_path
from ..search import search as search_index
from ..selection import select_visible_skills
from ..simple_yaml import YamlError, load_mapping
from ..trust import content_hash, load_trust, make_lint_override, save_trust, set_trust
from ..update_check import check_for_update


HANDOFF_REASON_AGENT_REQUIRED = "agent_required"
HANDOFF_REASON_WORKING_MISSING = "working_missing"
HANDOFF_REASON_WORKING_STALE = "working_stale"
HANDOFF_REASON_WORKING_UNMANAGED = "working_unmanaged"
HANDOFF_REASON_WORKING_LOCAL_CUSTOMIZATION = "working_local_customization"
HANDOFF_REASON_WORKING_WRONG_SOURCE = "working_wrong_source"
HANDOFF_REASON_WORKING_UNREADABLE_SIDECAR = "working_unreadable_sidecar"
HANDOFF_REASON_WORKING_DRIFT = "working_drift"
HANDOFF_REASON_PROJECT_NOTE_MISSING = "project_note_missing"
HANDOFF_REASON_PROJECT_NOTE_STALE = "project_note_stale"
HANDOFF_REASON_PROJECT_NOTE_UNKNOWN = "project_note_unknown"
HANDOFF_REASON_HANDOFF_ARTIFACTS = "handoff_artifacts"
HANDOFF_REASON_CODES = (
    HANDOFF_REASON_AGENT_REQUIRED,
    HANDOFF_REASON_WORKING_MISSING,
    HANDOFF_REASON_WORKING_STALE,
    HANDOFF_REASON_WORKING_UNMANAGED,
    HANDOFF_REASON_WORKING_LOCAL_CUSTOMIZATION,
    HANDOFF_REASON_WORKING_WRONG_SOURCE,
    HANDOFF_REASON_WORKING_UNREADABLE_SIDECAR,
    HANDOFF_REASON_WORKING_DRIFT,
    HANDOFF_REASON_PROJECT_NOTE_MISSING,
    HANDOFF_REASON_PROJECT_NOTE_STALE,
    HANDOFF_REASON_PROJECT_NOTE_UNKNOWN,
    HANDOFF_REASON_HANDOFF_ARTIFACTS,
)
DOCTOR_EXIT_READY = 0
DOCTOR_EXIT_REVIEW_NEEDED = 10
DOCTOR_EXIT_BOOTSTRAP_REPAIR = 11
DOCTOR_EXIT_LINT_BLOCKED = 12
DOCTOR_EXIT_MIGRATION_NEEDED = 13
DOCTOR_EXIT_MANUAL_REPAIR = 14
SETUP_BOOTSTRAP_REASON_NO_APPROVED = "no_approved_skills"
SETUP_BOOTSTRAP_REASON_DISABLED = "bootstrap_disabled"
SETUP_BOOTSTRAP_REASON_AGENT_NOT_SPECIFIED = "agent_not_specified"
WORKING_RESULT_SCHEMA = "skillager.working.v1"
_WORKING_DRIFT_REASON_CODES = {
    "local customization": HANDOFF_REASON_WORKING_LOCAL_CUSTOMIZATION,
    "target is not Skillager Working": HANDOFF_REASON_WORKING_WRONG_SOURCE,
    "unreadable sidecar": HANDOFF_REASON_WORKING_UNREADABLE_SIDECAR,
}
_PROJECT_NOTE_REASON_CODES = {
    "missing": HANDOFF_REASON_PROJECT_NOTE_MISSING,
    "stale": HANDOFF_REASON_PROJECT_NOTE_STALE,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
              1. skillager working
              2. Continue silently unless the task may benefit from a skill
              3. Inspect available metadata with search/list/show when useful
              4. Tag available skills and expose a narrow router, stub, native skill, or no new exposure
              5. Use skillager handoff only for explicit post-setup curation/onboarding

            Important rules:
              - Do not activate or materialize unavailable skills unless the user explicitly asks.
              - Agents should run `skillager working` after context resets; it is silent on normal success.
              - Agents should ask the user to run `skillager setup` when external skills need owner review.
              - Prefer project scope inside repos so users can inspect and customize local copies.
              - Use --json when another program or agent needs stable machine-readable output.

            Agent command contract:
              working is a pure-read readiness check and does not emit skill bodies.
              handoff/status/search/list/show without --content are safe metadata commands.
              setup/review/trust/block change approval state and need user intent.
              tag/project/materialize may curate or expose only available skills; report changes.
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
    add_doctor_parser(sub)
    add_working_parser(sub)
    add_handoff_parser(sub)
    add_bootstrap_parser(sub)
    add_collection_parser(sub)
    add_tag_parser(sub)
    add_project_parser(sub)
    add_state_parser(sub)
    add_new_parser(sub)

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
        description="List available effective project skills, including attached collection-tag skills.",
        epilog="Examples:\n  skillager list\n  skillager list --summary-json --agent codex\n  skillager list --no-packages --json\n  skillager list --include-global\n  skillager list --source python-package --json\n  skillager list --json --full-json",
    )
    p.add_argument("--source")
    p.add_argument("--activation")
    p.add_argument("--audience")
    p.add_argument("--package")
    p.add_argument("--agent", choices=["codex", "claude"], help="Annotate duplicate native variants with this agent's preference. Does not hide alternatives.")
    p.add_argument("--no-packages", action="store_true", help="Hide installed package skills from the listing.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global native skills. Defaults to local/project/package inventory.")
    p.add_argument("--json", action="store_true", help="Emit listed skills as JSON.")
    p.add_argument("--summary-json", action="store_true", help="Emit compact inventory counts, all listed skill IDs, and duplicate variant hints.")
    p.add_argument("--full-json", action="store_true", help="Emit full indexed metadata, including review diagnostics.")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "search",
        help="Search effective project skill metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Search compact available project skill metadata. Search does not activate or materialize skills.",
        epilog=(
            "Examples:\n"
            "  skillager search dataframe\n"
            "  skillager search pandas --json\n"
            "  skillager search pandas --json --limit 20\n"
            "  skillager search pandas --json --full-json"
        ),
    )
    p.add_argument("query")
    p.add_argument("--tag", help="Search skills in a curated tag.")
    p.add_argument("--include-global", action="store_true", help="Include global native skills. Defaults to project/environment/package and attached collection skills.")
    p.add_argument("--agent", choices=["codex", "claude"], help="Include compatibility warnings for this agent.")
    p.add_argument("--compatible-only", action="store_true", help="Hide skills explicitly marked incompatible with --agent. Skills without metadata are assumed compatible.")
    p.add_argument("--limit", type=int, default=10, help="Maximum search results to return. Use 0 for no limit.")
    p.add_argument("--no-session-record", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="Emit search results as JSON.")
    p.add_argument("--full-json", action="store_true", help="Emit full indexed metadata instead of compact agent-facing search results.")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "show",
        help="Show skill metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Show one available project skill's metadata. Use --content only when the user asks for the full body.",
        epilog="Examples:\n  skillager show fastapi/fastapi\n  skillager show fastapi/fastapi --json\n  skillager show fastapi/fastapi --content",
    )
    p.add_argument("skill_id")
    p.add_argument("--content", action="store_true", help="Show full SKILL.md content for an available skill.")
    p.add_argument("--activate", action="store_true", help="Record this show as an activation event.")
    p.add_argument("--json", action="store_true", help="Emit skill metadata/content as JSON.")
    p.add_argument("--full-json", action="store_true", help="Emit full indexed metadata, including review diagnostics.")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "activate",
        help="Emit full skill content.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Emit full skill content to stdout. Activation requires an available skill unless an explicit owner override is used.",
        epilog="Examples:\n  skillager activate fastapi/fastapi\n  skillager activate fastapi/fastapi --from-stub fastapi-fastapi\n  skillager activate fastapi/fastapi --format codex",
    )
    p.add_argument("skill_id")
    p.add_argument("--format", choices=["markdown", "codex", "claude", "json"], default="markdown")
    p.add_argument("--force", action="store_true", help="Allow activation despite review state. Use only with explicit user approval.")
    p.add_argument("--allow-incompatible", action="store_true", help="Allow activation even when skill metadata explicitly excludes this agent.")
    p.add_argument("--from-router", help="Router skill slug, e.g. skillager-gis. Refuses skills outside the attached router tag.")
    p.add_argument("--from-stub", help="Stub skill slug, e.g. fastapi-fastapi. Refuses activation unless that stub is materialized in this project.")
    p.add_argument("--agent", help="Agent name for compatibility checks, e.g. codex or claude.")
    p.add_argument("--external-session-id", help=argparse.SUPPRESS)
    p.add_argument("--no-session-record", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser(
        "scan",
        help="Scan one path or all indexed skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run the static safety scanner over a file, skill directory, skill ID, or all indexed skills.",
        epilog="Examples:\n  skillager scan fastapi/fastapi\n  skillager scan path/to/SKILL.md\n  skillager scan --all --json",
    )
    p.add_argument("target", nargs="?")
    p.add_argument("--all", action="store_true", help="Scan all indexed skills.")
    p.add_argument("--json", action="store_true", help="Emit scan findings as JSON.")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser(
        "lint",
        help="Show safe manifest lint findings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Report Skillager manifest lint findings without printing skill bodies or raw manifest contents.",
        epilog="Examples:\n  skillager lint\n  skillager lint project/demo\n  skillager lint --json",
    )
    p.add_argument("skill_id", nargs="?")
    p.add_argument("--include-global", action="store_true", help="Include global native skills.")
    p.add_argument("--json", action="store_true", help="Emit lint findings as JSON.")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser(
        "trust",
        help="Trust a skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Mark one skill reviewed/trusted/pinned by recording its current directory hash.",
        epilog="Examples:\n  skillager trust fastapi/fastapi\n  skillager trust fastapi/fastapi --state pinned",
    )
    p.add_argument("skill_id")
    p.add_argument("--state", choices=["reviewed", "trusted", "pinned"], default="reviewed")
    p.add_argument("--project-only", action="store_true", help="Store this approval only in the current project state instead of the reusable global catalog.")
    p.add_argument("--override-lint", action="store_true", help="Approve a lint-blocked skill with an audit reason.")
    p.add_argument("--reason", help="Required reason when --override-lint is used.")
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
    add_manifest_parser(sub)

    return parser


def add_manifest_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "manifest",
        help="Manage structured skillager.yaml metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage structured skillager.yaml metadata. Skill identity and searchable prose stay derived from SKILL.md and path/source provenance.",
    )
    manifest_sub = p.add_subparsers(required=True)
    init = manifest_sub.add_parser(
        "init",
        help="Create minimal skillager.yaml files for existing SKILL.md directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate minimal structured skillager.yaml metadata for existing skill directories.",
        epilog="Examples:\n  skillager manifest init ~/.codex/skills\n  skillager manifest init ~/.claude/skills --dry-run --json",
    )
    init.add_argument("path", type=Path)
    init.add_argument("--dry-run", action="store_true", help="Report sidecar files that would be written without writing them.")
    init.add_argument("--json", action="store_true", help="Emit manifest initialization results as JSON.")
    init.set_defaults(func=cmd_manifest_init)


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
              skillager setup --fresh-project
              skillager setup --source project --accept-low
              skillager setup --source project --accept-low --agent codex
              skillager setup --include-global
              skillager setup --package pandas --trust-selected reviewed
              skillager setup --block-high
              skillager setup --details
              skillager setup --non-interactive
              skillager setup --json
              skillager setup --summary-json

            Next step after trust changes:
              Restart the chosen agent in this project. It should run `skillager working`
              after context resets. Run `skillager handoff` when you want explicit
              post-setup curation/onboarding guidance.
            """
        ),
    )
    p.add_argument("paths", nargs="*", type=Path, help="Optional skill roots or directories to scan instead of default discovery roots.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills in setup review. Defaults to local/environment/package skills only.")
    p.add_argument("--fresh", action="store_true", help="Clear prior project-local trust decisions for the selected setup scope before review. Does not revoke reusable global approvals or delete materialized skill files.")
    p.add_argument(
        "--fresh-project",
        action="store_true",
        help="Reset this project's Skillager state before setup. Clears project trust decisions, tags, legacy session files, and saved setup scope; keeps reusable global approvals, global catalog entries, and materialized skill files.",
    )
    add_review_filters(p)
    add_review_actions(p)
    target = p.add_mutually_exclusive_group()
    target.add_argument("--agent", action="append", choices=["codex", "claude"], help="Bootstrap this agent's first-party project working artifacts after setup. Repeat to target multiple agents.")
    target.add_argument("--all-agents", action="store_true", help="Bootstrap first-party project working artifacts for both Codex and Claude after setup.")
    p.add_argument("--no-bootstrap", action="store_true", help="Review setup scope without writing first-party working artifacts.")
    p.add_argument("--details", action="store_true", help="Print every selected skill. Default output is compact.")
    p.add_argument("--non-interactive", action="store_true", help="Print report only; do not prompt for choices.")
    p.add_argument("--json", action="store_true", help="Emit setup report as JSON.")
    p.add_argument("--summary-json", action="store_true", help="Emit compact setup JSON without per-skill metadata bodies.")
    p.set_defaults(func=cmd_setup)


def add_status_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "status",
        help="Check Skillager availability and working artifact readiness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Check Skillager availability for the current project/environment.
            Compact JSON is available-only for agents. Full JSON is intended for
            explicit owner diagnostics. This command does not activate skills,
            emit skill bodies, approve anything, or materialize anything.

            Agents should prefer `skillager working` after context resets. Use status
            for explicit readiness diagnostics. If review is needed, ask the user to
            run `skillager setup`.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager status
              skillager status --json
              skillager status --agent codex --json
              skillager status --json --full-json
              skillager status --quiet
              skillager status --exit-code
            """
        ),
    )
    p.add_argument("paths", nargs="*", type=Path, help="Optional skill roots or directories to scan instead of default discovery roots.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills. Defaults to local/environment/package skills only.")
    p.add_argument("--include-lint-blocked", action="store_true", help="Include lint-blocked skills in diagnostic counts.")
    p.add_argument("--all", action="store_true", help="Ignore the saved setup scope and report all selected skills.")
    p.add_argument("--agent", choices=["codex", "claude"], help="Check first-party working artifact readiness for this agent.")
    p.add_argument("--quiet", action="store_true", help="Print one concise line.")
    p.add_argument("--exit-code", action="store_true", help="Exit 10 when review is needed, or 11 when approved skills exist but working artifacts need repair.")
    p.add_argument("--ack-migration", action="store_true", help="Acknowledge the current collection ID migration report.")
    p.add_argument("--migration-details", action="store_true", help="Print collection ID migration details for review before acking.")
    p.add_argument("--json", action="store_true", help="Emit status as JSON.")
    p.add_argument("--full-json", action="store_true", help="Include verbose scope baseline and review diagnostic details in JSON output.")
    p.set_defaults(func=cmd_status)


def add_doctor_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "doctor",
        help="Diagnose Skillager readiness and print exact next commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Diagnose the current project readiness without approving skills,
            activating skills, or exposing third-party content. Doctor rebuilds
            metadata, reports review and working artifact readiness, and chooses one exact
            next action. With --fix, doctor may repair first-party bootstrap
            artifacts only.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager doctor --agent codex
              skillager doctor --agent claude --json
              skillager doctor --agent codex --fix
            """
        ),
    )
    p.add_argument("--agent", choices=["codex", "claude"], help="Agent target for working artifact readiness and bootstrap repairs.")
    p.add_argument("--fix", action="store_true", help="Repair first-party bootstrap artifacts when that is the selected next action. Requires --agent to write.")
    p.add_argument("--no-packages", action="store_true", help="Skip installed package skill discovery.")
    p.add_argument("--include-global", action="store_true", help="Include already-installed global skills in diagnostics.")
    p.add_argument("--no-session-record", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="Emit doctor results as JSON.")
    p.set_defaults(func=cmd_doctor)


def add_working_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "working",
        help="Quietly check Skillager readiness for an active agent session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Read Skillager's local inventory for a resumed agent session.
            External package, collection, environment, and global skills still
            require review before body access, activation, or materialization.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager working
              skillager working --agent codex
              skillager working --json
            """
        ),
    )
    p.add_argument("--agent", choices=["codex", "claude"], help="Agent target for compact readiness metadata.")
    p.add_argument("--json", action="store_true", help="Emit compact readiness results as JSON.")
    p.set_defaults(func=cmd_working)


def add_handoff_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "handoff",
        help="Read current Skillager state and print an agent handoff brief.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Report the current Skillager state for explicit post-setup curation or
            onboarding without approving skills, activating skills, or fixing
            materialized files. Handoff lists relevant conditions and chooses one
            highest-priority next action. It is read-only except for completing pending
            collection trust migrations from earlier collection refreshes.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager handoff
              skillager handoff --agent codex
              skillager handoff --json
            """
        ),
    )
    p.add_argument("--agent", choices=["codex", "claude"], help="Agent target. Defaults to the detected agent or codex.")
    p.add_argument("--json", action="store_true", help="Emit handoff data as JSON.")
    p.set_defaults(func=cmd_handoff)


def add_bootstrap_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "bootstrap",
        help="Install or refresh Skillager's first-party project working artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Install or refresh Skillager Working and the project working note for
            a selected agent. Bootstrap is first-party plumbing only: it does not
            approve, activate, or expose third-party skills.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager bootstrap --agent codex
              skillager bootstrap --agent claude
              skillager bootstrap --all-agents
              skillager bootstrap --agent codex --dry-run --json
            """
        ),
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--agent", action="append", choices=["codex", "claude"], help="Agent target. Repeat to target multiple agents.")
    target.add_argument("--all-agents", action="store_true", help="Target both codex and claude.")
    p.add_argument("--dry-run", action="store_true", help="Report first-party artifacts that would be written without writing files.")
    p.add_argument("--force", action="store_true", help="Overwrite managed local customizations or unmanaged Skillager Working targets.")
    p.add_argument("--json", action="store_true", help="Emit bootstrap results as JSON.")
    p.set_defaults(func=cmd_bootstrap)


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
    search.add_argument("--include-lint-blocked", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_collection_search)
    show = collection_sub.add_parser("show")
    show.add_argument("skill_id")
    show.add_argument("--include-lint-blocked", action="store_true")
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
        description="Tags are project-local curated sets of available collection or project-inventory skill IDs.",
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager tag create gis
              skillager tag add gis community/gis-domain community/topology
              skillager tag add gis vibespatial/gis-domain
              skillager tag add community --from-collection community
              skillager tag add community --from-collection community --sync
              skillager tag show gis
              skillager tag remove gis community/gis-domain
              skillager tag delete gis
              skillager tag sync --from ../other-project --to .
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
    delete = tag_sub.add_parser("delete")
    delete.add_argument("tag")
    delete.set_defaults(func=cmd_tag_delete)
    sync = tag_sub.add_parser("sync")
    sync.add_argument("--from", dest="from_project", required=True, type=Path, help="Source project directory to copy tags from.")
    target = sync.add_mutually_exclusive_group()
    target.add_argument("--to", dest="to_project", type=Path, help="Destination project directory. Defaults to the current project.")
    target.add_argument("--to-all", action="store_true", help="Copy to every known project in the registry.")
    sync.add_argument("--tag", help="Sync only one tag.")
    sync.add_argument("--replace", action="store_true", help="Replace destination tag contents instead of merging.")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=cmd_tag_sync)
    list_cmd = tag_sub.add_parser("list")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_tag_list)
    show = tag_sub.add_parser("show")
    show.add_argument("tag")
    show.add_argument("--include-lint-blocked", action="store_true")
    show.add_argument("--json", action="store_true")
    show.add_argument("--full-json", action="store_true", help="Emit full indexed metadata, including review diagnostics.")
    show.set_defaults(func=cmd_tag_show)


def add_project_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "project",
        help="Manage project Skillager settings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Inspect project-local tags. attach-tag/detach-tag are legacy compatibility wrappers for local tag existence/deletion.",
        epilog=textwrap.dedent(
            """\
            Examples:
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


def add_state_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "state",
        help="Manage Skillager local state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage user-local Skillager state. Ordinary commands ignore legacy in-tree .skillager directories.",
    )
    state_sub = p.add_subparsers(required=True)
    migrate = state_sub.add_parser("migrate", help="Import legacy project-local .skillager state after review.")
    migrate.set_defaults(func=cmd_state_migrate)
    import_global = state_sub.add_parser("import-global-approvals", help="Import legacy in-tree reusable approvals after explicit review.")
    import_global.set_defaults(func=cmd_state_import_global_approvals)
    migrate_tags = state_sub.add_parser("migrate-tags", help="Copy legacy global tag attachments into project-local tag files.")
    migrate_tags.add_argument("--to", choices=["projects"], required=True)
    migrate_tags.add_argument("--json", action="store_true")
    migrate_tags.set_defaults(func=cmd_state_migrate_tags)


def add_new_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "new",
        help="Create a new authored native skill in this project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Scaffold a project-local native skill and record authored metadata for fast review UX. This does not approve the skill.",
        epilog="Examples:\n  skillager new gis-workflow\n  skillager new project/gis-workflow --agent claude",
    )
    p.add_argument("skill_id", help="Skill id or slug. The final path component becomes the native skill directory name.")
    p.add_argument("--agent", choices=["codex", "claude"], default="codex", help="Native agent directory to create. Defaults to codex.")
    p.set_defaults(func=cmd_new)


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
              skillager review fastapi/fastapi --trust-selected reviewed
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
        help="Copy available skills into agent-native skill directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Copy available skill directories into Codex or Claude native
            skill directories. This is how Skillager presents skills to agents.

            Native materialization copies the full skill directory and writes
            provenance. Stub materialization writes a tiny native handle that
            tells the agent how to activate the full available body through
            Skillager on demand. Materialization does not install or repair
            Skillager Working or project working notes; use `skillager bootstrap`
            or `skillager doctor --fix` for first-party working artifacts.
            Customized local copies are not overwritten unless --force is used.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Examples:
              skillager materialize --all-reviewed --agent codex --scope project
              skillager materialize --all-reviewed --agent claude --scope project
              skillager materialize --all-reviewed --all-agents --scope project
              skillager materialize fastapi/fastapi --agent codex
              skillager materialize --tag gis --mode router --agent codex
              skillager materialize fastapi/fastapi --mode stub --agent codex
              skillager materialize --dry-run --json
            """
        ),
    )
    p.add_argument("skill_ids", nargs="*")
    p.add_argument("--tag", help="Materialize skills from a curated tag.")
    p.add_argument(
        "--mode",
        choices=["native", "router", "stub"],
        default="native",
        help="native copies each skill; stub writes tiny activation handles; router creates one router skill for --tag.",
    )
    p.add_argument("--agent", action="append", choices=["codex", "claude"], help="Agent target. Repeat to target multiple agents. Defaults to codex.")
    p.add_argument("--all-agents", action="store_true", help="Target both codex and claude.")
    p.add_argument("--scope", choices=["project", "global"], default="project", help="Materialize into project .agents or global agent skill directory.")
    p.add_argument("--include-unreviewed", action="store_true", help="Allow discovered skills to be materialized.")
    p.add_argument("--all-reviewed", action="store_true", help="Materialize every available skill selected by filters.")
    p.add_argument("--allow-incompatible", action="store_true", help="Allow native/stub materialization even when skill metadata explicitly excludes the selected agent.")
    p.add_argument("--dry-run", action="store_true", help="Report target paths without writing files.")
    p.add_argument("--force", action="store_true", help="Overwrite existing Skillager-managed customized targets.")
    add_review_filters(p, include_lint_flag=False)
    p.add_argument("--json", action="store_true", help="Emit materialization results as JSON.")
    p.set_defaults(func=cmd_materialize)


def add_review_filters(parser: argparse.ArgumentParser, *, include_lint_flag: bool = True) -> None:
    parser.add_argument("--source", help="Filter by source type, e.g. project, global, environment, python-package.")
    parser.add_argument("--audience", help="Filter by declared audience: user, dev, or other/everything_else for undeclared skills.")
    parser.add_argument("--package", help="Filter by package name.")
    parser.add_argument("--activation", help="Filter by activation mode.")
    parser.add_argument("--include-blocked", action="store_true", help="Include blocked skills in the selection.")
    if include_lint_flag:
        parser.add_argument("--include-lint-blocked", action="store_true", help="Include lint-blocked skills in read-only review output.")


def add_review_actions(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--accept-low", action="store_true", help="Mark selected low-risk skills as reviewed.")
    parser.add_argument("--yolo", action="store_true", help="Mark all selected skills reviewed, including high-risk and lint-blocked findings. Same behavior as --trust-all; use only for fully trusted sources.")
    parser.add_argument("--trust-all", action="store_true", help="Mark all selected skills reviewed, including high-risk and lint-blocked findings. Use only for fully trusted sources.")
    parser.add_argument("--trust-selected", choices=["reviewed", "trusted", "pinned"], help="Trust selected skills after review.")
    parser.add_argument("--project-only", action="store_true", help="Store approval decisions only in this project state instead of the reusable global catalog.")
    parser.add_argument("--block-high", action="store_true", help="Block selected high-risk skills.")
    parser.add_argument("--override-lint", action="store_true", help="Approve selected lint-blocked skills with an audit reason, or allow them with another review action.")
    parser.add_argument("--reason", help="Required reason when --override-lint is used.")


def root(args: argparse.Namespace) -> Path:
    cached = getattr(args, "_skillager_state_root", None)
    if cached:
        return cached
    if args.state_dir:
        resolved = args.state_dir.resolve()
    else:
        resolved = state_root()
        if os.environ.get("SKILLAGER_STATE_DIR") is None and getattr(args, "func", None) not in {cmd_state_migrate, cmd_state_import_global_approvals, cmd_state_migrate_tags}:
            _warn_legacy_project_state(resolved)
    setattr(args, "_skillager_state_root", resolved)
    return resolved


def _current_project_dir() -> Path:
    return (find_project_root() or Path.cwd()).resolve()


def catalog_root(args: argparse.Namespace) -> Path:
    if getattr(args, "catalog_state_dir", None):
        return args.catalog_state_dir.resolve()
    stored = project_tags.load_tags(_current_project_dir()).get("catalog_state_dir")
    if stored:
        return Path(stored).expanduser().resolve()
    return catalog_state_root()


def _warn_legacy_project_state(new_state_root: Path) -> None:
    legacy = legacy_project_state_root()
    if not legacy or not legacy.exists():
        return
    try:
        legacy_entries = [entry for entry in legacy.iterdir() if entry.name != "tags.json"]
    except OSError:
        legacy_entries = [legacy]
    if not legacy_entries:
        return
    print(
        f"skillager: ignoring legacy in-tree state at {legacy}; using {new_state_root}. "
        "Run `skillager state migrate` to import reviewed project-local state.",
        file=sys.stderr,
    )


def cmd_collection_add(args: argparse.Namespace) -> int:
    result = add_collection(catalog_root(args), args.name, args.path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['collection']['name']}: indexed {result['indexed']} skill(s)")
        if result.get("errors"):
            print(f"errors: {len(result['errors'])}")
        print("No skills were exposed to agents. Review collection skills, then add available skills to project-local tags when useful.")
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
    apply_collection_trust_migrations(root(args), catalog_root(args))
    project_tag_migrations = _migrate_project_tags_for_collection_refresh(_current_project_dir(), catalog_root(args), data["name"])
    if project_tag_migrations:
        data["project_tag_migrations"] = project_tag_migrations
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"{data['name']}: indexed {len(data.get('skills', []))} skill(s)")
        if data.get("errors"):
            print(f"errors: {len(data['errors'])}")
    return 0


def cmd_collection_enable(args: argparse.Namespace) -> int:
    data = refresh_collection(catalog_root(args), args.name)
    apply_collection_trust_migrations(root(args), catalog_root(args))
    project_tag_migrations = _migrate_project_tags_for_collection_refresh(_current_project_dir(), catalog_root(args), data["name"])
    tag = args.tag or data["name"]
    skill_ids = [
        skill["id"]
        for skill in select_collection_skills(catalog_root(args), data["name"], trust_root=root(args), approval_root=catalog_root(args))
        if skill.get("trust") in TRUSTED_STATES
    ]
    tag_data = project_tags.set_tag_skills(
        _current_project_dir(),
        tag,
        skill_ids,
        sync=args.sync,
        source_collection=data["name"],
        catalog_state_dir=catalog_root(args),
    )
    result = {
        "collection": data["name"],
        "tag": tag_data["tag"],
        "skills": len(tag_data["skills"]),
        "attached_tags": _project_tag_names(_current_project_dir()),
        "errors": data.get("errors", []),
        "project_tag_migrations": project_tag_migrations,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{data['name']}: enabled {len(tag_data['skills'])} available skill(s) as project tag {tag_data['tag']}")
        if data.get("errors"):
            print(f"errors: {len(data['errors'])}")
        if not tag_data["skills"]:
            print("Next: run `skillager setup --source collection` from a user shell to review collection skills before tagging.")
    return 0


def cmd_collection_search(args: argparse.Namespace) -> int:
    results = search_collection(
        catalog_root(args),
        args.name,
        args.query,
        include_blocked=args.include_blocked,
        include_lint_blocked=args.include_lint_blocked,
        trust_root=root(args),
        approval_root=catalog_root(args),
    )
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for skill in results:
            print(f"{skill['score']}\t{skill['id']}\t{skill['trust']}\t{skill.get('summary', '-')}")
    return 0


def cmd_collection_show(args: argparse.Namespace) -> int:
    skills = select_collection_skills(
        catalog_root(args),
        trust_root=root(args),
        approval_root=catalog_root(args),
        include_lint_blocked=args.include_lint_blocked,
    )
    skill = next((item for item in skills if item.get("id") == args.skill_id), None)
    if skill is None:
        raise KeyError(f"collection skill not found: {args.skill_id}")
    if args.json:
        print(json.dumps(skill, indent=2, sort_keys=True))
    else:
        print(f"{skill['id']}")
        print(f"  name: {skill.get('name', '-')}")
        print(f"  trust: {skill['trust']}")
        print(f"  risk: {skill.get('scan', {}).get('risk')}")
        print(f"  file: {skill.get('entrypoint')}")
        if skill.get("summary"):
            _print_wrapped("  used for: ", skill["summary"], width=_output_width(), max_chars=260)
    return 0


def cmd_collection_remove(args: argparse.Namespace) -> int:
    removed = remove_collection(catalog_root(args), args.name)
    print(f"{args.name}: {'removed' if removed else 'not found'}")
    return 0


def _migrate_project_tags_for_collection_refresh(project_dir: Path, catalog_root: Path, collection: str) -> dict[str, Any] | None:
    outcome = (load_collection_migrations(catalog_root).get("collections") or {}).get(collection) or {}
    migrations_by_old: dict[str, list[str]] = defaultdict(list)
    for item in outcome.get("id_migrations", []):
        old_id = item.get("old_id")
        new_id = item.get("new_id")
        if old_id and new_id:
            migrations_by_old[old_id].append(new_id)
    id_map = {old_id: new_ids[0] for old_id, new_ids in migrations_by_old.items() if len(set(new_ids)) == 1}
    if not id_map:
        return None
    data = project_tags.load_tags(project_dir)
    changed = []
    for tag, entry in (data.get("tags") or {}).items():
        current = list(entry.get("skills") or [])
        next_ids = [id_map.get(skill_id, skill_id) for skill_id in current]
        deduped = sorted(dict.fromkeys(next_ids))
        if deduped == current:
            continue
        entry["skills"] = deduped
        changed.append({"tag": tag, "from": current, "to": deduped})
    if not changed:
        return None
    project_tags.save_tags(project_dir, data)
    return {"updated_tags": changed}


def cmd_tag_create(args: argparse.Namespace) -> int:
    tag = project_tags.create_tag(_current_project_dir(), args.tag, catalog_state_dir=catalog_root(args))
    print(f"{tag['tag']}: created")
    return 0


def cmd_tag_add(args: argparse.Namespace) -> int:
    skill_ids = list(args.skill_ids)
    source_collection = None
    if args.from_collection:
        source_collection = args.from_collection
        skill_ids.extend(
            skill["id"]
            for skill in select_collection_skills(catalog_root(args), args.from_collection, trust_root=root(args), approval_root=catalog_root(args))
        )
    if args.all:
        skill_ids.extend(skill["id"] for skill in select_collection_skills(catalog_root(args), trust_root=root(args), approval_root=catalog_root(args)))
    skill_ids = _validate_taggable_skill_ids(root(args), catalog_root(args), _current_project_dir(), skill_ids)
    if not skill_ids:
        raise ValueError("provide at least one available skill id")
    if args.sync or args.from_collection or args.all:
        updated_tag = project_tags.set_tag_skills(
            _current_project_dir(),
            args.tag,
            skill_ids,
            sync=args.sync,
            source_collection=source_collection,
            catalog_state_dir=catalog_root(args),
        )
        print(f"{updated_tag['tag']}: {len(updated_tag['skills'])} skill(s)")
        return 0
    tag = project_tags.add_tag_skills(_current_project_dir(), args.tag, skill_ids, catalog_state_dir=catalog_root(args))
    print(f"{tag['tag']}: {len(tag['skills'])} skill(s)")
    return 0


def cmd_tag_remove(args: argparse.Namespace) -> int:
    tag = project_tags.remove_tag_skills(_current_project_dir(), args.tag, args.skill_ids)
    print(f"{tag['tag']}: {len(tag['skills'])} skill(s)")
    return 0


def cmd_tag_delete(args: argparse.Namespace) -> int:
    data = project_tags.delete_tag(_current_project_dir(), args.tag)
    print(f"{data['tag']}: {'deleted' if data['removed'] else 'not found'}")
    return 0


def cmd_tag_sync(args: argparse.Namespace) -> int:
    source = args.from_project.expanduser().resolve()
    if not source.exists():
        raise ValueError(f"source project does not exist: {source}")
    if args.to_all:
        destinations = [project for project in project_registry.known_projects(catalog_root(args)) if project != source]
    else:
        destinations = [(args.to_project or _current_project_dir()).expanduser().resolve()]
    if not destinations:
        raise ValueError("no destination projects found")
    source_tags = project_tags.load_tags(source)
    selected = _select_sync_tags(source_tags, args.tag)
    results = []
    for destination in destinations:
        if destination == source:
            continue
        for tag, entry in selected.items():
            updated = project_tags.set_tag_skills(
                destination,
                tag,
                list(entry.get("skills") or []),
                sync=args.replace,
                catalog_state_dir=catalog_root(args),
            )
            results.append({"project": str(destination), "tag": updated["tag"], "skills": len(updated["skills"])})
    payload = {"schema": "skillager.tag-sync.v1", "source": str(source), "results": results}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in results:
            print(f"{item['project']}: {item['tag']} {item['skills']} skill(s)")
    return 0


def _select_sync_tags(data: dict[str, Any], tag: str | None) -> dict[str, dict[str, Any]]:
    tags = data.get("tags") or {}
    if tag is None:
        return dict(tags)
    tag_key = project_tags.normalize_tag(tag)
    if tag_key not in tags:
        raise KeyError(f"tag not found in source project: {tag_key}")
    return {tag_key: dict(tags[tag_key])}


def cmd_tag_list(args: argparse.Namespace) -> int:
    data = project_tags.load_tags(_current_project_dir())
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for tag, entry in sorted(data.get("tags", {}).items()):
            print(f"{tag}\t{len(entry.get('skills') or [])} skill(s)")
    return 0


def cmd_tag_show(args: argparse.Namespace) -> int:
    all_skills = _select_project_tag_skills(
        root(args),
        catalog_root(args),
        args.tag,
        include_lint_blocked=args.include_lint_blocked,
    )
    skills = _available_skills(all_skills)
    full_summary = _tag_trust_summary(args.tag, _select_project_tag_skills(root(args), catalog_root(args), args.tag, include_blocked=True, include_lint_blocked=True))
    summary = full_summary if args.full_json else _tag_available_summary(full_summary)
    references = _project_tag_reference_report(root(args), catalog_root(args), args.tag)
    if args.json:
        visible_skills = skills if args.full_json else [_compact_skill_metadata(skill) for skill in skills]
        print(json.dumps({"tag": project_tags.normalize_tag(args.tag), "summary": summary, "skills": visible_skills, "references": references}, indent=2, sort_keys=True))
    else:
        print(_tag_available_summary_line(summary))
        _print_tag_owner_review_note(full_summary)
        for skill in skills:
            print(f"{skill['id']}\t{skill['summary']}")
        for ref in references:
            if ref.get("note"):
                print(f"! {ref['id']}: {ref['note']}")
    return 0


def cmd_project_attach_tag(args: argparse.Namespace) -> int:
    project_dir = _current_project_dir()
    tag_key = project_tags.normalize_tag(args.tag)
    if tag_key not in project_tags.load_tags(project_dir).get("tags", {}):
        legacy_skill_ids = load_tags(catalog_root(args)).get("tags", {}).get(tag_key)
        if legacy_skill_ids is None:
            raise KeyError(f"tag not found: {tag_key}")
        project_tags.set_tag_skills(project_dir, tag_key, list(legacy_skill_ids), sync=True, catalog_state_dir=catalog_root(args))
    data = project_tags.load_tags(project_dir)
    print(f"{tag_key}: attached")
    print(f"attached tags: {', '.join(sorted(data.get('tags', {}))) or '-'}")
    _print_tag_owner_review_note(_tag_trust_summary(args.tag, _select_project_tag_skills(root(args), catalog_root(args), args.tag, include_lint_blocked=True)))
    return 0


def cmd_project_detach_tag(args: argparse.Namespace) -> int:
    data = project_tags.delete_tag(_current_project_dir(), args.tag)
    print(f"{data['tag']}: detached")
    print(f"attached tags: {', '.join(data.get('tags', [])) or '-'}")
    return 0


def cmd_project_tags(args: argparse.Namespace) -> int:
    data = project_tags.load_tags(_current_project_dir())
    tag_names = sorted(data.get("tags", {}))
    summaries = _tag_trust_summaries(root(args), catalog_root(args), tag_names)
    if args.json:
        payload = dict(data)
        payload["attached_tags"] = tag_names
        payload["tag_summaries"] = [_tag_available_summary(summary) for summary in summaries]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for summary in summaries:
            print(_tag_summary_line(summary))
            _print_tag_review_warning(summary, indent="  ")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    project = (find_project_root() or Path.cwd()).resolve()
    slug = _slug(args.skill_id.rsplit("/", 1)[-1])
    if not slug:
        raise ValueError("skill id must contain at least one alphanumeric character")
    base = project / (".claude/skills" if args.agent == "claude" else ".agents/skills")
    skill_root = base / slug
    if skill_root.exists():
        raise ValueError(f"skill already exists: {skill_root}")
    skill_root.mkdir(parents=True)
    title = _title_from_slug(slug)
    skill_file = skill_root / "SKILL.md"
    skill_file.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                "Use this skill when the task clearly matches this workflow.",
                "",
                "## Instructions",
                "",
                "- Replace this placeholder with the workflow, constraints, and examples.",
                "- Keep activation guidance specific enough that agents know when not to use it.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    record_authored_skill(skill_root, project_root=project, agent=args.agent)
    print(f"Created {skill_root}")
    print(f"Edit {skill_file}, then review before use.")
    print(f"Fast approval after review: skillager trust project/{slug} --state reviewed")
    return 0


def cmd_state_migrate(args: argparse.Namespace) -> int:
    project = find_project_root() or Path.cwd()
    legacy = project / ".skillager"
    if not legacy.exists():
        raise ValueError(f"legacy in-tree state not found: {legacy}")
    _refuse_unsafe_migration_project(project)
    destination = state_root(project)
    trust = load_trust(legacy)
    project_trust = {"skills": trust.get("skills", {})}
    global_approvals = trust.get("global_approvals") or {}
    print(f"Legacy state: {legacy}")
    print(f"New state: {destination}")
    _print_trust_records(project_trust.get("skills", {}), title="Project-local trust records to import")
    if global_approvals:
        print(f"Ignoring {len(global_approvals)} reusable global approval(s); use `skillager state import-global-approvals` after separate review.")
    _require_interactive_confirmation("Import this legacy project-local state? [y/N] ")
    _copy_legacy_project_state(legacy, destination, project_trust=project_trust)
    print(f"Imported legacy project-local state to {destination}")
    return 0


def cmd_state_import_global_approvals(args: argparse.Namespace) -> int:
    project = find_project_root() or Path.cwd()
    legacy = project / ".skillager"
    if not legacy.exists():
        raise ValueError(f"legacy in-tree state not found: {legacy}")
    _refuse_unsafe_migration_project(project)
    approvals = load_trust(legacy).get("global_approvals") or {}
    if not approvals:
        print("No legacy reusable global approvals found.")
        return 0
    print(f"Legacy state: {legacy}")
    print(f"Catalog state: {catalog_root(args)}")
    _print_trust_records(approvals, title="Reusable global approvals to import")
    _require_interactive_confirmation("Import these reusable global approvals into the user catalog? [y/N] ")
    data = load_trust(catalog_root(args))
    target = data.setdefault("global_approvals", {})
    for key, record in approvals.items():
        target[key] = record
    save_trust(catalog_root(args), data)
    print(f"Imported {len(approvals)} reusable global approval(s).")
    return 0


def cmd_state_migrate_tags(args: argparse.Namespace) -> int:
    if args.to != "projects":
        raise ValueError("--to must be projects")
    global_tags = load_tags(catalog_root(args)).get("tags") or {}
    if not global_tags:
        payload = {"schema": "skillager.tag-migration.v1", "projects": [], "migrated": 0}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("No legacy global tags found.")
        return 0
    projects = _migration_projects(catalog_root(args), _current_project_dir())
    results = []
    migrated = 0
    for project in projects:
        attached = _legacy_attached_tags_for_project(project)
        if not attached:
            continue
        project_results = []
        for tag in attached:
            skill_ids = global_tags.get(tag)
            if not skill_ids:
                continue
            updated = project_tags.set_tag_skills(project, tag, list(skill_ids), sync=True, catalog_state_dir=catalog_root(args))
            migrated += 1
            project_results.append({"tag": tag, "skills": len(updated["skills"])})
        if project_results:
            results.append({"project": str(project), "tags": project_results})
    payload = {"schema": "skillager.tag-migration.v1", "projects": results, "migrated": migrated}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for project in results:
            tags = ", ".join(f"{item['tag']}={item['skills']}" for item in project["tags"])
            print(f"{project['project']}: {tags}")
        if not results:
            print("No legacy project tag attachments found.")
    return 0


def _migration_projects(catalog_root: Path, current_project: Path) -> list[Path]:
    projects = [current_project.expanduser().resolve()]
    projects.extend(project_registry.known_projects(catalog_root))
    return sorted(dict.fromkeys(projects))


def _legacy_attached_tags_for_project(project: Path) -> list[str]:
    state_path = project_state_root(project) / "project_tags.json"
    if not state_path.exists() and project.resolve() == _current_project_dir():
        state_path = state_root(project) / "project_tags.json"
    if not state_path.exists():
        return []
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(str(tag) for tag in data.get("attached_tags") or [])


def _copy_legacy_project_state(legacy: Path, destination: Path, *, project_trust: dict[str, Any]) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"destination state already exists and is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    save_trust(destination, project_trust)
    for name in ("index.json", "status_scope.json", "native_inventory.json", "project_tags.json"):
        _copy_legacy_state_file(legacy / name, destination / name)
    sessions = legacy / "sessions"
    if sessions.exists():
        if sessions.is_symlink() or not sessions.is_dir():
            raise ValueError(f"refusing to import unsafe legacy sessions path: {sessions}")
        shutil.copytree(sessions, destination / "sessions", dirs_exist_ok=False, symlinks=False)


def _copy_legacy_state_file(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"refusing to import unsafe legacy state path: {source}")
    shutil.copy2(source, target)


def _print_trust_records(records: dict[str, Any], *, title: str) -> None:
    print(f"{title}: {len(records)}")
    for skill_id, record in sorted(records.items()):
        state = record.get("state", "?") if isinstance(record, dict) else "?"
        digest = record.get("content_hash", "?") if isinstance(record, dict) else "?"
        print(f"  - {skill_id}: {state} {digest}")


def _require_interactive_confirmation(prompt: str) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("state migration requires interactive confirmation")
    answer = _interactive_input(prompt).strip().lower()
    if answer not in {"y", "yes"}:
        raise ValueError("state migration canceled")


def _refuse_unsafe_migration_project(project: Path) -> None:
    project = project.expanduser().resolve()
    unsafe_roots = [
        Path(tempfile.gettempdir()).resolve(),
        (Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))).expanduser().resolve(),
    ]
    for root_path in unsafe_roots:
        try:
            project.relative_to(root_path)
        except ValueError:
            continue
        raise ValueError(f"refusing to migrate state for project under untrusted temporary/cache path: {project}")


def cmd_setup(args: argparse.Namespace) -> int:
    args.yolo = bool(args.yolo or args.trust_all)
    if args.json and args.summary_json:
        raise ValueError("--json and --summary-json cannot be combined")
    action_requested = _setup_action_requested(args)
    setup_agents = _bootstrap_agents(args)
    project_dir = (find_project_root() or Path.cwd()).resolve()
    fresh_project_reset = _clear_fresh_project_state(root(args), project_dir=project_dir) if args.fresh_project else None
    if fresh_project_reset is not None:
        fresh_project_reset["retained_global_state"] = _fresh_project_retained_global_state(catalog_root(args), project_dir)
    audience = args.audience
    if _should_prompt_setup_audience(args):
        audience = _prompt_setup_audience(root(args), args, catalog_root=catalog_root(args))
        if audience == "__cancel__":
            print("Setup canceled.")
            return 1
    report = setup_environment(
        root(args),
        paths=args.paths or None,
        extra_paths=_active_setup_paths(root(args), args.paths or None),
        include_packages=not args.no_packages,
        extra_skills=_review_extra_skills(args),
        source=args.source,
        audience=audience,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_lint_blocked=True,
        include_global=args.include_global,
        fresh=args.fresh,
        fresh_project=args.fresh_project,
        accept_low=args.accept_low,
        yolo=args.yolo,
        trust_state=args.trust_selected,
        block_high=args.block_high,
        override_lint=args.override_lint,
        reason=args.reason,
        approval_root=catalog_root(args),
        global_scope=not args.project_only,
    )
    if fresh_project_reset is not None:
        report["fresh_project_reset"] = fresh_project_reset
    report["no_manifest_skills"] = _no_manifest_skill_summary(report["selected"])
    _remember_setup_paths(root(args), args.paths or None)
    _mark_setup_complete(root(args), project_dir=project_dir)
    _record_project_registry(args, project_dir)
    if args.summary_json or args.json:
        bootstrap = _setup_bootstrap_after_review(
            args,
            report,
            project_dir=project_dir,
            agents=setup_agents,
            allow_prompt=False,
        )
        if bootstrap is not None:
            report["bootstrap"] = bootstrap
            if _setup_bootstrap_saves_scope(bootstrap):
                _save_status_scope(root(args), report["selected"], audience=audience, include_global=args.include_global, agents=list(bootstrap.get("agents") or setup_agents), paths=args.paths or None)
    if args.summary_json:
        print(json.dumps(_compact_setup_report(report), indent=2, sort_keys=True))
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Indexed {report['indexed']} skills")
        print(f"Project root: {project_dir}")
        if report.get("skipped_global"):
            print(f"Skipped {report['skipped_global']} global skill(s) already installed; use --include-global to review them.")
        if args.fresh_project:
            retained = ((report.get("fresh_project_reset") or {}).get("retained_global_state") or {})
            print(
                "Fresh project reset: "
                f"project trust decisions cleared={report.get('fresh_reset', 0)}, "
                "reusable global approvals retained. "
                f"Project tags detached={((report.get('fresh_project_reset') or {}).get('project_tags', 0))}, "
                f"legacy sessions cleared={((report.get('fresh_project_reset') or {}).get('sessions', 0))}, "
                f"saved setup scope cleared={int(bool((report.get('fresh_project_reset') or {}).get('status_scope')))}. "
                "Retained global state: "
                f"{retained.get('global_approvals', 0)} approval(s), "
                f"{retained.get('catalog_tags', 0)} catalog tag(s), "
                f"{retained.get('catalog_tag_members', 0)} tag member(s), "
                f"{retained.get('collections', 0)} collection(s), "
                f"plus {retained.get('materialized_skill_targets', 0)} materialized skill target(s)."
            )
        elif args.fresh:
            print(
                "Fresh reset: "
                f"project trust decisions cleared={report.get('fresh_reset', 0)}. "
                "Reusable global approvals, tags, collections, and materialized skill files were retained."
            )
        if report.get("global_approved"):
            print(f"Applied {report['global_approved']} reusable global approval(s).")
        _print_no_manifest_skill_summary(report.get("no_manifest_skills") or {})
        if report.get("errors"):
            _print_discovery_errors(report["errors"])
        _print_review_report(report["selected"], report["summary"], report["action"], compact=not args.details)
        if not report["selected"]:
            _print_empty_setup_guidance(args)
        _print_out_of_scope_collections(
            root(args),
            catalog_root(args),
            action_requested=bool(args.yolo or args.accept_low or args.trust_selected or args.block_high or args.override_lint),
        )
        if not action_requested and not args.non_interactive:
            _interactive_setup(
                root(args),
                report["selected"],
                audience=audience,
                include_global=args.include_global,
                catalog_root=catalog_root(args),
                global_scope=not args.project_only,
                paths=args.paths or None,
                agents=setup_agents,
                no_bootstrap=args.no_bootstrap,
                project_dir=project_dir,
            )
        elif not action_requested and not _setup_bootstrap_relevant(args, report):
            print()
            _print_setup_next_steps(report["selected"])
        else:
            print()
            bootstrap = _setup_bootstrap_after_review(
                args,
                report,
                project_dir=project_dir,
                agents=setup_agents,
                allow_prompt=not args.non_interactive and sys.stdin.isatty() and sys.stdout.isatty(),
            )
            if bootstrap is not None:
                report["bootstrap"] = bootstrap
                bootstrap_agents = list(bootstrap.get("agents") or setup_agents)
                if bootstrap.get("performed"):
                    _print_setup_bootstrap_result(bootstrap)
                elif bootstrap.get("reason_code") in {SETUP_BOOTSTRAP_REASON_DISABLED, SETUP_BOOTSTRAP_REASON_AGENT_NOT_SPECIFIED}:
                    _print_setup_bootstrap_reminder(bootstrap)
                if _setup_bootstrap_saves_scope(bootstrap):
                    _save_status_scope(root(args), report["selected"], audience=audience, include_global=args.include_global, agents=bootstrap_agents, paths=args.paths or None)
                if bootstrap.get("performed") and bootstrap.get("handoff_ready"):
                    _print_agent_next_steps(list(bootstrap.get("artifacts") or []))
            if bootstrap is None or not _setup_already_printed_specific_guidance(bootstrap):
                print("Next step: restart or clear the agent; it should run `skillager working`, then continue with your request. Run `skillager handoff` when you want explicit curation guidance.")
    return 0


def _print_empty_setup_guidance(args: argparse.Namespace) -> None:
    print()
    print("No skills matched this setup scope.")
    if not args.paths:
        print("  Skillager scans this project, child skill repositories in this directory, installed packages, and global skills.")
        print("  If your skills live somewhere else, run `skillager setup <path-to-skill-repo> ...`.")
    if not args.include_global:
        print("  Already-installed global skills are hidden by default; add --include-global to review them.")


def _print_discovery_errors(errors: list[dict[str, Any]], *, limit: int = 5) -> None:
    print(f"Errors: {len(errors)}")
    for item in errors[:limit]:
        path = item.get("path") or "<unknown>"
        error = item.get("error") or "unknown error"
        print(f"  - {path}: {error}")
    remaining = len(errors) - limit
    if remaining > 0:
        print(f"  ... {remaining} more")


def _no_manifest_skill_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [skill for skill in skills if skill.get("inferred") and not skill.get("manifest_path")]
    by_source = Counter(_no_manifest_source_label(skill) for skill in selected)
    return {
        "count": len(selected),
        "by_source": dict(sorted(by_source.items())),
        "metadata": "SKILL.md-only directories without skillager.yaml",
    }


def _no_manifest_source_label(skill: dict[str, Any]) -> str:
    source = skill.get("source") or {}
    for key in ("collection", "package", "name"):
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return str(source.get("type") or "unknown")


def _print_no_manifest_skill_summary(summary: dict[str, Any]) -> None:
    count = int(summary.get("count") or 0)
    if not count:
        return
    by_source = summary.get("by_source") or {}
    parts = ", ".join(f"{source}={amount}" for source, amount in sorted(by_source.items()))
    suffix = f" ({parts})" if parts else ""
    print(
        "No-manifest skills discovered: "
        f"{count}{suffix}. "
        "`skillager manifest init` is only needed if you want structured audience/activation metadata."
    )


def _compact_setup_report(report: dict[str, Any]) -> dict[str, Any]:
    selected = report.get("selected", [])
    review_needed = [skill for skill in selected if skill.get("trust") == "discovered"]
    lint_blocked = [skill for skill in selected if skill.get("trust") == "lint_blocked"]
    approved = [skill for skill in selected if skill.get("trust") in {"reviewed", "trusted", "pinned"}]
    compact = {
        "indexed": report.get("indexed", 0),
        "selected": len(selected),
        "approved": len(approved),
        "review_needed": len(review_needed),
        "lint_blocked": len(lint_blocked),
        "skipped_global": report.get("skipped_global", 0),
        "fresh_reset": report.get("fresh_reset", 0),
        "global_reset": report.get("global_reset", 0),
        "global_approved": report.get("global_approved", 0),
        "fresh_project_reset": report.get("fresh_project_reset"),
        "no_manifest_skills": report.get("no_manifest_skills"),
        "errors": len(report.get("errors", [])),
        "summary": report.get("summary", {}),
        "action": report.get("action", {}),
        "selected_ids": [skill.get("id") for skill in selected],
        "review_needed_ids": [skill.get("id") for skill in review_needed],
        "lint_blocked_ids": [skill.get("id") for skill in lint_blocked],
    }
    if "bootstrap" in report:
        compact["bootstrap"] = report["bootstrap"]
    return compact


def _setup_action_requested(args: argparse.Namespace) -> bool:
    return any((args.accept_low, args.yolo, args.trust_selected, args.block_high, args.override_lint))


def _setup_bootstrap_after_review(
    args: argparse.Namespace,
    report: dict[str, Any],
    *,
    project_dir: Path,
    agents: list[str],
    allow_prompt: bool,
) -> dict[str, Any] | None:
    if not _setup_bootstrap_relevant(args, report):
        return None
    approved = [skill for skill in report.get("selected", []) if skill.get("trust") in TRUSTED_STATES]
    if not approved:
        return _setup_bootstrap_payload(
            project_dir=project_dir,
            agents=agents,
            reason="no available skills in setup scope",
            reason_code=SETUP_BOOTSTRAP_REASON_NO_APPROVED,
        )
    if args.no_bootstrap:
        return _setup_bootstrap_payload(
            project_dir=project_dir,
            agents=agents,
            reason="disabled by --no-bootstrap",
            reason_code=SETUP_BOOTSTRAP_REASON_DISABLED,
        )
    if not agents and allow_prompt:
        agents = _choose_materialize_agents()
    if not agents:
        return _setup_bootstrap_payload(
            project_dir=project_dir,
            agents=agents,
            reason="agent not specified",
            reason_code=SETUP_BOOTSTRAP_REASON_AGENT_NOT_SPECIFIED,
        )
    bootstrap = _perform_bootstrap(agents=agents, project_dir=project_dir, dry_run=False, force=False)
    return _setup_bootstrap_payload(project_dir=project_dir, agents=agents, bootstrap=bootstrap)


def _setup_bootstrap_relevant(args: argparse.Namespace, report: dict[str, Any]) -> bool:
    if args.agent or args.all_agents or args.no_bootstrap:
        return True
    return bool(_setup_action_requested(args) and (report.get("action") or {}).get("changed"))


def _setup_bootstrap_payload(
    *,
    project_dir: Path,
    agents: list[str],
    bootstrap: dict[str, Any] | None = None,
    reason: str | None = None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    artifacts = list((bootstrap or {}).get("artifacts") or [])
    handoff_ready = _setup_handoff_ready(project_dir, agents=agents)
    next_commands = [] if handoff_ready else _setup_bootstrap_next_commands(agents)
    return {
        "performed": bootstrap is not None,
        "reason": reason,
        "reason_code": reason_code,
        "agents": agents,
        "handoff_ready": handoff_ready,
        "next_commands": next_commands,
        "artifacts": artifacts,
        "summary": (bootstrap or {}).get("summary") or _bootstrap_summary(artifacts),
    }


def _setup_bootstrap_saves_scope(bootstrap: dict[str, Any]) -> bool:
    if bootstrap.get("performed"):
        return bool(bootstrap.get("handoff_ready"))
    return bootstrap.get("reason_code") == SETUP_BOOTSTRAP_REASON_DISABLED and bool(bootstrap.get("agents"))


def _setup_already_printed_specific_guidance(bootstrap: dict[str, Any]) -> bool:
    if bootstrap.get("performed"):
        return True
    return bootstrap.get("reason_code") in {SETUP_BOOTSTRAP_REASON_DISABLED, SETUP_BOOTSTRAP_REASON_AGENT_NOT_SPECIFIED}


def _setup_handoff_ready(project_dir: Path, *, agents: list[str]) -> bool:
    return bool(agents) and all(_handoff_ready(_handoff_artifacts(project_dir, agent=agent)) for agent in agents)


def _setup_bootstrap_next_commands(agents: list[str]) -> list[str]:
    if agents == ["codex", "claude"]:
        return ["skillager bootstrap --all-agents"]
    if agents:
        return [f"skillager bootstrap --agent {agent}" for agent in agents]
    return ["skillager bootstrap --agent codex", "skillager bootstrap --agent claude"]


def _build_visible_skill_view(
    state_root: Path,
    *,
    catalog_root: Path,
    project_dir: Path,
    agent: str | None,
    paths: list[Path] | None = None,
    include_packages: bool = True,
    include_global: bool = False,
    use_saved_scope: bool = True,
) -> dict[str, Any]:
    data = build_index(
        state_root,
        paths,
        include_packages=include_packages,
        approval_root=catalog_root,
        extra_paths=_active_setup_paths(state_root, paths),
        persist=False,
    )
    extra_skills = _project_tag_collection_skills(
        state_root,
        catalog_root=catalog_root,
        project_dir=project_dir,
        include_lint_blocked=True,
    )
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    saved_scope = _load_status_scope(state_root) if use_saved_scope else None
    scope_audience = saved_scope.get("audience") if saved_scope else None
    selected_include_global = (include_global or bool(saved_scope.get("include_global"))) if saved_scope else include_global
    skills = select_visible_skills(
        data.get("skills", []),
        audience=scope_audience,
        include_global=selected_include_global,
        include_lint_blocked=True,
    )
    skills = annotate_duplicate_content(skills)
    review_needed = _status_review_needed(skills, saved_scope=saved_scope)
    lint_blocked = [skill for skill in skills if skill.get("trust") == "lint_blocked"]
    approved = [skill for skill in skills if skill.get("trust") in TRUSTED_STATES]
    authored_unreviewed = _authored_unreviewed(skills)
    blocked = [skill for skill in data.get("skills", []) if skill.get("trust") == "blocked"]
    project_exposure = _project_exposure(project_dir)
    attached_tags = _project_tag_names(project_dir)
    materialized_router_tags = sorted(_materialized_router_tags(project_dir, agent=agent)) if agent else []
    artifacts = _handoff_artifacts(project_dir, agent=agent) if agent else {}
    materialized_project_counts = _materialized_project_counts(project_dir)
    unmaterialized_attached_tags = sorted(tag for tag in attached_tags if tag not in set(materialized_router_tags))
    migration = collection_migration_summary(catalog_root)
    tagging = _status_tagging_summary(state_root, catalog_root)
    readiness = _build_readiness(
        review_needed=review_needed,
        lint_blocked=lint_blocked,
        approved=approved,
        artifacts=artifacts,
        agent=agent,
        project_exposure=project_exposure,
        materialized_project_counts=materialized_project_counts,
        attached_tags=attached_tags,
        materialized_router_tags=materialized_router_tags,
        unmaterialized_attached_tags=unmaterialized_attached_tags,
    )
    return {
        "index": data,
        "skills": skills,
        "saved_scope": saved_scope,
        "review_needed": review_needed,
        "lint_blocked": lint_blocked,
        "approved": approved,
        "authored_unreviewed": authored_unreviewed,
        "blocked": blocked,
        "project_exposure": project_exposure,
        "attached_tags": attached_tags,
        "materialized_router_tags": materialized_router_tags,
        "unmaterialized_attached_tags": unmaterialized_attached_tags,
        "artifacts": artifacts,
        "materialized_project_counts": materialized_project_counts,
        "migration": migration,
        "tagging": tagging,
        "duplicate_content": duplicate_content_summary(skills),
        "readiness": readiness,
    }


def _build_readiness(
    *,
    review_needed: list[dict[str, Any]],
    lint_blocked: list[dict[str, Any]],
    approved: list[dict[str, Any]],
    artifacts: dict[str, Any],
    agent: str | None,
    project_exposure: dict[str, list[dict[str, Any]]],
    materialized_project_counts: dict[str, int],
    attached_tags: list[str],
    materialized_router_tags: list[str],
    unmaterialized_attached_tags: list[str],
) -> dict[str, Any]:
    review_ready = not review_needed and not lint_blocked
    handoff_ready = _handoff_ready(artifacts) if agent else False
    handoff_action = _handoff_repair_action(artifacts, agent=agent) if not handoff_ready else None
    return {
        "review_ready": review_ready,
        "handoff_ready": handoff_ready,
        "ready": review_ready and handoff_ready,
        "handoff": handoff_action,
        "exposure": _readiness_exposure(
            approved=approved,
            project_exposure=project_exposure,
            materialized_project_counts=materialized_project_counts,
            attached_tags=attached_tags,
            materialized_router_tags=materialized_router_tags,
            unmaterialized_attached_tags=unmaterialized_attached_tags,
        ),
    }


def _readiness_exposure(
    *,
    approved: list[dict[str, Any]],
    project_exposure: dict[str, list[dict[str, Any]]],
    materialized_project_counts: dict[str, int],
    attached_tags: list[str],
    materialized_router_tags: list[str],
    unmaterialized_attached_tags: list[str],
) -> dict[str, Any]:
    approved_ids = {skill["id"] for skill in approved}
    breakdown = _project_exposure_breakdown(project_exposure, skill_ids=approved_ids)
    return {
        "approved": len(approved_ids),
        "approved_source_entries": len(approved_ids),
        "exposed": breakdown["exposed"],
        "exposed_source_entries": breakdown["exposed"],
        "native": breakdown["native"],
        "stubbed": breakdown["stubbed"],
        "routed": breakdown["routed"],
        "router_tags": breakdown["router_tags"],
        "available_on_demand": len(approved_ids.difference(breakdown["exposed_ids"])),
        "available_source_entries_on_demand": len(approved_ids.difference(breakdown["exposed_ids"])),
        "count_basis": "approved source entries",
        "project_materialized": materialized_project_counts,
        "attached_tags": attached_tags,
        "materialized_router_tags": materialized_router_tags,
        "unmaterialized_attached_tags": unmaterialized_attached_tags,
    }


def _project_exposure_breakdown(
    project_exposure: dict[str, list[dict[str, Any]]],
    *,
    skill_ids: set[str] | None = None,
) -> dict[str, Any]:
    native_ids: set[str] = set()
    stubbed_ids: set[str] = set()
    routed_ids: set[str] = set()
    router_tags: set[str] = set()
    for skill_id, targets in project_exposure.items():
        if skill_id == WORKING_SKILL_ID:
            continue
        if skill_ids is not None and skill_id not in skill_ids:
            continue
        for target in targets:
            kind = target.get("kind")
            if kind == "native":
                native_ids.add(skill_id)
            elif kind == "stub":
                stubbed_ids.add(skill_id)
            elif kind == "router":
                routed_ids.add(skill_id)
                tag = target.get("tag") or target.get("router")
                if tag:
                    router_tags.add(str(tag))
    exposed_ids = native_ids | stubbed_ids | routed_ids
    return {
        "exposed": len(exposed_ids),
        "native": len(native_ids),
        "stubbed": len(stubbed_ids),
        "routed": len(routed_ids),
        "router_tags": len(router_tags),
        "exposed_ids": exposed_ids,
    }


def _exposure_summary_text(exposure: dict[str, Any]) -> str:
    exposed = int(exposure.get("exposed") or 0)
    available = int(exposure.get("available_on_demand") or 0)
    details = []
    router_tags = int(exposure.get("router_tags") or 0)
    routed = int(exposure.get("routed") or 0)
    stubbed = int(exposure.get("stubbed") or 0)
    native = int(exposure.get("native") or 0)
    if router_tags or routed:
        details.append(f"{router_tags} router tag(s), {routed} routed")
    if stubbed:
        details.append(f"{stubbed} stubbed")
    if native:
        details.append(f"{native} native")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{exposed} exposed entr{'y' if exposed == 1 else 'ies'}{suffix}, {available} available entr{'y' if available == 1 else 'ies'} on demand"


def _handoff_ready(artifacts: dict[str, Any]) -> bool:
    if not artifacts:
        return False
    working = artifacts.get("working_skill") or {}
    notes = artifacts.get("project_notes") or []
    return working.get("status") == "present" and bool(notes) and all(note.get("status") == "present" for note in notes)


def _handoff_repair_action(artifacts: dict[str, Any], *, agent: str | None) -> dict[str, Any] | None:
    if not agent:
        return {
            "kind": "agent-required",
            "reason": "agent not specified",
            "reason_code": HANDOFF_REASON_AGENT_REQUIRED,
            "message": "Pass --agent codex or --agent claude to check first-party working artifacts.",
            "command": None,
        }
    working = artifacts.get("working_skill") or {}
    working_status = working.get("status")
    if working_status == "unmanaged":
        return {
            "kind": "manual",
            "reason": "unmanaged working skill",
            "reason_code": HANDOFF_REASON_WORKING_UNMANAGED,
            "message": f"Move or remove unmanaged Skillager Working target before refreshing: {working.get('path')}",
            "command": None,
        }
    if working_status == "drift":
        reason = working.get("reason") or "working skill drift"
        return {
            "kind": "manual",
            "reason": reason,
            "reason_code": _working_drift_reason_code(reason),
            "message": f"Review local Skillager Working changes before refreshing: {working.get('path')}",
            "command": None,
        }
    if working_status in {"missing", "stale"}:
        return _bootstrap_repair_action(agent, f"working skill {working_status}", reason_code=_working_status_reason_code(working_status))
    notes = artifacts.get("project_notes") or []
    stale_note = next((note for note in notes if note.get("status") != "present"), None)
    if stale_note:
        status = str(stale_note.get("status") or "unknown")
        return _bootstrap_repair_action(agent, f"project note {status}", reason_code=_project_note_reason_code(status))
    if _artifacts_need_attention(artifacts):
        return _bootstrap_repair_action(agent, "working artifacts need refresh", reason_code=HANDOFF_REASON_HANDOFF_ARTIFACTS)
    return None


def _working_status_reason_code(status: str | None) -> str:
    if status == "missing":
        return HANDOFF_REASON_WORKING_MISSING
    if status == "stale":
        return HANDOFF_REASON_WORKING_STALE
    return HANDOFF_REASON_WORKING_DRIFT


def _working_drift_reason_code(reason: str) -> str:
    return _WORKING_DRIFT_REASON_CODES.get(reason, HANDOFF_REASON_WORKING_DRIFT)


def _project_note_reason_code(status: str) -> str:
    return _PROJECT_NOTE_REASON_CODES.get(status, HANDOFF_REASON_PROJECT_NOTE_UNKNOWN)


def _bootstrap_repair_action(agent: str, reason: str, *, reason_code: str) -> dict[str, Any]:
    command = f"skillager bootstrap --agent {agent}"
    return {
        "kind": "bootstrap",
        "reason": reason,
        "reason_code": reason_code,
        "message": f"Run `{command}` to refresh Skillager's first-party project working artifacts.",
        "command": command,
    }


def cmd_status(args: argparse.Namespace) -> int:
    if args.ack_migration:
        ack_collection_migrations(catalog_root(args))
    state_root = root(args)
    approval_root = catalog_root(args)
    project_dir = (find_project_root() or Path.cwd()).resolve()
    agent, agent_source = _status_agent(args, state_root)
    view = _build_visible_skill_view(
        state_root,
        catalog_root=approval_root,
        project_dir=project_dir,
        agent=agent,
        paths=args.paths or None,
        include_packages=not args.no_packages,
        include_global=args.include_global,
        use_saved_scope=not args.all,
    )
    data = view["index"]
    skills = view["skills"]
    saved_scope = view.get("saved_scope")
    summary = review_summary(skills)
    materialized = view["materialized_project_counts"]
    review_needed = view["review_needed"]
    lint_blocked = view["lint_blocked"]
    scan_summary = _status_scan_summary(skills)
    manifest_lint = _status_manifest_lint_summary(skills)
    lint_warned = [skill for skill in skills if (skill.get("lint") or {}).get("status") == "warned"]
    approved = view["approved"]
    global_approved = [skill for skill in approved if skill.get("trust_reason") == "global-approval"]
    authored_unreviewed = view["authored_unreviewed"]
    blocked = view["blocked"]
    collection_summary = _status_collection_summary(state_root, approval_root)
    collection_inventory = _status_collection_inventory(skills)
    tagging_summary = view["tagging"]
    migration_summary = view["migration"]
    duplicate_content = view["duplicate_content"]
    update = check_for_update(cache_root(), current_version=__version__, write_cache=False)
    readiness = view["readiness"]
    status = {
        "indexed": len(data.get("skills", [])),
        "selected": len(skills),
        "agent": agent,
        "agent_source": agent_source,
        "review_needed": len(review_needed),
        "lint_blocked": len(lint_blocked),
        "lint_blocked_ids": [skill["id"] for skill in lint_blocked],
        "lint_warned": len(lint_warned),
        "scan": scan_summary,
        "manifest_lint": manifest_lint,
        "approved": len(approved),
        "global_approved": len(global_approved),
        "authored_unreviewed": {"count": len(authored_unreviewed), "ids": [skill["id"] for skill in authored_unreviewed]},
        "blocked": len(blocked),
        "skipped_global": sum(1 for skill in data.get("skills", []) if skill.get("source", {}).get("type") == "global") if not args.include_global else 0,
        "summary": summary,
        "materialized": materialized,
        "reviewed_scope_count": saved_scope.get("selected_count") if saved_scope else None,
        "setup_scope_count": saved_scope.get("selected_count") if saved_scope else None,
        "exposure_count": readiness["exposure"]["exposed"],
        "exposure_breakdown": {
            key: readiness["exposure"].get(key, 0)
            for key in ("native", "stubbed", "routed", "router_tags")
        },
        "readiness": readiness,
        "inventory": _available_inventory_summary(skills, agent=agent, project_exposure=view["project_exposure"]),
        "needs_setup": not readiness["review_ready"],
        "collections": collection_summary,
        "collection_inventory": collection_inventory,
        "tagging": tagging_summary,
        "duplicate_content": duplicate_content,
        "collection_migrations": migration_summary,
        "migration_details": args.migration_details,
        "update": update,
        "scope": saved_scope or None,
        "message": _status_message(
            review_needed,
            lint_blocked=lint_blocked,
            collection_summary=collection_summary,
            migration_summary=migration_summary,
            duplicate_content=duplicate_content,
            readiness=readiness,
        ),
    }
    if args.json:
        payload = status if args.full_json else _compact_status(status)
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.quiet:
        print(status["message"])
    else:
        _print_status(status)
    if args.exit_code:
        if not readiness["review_ready"]:
            return 10
        if agent and not readiness["handoff_ready"] and readiness.get("exposure", {}).get("approved"):
            return 11
    return 0


def cmd_working(args: argparse.Namespace) -> int:
    state_root = root(args)
    approval_root = catalog_root(args)
    project_dir = (find_project_root() or Path.cwd()).resolve()
    result = _build_working_result(
        state_root,
        catalog_root=approval_root,
        project_dir=project_dir,
        agent=args.agent or _detect_agent_optional(),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _build_working_result(
    state_root: Path,
    *,
    catalog_root: Path,
    project_dir: Path,
    agent: str | None,
) -> dict[str, Any]:
    data = build_index(
        state_root,
        include_packages=True,
        approval_root=catalog_root,
        extra_paths=_active_setup_paths(state_root),
        persist=False,
    )
    setup_complete = _working_setup_complete(state_root)
    pending_external = _working_pending_external_review(data.get("skills", []))
    return {
        "schema": WORKING_RESULT_SCHEMA,
        "status": "ok",
        "project": str(project_dir),
        "agent": agent,
        "setup_complete": setup_complete,
        "auto_approved_project_count": 0,
        "auto_approved_project_skills": [],
        "pending_external_review_count": len(pending_external),
        "pending_external_review": [_working_sync_item(skill) for skill in pending_external],
        "new_external_review_count": 0,
        "new_external_review": [],
    }


def _working_pending_external_review(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        skill
        for skill in sorted(skills, key=lambda item: str(item.get("id") or ""))
        if (skill.get("source") or {}).get("type") != "project"
        and skill.get("trust") in {"discovered", "lint_blocked"}
        and skill.get("id")
    ]


def _working_setup_path(state_root: Path) -> Path:
    return state_root / "setup.json"


def _mark_setup_complete(state_root: Path, *, project_dir: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    _working_setup_path(state_root).write_text(
        json.dumps(
            {
                "schema": "skillager.setup-state.v1",
                "project": str(project_dir),
                "setup_completed_at": _now_iso(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _working_setup_complete(state_root: Path) -> bool:
    if _working_setup_path(state_root).exists():
        return True
    if _load_status_scope(state_root):
        return True
    trust = load_trust(state_root)
    return bool(trust.get("skills"))


def _working_sync_item(skill: dict[str, Any]) -> dict[str, Any]:
    source = skill.get("source") or {}
    return {
        "id": skill.get("id"),
        "trust": skill.get("trust"),
        "source": {
            key: value
            for key, value in source.items()
            if key in {"type", "collection", "package", "environment", "agent"}
        },
        "path": skill.get("root"),
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    state_root = root(args)
    approval_root = catalog_root(args)
    project_dir = (find_project_root() or Path.cwd()).resolve()
    agent = args.agent or _detect_agent_optional()
    result = _build_doctor_result(
        state_root,
        catalog_root=approval_root,
        project_dir=project_dir,
        agent=agent,
        include_packages=not args.no_packages,
        include_global=args.include_global,
    )
    fix_result: dict[str, Any] | None = None
    if args.fix:
        fix_result = _doctor_apply_fix(result, project_dir=project_dir, agent=args.agent)
        if fix_result.get("applied"):
            result = _build_doctor_result(
                state_root,
                catalog_root=approval_root,
                project_dir=project_dir,
                agent=agent,
                include_packages=not args.no_packages,
                include_global=args.include_global,
            )
        result["fix"] = fix_result
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_doctor_result(result)
    return int(result.get("exit_code") or 0)


def _build_doctor_result(
    state_root: Path,
    *,
    catalog_root: Path,
    project_dir: Path,
    agent: str | None,
    include_packages: bool,
    include_global: bool,
) -> dict[str, Any]:
    view = _build_visible_skill_view(
        state_root,
        catalog_root=catalog_root,
        project_dir=project_dir,
        agent=agent,
        include_packages=include_packages,
        include_global=include_global,
        use_saved_scope=True,
    )
    diagnosis = _doctor_diagnosis(view, agent=agent)
    return {
        "schema": "skillager.doctor.v1",
        "project": str(project_dir),
        "agent": agent,
        "readiness": view["readiness"],
        "state": _doctor_state(view),
        "status": diagnosis["status"],
        "exit_code": diagnosis["exit_code"],
        "message": diagnosis["message"],
        "next": {
            "command": diagnosis.get("command"),
            "next_commands": diagnosis.get("next_commands", []),
        },
    }


def _doctor_state(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "review": {
            "needed": len(view["review_needed"]),
            "ids": [skill["id"] for skill in view["review_needed"]],
        },
        "lint_blocked": {
            "count": len(view["lint_blocked"]),
            "ids": [skill["id"] for skill in view["lint_blocked"]],
        },
        "authored_unreviewed": {
            "count": len(view["authored_unreviewed"]),
            "ids": [skill["id"] for skill in view["authored_unreviewed"]],
        },
        "migration": view["migration"],
        "artifacts": view["artifacts"],
        "duplicate_content": view["duplicate_content"],
    }


def _doctor_diagnosis(view: dict[str, Any], *, agent: str | None) -> dict[str, Any]:
    if view["lint_blocked"]:
        count = len(view["lint_blocked"])
        return _doctor_issue(
            "lint-blocked",
            DOCTOR_EXIT_LINT_BLOCKED,
            f"{count} skill(s) are lint-blocked. Fix the source or approve with an audited override.",
            "skillager lint",
        )
    if view["authored_unreviewed"]:
        count = len(view["authored_unreviewed"])
        return _doctor_issue(
            "authored-review-needed",
            DOCTOR_EXIT_REVIEW_NEEDED,
            f"{count} authored skill(s) are not reviewed yet.",
            "skillager review --summary",
        )
    migration = view["migration"]
    migration_totals = migration.get("totals") or {}
    if migration.get("pending") and (migration_totals.get("needs_review") or migration_totals.get("tag_needs_repair")):
        return _doctor_issue(
            "migration-review-needed",
            DOCTOR_EXIT_MIGRATION_NEEDED,
            "Review collection ID migration details before using migrated collection skills.",
            "skillager status --migration-details",
        )
    if migration.get("pending"):
        return _doctor_issue(
            "migration-ack-needed",
            DOCTOR_EXIT_MIGRATION_NEEDED,
            "Acknowledge the collection ID migration report after reviewing it.",
            "skillager status --ack-migration",
        )
    readiness = view["readiness"]
    handoff_action = readiness.get("handoff") or {}
    if handoff_action.get("kind") == "manual":
        return _doctor_issue(
            "manual-artifact-repair-needed",
            DOCTOR_EXIT_MANUAL_REPAIR,
            handoff_action.get("message") or "Repair local Skillager working artifacts manually before refreshing.",
            None,
        )
    if view["review_needed"]:
        count = len(view["review_needed"])
        command, next_commands = _doctor_setup_next(agent)
        duplicate_review = int((view.get("duplicate_content") or {}).get("review_needed") or 0)
        message = f"{count} unreviewed skill(s) need review in the active setup scope."
        if duplicate_review:
            message += f" {duplicate_review} are same-content duplicate(s) that need source-key approval."
        return _doctor_issue(
            "review-needed",
            DOCTOR_EXIT_REVIEW_NEEDED,
            message,
            command,
            next_commands=next_commands,
        )
    if not readiness.get("handoff_ready") and (readiness.get("exposure") or {}).get("approved"):
        if handoff_action.get("kind") == "agent-required":
            return _doctor_issue(
                "agent-required",
                DOCTOR_EXIT_BOOTSTRAP_REPAIR,
                "Review is complete. Choose which agent's working artifacts to check.",
                None,
                next_commands=["skillager doctor --agent codex", "skillager doctor --agent claude"],
            )
        command = handoff_action.get("command")
        return _doctor_issue(
            "artifact-attention-needed",
            DOCTOR_EXIT_BOOTSTRAP_REPAIR,
            handoff_action.get("message") or "Refresh Skillager's first-party project working artifacts.",
            str(command) if command else None,
        )
    return _doctor_issue("ready", DOCTOR_EXIT_READY, "Skillager is ready.", None)


def _doctor_issue(
    status: str,
    exit_code: int,
    message: str,
    command: str | None,
    *,
    next_commands: list[str] | None = None,
) -> dict[str, Any]:
    commands = list(next_commands) if next_commands is not None else ([command] if command else [])
    return {
        "status": status,
        "exit_code": exit_code,
        "message": message,
        "command": command,
        "next_commands": commands,
    }


def _doctor_setup_next(agent: str | None) -> tuple[str | None, list[str]]:
    if agent:
        command = f"skillager setup --agent {agent}"
        return command, [command]
    return None, ["skillager setup --agent codex", "skillager setup --agent claude"]


def _doctor_apply_fix(result: dict[str, Any], *, project_dir: Path, agent: str | None) -> dict[str, Any]:
    handoff_action = (result.get("readiness") or {}).get("handoff") or {}
    reason_code = handoff_action.get("reason_code")
    if result.get("status") != "artifact-attention-needed" or handoff_action.get("kind") != "bootstrap":
        return {
            "requested": True,
            "applied": False,
            "reason": "selected next action is not a first-party bootstrap repair",
            "reason_code": reason_code,
        }
    if not agent:
        return {
            "requested": True,
            "applied": False,
            "reason": "pass --agent to apply mutating repairs",
            "reason_code": reason_code,
        }
    bootstrap = _perform_bootstrap(agents=[agent], project_dir=project_dir, dry_run=False, force=False)
    artifacts = bootstrap["artifacts"]
    applied = any(item.get("status") == "materialized" for item in artifacts)
    fix: dict[str, Any] = {
        "requested": True,
        "applied": applied,
        "action": "bootstrap",
        "reason": None if applied else "bootstrap completed without writing artifacts",
        "reason_code": reason_code,
        "artifacts": artifacts,
        "summary": bootstrap["summary"],
    }
    return fix


def _print_doctor_result(result: dict[str, Any]) -> None:
    print(_style("Skillager doctor", "bold"))
    print(f"  project: {result['project']}")
    print(f"  agent: {result.get('agent') or 'not specified'}")
    readiness = result.get("readiness") or {}
    exposure = readiness.get("exposure") or {}
    print()
    print("Readiness:")
    print(f"  Ready: {'yes' if readiness.get('ready') else 'no'}")
    print(f"  Review: {'ready' if readiness.get('review_ready') else 'needs review'}")
    print(f"  Handoff: {_readiness_handoff_state(readiness)}")
    print(f"  Exposure: {_exposure_summary_text(exposure)}")
    fix = result.get("fix")
    if fix:
        print()
        if fix.get("applied"):
            print("Fix: bootstrap repair applied.")
        else:
            print(f"Fix: not applied ({fix.get('reason', 'no repair available')}).")
    print()
    print("Next:")
    print(f"  {result['message']}")
    commands = (result.get("next") or {}).get("next_commands") or []
    for command in commands:
        print(f"  - {command}")


def cmd_handoff(args: argparse.Namespace) -> int:
    agent = args.agent or _detect_agent()
    project_dir = find_project_root() or Path.cwd()
    handoff = _build_handoff(root(args), catalog_root=catalog_root(args), project_dir=project_dir, agent=agent)
    handoff["note_updates"] = []
    if args.json:
        print(json.dumps(handoff, indent=2, sort_keys=True))
    else:
        _print_handoff(handoff)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    agents = _bootstrap_agents(args)
    project_dir = (find_project_root() or Path.cwd()).resolve()
    bootstrap = _perform_bootstrap(agents=agents, project_dir=project_dir, dry_run=args.dry_run, force=args.force)
    if not args.dry_run:
        _record_project_registry(args, project_dir)
    result = {
        "schema": "skillager.bootstrap.v1",
        "project": str(project_dir),
        "agents": agents,
        "dry_run": args.dry_run,
        "artifacts": bootstrap["artifacts"],
        "summary": bootstrap["summary"],
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_bootstrap_result(result)
    return 11 if _bootstrap_has_local_blocker(bootstrap["artifacts"]) else 0


def _record_project_registry(args: argparse.Namespace, project_dir: Path) -> None:
    project_registry.record_project(catalog_root(args), project_dir, state_dir=root(args))


def _bootstrap_agents(args: argparse.Namespace) -> list[str]:
    if args.all_agents:
        return ["codex", "claude"]
    return sorted(dict.fromkeys(args.agent or []))


def _bootstrap_note_statuses(project_dir: Path, *, agents: list[str]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for agent in agents:
        for path in agent_note_paths(project_dir, agents=[agent]):
            statuses[str(path)] = _handoff_note_status(path)
    return statuses


def _perform_bootstrap(*, agents: list[str], project_dir: Path, dry_run: bool, force: bool) -> dict[str, Any]:
    note_before = _bootstrap_note_statuses(project_dir, agents=agents)
    working_results = _bootstrap_working_results(agents=agents, project_dir=project_dir, dry_run=dry_run, force=force)
    artifacts = [_bootstrap_working_artifact(item) for item in working_results]
    note_agents = _bootstrap_note_agents(working_results)
    artifacts.extend(
        _bootstrap_note_artifacts(
            project_dir,
            agents=agents,
            note_agents=note_agents,
            before=note_before,
            dry_run=dry_run,
        )
    )
    return {
        "artifacts": artifacts,
        "summary": _bootstrap_summary(artifacts),
    }


def _bootstrap_working_results(*, agents: list[str], project_dir: Path, dry_run: bool, force: bool) -> list[dict[str, Any]]:
    # Bootstrap writes project notes itself so note state stays per-agent in the result.
    return materialize_working_skill(
        agents=agents,
        scope="project",
        project_dir=project_dir,
        dry_run=dry_run,
        force=force,
        include_notes=False,
    )


def _bootstrap_working_artifact(item: dict[str, Any]) -> dict[str, Any]:
    reason = item.get("reason")
    result = {
        "kind": "working_skill",
        "agent": item.get("agent"),
        "scope": item.get("scope"),
        "target": item.get("target"),
        "status": item.get("status"),
        "reason": reason,
        "skill_id": item.get("skill_id"),
        "blocked_by_local_state": reason in {WORKING_REASON_LOCAL_CUSTOMIZATION, WORKING_REASON_UNMANAGED},
        "local_customization_blocked": reason == WORKING_REASON_LOCAL_CUSTOMIZATION,
        "unmanaged_artifact_blocked": reason == WORKING_REASON_UNMANAGED,
    }
    return result


def _bootstrap_note_agents(working_results: list[dict[str, Any]]) -> list[str]:
    agents = []
    for item in working_results:
        status = item.get("status")
        reason = item.get("reason")
        if status in {"materialized", "would_write"} or (status == "skipped" and reason == "already up to date"):
            agent = item.get("agent")
            if isinstance(agent, str):
                agents.append(agent)
    return sorted(dict.fromkeys(agents))


def _bootstrap_note_artifacts(
    project_dir: Path,
    *,
    agents: list[str],
    note_agents: list[str],
    before: dict[str, dict[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    if note_agents and not dry_run:
        ensure_agent_notes(project_dir, agents=note_agents)
    enabled = set(note_agents)
    artifacts: list[dict[str, Any]] = []
    for agent in agents:
        for path in agent_note_paths(project_dir, agents=[agent]):
            prior = before.get(str(path)) or _handoff_note_status(path)
            status = prior.get("status")
            if agent not in enabled:
                artifacts.append(_bootstrap_note_artifact(agent, path, "skipped", "working skill not ready"))
            elif status == "present":
                artifacts.append(_bootstrap_note_artifact(agent, path, "skipped", "already up to date"))
            elif dry_run:
                artifacts.append(_bootstrap_note_artifact(agent, path, "would_write", f"currently {status}"))
            else:
                after = _handoff_note_status(path)
                if after.get("status") == "present":
                    artifacts.append(_bootstrap_note_artifact(agent, path, "materialized", None))
                else:
                    artifacts.append(_bootstrap_note_artifact(agent, path, "skipped", f"still {after.get('status', 'unknown')}"))
    return artifacts


def _bootstrap_note_artifact(agent: str, path: Path, status: str, reason: str | None) -> dict[str, Any]:
    return {
        "kind": "project_note",
        "agent": agent,
        "scope": "project",
        "target": str(path),
        "status": status,
        "reason": reason,
        "blocked_by_local_state": False,
        "local_customization_blocked": False,
        "unmanaged_artifact_blocked": False,
    }


def _bootstrap_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(str(item.get("status") or "unknown") for item in artifacts)
    return {
        "total": len(artifacts),
        "by_status": dict(sorted(by_status.items())),
        "local_blockers": sum(1 for item in artifacts if item.get("blocked_by_local_state")),
    }


def _bootstrap_has_local_blocker(artifacts: list[dict[str, Any]]) -> bool:
    return any(item.get("blocked_by_local_state") for item in artifacts)


def _print_bootstrap_result(result: dict[str, Any]) -> None:
    print(_style("Skillager bootstrap", "bold"))
    print(f"  project: {result['project']}")
    print(f"  agents: {', '.join(result.get('agents') or [])}")
    for item in result.get("artifacts", []):
        line = f"  - {item.get('agent')} {item.get('kind')}: {item.get('status')}"
        if item.get("target"):
            line += f" {item['target']}"
        if item.get("reason"):
            line += f" ({item['reason']})"
        print(line)
    summary = result.get("summary") or {}
    artifacts = result.get("artifacts") or []
    ready = sum(
        1
        for item in artifacts
        if item.get("status") == "materialized"
        or (item.get("status") == "skipped" and item.get("reason") == "already up to date")
    )
    print(f"Ready: {ready} of {len(artifacts)} artifacts current.")
    if summary.get("local_blockers"):
        print()
        print("Local artifact repair needed. Re-run with --force only if you want Skillager to overwrite the listed local target(s).")


def _build_handoff(state_root: Path, *, catalog_root: Path, project_dir: Path, agent: str) -> dict[str, Any]:
    view = _build_visible_skill_view(
        state_root,
        catalog_root=catalog_root,
        project_dir=project_dir,
        agent=agent,
        include_packages=True,
        include_global=False,
        use_saved_scope=True,
    )
    review_needed = view["review_needed"]
    lint_blocked = view["lint_blocked"]
    migration = view["migration"]
    artifacts = view["artifacts"]
    tagging = view["tagging"]
    readiness = view["readiness"]
    inventory = _available_inventory_summary(
        view["skills"],
        agent=agent,
        project_exposure=view["project_exposure"],
    )
    state = {
        "setup": {"needed": not readiness["review_ready"], "pending_owner_review": len(review_needed) + len(lint_blocked)},
        "migration": migration,
        "artifacts": artifacts,
        "tagging": _compact_tagging_summary(tagging),
        "attached_tags": view["attached_tags"],
        "materialized_router_tags": view["materialized_router_tags"],
        "unmaterialized_attached_tags": view["unmaterialized_attached_tags"],
        "unmaterialized_attached_tags_policy": "diagnostic only; materialize a router only after the user's goal makes that tag relevant",
        "inventory": inventory,
    }
    next_step = _handoff_next(state, agent=agent, readiness=readiness)
    return {
        "schema": "skillager.handoff.v1",
        "agent": agent,
        "readiness": _compact_readiness(readiness),
        "state": state,
        "status": next_step["status"],
        "next": next_step,
    }


def _detect_agent() -> str:
    return _detect_agent_optional() or "codex"


def _detect_agent_optional() -> str | None:
    if os.environ.get("CLAUDE_SESSION_ID"):
        return "claude"
    if os.environ.get("CODEX_SESSION_ID"):
        return "codex"
    return None


def _status_agent(args: argparse.Namespace, state_root: Path) -> tuple[str | None, str | None]:
    if args.agent:
        return args.agent, "argument"
    detected = _detect_agent_optional()
    if detected:
        return detected, "environment"
    saved = _saved_status_scope_agent(state_root)
    if saved:
        return saved, "saved_setup_scope"
    return None, None


def _saved_status_scope_agent(state_root: Path) -> str | None:
    scope = _load_status_scope(state_root) or {}
    agents = sorted(
        {
            agent
            for agent in (scope.get("agents") or [])
            if agent in {"codex", "claude"}
        }
    )
    if len(agents) == 1:
        return agents[0]
    return None


def _handoff_artifacts(project_dir: Path, *, agent: str) -> dict[str, Any]:
    notes = [_handoff_note_status(path) for path in agent_note_paths(project_dir, agents=[agent])]
    working = _working_artifact_status(project_dir, agent=agent)
    return {"project_notes": notes, "working_skill": working}


def _handoff_note_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    text = path.read_text(encoding="utf-8", errors="replace")
    if "## Skillager" not in text:
        return {"path": str(path), "status": "missing"}
    if AGENT_NOTE not in text:
        return {"path": str(path), "status": "stale"}
    return {"path": str(path), "status": "present"}


def _working_artifact_status(project_dir: Path, *, agent: str) -> dict[str, Any]:
    target = target_dir(agent=agent, scope="project", skill={"id": WORKING_SKILL_ID}, project_dir=project_dir)
    sidecar = target / "skillager.materialized.yaml"
    skill_file = target / "SKILL.md"
    result: dict[str, Any] = {"path": str(target)}
    if not target.exists() or not skill_file.exists():
        result["status"] = "missing"
        return result
    if not sidecar.exists():
        result["status"] = "unmanaged"
        return result
    try:
        data = load_mapping(sidecar)
    except Exception:
        result["status"] = "drift"
        result["reason"] = "unreadable sidecar"
        return result
    if data.get("source_type") != "skillager-working":
        result["status"] = "drift"
        result["reason"] = "target is not Skillager Working"
        return result
    expected_hash = working_source_hash(agent)
    if data.get("source_hash") != expected_hash:
        result["status"] = "stale"
        return result
    materialized_hash = data.get("materialized_hash")
    if not isinstance(materialized_hash, str) or content_hash(target) != materialized_hash:
        result["status"] = "drift"
        result["reason"] = "local customization"
        return result
    result["status"] = "present"
    return result


def _materialized_router_tags(project_dir: Path, *, agent: str) -> set[str]:
    tags: set[str] = set()
    for root_path in _project_skill_roots(project_dir).get(agent, []):
        if not root_path.is_dir():
            continue
        for sidecar in root_path.glob("*/skillager.materialized.yaml"):
            try:
                data = load_mapping(sidecar)
            except (OSError, UnicodeError, YamlError):
                continue
            if data.get("source_type") == "skillager-router" and data.get("tag"):
                tags.add(str(data["tag"]))
    return tags


def _handoff_next(state: dict[str, Any], *, agent: str, readiness: dict[str, Any]) -> dict[str, Any]:
    migration = state.get("migration") or {}
    migration_totals = migration.get("totals") or {}
    if migration.get("pending") and (migration_totals.get("needs_review") or migration_totals.get("tag_needs_repair")):
        return {
            "status": "migration-review-needed",
            "message": "Review collection ID migration details before using migrated collection skills.",
            "command": "skillager status --migration-details",
            "next_commands": ["skillager status --migration-details"],
        }
    if migration.get("pending"):
        return {
            "status": "migration-ack-needed",
            "message": "Acknowledge the collection ID migration report after reviewing it.",
            "command": "skillager status --ack-migration",
            "next_commands": ["skillager status --ack-migration"],
        }
    handoff_action = readiness.get("handoff") or {}
    if handoff_action.get("kind") == "manual":
        return {
            "status": "manual-artifact-repair-needed",
            "message": handoff_action.get("message") or "Repair local Skillager working artifacts manually before refreshing.",
            "command": None,
            "next_commands": [],
        }
    setup = state.get("setup") or {}
    if setup.get("needed"):
        command = f"skillager setup --agent {agent}"
        message = f"Ask the user to run `{command}` from this project directory before using Skillager-managed skills."
        return {
            "status": "setup-needed",
            "message": message,
            "command": command,
            "next_commands": [command],
        }
    if not readiness.get("handoff_ready"):
        command = handoff_action.get("command") or f"skillager bootstrap --agent {agent}"
        return {
            "status": "artifact-attention-needed",
            "message": handoff_action.get("message") or f"Refresh Skillager's project working artifacts for {agent}.",
            "command": command,
            "next_commands": [command],
        }
    materialized_router_tags = state.get("materialized_router_tags") or []
    if materialized_router_tags:
        return {
            "status": "ready",
            "message": (
                "Ask the user what they plan to do. Existing materialized router tag(s) are already available: "
                f"{', '.join(materialized_router_tags)}. Search within an existing router tag when it matches the user's goal; "
                "otherwise search available metadata, curate a task tag, and materialize a narrow router, stub, native skill, or no new exposure as appropriate. "
                "Report any curation or exposure changes."
            ),
            "command": None,
            "next_commands": _ready_handoff_commands(agent, materialized_router_tags=materialized_router_tags),
        }
    return {
        "status": "ready",
        "message": "Ask the user what they plan to do, search available metadata when a specialized skill may help, build a scored slate of skills or groups, then tag available skills and materialize a narrow router, stub, native skill, or no new exposure as appropriate. Report any curation or exposure changes.",
        "command": None,
        "next_commands": _ready_handoff_commands(agent),
    }


def _ready_handoff_commands(agent: str, *, materialized_router_tags: list[str] | None = None) -> list[str]:
    commands = [
        f"skillager list --summary-json --agent {agent}",
        f"skillager search \"<user-goal>\" --agent {agent} --json",
        "skillager tag create <task-tag>",
        "skillager tag add <task-tag> <skill-id>...",
        f"skillager materialize --tag <task-tag> --mode router --agent {agent} --scope project",
        f"skillager materialize <skill-id> --mode stub --agent {agent} --scope project",
    ]
    router_commands = [
        f"skillager search \"<user-goal>\" --tag {tag} --agent {agent} --json"
        for tag in (materialized_router_tags or [])
    ]
    return [commands[0], *router_commands, *commands[1:]]


def _available_inventory_summary(
    skills: list[dict[str, Any]],
    *,
    agent: str | None,
    project_exposure: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    available = _available_skills(skills)
    source_entry_count = len(available)
    agent_visible = _collapse_agent_variant_results(available, agent) if agent else available
    exposed_ids = _project_exposure_breakdown(project_exposure or {}, skill_ids={skill["id"] for skill in available})["exposed_ids"]
    available_ids = {skill["id"] for skill in available}
    return {
        "selected_source_entries": len(skills),
        "available_source_entries": source_entry_count,
        "agent_visible_choices": len(agent_visible),
        "exposed_now": len(exposed_ids),
        "available_on_demand": len(available_ids.difference(exposed_ids)),
        "collapsed_variants": max(0, source_entry_count - len(agent_visible)),
        "basis": "available source entries; agent-visible choices collapse alternate native-agent variants when --agent is set",
    }


def _artifacts_need_attention(artifacts: dict[str, Any]) -> bool:
    notes = artifacts.get("project_notes") or []
    if any(note.get("status") != "present" for note in notes):
        return True
    working = artifacts.get("working_skill") or {}
    return working.get("status") != "present"


def _readiness_handoff_state(readiness: dict[str, Any]) -> str:
    if readiness.get("handoff_ready"):
        return "ready"
    action = readiness.get("handoff") or {}
    kind = action.get("kind")
    reason = action.get("reason")
    if kind == "agent-required":
        return "not checked (agent not specified)"
    if kind == "manual":
        return f"manual repair needed ({reason})" if reason else "manual repair needed"
    if kind == "bootstrap":
        return f"needs repair ({reason})" if reason else "needs repair"
    return "needs repair"


def _readiness_next_actions(readiness: dict[str, Any]) -> list[str]:
    if readiness.get("handoff_ready"):
        return []
    action = readiness.get("handoff") or {}
    if action.get("command"):
        return [str(action["command"])]
    if action.get("kind") == "agent-required":
        return ["skillager status --agent codex", "skillager status --agent claude"]
    message = action.get("message")
    return [str(message)] if message else []


def _print_handoff(handoff: dict[str, Any]) -> None:
    state = handoff["state"]
    print(_style("Skillager handoff complete", "bold"))
    note_updates = handoff.get("note_updates") or []
    if note_updates:
        print()
        print("Updated project note:")
        for item in note_updates:
            print(f"  {item.get('path')}")
    print()
    readiness = handoff.get("readiness") or {}
    exposure = readiness.get("exposure") or {}
    print("Readiness:")
    print(f"  Ready: {'yes' if readiness.get('ready') else 'no'}")
    print(f"  Review: {'ready' if readiness.get('review_ready') else 'needs review'}")
    print(f"  Handoff: {_readiness_handoff_state(readiness)}")
    print(f"  Exposure: {_exposure_summary_text(exposure)}")
    print()
    print("State:")
    setup = state["setup"]
    setup_text = f"needed, {setup.get('pending_owner_review', 0)} skill(s) pending owner review" if setup["needed"] else "clean"
    print(f"  Setup: {setup_text}")
    migration = state["migration"]
    migration_text = "pending" if migration.get("pending") else "clean"
    if migration.get("pending"):
        totals = migration.get("totals", {})
        migration_text += (
            f", {totals.get('id_migrations', 0)} ID(s), "
            f"{totals.get('needs_review', 0)} review, "
            f"{totals.get('tag_needs_repair', 0)} tag repair"
        )
    print(f"  Migration: {migration_text}")
    artifacts = state["artifacts"]
    print(f"  Working skill: {artifacts['working_skill'].get('status')}")
    note_statuses = ", ".join(f"{Path(note['path']).name}={note['status']}" for note in artifacts.get("project_notes", []))
    print(f"  Project note: {note_statuses or 'missing'}")
    print(f"  Attached tags: {', '.join(state['attached_tags']) if state['attached_tags'] else 'none'}")
    print(f"  Materialized router tags: {', '.join(state['materialized_router_tags']) if state['materialized_router_tags'] else 'none'}")
    print(
        "  Unmaterialized attached tags: "
        f"{', '.join(state['unmaterialized_attached_tags']) if state['unmaterialized_attached_tags'] else 'none'}"
    )
    tagging = state.get("tagging") or {}
    if tagging.get("tags_pending_owner_review"):
        print(f"  Attached tags pending owner review: {tagging.get('tags_pending_owner_review')}")
    if tagging.get("available_untagged_count"):
        names = ", ".join(
            f"{item['collection']}={item['available_untagged']}"
            for item in tagging.get("available_untagged_collections", [])[:5]
        )
        print(f"  Available untagged collection skills: {tagging['available_untagged_count']} ({names})")
    print()
    print("Next:")
    print(handoff["next"]["message"])
    if handoff["next"].get("command"):
        print(f"  {handoff['next']['command']}")
    commands = handoff["next"].get("next_commands") or []
    if commands:
        print()
        print("Suggested commands:")
        for command in commands:
            print(f"  {command}")


def _print_inventory_block(inventory: dict[str, Any], *, indent: str = "") -> None:
    if not inventory:
        return
    print(f"{indent}Inventory:")
    print(f"{indent}  selected source entries: {inventory.get('selected_source_entries', 0)}")
    print(f"{indent}  available source entries: {inventory.get('available_source_entries', 0)}")
    print(f"{indent}  agent-visible choices: {inventory.get('agent_visible_choices', 0)}")
    print(f"{indent}  exposed now: {inventory.get('exposed_now', 0)}")
    print(f"{indent}  on demand: {inventory.get('available_on_demand', 0)}")
    print(f"{indent}  collapsed variants: {inventory.get('collapsed_variants', 0)}")


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    payload["available"] = payload.pop("approved", 0)
    payload["pending_owner_review"] = int(payload.pop("review_needed", 0) or 0) + int(payload.pop("lint_blocked", 0) or 0)
    if payload["pending_owner_review"]:
        payload["message"] = (
            f"Skillager: {payload['pending_owner_review']} skill(s) pending owner review. "
            "Ask the user to run `skillager setup`."
        )
    payload.pop("global_approved", None)
    payload.pop("lint_blocked_ids", None)
    scope = payload.get("scope")
    if isinstance(scope, dict):
        payload["scope"] = {key: value for key, value in scope.items() if key != "baseline"}
    payload.pop("scan", None)
    payload.pop("lint_warned", None)
    payload.pop("manifest_lint", None)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        payload["summary"] = _compact_review_summary(summary)
    payload.pop("duplicate_content", None)
    authored = payload.get("authored_unreviewed")
    if isinstance(authored, dict):
        payload["authored_pending_owner_review"] = int(authored.get("count") or 0)
        payload.pop("authored_unreviewed", None)
    readiness = payload.get("readiness")
    if isinstance(readiness, dict):
        payload["readiness"] = _compact_readiness(readiness)
    tagging = payload.get("tagging")
    if isinstance(tagging, dict):
        payload["tagging"] = _compact_tagging_summary(tagging)
    collection_inventory = payload.get("collection_inventory")
    if isinstance(collection_inventory, dict):
        payload["collection_inventory"] = _compact_collection_inventory(collection_inventory)
    return payload


def _compact_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    compact = dict(readiness)
    exposure = compact.get("exposure")
    if isinstance(exposure, dict):
        compact["exposure"] = _compact_exposure(exposure)
    return compact


def _compact_exposure(exposure: dict[str, Any]) -> dict[str, Any]:
    compact = dict(exposure)
    if "approved" in compact:
        compact["available"] = compact.pop("approved")
    if "approved_source_entries" in compact:
        compact["available_source_entries"] = compact.pop("approved_source_entries")
    if compact.get("count_basis") == "approved source entries":
        compact["count_basis"] = "available source entries"
    return compact


def _compact_duplicate_content(duplicate: dict[str, Any]) -> dict[str, Any]:
    compact = dict(duplicate)
    for key in ("review_needed_ids", "approved_ids", "groups_detail"):
        compact.pop(key, None)
    return compact


def _compact_tagging_summary(tagging: dict[str, Any]) -> dict[str, Any]:
    compact = dict(tagging)
    if "mixed_trust_tag_count" in compact:
        compact["tags_pending_owner_review"] = compact.pop("mixed_trust_tag_count")
    compact.pop("mixed_trust_tags", None)
    if "approved_untagged_count" in compact:
        compact["available_untagged_count"] = compact.pop("approved_untagged_count")
    collections = compact.pop("approved_untagged_collections", None)
    if isinstance(collections, list):
        compact["available_untagged_collections"] = [
            {
                **item,
                "available_untagged": item.get("approved_untagged", 0),
            }
            for item in collections
        ]
        for item in compact["available_untagged_collections"]:
            item.pop("approved_untagged", None)
    return compact


def _compact_collection_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    compact = dict(inventory)
    if "approved" in compact:
        compact["available"] = compact.pop("approved")
    pending = int(compact.pop("review_needed", 0) or 0) + int(compact.pop("lint_blocked", 0) or 0)
    compact["pending_owner_review"] = pending
    items = []
    for item in compact.get("items") or []:
        next_item = dict(item)
        if "approved" in next_item:
            next_item["available"] = next_item.pop("approved")
        item_pending = int(next_item.pop("review_needed", 0) or 0) + int(next_item.pop("lint_blocked", 0) or 0)
        next_item["pending_owner_review"] = item_pending
        items.append(next_item)
    compact["items"] = items
    return compact


def _compact_review_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = dict(summary)
    compact.pop("by_risk", None)
    compact.pop("by_trust", None)
    by_source = compact.get("by_source")
    if isinstance(by_source, dict):
        compact["by_source"] = {
            source: sum(int(count) for count in counts.values())
            if isinstance(counts, dict)
            else counts
            for source, counts in by_source.items()
        }
    return compact


def _status_collection_summary(state_root: Path, catalog_root: Path) -> dict[str, Any]:
    collections = load_collections(catalog_root).get("collections", {})
    tag_data = project_tags.load_tags(_current_project_dir())
    tags = {tag: entry.get("skills") or [] for tag, entry in (tag_data.get("tags") or {}).items()}
    tag_metadata = tag_data.get("tags") or {}
    attached = set(tags)
    items = []
    total_skills = 0
    attached_count = 0
    for name, item in sorted(collections.items()):
        try:
            collection_ids = {
                skill["id"]
                for skill in select_collection_skills(
                    catalog_root,
                    name,
                    trust_root=state_root,
                    approval_root=catalog_root,
                    include_lint_blocked=True,
                )
            }
            count = len(collection_ids)
        except Exception:
            collection_ids = set()
            count = 0
        matching_tags = [
            tag
            for tag, skill_ids in tags.items()
            if name in set((tag_metadata.get(tag) or {}).get("source_collections") or [])
            or (tag in attached and collection_ids.intersection(skill_ids))
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


def _status_collection_inventory(skills: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for skill in skills:
        source = skill.get("source") or {}
        if source.get("type") != "collection":
            continue
        name = str(source.get("collection") or "collection")
        group = groups.setdefault(
            name,
            {
                "name": name,
                "path": source.get("path"),
                "skills": 0,
                "approved": 0,
                "review_needed": 0,
                "lint_blocked": 0,
                "blocked": 0,
                "sample_ids": [],
            },
        )
        group["skills"] += 1
        trust = skill.get("trust")
        if trust in TRUSTED_STATES:
            group["approved"] += 1
        elif trust == "discovered":
            group["review_needed"] += 1
        elif trust == "lint_blocked":
            group["lint_blocked"] += 1
        elif trust == "blocked":
            group["blocked"] += 1
        if len(group["sample_ids"]) < 5:
            group["sample_ids"].append(skill["id"])
    items = [groups[name] for name in sorted(groups)]
    return {
        "count": len(items),
        "skill_count": sum(item["skills"] for item in items),
        "approved": sum(item["approved"] for item in items),
        "review_needed": sum(item["review_needed"] for item in items),
        "lint_blocked": sum(item["lint_blocked"] for item in items),
        "blocked": sum(item["blocked"] for item in items),
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
    print("  run `skillager setup --source collection` to review, then `skillager collection enable <name>` to create a project tag")


def _tag_trust_summaries(state_root: Path, catalog_root: Path, tags: list[str]) -> list[dict[str, Any]]:
    return [
        _tag_trust_summary(
            tag,
            _select_project_tag_skills(state_root, catalog_root, tag, include_blocked=True, include_lint_blocked=True),
        )
        for tag in tags
    ]


def _tag_trust_summary(tag: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    by_trust = Counter(str(skill.get("trust") or "unknown") for skill in skills)
    review_needed_ids = [skill["id"] for skill in skills if skill.get("trust") == "discovered"]
    lint_blocked_ids = [skill["id"] for skill in skills if skill.get("trust") == "lint_blocked"]
    blocked_ids = [skill["id"] for skill in skills if skill.get("trust") == "blocked"]
    approved = sum(count for trust, count in by_trust.items() if trust in TRUSTED_STATES)
    review_needed = by_trust.get("discovered", 0)
    lint_blocked = by_trust.get("lint_blocked", 0)
    blocked = by_trust.get("blocked", 0)
    active_buckets = sum(1 for count in by_trust.values() if count)
    return {
        "tag": project_tags.normalize_tag(tag),
        "skills": len(skills),
        "approved": approved,
        "review_needed": review_needed,
        "lint_blocked": lint_blocked,
        "blocked": blocked,
        "by_trust": dict(sorted(by_trust.items())),
        "mixed_trust": active_buckets > 1,
        "review_needed_ids": review_needed_ids[:10],
        "lint_blocked_ids": lint_blocked_ids[:10],
        "blocked_ids": blocked_ids[:10],
    }


def _tag_summary_line(summary: dict[str, Any]) -> str:
    parts = [
        f"{summary['tag']}: {summary['skills']} skill(s)",
        f"approved={summary['approved']}",
        f"review_needed={summary['review_needed']}",
    ]
    if summary.get("lint_blocked"):
        parts.append(f"lint_blocked={summary['lint_blocked']}")
    if summary.get("blocked"):
        parts.append(f"blocked={summary['blocked']}")
    return ", ".join(parts)


def _tag_available_summary(summary: dict[str, Any]) -> dict[str, Any]:
    pending = int(summary.get("review_needed") or 0) + int(summary.get("lint_blocked") or 0)
    blocked = int(summary.get("blocked") or 0)
    available = int(summary.get("approved") or 0)
    return {
        "tag": summary.get("tag"),
        "skills": int(summary.get("skills") or 0),
        "available": available,
        "pending_owner_review": pending,
        "unavailable": pending + blocked,
        "blocked": blocked,
    }


def _tag_available_summary_line(summary: dict[str, Any]) -> str:
    parts = [
        f"{summary['tag']}: {summary['skills']} skill(s)",
        f"available={summary['available']}",
    ]
    if summary.get("pending_owner_review"):
        parts.append(f"pending_owner_review={summary['pending_owner_review']}")
    if summary.get("blocked"):
        parts.append(f"blocked={summary['blocked']}")
    return ", ".join(parts)


def _print_tag_review_warning(summary: dict[str, Any], *, indent: str = "") -> None:
    if not (summary.get("review_needed") or summary.get("lint_blocked")):
        return
    print(
        f"{indent}warning: tag {summary['tag']} contains "
        f"{summary.get('review_needed', 0)} unreviewed and {summary.get('lint_blocked', 0)} lint-blocked skill(s); "
        "search and router materialization will use only available members."
    )
    print(f"{indent}review remaining tag members with: skillager setup --source collection")


def _print_tag_owner_review_note(summary: dict[str, Any], *, indent: str = "") -> None:
    pending = int(summary.get("review_needed") or 0) + int(summary.get("lint_blocked") or 0)
    if not pending:
        return
    print(
        f"{indent}note: {pending} tag member(s) need owner review before they become available. "
        "Ask the user to run `skillager setup --source collection` when they want to inspect them."
    )


def _status_tagging_summary(state_root: Path, catalog_root: Path) -> dict[str, Any]:
    tag_data = project_tags.load_tags(_current_project_dir())
    tags = {tag: entry.get("skills") or [] for tag, entry in (tag_data.get("tags") or {}).items()}
    attached_tags = sorted(tags)
    attached_summaries = _tag_trust_summaries(state_root, catalog_root, attached_tags)
    mixed_attached = [
        summary
        for summary in attached_summaries
        if summary.get("review_needed") or summary.get("lint_blocked")
    ]
    tagged_ids = {skill_id for skill_ids in tags.values() for skill_id in skill_ids}
    groups: dict[str, dict[str, Any]] = {}
    for skill in _effective_project_skills(state_root, catalog_root=catalog_root):
        if skill.get("trust") not in TRUSTED_STATES:
            continue
        if skill.get("id") in tagged_ids:
            continue
        source = skill.get("source", {})
        if source.get("type") != "collection":
            continue
        name = str(source.get("collection") or "collection")
        group = groups.setdefault(
            name,
            {
                "collection": name,
                "path": source.get("path"),
                "approved_untagged": 0,
                "sample_ids": [],
            },
        )
        group["approved_untagged"] += 1
        if len(group["sample_ids"]) < 5:
            group["sample_ids"].append(skill["id"])
    items = [groups[name] for name in sorted(groups)]
    return {
        "tag_count": len(tags),
        "attached_tag_summaries": attached_summaries,
        "mixed_trust_tag_count": len(mixed_attached),
        "mixed_trust_tags": mixed_attached,
        "approved_untagged_count": sum(item["approved_untagged"] for item in items),
        "approved_untagged_collections": items,
    }


def _status_message(
    review_needed: list[dict[str, Any]],
    *,
    lint_blocked: list[dict[str, Any]] | None = None,
    collection_summary: dict[str, Any] | None = None,
    migration_summary: dict[str, Any] | None = None,
    duplicate_content: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> str:
    if lint_blocked:
        return f"Skillager: {len(lint_blocked)} skill(s) are lint-blocked. Run `skillager lint` and fix the source or approve with an audited override."
    if migration_summary and migration_summary.get("pending"):
        totals = migration_summary.get("totals", {})
        return (
            "Skillager: collection skill ID migration pending. "
            f"{totals.get('id_migrations', 0)} ID(s) migrated, "
            f"{totals.get('needs_review', 0)} skill(s) need re-review, "
            f"{totals.get('tag_needs_repair', 0)} tag entries need repair. "
            "Run `skillager status --ack-migration` after reviewing."
        )
    if review_needed:
        duplicate_review = int((duplicate_content or {}).get("review_needed") or 0)
        if duplicate_review:
            return (
                f"Skillager: {len(review_needed)} skill(s) need owner review in the active setup scope; "
                f"{duplicate_review} same-content duplicate(s) already match approved content and need source-key approval. "
                "Ask the user to run `skillager setup`."
            )
        return f"Skillager: {len(review_needed)} skill(s) need owner review in the active setup scope. Ask the user to run `skillager setup`."
    if readiness and not readiness.get("handoff_ready") and (readiness.get("exposure") or {}).get("approved"):
        handoff = readiness.get("handoff") or {}
        if handoff.get("kind") == "agent-required":
            return "Skillager: review is complete. Run `skillager status --agent codex` or `skillager status --agent claude` to check working artifact readiness."
        command = handoff.get("command")
        if command:
            return f"Skillager: review is complete, but working artifacts need refresh. Run `{command}`."
        return f"Skillager: review is complete, but working artifacts need manual repair. {handoff.get('message', '').strip()}"
    if collection_summary and collection_summary.get("unattached_count"):
        return "Skillager: registered collections have no project tag. Run `skillager setup --source collection` to review, then `skillager collection enable <name>`."
    return "Skillager: no skills pending owner review. Use only available materialized skills."


def _print_status(status: dict[str, Any]) -> None:
    print(_style("Skillager status", "bold"))
    readiness = status.get("readiness") or {}
    exposure = readiness.get("exposure") or {}
    print(f"  - readiness: {'ready' if readiness.get('ready') else 'not ready'}")
    print(f"    review: {'ready' if readiness.get('review_ready') else 'needs review'}")
    print(f"    handoff: {_readiness_handoff_state(readiness)}")
    next_actions = _readiness_next_actions(readiness)
    if len(next_actions) == 1:
        print(f"    next: {next_actions[0]}")
    elif next_actions:
        print("    next:")
        for action in next_actions:
            print(f"      - {action}")
    print(f"    exposure: {_exposure_summary_text(exposure)}")
    if status.get("agent"):
        source = status.get("agent_source")
        source_suffix = " from saved setup scope" if source == "saved_setup_scope" else ""
        print(f"  - agent: {status['agent']}{source_suffix}")
    print(f"  - selected skills: {status['selected']}")
    print(f"  - available: {status['approved']}")
    if status.get("global_approved"):
        print(f"  - reusable global availability records: {status['global_approved']}")
    authored = status.get("authored_unreviewed") or {}
    if authored.get("count"):
        print(f"  - authored pending owner review: {authored['count']}")
    print(f"  - pending owner review: {status['review_needed']}")
    if status.get("lint_blocked"):
        print(f"  - lint blocked: {status['lint_blocked']} (run `skillager lint`)")
    _print_duplicate_content_status(status.get("duplicate_content") or {}, indent="  - ")
    manifest_lint = status.get("manifest_lint") or {}
    if manifest_lint.get("warned"):
        print(f"  - manifest lint warned: {manifest_lint['warned']}")
    print(f"  - blocked: {status['blocked']}")
    if status["skipped_global"]:
        print(f"  - skipped global: {status['skipped_global']} (use --include-global to include)")
    collections = status.get("collections") or {}
    if collections.get("count"):
        names = ", ".join(f"{item['name']}={item['skills']}" for item in collections.get("items", [])[:5])
        if collections.get("count", 0) > 5:
            names += f", ... {collections['count'] - 5} more"
        print(
            "  - registered collection repos: "
            f"{collections['count']} ({names}) - "
            f"{collections.get('attached_count', 0)} attached"
        )
        if collections.get("unattached_count"):
            print("    run `skillager setup --source collection` to review, then `skillager collection enable <name>`")
    collection_inventory = status.get("collection_inventory") or {}
    if collection_inventory.get("count"):
        names = ", ".join(f"{item['name']}={item['skills']}" for item in collection_inventory.get("items", [])[:5])
        if collection_inventory.get("count", 0) > 5:
            names += f", ... {collection_inventory['count'] - 5} more"
        print(
            "  - discovered collection skill repos: "
            f"{collection_inventory['count']} ({names})"
        )
    tagging = status.get("tagging") or {}
    if tagging.get("mixed_trust_tag_count"):
        names = ", ".join(
            f"{item['tag']}={item['review_needed']} unreviewed"
            for item in tagging.get("mixed_trust_tags", [])[:5]
        )
        print(f"  - attached tags needing review: {tagging['mixed_trust_tag_count']} ({names})")
    if tagging.get("approved_untagged_count"):
        names = ", ".join(
            f"{item['collection']}={item['approved_untagged']}"
            for item in tagging.get("approved_untagged_collections", [])[:5]
        )
        if len(tagging.get("approved_untagged_collections", [])) > 5:
            names += f", ... {len(tagging['approved_untagged_collections']) - 5} more"
        print(f"  - approved untagged collection skills: {tagging['approved_untagged_count']} ({names})")
    migrations = status.get("collection_migrations") or {}
    if migrations.get("pending"):
        totals = migrations.get("totals", {})
        print(
            "  - collection ID migration: "
            f"{totals.get('id_migrations', 0)} ID(s), "
            f"{totals.get('trust_migrated', 0)} trust entries migrated, "
            f"{totals.get('needs_review', 0)} skill(s) need re-review, "
            f"{totals.get('tag_migrated', 0)} tag entries migrated, "
            f"{totals.get('tag_needs_repair', 0)} tag entries need repair"
        )
        if totals.get("needs_review"):
            print("    skills modified since the last collection refresh are listed as needs-review")
        print("    run `skillager status --ack-migration` after reviewing")
    if status.get("migration_details"):
        _print_migration_details(migrations)
    scope = status.get("scope")
    if scope:
        scope_bits = []
        if scope.get("audience"):
            scope_bits.append(f"audience={scope['audience']}")
        if scope.get("selected_count") is not None:
            scope_bits.append(f"selected={scope['selected_count']}")
        if scope.get("paths"):
            scope_bits.append(f"paths={len(scope['paths'])}")
        if scope_bits:
            print(f"  - setup scope: {', '.join(scope_bits)}")
    materialized = status.get("materialized", {})
    if materialized:
        parts = [f"{agent}={count}" for agent, count in sorted(materialized.items())]
        print(f"  - materialized project skills: {', '.join(parts)}")
    if status.get("exposure_count"):
        print(f"  - exposure detail: {_exposure_summary_text(exposure)}")
    _print_inventory_block(status.get("inventory") or {}, indent="  - ")
    update = status.get("update") or {}
    if update.get("available"):
        print(f"  - update available: skillager {update.get('latest_version')} (run `{update.get('command')}`)")
    print()
    print(status["message"])


def _print_duplicate_content_status(duplicate_content: dict[str, Any], *, indent: str = "") -> None:
    review_needed = int(duplicate_content.get("review_needed") or 0)
    if not review_needed:
        return
    groups = int(duplicate_content.get("approved_overlap_groups") or 0)
    print(f"{indent}duplicate approved content: {review_needed} source-key approval(s) across {groups} group(s)")
    relevant_groups = [
        group
        for group in (duplicate_content.get("groups_detail") or [])
        if group.get("source_key_approval_required")
    ]
    for group in relevant_groups[:3]:
        review_ids = ", ".join(group.get("review_needed_ids") or [])
        approved_ids = ", ".join(group.get("approved_ids") or [])
        print(f"    {review_ids} matches approved {approved_ids}")
    if len(relevant_groups) > 3:
        print(f"    ... {len(relevant_groups) - 3} more duplicate group(s)")


def _print_migration_details(migrations: dict[str, Any]) -> None:
    collections = migrations.get("collections") or []
    if not collections:
        print("  - collection ID migration details: none")
        return
    print("  - collection ID migration details:")
    for outcome in collections:
        print(f"    {outcome.get('collection')}:")
        for item in outcome.get("id_migrations", []):
            print(f"      id: {item.get('old_id')} -> {item.get('new_id')}")
        for item in outcome.get("needs_review", []):
            new_id = item.get("new_id") or item.get("old_id")
            print(f"      needs review: {new_id} ({item.get('reason')})")
        for item in outcome.get("tag_needs_repair", []):
            candidates = ", ".join(item.get("candidate_ids") or [])
            print(f"      tag repair: {item.get('tag')} has {item.get('old_id')} candidates: {candidates}")


def _status_review_needed(skills: list[dict[str, Any]], *, saved_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [skill for skill in skills if skill.get("trust") == "discovered"]


def _authored_unreviewed(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        skill
        for skill in skills
        if skill.get("authored") and skill.get("trust") == "discovered"
    ]


def _status_scope_path(state_root: Path) -> Path:
    return state_root / "status_scope.json"


def _clear_fresh_project_state(state_root: Path, *, project_dir: Path) -> dict[str, Any]:
    return {
        "project_tags": project_tags.clear_tags(project_dir),
        "sessions": _clear_legacy_sessions(state_root),
        "status_scope": _clear_status_scope(state_root),
        "setup_state": _clear_setup_state(state_root),
    }


def _clear_legacy_sessions(state_root: Path) -> int:
    root = state_root / "sessions"
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"refusing to clear unsafe legacy sessions path: {root}")
    session_count = len(list(root.glob("sks_*.jsonl")))
    shutil.rmtree(root)
    return session_count


def _fresh_project_retained_global_state(catalog_root: Path, project_dir: Path) -> dict[str, Any]:
    tags = load_tags(catalog_root).get("tags") or {}
    return {
        "global_approvals": len(load_trust(catalog_root).get("global_approvals") or {}),
        "catalog_tags": len(tags),
        "catalog_tag_members": sum(len(skill_ids or []) for skill_ids in tags.values()),
        "collections": len(load_collections(catalog_root).get("collections") or {}),
        "materialized_skill_targets": len(_materialized_target_paths(project_dir, agents=["codex", "claude"])),
    }


def _clear_status_scope(state_root: Path) -> bool:
    path = _status_scope_path(state_root)
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"refusing to clear unsafe status scope path: {path}")
    path.unlink()
    return True


def _clear_setup_state(state_root: Path) -> bool:
    path = _working_setup_path(state_root)
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"refusing to clear unsafe setup state path: {path}")
    path.unlink()
    return True


def _load_status_scope(state_root: Path) -> dict[str, Any] | None:
    path = _status_scope_path(state_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _status_scan_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    by_risk = Counter((skill.get("scan") or {}).get("risk") or "unknown" for skill in skills)
    finding_count = sum(len((skill.get("scan") or {}).get("findings") or []) for skill in skills)
    return {
        "by_risk": dict(sorted(by_risk.items())),
        "finding_count": finding_count,
    }


def _status_manifest_lint_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter((skill.get("lint") or {}).get("status") or "ok" for skill in skills)
    finding_count = sum(len((skill.get("lint") or {}).get("findings") or []) for skill in skills)
    return {
        "by_status": dict(sorted(by_status.items())),
        "blocked": by_status.get("blocked", 0),
        "warned": by_status.get("warned", 0),
        "ok": by_status.get("ok", 0),
        "finding_count": finding_count,
    }


def _active_setup_paths(state_root: Path, explicit_paths: list[Path] | None = None) -> list[Path] | None:
    if explicit_paths:
        return None
    scope = _load_status_scope(state_root)
    if not scope:
        return None
    paths = []
    for raw in scope.get("paths") or []:
        if not isinstance(raw, str):
            continue
        path = Path(raw).expanduser()
        if path.exists():
            paths.append(path)
    return paths or None


def _remember_setup_paths(state_root: Path, paths: list[Path] | None) -> None:
    resolved = _serialize_setup_paths(paths)
    if not resolved:
        return
    data = _load_status_scope(state_root) or {"schema": "skillager.status-scope.v1"}
    data["paths"] = resolved
    state_root.mkdir(parents=True, exist_ok=True)
    _status_scope_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _serialize_setup_paths(paths: list[Path] | None) -> list[str]:
    if not paths:
        return []
    resolved = []
    for path in paths:
        try:
            resolved.append(str(path.expanduser().resolve()))
        except OSError:
            resolved.append(str(path.expanduser()))
    return sorted(dict.fromkeys(resolved))


def _save_status_scope(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    audience: str | None,
    include_global: bool,
    agents: list[str],
    paths: list[Path] | None = None,
) -> None:
    existing = _load_status_scope(state_root) or {}
    data = {
        "schema": "skillager.status-scope.v1",
        "audience": audience,
        "include_global": include_global,
        "agents": agents,
        "selected_count": len(skills),
        "baseline": {skill["id"]: skill.get("content_hash") for skill in skills if skill.get("id") and skill.get("content_hash")},
    }
    serialized_paths = _serialize_setup_paths(paths)
    if not serialized_paths:
        serialized_paths = [path for path in existing.get("paths", []) if isinstance(path, str)]
    if serialized_paths:
        data["paths"] = serialized_paths
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
        and not any((args.accept_low, args.trust_selected, args.block_high, args.override_lint))
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _prompt_setup_audience(state_root: Path, args: argparse.Namespace, *, catalog_root: Path | None = None) -> str | None:
    catalog_root = catalog_root or state_root
    data = build_index(
        state_root,
        args.paths or None,
        include_packages=not args.no_packages,
        approval_root=catalog_root,
        extra_paths=_active_setup_paths(state_root, args.paths or None),
    )
    extra_skills = _project_tag_collection_skills(
        state_root,
        catalog_root=catalog_root,
        project_dir=_current_project_dir(),
        include_lint_blocked=True,
    )
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    skills = select_visible_skills(
        data.get("skills", []),
        source=args.source,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_lint_blocked=True,
        include_global=args.include_global,
    )
    counts: dict[str, int] = {}
    for skill in skills:
        audience = audience_bucket(skill)
        counts[audience] = counts.get(audience, 0) + 1
    if len([count for count in counts.values() if count]) <= 1:
        return None

    print(_style("Audience scope", "bold"))
    print("  This setup selection spans declared audiences and undeclared skills.")
    print("  Choose all when setting up before a specific task is known; agents can narrow after asking the user goal.")
    for audience, count in sorted(counts.items()):
        print(f"    - {audience_bucket_label(audience)}: {count}")
    while True:
        print("  1. Declared user-facing skills")
        print("  2. Declared dev/maintainer skills")
        print("  3. Everything else (no declared audience)")
        print("  4. All selected skills")
        print("  5. Cancel setup")
        choice = _interactive_input("> ").strip().lower()
        if choice == "1" or choice == "user":
            return "user"
        if choice == "2" or choice in {"dev", "developer", "maintainer"}:
            return "dev"
        if choice == "3" or choice in {AUDIENCE_OTHER, "unknown", "undeclared", "everything_else", "everything-else", "everything else"}:
            return AUDIENCE_OTHER
        if choice == "4" or choice in {"both", "all"}:
            return None
        if choice == "5" or choice in {"q", "quit", "exit", "cancel"}:
            return "__cancel__"
        print("Enter a listed number, user, dev, everything else, all, or cancel.")


def cmd_index(args: argparse.Namespace) -> int:
    data = build_index(
        root(args),
        args.paths or None,
        include_packages=not args.no_packages,
        approval_root=catalog_root(args),
        extra_paths=_active_setup_paths(root(args), args.paths or None),
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Indexed {len(data['skills'])} skills")
        if data.get("errors"):
            _print_discovery_errors(data["errors"])
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if args.json and args.summary_json:
        raise ValueError("--json and --summary-json cannot be combined")
    skills = _effective_project_skills(
        root(args),
        catalog_root=catalog_root(args),
    )
    skills = _available_skills(skills)
    if not args.include_global and not args.source:
        skills = [skill for skill in skills if skill.get("source", {}).get("type") != "global"]
    if args.no_packages and not args.source:
        skills = [skill for skill in skills if skill.get("source", {}).get("type") != "python-package"]
    skills = [_skill for _skill in skills if _matches_filters(_skill, args)]
    source_entry_count = len(skills)
    if args.agent:
        skills = _collapse_agent_variant_results(skills, args.agent)
    elif args.summary_json:
        skills = _annotate_agent_variants(skills, args.agent)
    if args.agent:
        skills = _sort_agent_variant_inventory(skills, args.agent)
    if args.summary_json:
        print(json.dumps(_inventory_summary(skills, agent=args.agent, source_entry_count=source_entry_count), indent=2, sort_keys=True))
    elif args.json:
        payload = skills if args.full_json else [_compact_skill_metadata(skill, agent=args.agent) for skill in skills]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for skill in skills:
            print(f"{skill['id']}\t{skill.get('activation', '-')}\t{skill['source'].get('type')}\t{skill.get('summary', '-')}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if args.tag:
        tag_key = project_tags.normalize_tag(args.tag)
        skills = []
        for skill in _select_project_tag_skills(
            root(args),
            catalog_root(args),
            args.tag,
        ):
            item = dict(skill)
            availability = set(item.get("availability", []))
            availability.add("attached-tag")
            item["availability"] = sorted(availability)
            item["tags"] = sorted(set(item.get("tags", [])) | {tag_key})
            skills.append(item)
    else:
        skills = _effective_project_skills(
            root(args),
            catalog_root=catalog_root(args),
        )
        if not args.include_global:
            skills = [skill for skill in skills if skill.get("source", {}).get("type") != "global"]
    skills = _available_skills(skills)
    if args.agent:
        skills = _collapse_agent_variant_results(skills, args.agent)
    results = search_index(
        skills,
        args.query,
        include_untrusted=False,
    )
    if args.compatible_only:
        if not args.agent:
            raise ValueError("--compatible-only requires --agent")
        results = [skill for skill in results if compatibility_problem(skill, args.agent) is None]
    if args.limit < 0:
        raise ValueError("--limit must be 0 or greater")
    if args.agent:
        results = _sort_agent_variant_search(results, args.agent)
    if args.limit:
        results = results[: args.limit]
    if args.json:
        payload = results if args.full_json else [_compact_search_result(skill, agent=args.agent) for skill in results]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for skill in results:
            print(f"{skill['score']}\t{skill['id']}\t{skill['summary']}")
    return 0


def _is_available_skill(skill: dict[str, Any]) -> bool:
    return skill.get("trust") in TRUSTED_STATES


def _available_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill for skill in skills if _is_available_skill(skill)]


def _compact_search_result(skill: dict[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    compatibility = skill.get("compatibility") or {}
    problem = compatibility_problem(skill, agent)
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
        "available": True,
        "score": skill.get("score"),
        "score_detail": skill.get("score_detail"),
        "reasons": skill.get("reasons", []),
        "source": skill.get("source", {}),
        "availability": skill.get("availability", []),
        "exposure": skill.get("exposure", "hidden"),
        "materialized_targets": skill.get("materialized_targets", []),
        "tags": skill.get("tags", []),
        "source_root": (skill.get("source") or {}).get("path") or skill.get("root"),
        "entrypoint": skill.get("entrypoint"),
        "agent_hint": skill.get("agent_hint") or _agent_hint(skill),
        "agent_variant": skill.get("agent_variant"),
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


def _compact_skill_metadata(skill: dict[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    compatibility = skill.get("compatibility") or {}
    problem = compatibility_problem(skill, agent)
    payload: dict[str, Any] = {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
        "available": True,
        "source": skill.get("source", {}),
        "availability": skill.get("availability", []),
        "activation": skill.get("activation"),
        "exposure": skill.get("exposure", "hidden"),
        "materialized_targets": skill.get("materialized_targets", []),
        "tags": skill.get("tags", []),
        "source_root": (skill.get("source") or {}).get("path") or skill.get("root"),
        "entrypoint": skill.get("entrypoint"),
        "agent_hint": skill.get("agent_hint") or _agent_hint(skill),
        "agent_variant": skill.get("agent_variant"),
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
    return payload


def _inventory_summary(
    skills: list[dict[str, Any]],
    *,
    agent: str | None = None,
    source_entry_count: int | None = None,
) -> dict[str, Any]:
    annotated = _annotate_agent_variants(skills, agent)
    source_counts = Counter((skill.get("source") or {}).get("type") or "unknown" for skill in annotated)
    exposure_counts = Counter(skill.get("exposure") or "hidden" for skill in annotated)
    availability_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    for skill in annotated:
        availability_counts.update(skill.get("availability") or [])
        tag_counts.update(skill.get("tags") or [])
    return {
        "schema": "skillager.inventory-summary.v1",
        "agent": agent,
        "total": len(annotated),
        "total_label": "agent-visible choices" if agent else "inventory entries",
        "source_entry_count": source_entry_count if source_entry_count is not None else len(annotated),
        "variant_collapse": {
            "applied": bool(agent),
            "before": source_entry_count if source_entry_count is not None else len(annotated),
            "after": len(annotated),
            "basis": "native-agent variants collapse to one preferred agent-visible choice when --agent is set",
        },
        "counts": {
            "by_source": dict(sorted(source_counts.items())),
            "by_exposure": dict(sorted(exposure_counts.items())),
            "by_availability": dict(sorted(availability_counts.items())),
        },
        "tags": [
            {"tag": tag, "count": count}
            for tag, count in sorted(tag_counts.items())
        ],
        "sources": _inventory_source_groups(annotated),
        "duplicate_families": _agent_variant_families(annotated, agent=agent),
        "skills": [_compact_inventory_item(skill) for skill in annotated],
        "search_command": "skillager search \"<query>\" --json",
    }


def _compact_inventory_item(skill: dict[str, Any]) -> dict[str, Any]:
    source = skill.get("source") or {}
    return {
        "id": skill.get("id"),
        "name": skill.get("name"),
        "summary": skill.get("summary"),
        "available": True,
        "source": {
            key: value
            for key, value in source.items()
            if key in {"type", "collection", "package", "agent"}
        },
        "availability": skill.get("availability", []),
        "exposure": skill.get("exposure", "hidden"),
        "tags": skill.get("tags", []),
        "agent_hint": skill.get("agent_hint") or _agent_hint(skill),
        "agent_variant": skill.get("agent_variant"),
    }


def _inventory_source_groups(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for skill in skills:
        source = skill.get("source") or {}
        label = str(source.get("collection") or source.get("package") or source.get("type") or "unknown")
        group = groups.setdefault(label, {"source": label, "count": 0, "ids": []})
        group["count"] += 1
        group["ids"].append(skill.get("id"))
    return [
        {"source": label, "count": item["count"], "ids": sorted(item["ids"])}
        for label, item in sorted(groups.items())
    ]


def _annotate_agent_variants(skills: list[dict[str, Any]], agent: str | None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for skill in skills:
        key = _agent_variant_family_key(skill)
        if key:
            groups[key].append(skill)
    preferred: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        if len(group) > 1:
            preferred[key] = sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent))[0]
    annotated = []
    for skill in skills:
        item = dict(skill)
        item["agent_hint"] = _agent_hint(item)
        key = _agent_variant_family_key(item)
        group = groups.get(key, [])
        if key and len(group) > 1:
            preferred_item = preferred[key]
            variants = sorted(
                (
                    {
                        "id": variant.get("id"),
                        "agent_hint": _agent_hint(variant),
                        "source_type": (variant.get("source") or {}).get("type"),
                        "entrypoint": variant.get("entrypoint"),
                    }
                    for variant in group
                ),
                key=lambda variant: (str(variant.get("id") or ""), str(variant.get("entrypoint") or "")),
            )
            item["agent_variant"] = {
                "family_key": key,
                "agent": agent,
                "preferred_id": preferred_item.get("id"),
                "is_preferred": _same_skill_variant(item, preferred_item),
                "alternatives": variants,
                "policy": "rank matching native-agent variants first when duplicates exist; do not hide alternatives",
            }
        annotated.append(item)
    return annotated


def _collapse_agent_variant_results(skills: list[dict[str, Any]], agent: str) -> list[dict[str, Any]]:
    collapsed = []
    for skill in _annotate_agent_variants(skills, agent):
        variant = skill.get("agent_variant") or {}
        if variant and not variant.get("is_preferred"):
            continue
        if variant:
            variant["policy"] = "agent-scoped result collapsed to the preferred variant; alternatives are listed for inspection"
        collapsed.append(skill)
    return collapsed


def _sort_agent_variant_search(skills: list[dict[str, Any]], agent: str) -> list[dict[str, Any]]:
    return sorted(
        skills,
        key=lambda skill: (
            -int(skill.get("score") or 0),
            _visibility_rank_for_cli(skill),
            _agent_variant_rank(skill, agent),
            str(skill.get("id") or ""),
        ),
    )


def _sort_agent_variant_inventory(skills: list[dict[str, Any]], agent: str) -> list[dict[str, Any]]:
    return sorted(
        skills,
        key=lambda skill: (
            _agent_variant_family_key(skill) or str(skill.get("id") or ""),
            _agent_variant_rank(skill, agent),
            str(skill.get("id") or ""),
        ),
    )


def _agent_variant_families(skills: list[dict[str, Any]], *, agent: str | None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for skill in skills:
        key = _agent_variant_family_key(skill)
        if key:
            groups[key].append(skill)
    families = []
    for key, group in sorted(groups.items()):
        if len(group) <= 1:
            continue
        preferred = sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent))[0]
        families.append(
            {
                "family_key": key,
                "agent": agent,
                "preferred_id": preferred.get("id"),
                "variants": [
                    {
                        "id": skill.get("id"),
                        "agent_hint": _agent_hint(skill),
                        "source_type": (skill.get("source") or {}).get("type"),
                        "entrypoint": skill.get("entrypoint"),
                    }
                    for skill in sorted(group, key=lambda item: str(item.get("id") or ""))
                ],
            }
        )
    return families


def _agent_variant_rank(skill: dict[str, Any], agent: str | None) -> int:
    variant = skill.get("agent_variant") or {}
    if not variant:
        return 0
    hint = skill.get("agent_hint") or _agent_hint(skill)
    if agent and hint == agent:
        return 0
    if hint is None:
        return 1
    return 2


def _agent_variant_preference_key(skill: dict[str, Any], agent: str | None) -> tuple[int, int, str]:
    hint = _agent_hint(skill)
    if agent and hint == agent:
        agent_rank = 0
    elif hint is None:
        agent_rank = 1
    else:
        agent_rank = 2
    return (agent_rank, _visibility_rank_for_cli(skill), str(skill.get("id") or ""))


def _visibility_rank_for_cli(skill: dict[str, Any]) -> int:
    exposure = skill.get("exposure")
    if exposure == "multiple":
        return 0
    if exposure == "native":
        return 1
    if exposure == "stub":
        return 2
    if exposure == "router":
        return 3
    if "attached-tag" in set(skill.get("availability", [])):
        return 4
    source_type = (skill.get("source") or {}).get("type")
    if source_type == "project":
        return 5
    if source_type == "collection":
        return 6
    if source_type == "python-package":
        return 7
    return 8


def _agent_variant_family_key(skill: dict[str, Any]) -> str:
    return agent_variant_family_key(skill)


def _canonical_agent_variant_slug(value: str) -> str:
    return canonical_agent_variant_slug(value)


def _same_skill_variant(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("id") == right.get("id") and left.get("entrypoint") == right.get("entrypoint")


def cmd_show(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args), include_lint_blocked=True)
    if not _is_available_skill(skill):
        raise ValueError(f"skill is not available: {args.skill_id}; ask the user to run `skillager setup`")
    if skill.get("trust") == "lint_blocked" and args.content:
        raise ValueError(f"skill content is not available while lint-blocked: {args.skill_id}")
    if args.content and skill.get("trust") not in {"reviewed", "trusted", "pinned"}:
        raise ValueError(f"skill content is not available: {args.skill_id}; {_approval_hint(skill)}")
    if args.json:
        payload: dict[str, Any] = {"skill": skill if args.full_json else _compact_skill_metadata(skill)}
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
    skill = _find_project_skill(
        root(args),
        args.skill_id,
        catalog_root=catalog_root(args),
        include_collection_inventory=bool(args.from_router),
        include_lint_blocked=True,
    )
    if skill.get("trust") == "lint_blocked":
        raise ValueError(f"skill is lint-blocked: {args.skill_id}; run skillager lint and fix the source or approve with --override-lint --reason")
    if args.from_router:
        _validate_router_activation(root(args), catalog_root(args), args.from_router, skill)
    if args.from_stub:
        _validate_stub_activation(skill, args.from_stub)
    if skill.get("trust") == "blocked" and not args.force:
        raise ValueError(f"skill is blocked: {args.skill_id}")
    if skill.get("trust") == "discovered" and not args.force:
        raise ValueError(f"skill is not available: {args.skill_id}; {_approval_hint(skill)}")
    activation_agent = _activation_agent(args, skill)
    problem = compatibility_problem(skill, activation_agent)
    if problem and not args.allow_incompatible:
        raise ValueError(f"skill is {problem}; use --allow-incompatible only with explicit user approval")
    for warning in compatibility_warnings(skill, activation_agent):
        print(f"warning: {warning}", file=sys.stderr)
    print(render_skill(skill, fmt=args.format))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    reports = []
    if args.all:
        for skill in load_index(root(args), approval_root=catalog_root(args), persist_missing=False).get("skills", []):
            report = scan_path(Path(skill["root"]), allow_tools=False)
            reports.append({"skill_id": skill["id"], **report})
    elif args.target:
        target = Path(args.target)
        if target.exists():
            reports.append({"path": str(target), **scan_path(target)})
        else:
            skill = find_skill(root(args), args.target, approval_root=catalog_root(args))
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


def cmd_lint(args: argparse.Namespace) -> int:
    skills = _effective_project_skills(root(args), catalog_root=catalog_root(args), include_lint_blocked=True)
    if not args.include_global:
        skills = [skill for skill in skills if skill.get("source", {}).get("type") != "global"]
    if args.skill_id:
        skills = [skill for skill in skills if skill.get("id") == args.skill_id]
        if not skills:
            raise KeyError(f"skill not found: {args.skill_id}")
    else:
        skills = [skill for skill in skills if (skill.get("lint") or {}).get("status") in {"warned", "blocked"}]
    reports = [
        {
            "skill_id": skill.get("id"),
            "root": skill.get("root"),
            "manifest_path": skill.get("manifest_path"),
            "trust": skill.get("trust"),
            "lint": skill.get("lint", {"status": "ok", "findings": []}),
        }
        for skill in skills
    ]
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
    else:
        if not reports:
            print("No manifest lint findings.")
            return 0
        for report in reports:
            lint = report["lint"]
            print(f"{report['skill_id']}: lint={lint.get('status')} trust={report.get('trust')}")
            for item in lint.get("findings", []):
                print(f"  - {item.get('severity')} {item.get('code')} {item.get('field')}: {item.get('detail')}")
    return 0


def _effective_project_skills(
    state_root: Path,
    *,
    catalog_root: Path | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    project_dir: Path | None = None,
) -> list[dict[str, Any]]:
    catalog_root = catalog_root or state_root
    project_dir = (project_dir or _current_project_dir()).resolve()
    by_id = _base_project_skill_map(state_root, catalog_root=catalog_root, project_dir=project_dir)
    tag_membership = _project_tag_membership(project_dir)
    for skill in _project_tag_collection_skills(
        state_root,
        catalog_root=catalog_root,
        project_dir=project_dir,
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    ):
        item = dict(skill)
        item["availability"] = sorted(set(item.get("availability", [])) | {"attached-tag"})
        _merge_skill_inventory(by_id, item)
    for skill_id, tags in tag_membership.items():
        item = by_id.get(skill_id)
        if not item:
            continue
        item["tags"] = sorted(set(item.get("tags", [])) | tags)
        item["availability"] = sorted(set(item.get("availability", [])) | {"attached-tag"})
    return [by_id[skill_id] for skill_id in sorted(by_id)]


def _base_project_skill_map(state_root: Path, *, catalog_root: Path, project_dir: Path) -> dict[str, dict[str, Any]]:
    exposure = _project_exposure(project_dir)
    extra_paths = _active_setup_paths(state_root)
    if extra_paths:
        data = build_index(state_root, include_packages=True, approval_root=catalog_root, extra_paths=extra_paths, persist=False)
    else:
        data = load_index(state_root, approval_root=catalog_root, persist_missing=False)
    by_id: dict[str, dict[str, Any]] = {}
    for skill in data.get("skills", []):
        item = _with_project_inventory_fields(skill, exposure)
        by_id[item["id"]] = item
    return by_id


def _project_tag_collection_skills(
    state_root: Path,
    *,
    catalog_root: Path,
    project_dir: Path,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
) -> list[dict[str, Any]]:
    tag_membership = _project_tag_membership(project_dir)
    if not tag_membership:
        return []
    tag_ids = set(tag_membership)
    exposure = _project_exposure(project_dir)
    by_id: dict[str, dict[str, Any]] = {}
    for skill in select_collection_skills(
        catalog_root,
        trust_root=state_root,
        approval_root=catalog_root,
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    ):
        if skill.get("id") not in tag_ids:
            continue
        item = _with_project_inventory_fields(skill, exposure)
        item["tags"] = sorted(set(item.get("tags", [])) | tag_membership.get(item["id"], set()))
        _merge_skill_inventory(by_id, item)
    return [by_id[skill_id] for skill_id in sorted(by_id)]


def _project_tag_membership(project_dir: Path) -> dict[str, set[str]]:
    membership: dict[str, set[str]] = {}
    for tag, entry in project_tags.load_tags(project_dir).get("tags", {}).items():
        for skill_id in entry.get("skills") or []:
            membership.setdefault(str(skill_id), set()).add(str(tag))
    return membership


def _project_tag_names(project_dir: Path) -> list[str]:
    return sorted(project_tags.load_tags(project_dir).get("tags", {}))


def _all_taggable_skill_map(state_root: Path, catalog_root: Path, project_dir: Path) -> dict[str, dict[str, Any]]:
    by_id = _base_project_skill_map(state_root, catalog_root=catalog_root, project_dir=project_dir)
    exposure = _project_exposure(project_dir)
    for skill in select_collection_skills(
        catalog_root,
        trust_root=state_root,
        approval_root=catalog_root,
        include_blocked=True,
        include_lint_blocked=True,
    ):
        item = _with_project_inventory_fields(skill, exposure)
        _merge_skill_inventory(by_id, item)
    return by_id


def _validate_taggable_skill_ids(state_root: Path, catalog_root: Path, project_dir: Path, skill_ids: list[str]) -> list[str]:
    if not skill_ids:
        return []
    candidates = _all_taggable_skill_map(state_root, catalog_root, project_dir)
    missing = []
    unavailable = []
    for skill_id in sorted(dict.fromkeys(skill_ids)):
        skill = candidates.get(skill_id)
        if not skill:
            missing.append(skill_id)
            continue
        trust = skill.get("trust")
        if trust not in TRUSTED_STATES:
            unavailable.append((skill_id, trust or "unknown"))
    if missing:
        raise KeyError(f"skill not found in collection catalog or current project inventory: {missing[0]}")
    if unavailable:
        skill_id, trust = unavailable[0]
        raise ValueError(f"skill is not available for tagging ({trust}): {skill_id}; owner review first")
    return sorted(dict.fromkeys(skill_ids))


def _select_project_tag_skills(
    state_root: Path,
    catalog_root: Path,
    tag: str,
    *,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
) -> list[dict[str, Any]]:
    project_dir = _current_project_dir()
    tag_key = project_tags.normalize_tag(tag)
    tag_ids = set(project_tags.tag_skills(project_dir, tag_key))
    if not tag_ids:
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for skill in _all_taggable_skill_map(state_root, catalog_root, project_dir).values():
        if skill.get("id") not in tag_ids:
            continue
        if skill.get("trust") == "blocked" and not include_blocked:
            continue
        if skill.get("trust") == "lint_blocked" and not include_lint_blocked:
            continue
        item = dict(skill)
        item["tags"] = sorted(set(item.get("tags", [])) | {tag_key})
        item["availability"] = sorted(set(item.get("availability", [])) | {"attached-tag"})
        _merge_skill_inventory(by_id, item)
    return [by_id[skill_id] for skill_id in sorted(by_id)]


def _project_tag_reference_report(state_root: Path, catalog_root: Path, tag: str) -> list[dict[str, Any]]:
    project_dir = _current_project_dir()
    tag_key = project_tags.normalize_tag(tag)
    skill_ids = project_tags.tag_skills(project_dir, tag_key)
    candidates = _all_taggable_skill_map(state_root, catalog_root, project_dir)
    report = []
    for skill_id in skill_ids:
        skill = candidates.get(skill_id)
        if not skill:
            report.append({"id": skill_id, "status": "missing", "note": "not found in collection catalog or current project inventory"})
            continue
        trust = skill.get("trust") or "unknown"
        item = {"id": skill_id, "status": trust}
        if trust == "blocked":
            item["note"] = "blocked by owner; materialize and activation skip this reference"
        elif trust == "lint_blocked":
            item["note"] = "lint-blocked; owner review or source fix required"
        elif trust == "discovered":
            item["note"] = "not available until owner review"
        report.append(item)
    return report


def _merge_skill_inventory(by_id: dict[str, dict[str, Any]], item: dict[str, Any]) -> None:
    skill_id = item["id"]
    if skill_id not in by_id:
        by_id[skill_id] = item
        return
    existing = dict(by_id[skill_id])
    existing["availability"] = sorted(set(existing.get("availability", [])) | set(item.get("availability", [])))
    existing["tags"] = sorted(set(existing.get("tags", [])) | set(item.get("tags", [])))
    targets = {target.get("path"): target for target in existing.get("materialized_targets", []) if target.get("path")}
    for target in item.get("materialized_targets", []):
        path = target.get("path")
        if path:
            targets[path] = target
    if targets:
        existing["materialized_targets"] = [targets[path] for path in sorted(targets)]
    existing["trust"] = item.get("trust", existing.get("trust"))
    existing["exposure"] = item.get("exposure", existing.get("exposure", "hidden"))
    by_id[skill_id] = existing


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
                    target["tag"] = data.get("tag")
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
    include_lint_blocked: bool = False,
) -> dict[str, Any]:
    catalog_root = catalog_root or state_root
    skills = _effective_project_skills(state_root, catalog_root=catalog_root, include_lint_blocked=include_lint_blocked)
    if include_collection_inventory:
        skills.extend(
            select_collection_skills(
                catalog_root,
                trust_root=state_root,
                approval_root=catalog_root,
                include_lint_blocked=include_lint_blocked,
            )
        )
    for skill in skills:
        if skill.get("trust") == "lint_blocked" and not include_lint_blocked:
            continue
        if skill.get("id") == skill_id:
            return skill
    raise KeyError(f"skill not found: {skill_id}")


def _approval_hint(skill: dict[str, Any]) -> str:
    skill_id = skill.get("id") or "<skill-id>"
    if skill.get("authored") and skill.get("scan", {}).get("risk") == "low":
        return f"to approve authored skill after review: skillager trust {skill_id} --state reviewed"
    return f"review first: skillager review {skill_id}"


def _require_attached_tag(state_root: Path, tag: str) -> None:
    if project_tags.normalize_tag(tag) not in project_tags.load_tags(_current_project_dir()).get("tags", {}):
        raise ValueError(f"tag is not attached to this project: {tag}")


def _validate_router_activation(state_root: Path, catalog_root: Path, router: str, skill: dict[str, Any]) -> None:
    tag = _tag_from_router(router)
    _require_attached_tag(state_root, tag)
    allowed = {
        item["id"]
        for item in _select_project_tag_skills(state_root, catalog_root, tag)
        if item.get("trust") in {"reviewed", "trusted", "pinned"}
    }
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


def _title_from_slug(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split("-") if part)


def cmd_trust(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args), include_lint_blocked=True)
    if skill.get("scan", {}).get("risk") == "high":
        print(f"warning: trusting high-risk skill {args.skill_id}", file=sys.stderr)
    lint_override = make_lint_override(args.reason or "", skill.get("lint") or {}) if args.override_lint else None
    record = set_trust(
        root(args),
        args.skill_id,
        args.state,
        skill["content_hash"],
        skill["source"],
        lint=skill.get("lint"),
        lint_override=lint_override,
        approval_key=skill.get("approval_key"),
        approval_root=catalog_root(args),
        global_scope=not args.project_only,
    )
    print(f"{args.skill_id}: {record['state']}")
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    skill = _find_project_skill(root(args), args.skill_id, catalog_root=catalog_root(args), include_lint_blocked=True)
    record = set_trust(root(args), args.skill_id, "blocked", skill["content_hash"], skill["source"], lint=skill.get("lint"))
    print(f"{args.skill_id}: {record['state']}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    args.yolo = bool(args.yolo or args.trust_all)
    data = load_index(root(args), approval_root=catalog_root(args))
    extra_skills = _review_extra_skills(args)
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    review_include_lint_blocked = args.include_lint_blocked or args.override_lint or args.yolo
    skills = select_visible_skills(
        data.get("skills", []),
        skill_ids=args.skill_ids,
        source=args.source,
        audience=args.audience,
        package=args.package,
        activation=args.activation,
        include_blocked=args.include_blocked,
        include_lint_blocked=review_include_lint_blocked,
        include_global=args.include_global,
    )
    duplicate_context = select_visible_skills(
        data.get("skills", []),
        include_blocked=args.include_blocked,
        include_lint_blocked=review_include_lint_blocked,
        include_global=args.include_global,
    )
    skills = annotate_duplicate_content(skills, context=duplicate_context)
    summary = review_summary(skills)
    action = apply_review_action(
        root(args),
        skills,
        accept_low=args.accept_low,
        yolo=args.yolo,
        trust_state=args.trust_selected,
        block_high=args.block_high,
        override_lint=args.override_lint,
        reason=args.reason,
        approval_root=catalog_root(args),
        global_scope=not args.project_only,
    )
    if action["changed"]:
        data = load_index(root(args), approval_root=catalog_root(args))
        extra_skills = _review_extra_skills(args)
        if extra_skills:
            data["skills"] = [*data.get("skills", []), *extra_skills]
        skills = select_visible_skills(
            data.get("skills", []),
            skill_ids=args.skill_ids,
            source=args.source,
            audience=args.audience,
            package=args.package,
            activation=args.activation,
            include_blocked=args.include_blocked or args.block_high,
            include_lint_blocked=True,
            include_global=args.include_global,
        )
        duplicate_context = select_visible_skills(
            data.get("skills", []),
            include_blocked=args.include_blocked or args.block_high,
            include_lint_blocked=True,
            include_global=args.include_global,
        )
        skills = annotate_duplicate_content(skills, context=duplicate_context)
        summary = review_summary(skills)
    if args.json:
        print(json.dumps({"selected": skills, "summary": summary, "action": action}, indent=2, sort_keys=True))
    else:
        _print_review_report(skills, summary, action, compact=args.summary)
    return 0


def _review_extra_skills(args: argparse.Namespace) -> list[dict[str, Any]]:
    if getattr(args, "source", None) == "collection":
        return select_collection_skills(
            catalog_root(args),
            trust_root=root(args),
            approval_root=catalog_root(args),
            include_blocked=getattr(args, "include_blocked", False),
            include_lint_blocked=True,
        )
    return _project_tag_collection_skills(
        root(args),
        catalog_root=catalog_root(args),
        project_dir=_current_project_dir(),
        include_lint_blocked=True,
    )


def cmd_materialize(args: argparse.Namespace) -> int:
    mode = args.mode
    if mode == "router" and not args.tag:
        raise ValueError("--mode router requires --tag")
    _require_materialize_selection(args)
    agents = ["codex", "claude"] if args.all_agents else args.agent or ["codex"]
    agent_notes_ready_before = _agent_notes_ready(Path.cwd(), agents=agents) if args.scope == "project" else False
    materialized_targets_before = _materialized_target_paths(Path.cwd(), agents=agents) if args.scope == "project" else set()
    if args.tag and mode == "router":
        _require_attached_tag(root(args), args.tag)
        skills = _select_project_tag_skills(root(args), catalog_root(args), args.tag)
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
        if args.tag:
            _require_attached_tag(root(args), args.tag)
        tag_skill_ids = {skill["id"] for skill in _select_project_tag_skills(root(args), catalog_root(args), args.tag)} if args.tag else None
        inventory = _effective_project_skills(
            root(args),
            catalog_root=catalog_root(args),
            include_blocked=args.include_blocked,
            include_lint_blocked=True,
        )
        skills = select_visible_skills(
            inventory,
            skill_ids=args.skill_ids,
            source=args.source,
            audience=args.audience,
            package=args.package,
            activation=args.activation,
            include_blocked=args.include_blocked,
        )
        if tag_skill_ids is not None:
            skills = [skill for skill in skills if skill["id"] in tag_skill_ids]
        _require_materialize_matches(args.skill_ids, inventory, skills, tag_skill_ids=tag_skill_ids)
        results = materialize_skills(
            skills,
            agents=agents,
            scope=args.scope,
            mode=mode,
            dry_run=args.dry_run,
            force=args.force,
            reviewed_only=not args.include_unreviewed,
            project_dir=Path.cwd(),
            allow_incompatible=args.allow_incompatible,
        )
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        _print_materialize_results(results)
        if args.scope == "project" and mode == "router" and args.tag and not args.dry_run:
            _print_router_verification(args.tag, agents, results)
        if args.scope == "project" and not args.dry_run and any(item.get("status") == "materialized" for item in results):
            saved_scope = _load_status_scope(root(args))
            if saved_scope is None or saved_scope.get("selected_count") is None:
                _save_status_scope(
                    root(args),
                    skills,
                    audience=args.audience or _common_audience(skills),
                    include_global=False,
                    agents=_materialized_agents(results),
                    paths=None,
                )
            if _should_print_agent_next_steps(
                results,
                agent_notes_ready_before=agent_notes_ready_before,
                materialized_targets_before=materialized_targets_before,
            ):
                _print_agent_next_steps(results)
    return 0


def _require_materialize_selection(args: argparse.Namespace) -> None:
    if args.tag or args.skill_ids or args.all_reviewed:
        if args.all_reviewed and args.include_unreviewed:
            raise ValueError("--all-reviewed cannot be combined with --include-unreviewed")
        return
    raise ValueError(
        "materialize requires explicit skill IDs, --tag, or --all-reviewed. "
        "To refresh first-party working artifacts, run skillager bootstrap --agent <agent>."
    )


def _require_materialize_matches(
    requested_ids: list[str],
    inventory: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    *,
    tag_skill_ids: set[str] | None,
) -> None:
    if not requested_ids:
        return
    inventory_ids = {skill["id"] for skill in inventory}
    selected_ids = {skill["id"] for skill in selected}
    for skill_id in requested_ids:
        if skill_id not in inventory_ids:
            raise KeyError(f"skill not found: {skill_id}")
        if tag_skill_ids is not None and skill_id not in tag_skill_ids:
            raise ValueError(f"skill is not listed by the selected tag: {skill_id}")
        if skill_id not in selected_ids:
            raise ValueError(f"skill is not selectable with the requested filters: {skill_id}")


def _print_materialize_results(results: list[dict[str, Any]]) -> None:
    for item in results:
        if _suppress_materialize_result(item):
            continue
        line = f"{item['skill_id']}: {item['status']}"
        if item.get("target"):
            line += f" {item['target']}"
        if item.get("reason"):
            line += f" ({item['reason']})"
        print(line)


def _suppress_materialize_result(item: dict[str, Any]) -> bool:
    if item.get("skill_id") != "skillager/working":
        return False
    if item.get("status") == "materialized":
        return True
    return item.get("status") == "skipped" and item.get("reason") == "already up to date"


def _print_router_verification(tag: str, agents: list[str], results: list[dict[str, Any]]) -> None:
    current_agents = sorted(
        agent
        for agent in {item.get("agent") for item in results if item.get("skill_id") == f"skillager/{tag}" and _agent_next_step_artifact_current(item)}
        if isinstance(agent, str)
    )
    if not current_agents:
        return
    print()
    print("Verify router exposure:")
    for agent in current_agents or agents:
        print(f"  skillager status --agent {agent} --json")


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
    changed = [item for item in results if item.get("status") == "materialized" and item.get("skill_id") and item.get("skill_id") != "skillager/working"]
    if not changed:
        return False
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
    # If no new target appeared, only print restart guidance for a fresh project.
    return not agent_notes_ready_before and not materialized_targets_before


def cmd_manifest_init(args: argparse.Namespace) -> int:
    results = init_manifests(args.path, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for item in results:
            action = "would write" if args.dry_run else "wrote"
            print(f"{item['skill_id']}: {action} {item['manifest']} risk={item['scan']['risk']}")
    return 0


def _matches_filters(skill: dict[str, Any], args: argparse.Namespace) -> bool:
    if skill.get("trust") == "blocked" and not getattr(args, "include_blocked", False):
        return False
    if skill.get("trust") == "lint_blocked" and not getattr(args, "include_lint_blocked", False):
        return False
    if args.source and skill.get("source", {}).get("type") != args.source:
        return False
    if getattr(args, "trust", None) and skill.get("trust") != args.trust:
        return False
    if args.activation and skill.get("activation") != args.activation:
        return False
    if args.audience and not _matches_declared_audience(skill, args.audience):
        return False
    if args.package and skill.get("package") != args.package:
        return False
    return True


def _format_skill(skill: dict[str, Any]) -> str:
    lines = [
        f"id: {skill['id']}",
        f"name: {skill.get('name', '-')}",
        f"summary: {skill.get('summary', '-')}",
        "available: true",
        f"source: {skill['source'].get('type')}",
        f"availability: {', '.join(skill.get('availability', [])) or '-'}",
        f"activation: {skill.get('activation', '-')}",
        f"exposure: {skill.get('exposure', 'hidden')}",
        f"entrypoint: {skill.get('entrypoint')}",
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
    for source, counts in summary.get("by_source", {}).items():
        risk_bits = ", ".join(_risk_count(risk, count) for risk, count in sorted(counts.items()))
        print(f"  - {source}: {risk_bits}")
    if summary.get("by_audience"):
        audience_bits = ", ".join(f"{audience_bucket_label(audience)}={_style(str(count), 'bold')}" for audience, count in sorted(summary["by_audience"].items()))
        print(f"  - audience: {audience_bits}")
    if summary.get("by_trust"):
        trust_bits = ", ".join(_trust_count(state, count) for state, count in sorted(summary["by_trust"].items()))
        print(f"  - trust: {trust_bits}")
    _print_review_duplicate_summary(summary)
    if action.get("changed"):
        print(_style("Changed:", "bold"))
        for item in action["changed"]:
            line = f"  - {item['skill_id']}: {_trust_label(item['state'])}"
            duplicate = item.get("duplicate_of_reviewed") or {}
            approved_ids = duplicate.get("approved_ids") or []
            if approved_ids:
                line += f" (same content as approved {', '.join(approved_ids)})"
            print(line)
    if action.get("skipped"):
        print(_style("Skipped:", "bold"))
        for item in action["skipped"]:
            print(f"  - {item['skill_id']}: {item['reason']}")
    if compact:
        _print_lint_blocked(skills)
        _print_needs_review(skills)
        _print_ready_for_approval(skills)
        return
    print(_style("Skills:", "bold"))
    for skill in skills:
        risk = skill.get("scan", {}).get("risk")
        source = skill.get("source", {}).get("type")
        print(f"  - {_style(skill['id'], 'bold')} [{_risk_label(risk)}] {_trust_label(skill.get('trust'))} {source}/{skill.get('activation', '-')} - {skill.get('summary', '-')}")
        print(f"    audience: {_audience_label(skill)}")
        for item in (skill.get("lint") or {}).get("findings", [])[:3]:
            print(f"    lint {item.get('severity')} {item.get('code')} {item.get('field')}: {item.get('detail')}")
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
    for source, counts in summary.get("by_source", {}).items():
        table.add_row(source, ", ".join(_risk_count(risk, count) for risk, count in sorted(counts.items())))
    if summary.get("by_audience"):
        table.add_row("audience", ", ".join(f"{audience_bucket_label(audience)}={count}" for audience, count in sorted(summary["by_audience"].items())))
    if summary.get("by_trust"):
        table.add_row("trust", ", ".join(_trust_count(state, count) for state, count in sorted(summary["by_trust"].items())))
    duplicate = (summary.get("duplicate_content") or {})
    if duplicate.get("review_needed"):
        table.add_row(
            "duplicate approved content",
            f"{duplicate.get('review_needed', 0)} source-key approval(s) across {duplicate.get('approved_overlap_groups', 0)} group(s)",
        )
    console.print(table)
    if action.get("changed") or action.get("skipped"):
        lines = []
        for item in action.get("changed", []):
            suffix = ""
            duplicate = item.get("duplicate_of_reviewed") or {}
            approved_ids = duplicate.get("approved_ids") or []
            if approved_ids:
                suffix = f" (same content as approved {', '.join(approved_ids)})"
            lines.append(f"[green]{item['skill_id']}[/green]: {item['state']}{suffix}")
        for item in action.get("skipped", []):
            lines.append(f"[yellow]{item['skill_id']}[/yellow]: skipped ({item['reason']})")
        console.print(Panel("\n".join(lines), title="Review action", border_style="cyan"))
    if compact:
        _print_lint_blocked(skills)
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


def _print_review_duplicate_summary(summary: dict[str, Any]) -> None:
    duplicate = summary.get("duplicate_content") or {}
    if not duplicate.get("review_needed"):
        return
    print(
        "  - duplicate approved content: "
        f"{duplicate.get('review_needed', 0)} source-key approval(s), "
        f"groups={duplicate.get('approved_overlap_groups', 0)}"
    )


def _print_lint_blocked(skills: list[dict[str, Any]]) -> None:
    blocked = [skill for skill in skills if skill.get("trust") == "lint_blocked"]
    if not blocked:
        return
    print()
    print(_style(f"Lint blocked ({len(blocked)})", "bold"))
    for skill in blocked:
        print(f"  - {_style(skill['id'], 'bold')}")
        for item in (skill.get("lint") or {}).get("findings", [])[:3]:
            print(f"    {item.get('severity')} {item.get('code')} {item.get('field')}: {item.get('detail')}")
    print("  Fix the source, or approve with `--override-lint --reason <text>`.")


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
        duplicate = skill.get("duplicate_of_reviewed") or {}
        if duplicate.get("approved_ids"):
            print(f"    duplicate of approved: {', '.join(duplicate['approved_ids'])}")
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
        detail = _truncate(_first_sentence(skill.get("summary") or ""), 140)
        duplicate = skill.get("duplicate_of_reviewed") or {}
        if duplicate.get("approved_ids"):
            detail = f"{detail} Same content as approved {', '.join(duplicate['approved_ids'])}.".strip()
        table.add_row(
            skill["id"],
            _audience_label(skill),
            detail,
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
        source = max(by_source, key=by_source.__getitem__)
        print(f"  - Narrow by source: skillager review --source {source}")
    if by_package:
        package = max(by_package, key=by_package.__getitem__)
        print(f"  - Narrow by package: skillager review --package {package}")
    print("  - Approve one skill: skillager review <skill-id> --trust-selected reviewed")
    print("  - Show full list: skillager setup --details")


def _print_setup_bootstrap_result(result: dict[str, Any]) -> None:
    print(_style("Skillager bootstrap", "bold"))
    for item in result.get("artifacts", []):
        line = _setup_bootstrap_artifact_line(item)
        if line:
            print(line)
    if result.get("handoff_ready"):
        agents = list(result.get("agents") or [])
        print("Hand Skillager skill discovery, tagging, materialization, and router/stub/native exposure to the agent so it can shape the project's skill surface:")
        if len(agents) == 1:
            print(f"  skillager handoff --agent {agents[0]}")
        elif agents:
            for agent in agents:
                print(f"  - skillager handoff --agent {agent}")
        else:
            print("  skillager handoff")
    else:
        _print_setup_bootstrap_reminder(result)


def _setup_bootstrap_artifact_line(item: dict[str, Any]) -> str | None:
    kind = item.get("kind")
    if kind == "working_skill":
        line = f"{item.get('skill_id')}: {item.get('status')}"
    elif kind == "project_note":
        line = f"{item.get('agent')} project note: {item.get('status')}"
    else:
        return None
    if item.get("target"):
        line += f" {item['target']}"
    if item.get("reason"):
        line += f" ({item['reason']})"
    return line


def _print_setup_bootstrap_reminder(result: dict[str, Any]) -> None:
    commands = result.get("next_commands") or []
    if len(commands) == 1:
        print(f"Working artifacts not ready: run {commands[0]}")
    elif commands:
        print("Working artifacts not ready. Run one of:")
        for command in commands:
            print(f"  - {command}")


def _interactive_setup(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    audience: str | None,
    include_global: bool,
    catalog_root: Path | None = None,
    global_scope: bool = True,
    paths: list[Path] | None = None,
    agents: list[str] | None = None,
    no_bootstrap: bool = False,
    project_dir: Path | None = None,
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
            results = _materialize_reviewed_for_project(
                approved,
                state_root=state_root,
                catalog_root=catalog_root,
                prompt_prefix="Review complete. ",
                agents=agents,
                no_bootstrap=no_bootstrap,
                project_dir=project_dir,
            )
            if results is not None:
                result_agents = _setup_result_agents(results)
                _save_status_scope(state_root, selected, audience=audience, include_global=include_global, agents=result_agents, paths=paths)
                print("Setup complete.")
                _print_setup_completion_summary(selected, results, agents=result_agents)
                _print_agent_next_steps(results)
            else:
                print("Setup complete; no skills materialized.")
            return
        print()
        print(_style("Choose an action", "bold"))
        print(f"  {_style('1', 'cyan')}. Review unapproved skills one by one")
        print(f"  {_style('2', 'green')}. Approve all low-risk selected skills")
        print(f"  {_style('3', 'red')}. Block all high-risk selected skills")
        print(f"  {_style('4', 'cyan')}. Install Skillager working skill for project scope (requires approved skills)")
        print(f"  {_style('5', 'dim')}. Exit")
        choice = _interactive_input("> ").strip()
        if choice == "1":
            decided_ids.update(_interactive_review_skills(state_root, candidates, catalog_root=catalog_root, global_scope=global_scope))
        elif choice == "2":
            low = [skill for skill in candidates if skill.get("scan", {}).get("risk") == "low"]
            if not low:
                print("No unreviewed low-risk skills remain in this setup selection.")
                continue
            selected_low = _choose_low_risk_audience_group(low)
            if selected_low and _confirm(f"Approve {len(selected_low)} low-risk skill(s) as reviewed?"):
                _print_action_result(
                    apply_review_action(
                        state_root,
                        selected_low,
                        trust_state="reviewed",
                        approval_root=catalog_root,
                        global_scope=global_scope,
                    )
                )
        elif choice == "3":
            high = [skill for skill in candidates if skill.get("scan", {}).get("risk") == "high"]
            if not high:
                print("No unreviewed high-risk skills remain in this setup selection.")
            elif _confirm(f"Block {len(high)} high-risk skill(s)?"):
                _print_action_result(apply_review_action(state_root, high, block_high=True))
        elif choice == "4":
            reviewed = _approved_skills(selected)
            results = _materialize_reviewed_for_project(
                reviewed,
                state_root=state_root,
                catalog_root=catalog_root,
                agents=agents,
                no_bootstrap=no_bootstrap,
                project_dir=project_dir,
            )
            if results is not None:
                result_agents = _setup_result_agents(results)
                _save_status_scope(state_root, selected, audience=audience, include_global=include_global, agents=result_agents, paths=paths)
                print("Setup complete.")
                _print_setup_completion_summary(selected, results, agents=result_agents)
                _print_agent_next_steps(results)
                return
        elif choice == "5" or choice.lower() in {"q", "quit", "exit"}:
            return
        else:
            print("Enter 1, 2, 3, 4, or 5.")


def _current_selected_skills(state_root: Path, selected_ids: list[str], *, catalog_root: Path | None = None) -> list[dict[str, Any]]:
    by_id = {
        skill["id"]: skill
        for skill in _effective_project_skills(
            state_root,
            catalog_root=catalog_root,
            include_lint_blocked=True,
        )
    }
    return annotate_duplicate_content([by_id[skill_id] for skill_id in selected_ids if skill_id in by_id])


def _unreviewed_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill for skill in skills if skill.get("trust") == "discovered"]


def _approved_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill for skill in skills if skill.get("trust") in {"reviewed", "trusted", "pinned"}]


def _choose_low_risk_audience_group(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        audience = audience_bucket(skill)
        groups.setdefault(audience, []).append(skill)
    if len(groups) <= 1:
        return skills
    ordered = sorted(groups)
    print("Low-risk skills span declared audiences and undeclared skills:")
    for audience in ordered:
        print(f"  - {audience_bucket_label(audience)}: {len(groups[audience])}")
    answer = _interactive_input("Approve which group? Enter user/dev/other/all, or blank to cancel: ").strip().lower()
    if not answer:
        return []
    if answer == "all":
        return skills
    audience = _normalize_audience_choice(answer)
    if audience in groups:
        return groups[audience]
    print(f"Unknown audience choice: {answer}")
    return []


def _interactive_review_skills(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    catalog_root: Path | None = None,
    global_scope: bool = True,
) -> set[str]:
    decided: set[str] = set()
    agent = _detect_agent()
    review_items = _review_family_items(skills, agent=agent)
    for index, group in enumerate(review_items, start=1):
        if len(group) > 1:
            if not _interactive_review_family(
                state_root,
                group,
                index=index,
                total=len(review_items),
                agent=agent,
                catalog_root=catalog_root,
                global_scope=global_scope,
            ):
                return decided
            decided.update(skill["id"] for skill in group)
            continue
        skill = group[0]
        risk = skill.get("scan", {}).get("risk")
        source = skill.get("source", {}).get("type")
        package = skill.get("package") or skill.get("source", {}).get("package") or "-"
        print()
        print(_style(f"Review skill {index} of {len(review_items)}", "bold"))
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
        choice = _interactive_input("Review decision? [y] approve / [s]kip / [b]lock / [q]uit (default: skip): ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            return decided
        decided.add(skill["id"])
        if choice in {"y", "yes"}:
            _print_action_result(
                apply_review_action(
                    state_root,
                    [skill],
                    trust_state="reviewed",
                    approval_root=catalog_root,
                    global_scope=global_scope,
                )
            )
        elif choice in {"b", "block"}:
            _block_review_item(state_root, skill)
        else:
            print(f"{skill['id']}: skipped; remains unreviewed")
    return decided


def _review_family_items(skills: list[dict[str, Any]], *, agent: str | None) -> list[list[dict[str, Any]]]:
    items: list[list[dict[str, Any]]] = []
    grouped_objects: set[int] = set()
    for _, _, group in duplicate_content_group_entries(skills):
        ordered = sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent))
        items.append(ordered)
        grouped_objects.update(id(skill) for skill in group)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for skill in skills:
        if id(skill) in grouped_objects:
            continue
        groups[_agent_variant_family_key(skill)].append(skill)
    items.extend(sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent)) for group in groups.values())
    return sorted(items, key=lambda group: _review_family_sort_key(group, agent))


def _review_family_sort_key(group: list[dict[str, Any]], agent: str | None) -> tuple[str, int, str]:
    representative = sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent))[0]
    return (
        _agent_variant_family_key(representative),
        _agent_variant_preference_key(representative, agent)[0],
        str(representative.get("id") or ""),
    )


def _interactive_review_family(
    state_root: Path,
    group: list[dict[str, Any]],
    *,
    index: int,
    total: int,
    agent: str | None,
    catalog_root: Path | None,
    global_scope: bool,
) -> bool:
    ordered = sorted(group, key=lambda skill: _agent_variant_preference_key(skill, agent))
    preferred = ordered[0]
    risk_counts = Counter(skill.get("scan", {}).get("risk") or "unknown" for skill in ordered)
    risk_text = ", ".join(
        f"{_risk_label(risk)}={risk_counts[risk]}"
        for risk in sorted(risk_counts, key=_risk_sort_key)
    )
    print()
    print(_style(f"Review related skills {index} of {total}", "bold"))
    print(f"  group: {_agent_variant_family_key(preferred)} ({len(ordered)} variants)")
    if _same_content_cross_source_group(ordered):
        print("  duplicate content: same content appears under multiple source keys; approval records each source.")
    print(f"  preferred for {agent or 'this agent'}: {preferred['id']}")
    print(f"  risks: {risk_text}")
    for skill in ordered:
        _print_review_family_variant(skill, preferred=preferred)
    choice = _interactive_input("Review group? [y] approve / [s]kip / [b]lock / [q]uit (default: skip): ").strip().lower()
    if choice in {"q", "quit", "exit"}:
        return False
    if choice in {"y", "yes"}:
        _print_action_result(
            apply_review_action(
                state_root,
                ordered,
                trust_state="reviewed",
                approval_root=catalog_root,
                global_scope=global_scope,
            )
        )
    elif choice in {"b", "block"}:
        for skill in ordered:
            _block_review_item(state_root, skill)
    else:
        print(f"{_agent_variant_family_key(preferred)}: skipped; remains unreviewed")
    return True


def _block_review_item(state_root: Path, skill: dict[str, Any]) -> None:
    set_trust(state_root, skill["id"], "blocked", skill["content_hash"], skill["source"], lint=skill.get("lint"))
    print(f"{skill['id']}: blocked")


def _print_review_family_variant(skill: dict[str, Any], *, preferred: dict[str, Any]) -> None:
    risk = skill.get("scan", {}).get("risk")
    source = skill.get("source", {}).get("type")
    package = skill.get("package") or skill.get("source", {}).get("package") or "-"
    hint = _agent_hint(skill) or "agent-neutral"
    marker = "preferred" if _same_skill_variant(skill, preferred) else "variant"
    content_status = "same content" if skill.get("content_hash") == preferred.get("content_hash") else "differs"
    print(f"    - {marker}: {skill['id']} [{_risk_label(risk)}] {hint} {source}/{package} {content_status}")
    print(f"      audience: {_audience_label(skill)}")
    if skill.get("summary"):
        _print_wrapped("      used for: ", skill["summary"], width=_output_width(), max_chars=180)
    _print_wrapped("      file: ", skill.get("entrypoint", "<unknown>"), width=_output_width(), break_long_words=True)
    findings = skill.get("scan", {}).get("findings", [])
    for finding in findings[:2]:
        _print_wrapped("      at: ", _finding_location(finding), width=_output_width(), break_long_words=True)
        _print_wrapped("          ", _finding_detail(finding, group_risk=risk), width=_output_width())
    if len(findings) > 2:
        print(f"      ... {len(findings) - 2} more findings")


def _same_content_cross_source_group(group: list[dict[str, Any]]) -> bool:
    if len(group) <= 1:
        return False
    content_hashes = {skill.get("content_hash") for skill in group}
    if len(content_hashes) != 1:
        return False
    return len({_agent_variant_family_key(skill) for skill in group}) > 1


def _risk_sort_key(risk: str) -> tuple[int, str]:
    return ({"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(risk, 4), risk)


def _materialize_reviewed_for_project(
    skills: list[dict[str, Any]],
    *,
    state_root: Path,
    catalog_root: Path | None,
    prompt_prefix: str = "",
    agents: list[str] | None = None,
    no_bootstrap: bool = False,
    project_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    project_dir = (project_dir or find_project_root() or Path.cwd()).resolve()
    if not skills:
        print("No reviewed/trusted/pinned skills are ready for project setup.")
        print("Approve low-risk skills first with setup option 2, or review warned/high-risk skills with option 1.")
        return None
    agents = list(agents or [])
    if not agents:
        agents = _choose_materialize_agents()
    if not agents:
        return None
    results: list[dict[str, Any]] = []
    if no_bootstrap:
        _print_setup_bootstrap_reminder(
            _setup_bootstrap_payload(
                project_dir=project_dir,
                agents=agents,
                reason="disabled by --no-bootstrap",
                reason_code=SETUP_BOOTSTRAP_REASON_DISABLED,
            )
        )
    else:
        target_label = " and ".join(agent.title() for agent in agents)
        if not _confirm(f"{prompt_prefix}Install Skillager working skill for {target_label} project scope?"):
            return None
        bootstrap = _perform_bootstrap(agents=agents, project_dir=project_dir, dry_run=False, force=False)
        results = list(bootstrap["artifacts"])
        for item in results:
            line = _setup_bootstrap_artifact_line(item)
            if line:
                print(line)
        if _bootstrap_has_local_blocker(results) or not _setup_handoff_ready(project_dir, agents=agents):
            return None
    native = _choose_native_project_skills(skills, agents=agents)
    if native:
        native_results = materialize_skills(
            native,
            agents=agents,
            scope="project",
            project_dir=project_dir,
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
    return results or None


def _print_router_suggestions(state_root: Path, *, catalog_root: Path | None, agents: list[str]) -> None:
    catalog_root = catalog_root or state_root
    attached = _project_tag_names(_current_project_dir())
    if not attached:
        return
    suggestions = []
    for tag in attached:
        reviewed = [skill for skill in _select_project_tag_skills(state_root, catalog_root, tag) if skill.get("trust") in {"reviewed", "trusted", "pinned"}]
        if reviewed:
            suggestions.append((tag, len(reviewed)))
    if not suggestions:
        return
    agent = agents[0] if len(agents) == 1 else "codex"
    print()
    print(_style("Router suggestions", "bold"))
    print("  Broad project-local tags are best exposed as router skills when relevant to the task:")
    for tag, count in suggestions:
        print(f"  - {tag}: {count} approved skill(s)")
        print(f"    skillager materialize --tag {tag} --mode router --agent {agent} --scope project")


def _print_setup_completion_summary(
    skills: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    agents: list[str],
) -> None:
    approved = [skill for skill in skills if skill.get("trust") in TRUSTED_STATES]
    exposed_ids: set[str] = set()
    for item in results:
        skill_id = item.get("skill_id")
        if (
            item.get("status") in {"materialized", "already_native"}
            and isinstance(skill_id, str)
            and skill_id != "skillager/working"
        ):
            exposed_ids.add(skill_id)
    hidden = [skill for skill in approved if skill["id"] not in exposed_ids]
    if not approved:
        return
    agent = agents[0] if len(agents) == 1 else "codex"
    inventory = _available_inventory_summary(
        skills,
        agent=agent,
        project_exposure={skill_id: [{"kind": "native"}] for skill_id in exposed_ids},
    )
    print()
    print(_style("Setup summary", "bold"))
    _print_inventory_block(inventory, indent="  ")
    if hidden:
        print()
        print("  Stub candidates")
        print("    These are available but not loaded as native skills. Stub any that should be easy to invoke by name:")
        for index, skill in enumerate(hidden[:25], start=1):
            summary = _first_sentence(skill.get("summary", ""))
            print(f"    {index}. {skill['id']}")
            if summary:
                _print_wrapped("       ", summary, width=_output_width(), max_chars=110)
        if len(hidden) > 25:
            print(f"    ... {len(hidden) - 25} more available hidden skill(s)")
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
        print(f"  {_style(skill['id'], 'bold')}")
        variants = skill.get("_family_variants") or []
        if len(variants) > 1:
            print(f"  variants: {skill.get('family_key')} ({len(variants)} related skills)")
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
        if skill.get("source", {}).get("type") != "project":
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
    audience = audience_bucket(skill)
    source_rank = {"project": 0, "python-package": 1, "environment": 2, "global": 3}.get(source_type, 4)
    audience_rank = {"user": 0, "user+dev": 1, AUDIENCE_OTHER: 2, "dev": 3}.get(audience, 4)
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
    return _canonical_agent_variant_slug(skill["id"].rsplit("/", 1)[-1])


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
    agents = sorted(
        agent
        for agent in {item.get("agent") for item in results if _agent_next_step_artifact_current(item) and item.get("agent")}
        if isinstance(agent, str)
    )
    first_party_handoff = _first_party_handoff_current(results)
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
        if first_party_handoff:
            notes = agent_note_paths(project_dir, agents=agents)
            if len(notes) == 1:
                print(f"  - Project working note: {notes[0]}")
            else:
                print("  - Project working notes:")
                for note in notes:
                    print(f"    - {note}")
    else:
        print(f"  - Restart {_agent_label(agents)} in the directory where you ran Skillager.")
    if first_party_handoff:
        print("  - The agent should run `skillager working` after context resets; run `skillager handoff` only for explicit curation.")
    else:
        print("  - The agent will discover Skillager-managed native skills from the native skill directory.")


def _agent_next_step_artifact_current(item: dict[str, Any]) -> bool:
    if item.get("status") == "materialized":
        return True
    return item.get("kind") in {"working_skill", "project_note"} and item.get("status") == "skipped" and item.get("reason") == "already up to date"


def _first_party_handoff_current(results: list[dict[str, Any]]) -> bool:
    return any(
        (item.get("kind") in {"working_skill", "project_note"} or item.get("skill_id") == WORKING_SKILL_ID)
        and _agent_next_step_artifact_current(item)
        for item in results
    )


def _agent_label(agents: list[str]) -> str:
    if agents == ["claude"]:
        return "Claude"
    if agents == ["codex"]:
        return "Codex"
    if agents:
        return "Codex/Claude"
    return "the agent"


def _materialized_agents(results: list[dict[str, Any]]) -> list[str]:
    return sorted(
        agent
        for agent in {item.get("agent") for item in results if item.get("status") == "materialized" and item.get("agent")}
        if isinstance(agent, str)
    )


def _setup_result_agents(results: list[dict[str, Any]]) -> list[str]:
    return sorted(
        agent
        for agent in {item.get("agent") for item in results if item.get("agent")}
        if isinstance(agent, str)
    )


def _common_audience(skills: list[dict[str, Any]]) -> str | None:
    audiences = {audience_bucket(skill) for skill in skills}
    audiences.discard(None)
    audiences.discard(AUDIENCE_OTHER)
    if len(audiences) == 1:
        audience = next(iter(audiences))
        return audience if audience in {"user", "dev"} else None
    return None


def _materialized_target_bases(results: list[dict[str, Any]]) -> list[Path]:
    targets = [Path(item["target"]) for item in results if item.get("target") and _agent_next_step_artifact_current(item) and item.get("kind") != "project_note"]
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
    if value == "lint_blocked":
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
    audience = guess.get("audience") or audience_bucket(skill)
    confidence = guess.get("confidence") or "undeclared"
    reasons = guess.get("reasons") or []
    label = f"{audience_bucket_label(audience)} ({confidence})"
    if reasons:
        label += " - " + "; ".join(str(reason) for reason in reasons[:2])
    return label


def _matches_declared_audience(skill: dict[str, Any], audience: str) -> bool:
    requested = _normalize_audience_choice(audience)
    if requested == AUDIENCE_OTHER:
        return not declared_audiences(skill)
    return requested in declared_audiences(skill)


def _normalize_audience_choice(audience: str | None) -> str:
    value = (audience or "").strip().lower().replace(" ", "_")
    if value in {"developer", "maintainer", "maintainers"}:
        return "dev"
    if value in {"unknown", "undeclared", "everything_else", "everything-else", "other"}:
        return AUDIENCE_OTHER
    return value


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
