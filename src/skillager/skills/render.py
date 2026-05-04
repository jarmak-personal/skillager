from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_skill(skill: dict[str, Any], *, fmt: str = "markdown") -> str:
    content = Path(skill["entrypoint"]).read_text(encoding="utf-8", errors="replace")
    if fmt == "markdown":
        return content
    if fmt == "json":
        return json.dumps({"skill": skill, "content": content}, indent=2, sort_keys=True)
    if fmt == "codex":
        return f"# Skillager Skill: {skill['id']}\n\n{content}"
    if fmt == "claude":
        return f"---\nname: {skill['id']}\nsummary: {skill.get('summary', '')}\n---\n\n{content}"
    raise ValueError(f"unsupported format: {fmt}")
