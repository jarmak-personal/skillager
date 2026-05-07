from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_skill(skill: dict[str, Any], *, fmt: str = "markdown") -> str:
    content = Path(skill["entrypoint"]).read_text(encoding="utf-8", errors="replace")
    context = activation_context(skill)
    preamble = _activation_preamble(context)
    if fmt == "markdown":
        return f"{preamble}\n\n{content}"
    if fmt == "json":
        return json.dumps({"skill": skill, "activation_context": context, "content": content}, indent=2, sort_keys=True)
    if fmt == "codex":
        return f"# Skillager Skill: {skill['id']}\n\n{preamble}\n\n{content}"
    if fmt == "claude":
        return f"---\nname: {skill['id']}\nsummary: {skill.get('summary', '')}\n---\n\n{preamble}\n\n{content}"
    raise ValueError(f"unsupported format: {fmt}")


def activation_context(skill: dict[str, Any]) -> dict[str, Any]:
    entrypoint = Path(skill["entrypoint"]).expanduser().resolve()
    source = skill.get("source") or {}
    source_root = Path(source.get("path") or skill.get("root") or entrypoint.parent).expanduser().resolve()
    return {
        "skill_id": skill.get("id"),
        "source_root": str(source_root),
        "entrypoint": str(entrypoint),
        "source": source,
        "policy": "Resolve relative paths and run repository-local commands from source_root unless the skill explicitly says otherwise.",
    }


def _activation_preamble(context: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## Skillager Activation Context",
            "",
            f"- Skill ID: `{context.get('skill_id')}`",
            f"- Source root: `{context.get('source_root')}`",
            f"- Entrypoint: `{context.get('entrypoint')}`",
            "- Resolve relative paths and run repository-local commands from the source root unless the skill explicitly says otherwise.",
        ]
    )
