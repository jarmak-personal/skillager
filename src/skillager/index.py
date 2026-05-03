from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audience import classify_audience
from .authored import mark_authored_metadata
from .discovery import discover
from .families import canonical_agent_variant_slug
from .lint import lint_skill
from .scan import scan_path
from .trust import approval_key_for, content_hash, trust_info


def index_path(state_root: Path) -> Path:
    return state_root / "index.json"


def build_index(
    state_root: Path,
    paths: list[Path] | None = None,
    *,
    include_packages: bool = True,
    approval_root: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> dict[str, Any]:
    approval_root = approval_root or state_root
    skills, errors = discover(paths, include_packages=include_packages, extra_paths=extra_paths)
    entries = []
    for skill in skills:
        digest = content_hash(skill.root)
        scan = scan_path(skill.root, allow_tools=False)
        lint = skill.lint if getattr(skill, "lint", None) else lint_skill(skill)
        approval_key = approval_key_for(skill.id, skill.root, skill.source, entrypoint=skill.entrypoint)
        trust = trust_info(state_root, skill.id, digest, lint=lint, approval_key=approval_key, approval_root=approval_root)
        entry = skill.to_index(digest, scan, trust.get("state", "discovered"))
        _apply_approval_metadata(entry, approval_key, trust)
        entry["lint"] = lint
        if not entry.get("quarantined"):
            entry["audience_guess"] = classify_audience(skill)
        native = _native_info(entry)
        if native:
            entry["native"] = native
        mark_authored_metadata(entry)
        entries.append(entry)
    entries.sort(key=lambda item: item["id"])
    data = {"version": 1, "skills": entries, "errors": errors}
    state_root.mkdir(parents=True, exist_ok=True)
    index_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def load_index(state_root: Path, *, approval_root: Path | None = None) -> dict[str, Any]:
    approval_root = approval_root or state_root
    path = index_path(state_root)
    if not path.exists():
        return build_index(state_root, approval_root=approval_root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for skill in data.get("skills", []):
        if skill.get("id") and skill.get("content_hash"):
            approval_key = skill.get("approval_key") or approval_key_for(
                skill["id"],
                skill.get("root"),
                skill.get("source") or {},
                entrypoint=skill.get("entrypoint"),
            )
            trust = trust_info(
                state_root,
                skill["id"],
                skill["content_hash"],
                lint=skill.get("lint"),
                approval_key=approval_key,
                approval_root=approval_root,
            )
            _apply_approval_metadata(skill, approval_key, trust)
            mark_authored_metadata(skill)
    return data


def find_skill(state_root: Path, skill_id: str, *, approval_root: Path | None = None) -> dict[str, Any]:
    data = load_index(state_root, approval_root=approval_root)
    for skill in data.get("skills", []):
        if skill.get("id") == skill_id:
            return skill
    raise KeyError(f"skill not found: {skill_id}")


def _apply_approval_metadata(entry: dict[str, Any], approval_key: str | None, trust: dict[str, Any]) -> None:
    entry["trust"] = trust.get("state", "discovered")
    for key in ("trust_reason", "trust_scope"):
        entry.pop(key, None)
    if approval_key:
        entry["approval_key"] = approval_key
    if trust.get("reason"):
        entry["trust_reason"] = trust["reason"]
    if trust.get("scope"):
        entry["trust_scope"] = trust["scope"]


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
        "family_key": canonical_agent_variant_slug(skill["id"].rsplit("/", 1)[-1]),
    }
