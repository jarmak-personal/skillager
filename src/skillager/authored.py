from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import find_project_root, state_home
from .statefiles import read_user_json, write_user_json
from .trust import content_hash

AUTHORED_SCHEMA = "skillager.authored.v1"


def authored_path() -> Path:
    return state_home() / "skillager" / "authored.json"


def project_key(project_root: Path) -> str:
    return hashlib.sha256(str(project_root.expanduser().resolve()).encode("utf-8")).hexdigest()


def load_authored() -> dict[str, Any]:
    data = read_user_json(authored_path(), {"schema": AUTHORED_SCHEMA, "skills": {}})
    data.setdefault("schema", AUTHORED_SCHEMA)
    data.setdefault("skills", {})
    return data


def save_authored(data: dict[str, Any]) -> None:
    data.setdefault("schema", AUTHORED_SCHEMA)
    data.setdefault("skills", {})
    write_user_json(authored_path(), data)


def record_authored_skill(skill_root: Path, *, project_root: Path, agent: str) -> dict[str, Any]:
    skill_root = skill_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    record = {
        "project_key": project_key(project_root),
        "skill_root": str(skill_root),
        "created_by_skillager_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_hash_at_creation": content_hash(skill_root),
        "agent": agent,
    }
    data = load_authored()
    data.setdefault("skills", {})[str(skill_root)] = record
    save_authored(data)
    return record


def authored_info(skill: dict[str, Any], *, project_root: Path | None = None) -> dict[str, Any] | None:
    root = skill.get("root")
    if not root:
        return None
    try:
        skill_root = Path(str(root)).expanduser().resolve()
    except (OSError, ValueError):
        return None
    project_root = (project_root or find_project_root()).expanduser().resolve()
    if not _is_relative_to(skill_root, project_root):
        return None
    try:
        authored = load_authored()
    except ValueError:
        return None
    record = authored.get("skills", {}).get(str(skill_root))
    if not isinstance(record, dict):
        return None
    if record.get("project_key") != project_key(project_root):
        return None
    if record.get("skill_root") != str(skill_root):
        return None
    if not skill_root.exists():
        return None
    if (skill.get("lint") or {}).get("status") == "blocked":
        return None
    return dict(record)


def mark_authored_metadata(skill: dict[str, Any], *, project_root: Path | None = None) -> None:
    info = authored_info(skill, project_root=project_root)
    if not info:
        skill.pop("authored", None)
        skill.pop("authored_agent", None)
        return
    skill["authored"] = True
    if info.get("agent"):
        skill["authored_agent"] = info["agent"]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
