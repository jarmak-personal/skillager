"""Paging composition for the public search command; ranking stays with search."""
from __future__ import annotations

import argparse
from typing import Any

from .. import project_tags
from .context import catalog_root, current_project_dir, root
from .pagination import fingerprint, page_metadata


def search_snapshot(args: argparse.Namespace, skills: list[dict[str, Any]], **observations: Any) -> str:
    # Bind the complete observed inventory, including nonmatches and pending rows,
    # before presentation mutates metadata or filters it down to a ranked window.
    tag_members = sorted(project_tags.tag_skills(current_project_dir(), args.tag)) if args.tag else None
    return fingerprint({"skills": skills, "tag_members": tag_members, "observations": observations})


def search_page(
    args: argparse.Namespace, rows: list[dict[str, Any]], *, snapshot: str,
) -> tuple[list[dict[str, Any]], str | None]:
    personal = args.scope == "library"
    request = {
        "command": "search", "scope": args.scope, "query": args.query,
        "catalog": str(catalog_root(args)),
        "project": None if personal else str(current_project_dir()),
        "state": None if personal else str(root(args)),
        "tag": args.tag, "agent": args.agent,
        "compatible_only": args.compatible_only, "include_global": args.include_global,
        "view": args.view, "include_installed": args.include_installed,
        "installed_project": str(args.installed_project) if args.installed_project else None,
        "installed_identities": str(args.installed_identities.resolve()) if args.installed_identities else None,
        "full_json": args.full_json, "limit": args.limit,
    }
    return page_metadata(rows, snapshot=snapshot, request=request, limit=args.limit, cursor=args.cursor)
