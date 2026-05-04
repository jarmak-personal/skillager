from __future__ import annotations

from typing import Any


def select_visible_skills(
    skills: list[dict[str, Any]],
    *,
    skill_ids: list[str] | None = None,
    source: str | None = None,
    audience: str | None = None,
    package: str | None = None,
    activation: str | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    include_global: bool = True,
) -> list[dict[str, Any]]:
    requested = set(skill_ids or [])
    result = []
    for skill in skills:
        source_type = skill.get("source", {}).get("type")
        if requested and skill["id"] not in requested:
            continue
        if skill.get("trust") == "blocked" and not include_blocked:
            continue
        if skill.get("trust") == "lint_blocked" and not include_lint_blocked:
            continue
        if source and source_type != source:
            continue
        if not include_global and source is None and source_type == "global":
            continue
        if audience and not _matches_audience(skill, audience):
            continue
        if package and skill.get("package") != package and skill.get("source", {}).get("package") != package:
            continue
        if activation and skill.get("activation") != activation:
            continue
        result.append(skill)
    return result


def _matches_audience(skill: dict[str, Any], audience: str) -> bool:
    requested = "dev" if audience in {"developer", "maintainer", "maintainers"} else audience
    guess = skill.get("audience_guess", {}).get("audience")
    if guess == requested:
        return True
    if guess and guess != "unknown":
        return False
    return requested in skill.get("audience", [])
