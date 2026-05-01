from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audience import classify_audience
from .discovery import discover
from .scan import scan_path
from .trust import content_hash, trust_state


def index_path(state_root: Path) -> Path:
    return state_root / "index.json"


def build_index(state_root: Path, paths: list[Path] | None = None, *, include_packages: bool = True) -> dict[str, Any]:
    skills, errors = discover(paths, include_packages=include_packages)
    entries = []
    for skill in skills:
        digest = content_hash(skill.root)
        scan = scan_path(skill.root, allow_tools=bool(skill.safety.get("allow_tools", False)))
        trust = trust_state(state_root, skill.id, digest)
        entry = skill.to_index(digest, scan, trust)
        entry["audience_guess"] = classify_audience(skill)
        native = _native_info(entry)
        if native:
            entry["native"] = native
            _apply_user_installed_trust(entry)
        entries.append(entry)
    entries.sort(key=lambda item: item["id"])
    data = {"version": 1, "skills": entries, "errors": errors}
    state_root.mkdir(parents=True, exist_ok=True)
    index_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def load_index(state_root: Path) -> dict[str, Any]:
    path = index_path(state_root)
    if not path.exists():
        return build_index(state_root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for skill in data.get("skills", []):
        if skill.get("id") and skill.get("content_hash"):
            skill["trust"] = trust_state(state_root, skill["id"], skill["content_hash"])
            _apply_user_installed_trust(skill)
    return data


def find_skill(state_root: Path, skill_id: str) -> dict[str, Any]:
    data = load_index(state_root)
    for skill in data.get("skills", []):
        if skill.get("id") == skill_id:
            return skill
    raise KeyError(f"skill not found: {skill_id}")


def _native_info(skill: dict[str, Any]) -> dict[str, Any] | None:
    source_type = skill.get("source", {}).get("type")
    if source_type not in {"project", "global"}:
        return None
    root = str(skill.get("root") or "")
    agent = skill.get("source", {}).get("agent")
    if not agent:
        if "/.agents/skills/" in root or "/.agents/codex/skills/" in root or "/.codex/skills/" in root:
            agent = "codex"
        elif "/.claude/skills/" in root or "/.agents/claude/skills/" in root:
            agent = "claude"
    if not agent:
        return None
    path = Path(root)
    return {
        "agent": agent,
        "scope": "global" if source_type == "global" else "project",
        "path": root,
        "managed": (path / "skillager.materialized.yaml").exists(),
        "customized": False,
        "family_key": skill["id"].rsplit("/", 1)[-1],
    }


def _apply_user_installed_trust(skill: dict[str, Any]) -> None:
    native = skill.get("native") or {}
    if not native:
        return
    if native.get("managed"):
        return
    if skill.get("trust") != "discovered":
        return
    skill["trust"] = "trusted"
    skill["trust_reason"] = "user-installed"
