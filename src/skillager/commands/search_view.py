"""Opt-in metadata search contract using the existing discovery/ranking owners."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from ..exposure.management import _exposure_records
from ..library.metadata import load_library_identity, load_library_provenance
from ..library.paths import load_library_registration
from ..skills.index import build_index
from ..skills.search_view import (
    INSTALLED_INPUT_BYTES, SEARCH_INVENTORY_LIMIT, SEARCH_RESULT_BYTES, SEARCH_RESULT_LIMIT,
    SEARCH_VIEW_SCHEMA, SearchView, installed_keys, lineage_relations,
)
from ..state.paths import cache_root, find_project_root, project_state_root
from ..state import approvals
from .context import catalog_root, current_project_dir, root


def add_search_view_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--view", choices=["skills", "copies"], help="Opt in to skillager.search.v1: group proven identities or show concrete copies, excluding installed identities by default.")
    parser.add_argument("--include-installed", action="store_true", help="Include identities already present for any project agent (requires --view).")
    parser.add_argument("--installed-identities", type=Path, help="Bounded skillager.search-installed.v1 canonical identity exclusions for library search; no project discovery.")
    parser.add_argument("--installed-project", type=Path, help="Explicit canonical local project whose presence is observed separately from library candidate authority.")


def run_search_view(args: argparse.Namespace) -> int:
    # This command composes existing CLI operations; policy has no command imports.
    from . import impl as cli

    payload: dict[str, Any] = {
        "schema": SEARCH_VIEW_SCHEMA, "status": "unavailable", "reason_code": None,
        "policy": {"view": args.view, "include_installed": args.include_installed,
                   "scope": args.scope, "preferred_agent": args.agent, "compatible_only": args.compatible_only},
        "context": {"project_root": None, "installed_observation": "unknown"},
        "limit": args.limit, "results": [],
    }
    try:
        if not args.json or args.limit < 1 or args.limit > SEARCH_RESULT_LIMIT:
            raise SearchRefusal("invalid-options")
        if len(args.query.encode("utf-8")) > 1_000:
            raise SearchRefusal("query-limit")
        if args.compatible_only and not args.agent:
            raise SearchRefusal("invalid-options")
        personal = args.scope == "library"
        if personal and (args.tag or args.include_global):
            raise SearchRefusal("invalid-options")
        if args.installed_identities and (not personal or args.installed_project):
            raise SearchRefusal("invalid-options")
        project = None if personal else current_project_dir()
        if args.installed_project:
            selected = args.installed_project
            if (not selected.is_absolute() or not selected.is_dir() or selected != selected.resolve()
                    or find_project_root(selected) != selected or (project is not None and selected != project)):
                raise SearchRefusal("project-mismatch")
            project = selected
        catalog = catalog_root(args)
        registration = load_library_registration(catalog)
        identifier = registration.library_id if registration else None
        provenance = None
        if registration:
            library = load_library_identity(registration.layout)
            if not library or library.library_id != registration.library_id:
                raise SearchRefusal("library-changed")
            provenance = load_library_provenance(registration.layout)
        records = approvals.load(catalog).get("global_approvals", {}) if registration else {}
        relations, lineage_complete = lineage_relations(provenance, identifier, records)
        supplied = _read_installed(args.installed_identities) if args.installed_identities else None
        skills = cli._search_inventory(args, deferred=True)
        if any(skill.get("identity_collision") for skill in skills):
            skills = cli._search_inventory(args, deferred=False)
        if len(skills) > SEARCH_INVENTORY_LIMIT:
            raise SearchRefusal("inventory-limit")
        # Presence is only project-native discovery, using the existing scanner
        # and trust owner. It never borrows personal --state-dir or ambient roots.
        observation = build_index(project_state_root(project) if personal else root(args), approval_root=catalog,
                                  project_native_root=project, include_packages=False, persist=False) if project else {}
        errors: list[str] = []
        exposures = _exposure_records(project, agents=["codex", "claude"], scope="project", errors=errors) if project else []
        installed_complete = supplied is not None or (project is not None and not errors and not observation.get("errors"))
        # Unknown lineage cannot prove that a canonical identity is not represented
        # by one of the observed originals.
        installed_complete = installed_complete and (lineage_complete or supplied is not None)
        presence_skills = observation.get("skills", [])
        # Include pending originals only in the presence map, never in ranking.
        presence = SearchView(presence_skills, relations=relations, exposures=[], present=set(),
                              installed_complete=installed_complete, project=project)
        view = SearchView(skills, relations=relations, exposures=exposures,
                          present=presence.present | (supplied or set()),
                          installed_complete=presence.installed_complete, project=project)
        payload["context"] = {"project_root": str(project) if project else None,
                              "installed_observation": "provided" if supplied is not None else "observed" if view.installed_complete else "unknown"}
        if not view.installed_complete and not args.include_installed:
            raise SearchRefusal("installed-state-unknown")
        candidates = cli._available_skills(view.candidates(args.include_installed))
        if args.tag:
            assert project is not None
            tag_key = cli.project_tags.normalize_tag(args.tag)
            tag_ids = set(cli.project_tags.tag_skills(project, tag_key))
            candidates = [{**skill, "tags": sorted(set(skill.get("tags", [])) | {tag_key})}
                          for skill in candidates if skill["id"] in tag_ids]
        # One native ranking, with no display-name family collapse. All versions of
        # a proven identity compete as match evidence for that group's representative.
        ranked = cli.search_index(candidates, args.query, include_untrusted=False, cache_path=cache_root() / "search-v1.sqlite3")
        if args.compatible_only:
            ranked = [skill for skill in ranked if cli.compatibility_problem(skill, args.agent) is None]
        if args.agent:
            ranked = cli._sort_agent_variant_search(ranked, args.agent)
        trust_root = catalog if personal else root(args)
        results = view.results(ranked, copies=args.view == "copies", limit=args.limit,
                               current=lambda skill: cli._search_result_is_current(skill, trust_root, catalog)
                               and (not args.compatible_only or cli.compatibility_problem(skill, args.agent) is None),
                               public=lambda skill: cli._public_full_skill_metadata({**skill, "exposure": "unknown"} if personal else skill),
                               agent=args.agent)
        payload.update(status="completed", results=results)
    except (OSError, ValueError) as error:
        payload.update(status="unavailable", reason_code=error.code if isinstance(error, SearchRefusal) else "observation-unavailable", results=[])
    except (KeyError, TypeError) as error:
        payload.update(status="unavailable", reason_code="internal-error", results=[])
        kind = "KeyError" if isinstance(error, KeyError) else "TypeError"
        print(f"skillager: internal search view error ({kind}).", file=sys.stderr)
    encoded = json.dumps(payload, sort_keys=True)
    if len(encoded.encode("utf-8")) > SEARCH_RESULT_BYTES:
        payload.update(status="unavailable", reason_code="result-limit", results=[])
        encoded = json.dumps(payload, sort_keys=True)
    print(encoded)
    return 0 if payload["status"] == "completed" else 1 if payload["reason_code"] == "internal-error" else 2


class SearchRefusal(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _read_installed(path: Path) -> set[str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SearchRefusal("invalid-installed-input")
        value = stream.read(INSTALLED_INPUT_BYTES + 1)
    if len(value) > INSTALLED_INPUT_BYTES:
        raise SearchRefusal("installed-input-limit")
    try:
        return installed_keys(json.loads(value))
    except RecursionError:
        raise SearchRefusal("invalid-installed-input") from None
