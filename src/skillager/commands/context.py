from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .. import project_tags
from ..state.paths import catalog_state_root, find_project_root, legacy_project_state_root, state_root


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


def catalog_root(args: argparse.Namespace) -> Path:
    if getattr(args, "catalog_state_dir", None):
        return args.catalog_state_dir.resolve()
    stored = project_tags.load_tags(current_project_dir()).get("catalog_state_dir")
    if stored:
        return Path(stored).expanduser().resolve()
    return catalog_state_root()


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
