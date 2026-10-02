from __future__ import annotations

import argparse
import json

from ..library.inventory import library_inventory
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
    rows, state = library_inventory(personal_catalog_root(args))
    page, next_cursor = page_metadata(
        rows, snapshot=fingerprint(state), request={"command": "list", "scope": "library", "limit": limit},
        limit=limit, cursor=args.cursor,
    )
    print(json.dumps({"schema": "skillager.list.v1", "scope": "library", "skills": page, "next_cursor": next_cursor}, indent=2, sort_keys=True))
    return 0
