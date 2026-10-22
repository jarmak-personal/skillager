from __future__ import annotations

import argparse
import json
from typing import Any

from ..library.inventory import LibraryInventoryUnavailable, library_inventory
from ..library.source_groups import filter_source_group, group_inventory
from .context import personal_catalog_root
from .pagination import fingerprint, page_metadata


def cmd_list_library(args: argparse.Namespace) -> int:
    if any((args.source, args.activation, args.audience, args.package, args.agent,
            args.no_packages, args.include_global, args.include_lint_blocked,
            args.summary_json, args.full_json)):
        raise ValueError("workspace filters and output options cannot be combined with --scope library")
    if not args.json:
        raise ValueError("--scope library requires --json")
    limit = args.limit if args.limit is not None else 100
    if args.group_by and args.source_group:
        raise ValueError("--group-by and --source-group cannot be combined")
    if (args.group_by or args.source_group) and not 1 <= limit <= 100:
        raise ValueError("source group pages require --limit between 1 and 100")
    try:
        rows, state = library_inventory(personal_catalog_root(args))
    except LibraryInventoryUnavailable as exc:
        print(json.dumps({"schema": "skillager.error.v1", "error": {"code": "inventory_unavailable", "message": str(exc)}}, indent=2, sort_keys=True))
        return 2
    rows = filter_source_group(rows, args.source_group)
    groups = group_inventory(rows) if args.group_by else None
    page, next_cursor = page_metadata(
        groups if groups is not None else rows, snapshot=fingerprint(state),
        request={"command": "list", "scope": "library", "limit": limit,
                 "group_by": args.group_by, "source_group": args.source_group},
        limit=limit, cursor=args.cursor,
    )
    payload: dict[str, Any] = {"scope": "library", "next_cursor": next_cursor, "discovery_error_count": len(state["errors"]), "total_skills": len(rows)}
    if groups is not None:
        payload.update(schema="skillager.library-groups.v1", groups=page, total_groups=len(groups))
    else:
        payload.update(schema="skillager.list.v1", skills=page)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
