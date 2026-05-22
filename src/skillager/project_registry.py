from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_REGISTRY_SCHEMA = "skillager.projects.v1"


def registry_path(catalog_root: Path) -> Path:
    return catalog_root.expanduser().resolve() / "projects.json"


def load_registry(catalog_root: Path) -> dict[str, Any]:
    path = registry_path(catalog_root)
    if not path.exists():
        return {"schema": PROJECT_REGISTRY_SCHEMA, "projects": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"schema": PROJECT_REGISTRY_SCHEMA, "projects": {}}
    data.setdefault("schema", PROJECT_REGISTRY_SCHEMA)
    projects = data.get("projects")
    if not isinstance(projects, dict):
        data["projects"] = {}
    return data


def save_registry(catalog_root: Path, data: dict[str, Any]) -> None:
    data = {"schema": PROJECT_REGISTRY_SCHEMA, "projects": dict(data.get("projects") or {})}
    path = registry_path(catalog_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_project(catalog_root: Path, project_dir: Path, *, state_dir: Path | None = None) -> dict[str, Any]:
    project = project_dir.expanduser().resolve()
    data = load_registry(catalog_root)
    projects = data.setdefault("projects", {})
    entry = {
        "path": str(project),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if state_dir is not None:
        entry["state_dir"] = str(state_dir.expanduser().resolve())
    projects[str(project)] = entry
    save_registry(catalog_root, data)
    return entry


def known_projects(catalog_root: Path) -> list[Path]:
    projects = []
    for raw in (load_registry(catalog_root).get("projects") or {}):
        try:
            project = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if project.exists():
            projects.append(project)
    return sorted(dict.fromkeys(projects))
