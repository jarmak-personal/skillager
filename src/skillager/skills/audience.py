from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AudienceSignal:
    audience: str
    weight: int
    reason: str


DEV_SIGNALS = [
    ("commit", 3, "mentions commit workflow"),
    ("pre-land", 3, "mentions pre-land workflow"),
    ("land this", 3, "mentions landing workflow"),
    ("ship it", 2, "mentions shipping workflow"),
    ("review gate", 3, "mentions review gate"),
    ("code-review", 3, "mentions code review"),
    ("code review", 3, "mentions code review"),
    ("maintainer", 3, "mentions maintainer work"),
    ("kernel", 2, "mentions kernel work"),
    ("cuda", 2, "mentions CUDA development"),
    ("dispatch", 2, "mentions internal dispatch"),
    ("wiring", 2, "mentions internal wiring"),
    ("autonomous-execution", 2, "mentions agent execution workflow"),
    ("intake-router", 2, "mentions internal routing"),
    ("precision-compliance", 2, "mentions precision/compliance workflow"),
]

USER_SIGNALS = [
    ("gis-domain", 3, "mentions GIS domain guidance"),
    ("domain", 2, "mentions domain guidance"),
    ("concept", 2, "mentions conceptual guidance"),
    ("library usage", 3, "mentions library usage"),
    ("how to use", 3, "mentions usage guidance"),
    ("api", 2, "mentions API usage"),
    ("example", 1, "mentions examples"),
    ("tutorial", 2, "mentions tutorial guidance"),
    ("dataframe", 2, "mentions dataframe usage"),
]


def classify_audience(skill: Any) -> dict[str, Any]:
    """Classify intended audience using only inert metadata and path-derived signals."""
    text = _classification_text(skill)
    signals = _signals(text)
    declared = _declared_audiences(skill)
    for audience in declared:
        signals.append(AudienceSignal(audience, 2, f"declared audience: {audience}"))

    scores: dict[str, int] = {"user": 0, "dev": 0}
    reasons: dict[str, list[str]] = {"user": [], "dev": []}
    for signal in signals:
        if signal.audience not in scores:
            continue
        scores[signal.audience] += signal.weight
        if signal.reason not in reasons[signal.audience]:
            reasons[signal.audience].append(signal.reason)

    audience = "unknown"
    confidence = "low"
    selected_reasons: list[str] = []
    if scores["dev"] > scores["user"] and scores["dev"] >= 2:
        audience = "dev"
        confidence = _confidence(scores["dev"], scores["user"])
        selected_reasons = reasons["dev"][:3]
    elif scores["user"] > scores["dev"] and scores["user"] >= 2:
        audience = "user"
        confidence = _confidence(scores["user"], scores["dev"])
        selected_reasons = reasons["user"][:3]
    elif declared:
        audience = "unknown"
        selected_reasons = [f"conflicting or weak declared audience: {', '.join(declared)}"]
    else:
        selected_reasons = ["no strong audience signals in metadata"]

    return {
        "audience": audience,
        "confidence": confidence,
        "reasons": selected_reasons,
        "scores": scores,
        "method": "metadata-heuristic",
    }


def _classification_text(skill: Any) -> str:
    parts = [
        getattr(skill, "id", ""),
        getattr(skill, "name", ""),
        getattr(skill, "summary", ""),
        str(getattr(skill, "entrypoint", "")),
        str(getattr(skill, "root", "")),
        str(getattr(skill, "package", "") or ""),
    ]
    source = getattr(skill, "source", {}) or {}
    if isinstance(source, dict):
        parts.extend(str(value) for value in source.values())
    return " ".join(parts).lower().replace("_", "-")


def _declared_audiences(skill: Any) -> list[str]:
    if getattr(skill, "inferred", False):
        return []
    result = []
    for item in getattr(skill, "audience", []) or []:
        value = item.lower()
        if value in {"developer", "maintainer", "maintainers"}:
            value = "dev"
        if value in {"user", "dev"} and value not in result:
            result.append(value)
    return result


def _signals(text: str) -> list[AudienceSignal]:
    result: list[AudienceSignal] = []
    for needle, weight, reason in DEV_SIGNALS:
        if needle in text:
            result.append(AudienceSignal("dev", weight, reason))
    for needle, weight, reason in USER_SIGNALS:
        if needle in text:
            result.append(AudienceSignal("user", weight, reason))
    return result


def _confidence(score: int, other_score: int) -> str:
    margin = score - other_score
    if score >= 5 and margin >= 3:
        return "high"
    if score >= 3 and margin >= 2:
        return "medium"
    return "low"
