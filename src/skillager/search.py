from __future__ import annotations

from typing import Any


def search(skills: list[dict[str, Any]], query: str, *, include_blocked: bool = False, include_untrusted: bool = True) -> list[dict[str, Any]]:
    terms = [term.lower() for term in query.split() if term.strip()]
    results: list[dict[str, Any]] = []
    for skill in skills:
        if skill.get("trust") == "blocked" and not include_blocked:
            continue
        if skill.get("trust") == "discovered" and not include_untrusted:
            continue
        haystack = _haystack(skill)
        score = 0
        reasons: list[str] = []
        for term in terms:
            if term in skill.get("id", "").lower():
                score += 5
                reasons.append(f"id:{term}")
            if term in skill.get("name", "").lower():
                score += 4
                reasons.append(f"name:{term}")
            if term in haystack:
                score += 1
                reasons.append(term)
        if not terms or score:
            item = dict(skill)
            item["score"] = score
            item["reasons"] = sorted(set(reasons))
            results.append(item)
    return sorted(results, key=lambda item: (-item["score"], _visibility_rank(item), item["id"]))


def _haystack(skill: dict[str, Any]) -> str:
    parts = [
        skill.get("summary", ""),
        " ".join(skill.get("audience", [])),
        " ".join(skill.get("domains", [])),
        skill.get("package") or "",
    ]
    triggers = skill.get("triggers", {})
    if isinstance(triggers, dict):
        for value in triggers.values():
            if isinstance(value, list):
                parts.append(" ".join(str(item) for item in value))
            else:
                parts.append(str(value))
    return " ".join(parts).lower()


def _visibility_rank(skill: dict[str, Any]) -> int:
    exposure = skill.get("exposure")
    if exposure == "multiple":
        return 0
    if exposure == "native":
        return 1
    if exposure == "stub":
        return 2
    if exposure == "router":
        return 3
    if "attached-tag" in set(skill.get("availability", [])):
        return 4
    if skill.get("source", {}).get("type") == "project":
        return 5
    if skill.get("source", {}).get("type") == "collection":
        return 6
    if skill.get("source", {}).get("type") == "python-package":
        return 7
    return 8
