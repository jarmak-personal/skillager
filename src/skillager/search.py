from __future__ import annotations

from typing import Any


def search(
    skills: list[dict[str, Any]],
    query: str,
    *,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    include_untrusted: bool = True,
) -> list[dict[str, Any]]:
    terms = [term.lower() for term in query.split() if term.strip()]
    normalized_query = query.strip().lower()
    results: list[dict[str, Any]] = []
    for skill in skills:
        if skill.get("trust") == "blocked" and not include_blocked:
            continue
        if skill.get("trust") == "lint_blocked" and not include_lint_blocked:
            continue
        if skill.get("trust") == "discovered" and not include_untrusted:
            continue
        haystack = _haystack(skill)
        score = 0
        reasons: list[str] = []
        if normalized_query and normalized_query == skill.get("id", "").lower():
            score += 6
            reasons.append("id:exact")
        for term in terms:
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
        skill.get("package") or "",
    ]
    targets = skill.get("targets", {}).get("python_packages", []) if isinstance(skill.get("targets"), dict) else []
    for target in targets:
        if isinstance(target, dict):
            parts.append(str(target.get("name") or ""))
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
