from __future__ import annotations

from typing import Any


AUDIENCE_OTHER = "other"


def classify_audience(skill: Any) -> dict[str, Any]:
    """Classify intended audience from explicit structured metadata only."""
    declared = declared_audiences(skill)
    audience = audience_bucket(skill)
    if declared:
        reasons = [f"declared audience: {', '.join(declared)}"]
        confidence = "declared"
    else:
        reasons = ["no declared audience metadata"]
        confidence = "undeclared"

    return {
        "audience": audience,
        "audiences": declared,
        "confidence": confidence,
        "reasons": reasons,
        "method": "declared-metadata",
    }


def declared_audiences(skill: Any) -> list[str]:
    if _value(skill, "inferred", False):
        return []
    result = []
    for item in _value(skill, "audience", []) or []:
        value = item.lower()
        if value in {"developer", "maintainer", "maintainers"}:
            value = "dev"
        if value in {"user", "dev"} and value not in result:
            result.append(value)
    return result


def audience_bucket(skill: Any) -> str:
    declared = declared_audiences(skill)
    if not declared:
        return AUDIENCE_OTHER
    if len(declared) == 1:
        return declared[0]
    return "+".join(declared)


def audience_bucket_label(value: str | None) -> str:
    if value == AUDIENCE_OTHER:
        return "everything else"
    if value == "user+dev":
        return "user+dev"
    return value or AUDIENCE_OTHER


def _value(skill: Any, key: str, default: Any = None) -> Any:
    if isinstance(skill, dict):
        return skill.get(key, default)
    return getattr(skill, key, default)
