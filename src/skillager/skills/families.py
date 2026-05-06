from __future__ import annotations

from typing import Any, Mapping


def agent_variant_family_key(skill: Mapping[str, Any]) -> str:
    skill_id = str(skill.get("id") or "")
    namespace, separator, slug = skill_id.rpartition("/")
    slug = canonical_agent_variant_slug(slug or skill_id)
    if separator and namespace:
        return f"{namespace}/{slug}"
    source = skill.get("source") or {}
    if isinstance(source, Mapping):
        prefix = source.get("collection") or source.get("package") or source.get("type") or "skill"
    else:
        prefix = "skill"
    return f"{prefix}/{slug}"


def duplicate_content_family_key(skill: Mapping[str, Any]) -> str:
    skill_id = str(skill.get("id") or "")
    slug = skill_id.rsplit("/", 1)[-1] if skill_id else ""
    slug = canonical_agent_variant_slug(slug or skill_id)
    return slug or agent_variant_family_key(skill)


def canonical_agent_variant_slug(value: str) -> str:
    slug = value.strip().lower()
    suffixes = (
        "-vibespatial-claude",
        "-vibespatial-codex",
        "-claude-skill",
        "-codex-skill",
        "-claude",
        "-codex",
    )
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if slug.endswith(suffix) and len(slug) > len(suffix):
                slug = slug[: -len(suffix)]
                changed = True
                break
    return slug
