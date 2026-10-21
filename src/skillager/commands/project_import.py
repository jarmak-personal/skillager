"""Public JSON admission and pagination for project-wide explicit import."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..library.project_import import IMPORT_SCHEMA, INVENTORY_SCHEMA, LIMITS, apply_project_import, encode, parse_selection, prepare_project_import, project_import_inventory, project_import_status, public
from ..library.review import ReviewRefusal
from .context import catalog_root, root
from .pagination import CursorError, fingerprint, page_metadata


def add_project_import_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("import-project", help="Review and save selected project originals in one accepted batch.")
    parser.add_argument("--json", action="store_true", help="Required versioned metadata output.")
    parser.add_argument("--limit", type=int, default=100, help="Inventory page size, 1..100. Summary always covers the whole project.")
    parser.add_argument("--cursor", help="Opaque inventory paging cursor.")
    parser.add_argument("--select", help="JSON array of 1..32 exact project occurrence IDs from inventory.")
    parser.add_argument("--status", action="store_true", help="Observe exact current selected destination facts without replaying an operation.")
    parser.add_argument("--destinations", help="For --status: JSON array of library IDs parallel to --select, from the review.")
    parser.add_argument("--review-manifest", action="store_true", help="Return the complete bounded selected-file review.")
    parser.add_argument("--yes", action="store_true", help="Save and accept the exact selected reviewed versions.")
    parser.add_argument("--confirmation-token", help="Opaque identity from the complete review.")
    parser.set_defaults(func=cmd_project_import)


def cmd_project_import(args: argparse.Namespace) -> int:
    schema = "skillager.project-import-status.v1" if args.status else IMPORT_SCHEMA if args.select is not None or args.review_manifest or args.yes else INVENTORY_SCHEMA
    try:
        if not args.json or not 1 <= args.limit <= LIMITS["page_items"]:
            raise ReviewRefusal("invalid-options", "Use --json and an inventory limit between 1 and 100")
        if args.status:
            if args.select is None or args.destinations is None or args.review_manifest or args.yes or args.confirmation_token or args.cursor or args.limit != 100:
                raise ReviewRefusal("invalid-options", "Status requires --select --destinations --json without review or mutation options")
            value = project_import_status(root(args).resolve(), catalog_root(args).resolve(), Path.cwd().resolve(), args.select, args.destinations)
            sys.stdout.write(encode(value))
            return 0
        if args.destinations is not None:
            raise ReviewRefusal("invalid-options", "--destinations requires --status")
        if (bool(args.select is not None) != args.review_manifest or args.yes and args.select is None
                or args.confirmation_token and not args.yes or args.select is not None and (args.cursor or args.limit != 100)):
            raise ReviewRefusal("invalid-options", "Review requires --select and --review-manifest; apply also requires --yes and its token")
        state, catalog, project = root(args).resolve(), catalog_root(args).resolve(), Path.cwd().resolve()
        if args.select is None:
            current = project_import_inventory(state, catalog, project)
            page, cursor = page_metadata(current["items"], snapshot=fingerprint(public(current)),
                request={"command": "import-project", "project": str(project), "state": str(state), "catalog": str(catalog), "limit": args.limit},
                limit=args.limit, cursor=args.cursor)
            value, code = {**public(current), "items": page, "next_cursor": cursor}, 0
        else:
            selection = parse_selection(args.select)
            if args.yes:
                value, code = apply_project_import(state, catalog, project, selection, args.confirmation_token)
            else:
                value, code = public(prepare_project_import(state, catalog, project, selection)), 0
        if not args.yes:
            encode(value)
    except CursorError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        # Only preflight failures reach here. Mutation-stage failures are retained
        # by the domain owner and cannot be mislabeled as no-effect refusals.
        value = {"schema": schema, "status": "refused", "error": {
            "code": error.code if isinstance(error, ReviewRefusal) else "unavailable",
            "message": str(error) if isinstance(error, ReviewRefusal) else "Project import is unavailable or changed; observe and review again"},
            "limits": dict(LIMITS)}
        code = 2
    # Encoding/output after an attempted apply must never manufacture a refusal.
    sys.stdout.write(encode(value))
    return code
