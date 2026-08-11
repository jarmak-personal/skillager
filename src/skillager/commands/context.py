from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .. import project_tags
from ..state.paths import catalog_state_root, find_project_root, legacy_project_state_root, state_root
from ..state.statefiles import mutate_user_json, read_user_json


CATALOG_BINDING_SCHEMA = "skillager.project-catalog-binding.v1"


def root(args: argparse.Namespace) -> Path:
    cached = getattr(args, "_skillager_state_root", None)
    if cached:
        return cached
    if args.state_dir:
        resolved = args.state_dir.resolve()
    else:
        resolved = state_root()
        if os.environ.get("SKILLAGER_STATE_DIR") is None:
            warn_legacy_project_state(resolved)
    setattr(args, "_skillager_state_root", resolved)
    return resolved


def current_project_dir() -> Path:
    return (find_project_root() or Path.cwd()).resolve()


def terminal_can_prompt() -> bool:
    """Return whether a CLI prompt would be visible and answerable."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def catalog_root(args: argparse.Namespace) -> Path:
    cached = getattr(args, "_skillager_catalog_root", None)
    if cached:
        return cached
    if getattr(args, "catalog_state_dir", None):
        resolved = args.catalog_state_dir.expanduser().resolve()
        setattr(args, "_skillager_catalog_root", resolved)
        return resolved
    if os.environ.get("SKILLAGER_CATALOG_STATE_DIR") is not None:
        resolved = catalog_state_root().resolve()
        setattr(args, "_skillager_catalog_root", resolved)
        return resolved
    stored = project_tags.load_tags(current_project_dir()).get("catalog_state_dir")
    trusted = _trusted_project_catalog(args)
    if stored and trusted:
        candidate = Path(stored).expanduser().resolve()
        if candidate == trusted:
            setattr(args, "_skillager_catalog_root", candidate)
            return candidate
    resolved = catalog_state_root().resolve()
    setattr(args, "_skillager_catalog_root", resolved)
    return resolved


def remember_project_catalog(args: argparse.Namespace, catalog: Path) -> None:
    """Bind a repository catalog hint to user-owned per-project state."""

    remember_project_catalog_for_state(root(args), current_project_dir(), catalog)


def remember_project_catalog_for_state(project_state: Path, project: Path, catalog: Path) -> None:
    project = project.expanduser().resolve()
    resolved = catalog.expanduser().resolve()

    def mutation(data: dict[str, Any]) -> None:
        data.clear()
        data.update(
            {
                "schema": CATALOG_BINDING_SCHEMA,
                "project": str(project),
                "catalog_state_dir": str(resolved),
            }
        )

    mutate_user_json(_catalog_binding_path(project_state), {}, mutation)


def _trusted_project_catalog(args: argparse.Namespace) -> Path | None:
    data = read_user_json(_catalog_binding_path(root(args)), {})
    if data.get("schema") != CATALOG_BINDING_SCHEMA:
        return None
    if data.get("project") != str(current_project_dir()):
        return None
    stored = data.get("catalog_state_dir")
    if not isinstance(stored, str) or not stored:
        return None
    return Path(stored).expanduser().resolve()


def _catalog_binding_path(project_state: Path) -> Path:
    return project_state / "catalog_binding.json"


def warn_legacy_project_state(new_state_root: Path) -> None:
    legacy_state = legacy_project_state_report(new_state_root)
    if not legacy_state.get("present"):
        return
    legacy = legacy_state["path"]
    print(
        f"skillager: ignoring legacy in-tree state at {legacy}; using {new_state_root}. "
        "Remove the legacy directory after review, then rerun `skillager setup`; Skillager no longer migrates legacy state in place.",
        file=sys.stderr,
    )


def legacy_project_state_report(new_state_root: Path, *, project_dir: Path | None = None) -> dict[str, Any]:
    if os.environ.get("SKILLAGER_STATE_DIR") is not None:
        return {"present": False}
    legacy = legacy_project_state_root(project_dir)
    if not legacy or not legacy.exists():
        return {"present": False}
    try:
        if legacy.resolve() == new_state_root.resolve():
            return {"present": False}
    except OSError:
        pass
    entries = _legacy_project_state_entries(legacy)
    if not entries:
        return {"present": False}
    return {
        "present": True,
        "path": str(legacy),
        "entries": entries,
        "action": "remove-legacy-state-and-rerun-setup",
        "migration": "not-supported",
    }


def _legacy_project_state_entries(legacy: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in legacy.iterdir() if entry.name != "tags.json")
    except OSError:
        return [legacy.name]
