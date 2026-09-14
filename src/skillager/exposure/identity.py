"""Canonical source identity recorded by existing direct and router projections."""
from __future__ import annotations

from typing import Any

from ..library.model import normalize_library_id


def library_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if normalize_library_id(value) == value else None
    except ValueError:
        return None


def source_key(skill_id: str, source_library_id: object = None) -> str | None:
    """Library IDs are local names; all other source policies stay unchanged."""
    if skill_id.startswith("lib/"):
        qualified = library_id(source_library_id)
        return f"library:{qualified}\0{skill_id}" if qualified else None
    return skill_id


def skill_source_key(skill: dict[str, Any]) -> str | None:
    return source_key(str(skill["id"]), (skill.get("source") or {}).get("library_id"))


def member_sources(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"skill_id": str(skill["id"]), "source_library_id": library_id((skill.get("source") or {}).get("library_id"))}
            for skill in skills]


def router_member_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Never partially trust malformed membership or infer legacy library UUIDs."""
    ids = data.get("skill_ids")
    if not isinstance(ids, list) or not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
        return []
    id_set = set(ids)
    unknown = [{"skill_id": skill_id, "source_library_id": None} for skill_id in ids]
    values = data.get("member_sources")
    if not isinstance(values, list) or len(values) != len(ids):
        return unknown
    by_id: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"skill_id", "source_library_id"}:
            return unknown
        skill_id, raw = value["skill_id"], value["source_library_id"]
        if not isinstance(skill_id, str) or skill_id not in id_set or skill_id in by_id or (raw is not None and library_id(raw) is None):
            return unknown
        by_id[skill_id] = {"skill_id": skill_id, "source_library_id": library_id(raw)}
    return [by_id[skill_id] for skill_id in ids]


def recorded_source_keys(data: dict[str, Any]) -> dict[str, str | None]:
    if data.get("source_type") == "skillager-router":
        return {item["skill_id"]: source_key(item["skill_id"], item["source_library_id"])
                for item in router_member_sources(data)}
    skill_id = str(data.get("source_id") or data.get("id"))
    return {skill_id: source_key(skill_id, data.get("source_library_id"))}


def router_sources_match(data: dict[str, Any], skills: list[dict[str, Any]]) -> bool:
    members = router_member_sources(data)
    expected = {str(skill["id"]): skill_source_key(skill) for skill in skills}
    return len(members) == len(expected) == len(skills) and all(
        (key := source_key(item["skill_id"], item["source_library_id"])) is not None
        and expected.get(item["skill_id"]) == key for item in members
    )
