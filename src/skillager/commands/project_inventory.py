"""Metadata-only, exact-project occurrence inventory for interactive consumers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..skills.project_inventory import InventoryUnavailable, observe_project
from .context import catalog_root, root
from .pagination import CursorError, fingerprint, page_metadata


PROJECT_RESULT_BYTES = 512_000
PROJECT_QUERY_BYTES = 1_000


def cmd_list_project(args: argparse.Namespace) -> int:
    if any((args.source, args.activation, args.audience, args.package, args.agent,
            args.no_packages, args.include_global, args.include_lint_blocked,
            args.summary_json, args.full_json)) or not args.json:
        raise ValueError("--scope project requires --json without workspace filters or output options")
    limit = args.limit if args.limit is not None else 100
    if not 1 <= limit <= 100:
        raise ValueError("project --limit must be between 1 and 100")
    raw_query = getattr(args, "query", None) or ""
    if len(raw_query.encode("utf-8")) > PROJECT_QUERY_BYTES:
        raise ValueError("project --query must be at most 1000 UTF-8 bytes")
    query = " ".join(raw_query.split()).casefold()
    project = Path.cwd().resolve()
    payload: dict[str, Any] = {
        "schema": "skillager.list.v1", "scope": "project", "project_root": str(project),
        "observation": {"status": "unavailable", "errors": [], "error_count": 0},
        "skills": [], "next_cursor": None,
    }
    try:
        state, catalog = root(args), catalog_root(args)
        current = observe_project(state, catalog, project)
        rows, observation = current["rows"], current["observation"]
        matches = [row for row in rows if not query or any(
            query in row[field].casefold() for field in ("id", "name", "description")
        )]
        page, cursor = page_metadata(
            matches, snapshot=fingerprint(current["snapshot"]),
            request={"command": "list", "scope": "project", "project": str(project),
                     "state": str(state), "catalog": str(catalog), "limit": limit,
                     **({"query": query} if query else {})},
            limit=limit, cursor=args.cursor,
        )
        payload.update(skills=page, next_cursor=cursor, observation=observation)
    except CursorError:
        raise
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        code = exc.code if isinstance(exc, InventoryUnavailable) else "observation-unavailable"
        payload["observation"] = {"status": "unavailable", "errors": [{"code": code, "path": None}], "error_count": 1}
    encoded = json.dumps(payload, sort_keys=True)
    if len(encoded.encode("utf-8")) > PROJECT_RESULT_BYTES:
        payload.update(skills=[], next_cursor=None,
                       observation={"status": "unavailable", "errors": [{"code": "result-limit", "path": None}], "error_count": 1})
        encoded = json.dumps(payload, sort_keys=True)
    print(encoded)
    return 2 if payload["observation"]["status"] == "unavailable" else 0
