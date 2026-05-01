from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .index import build_index, load_index
from .trust import clear_trust, set_trust, trust_state


def selected_skills(
    skills: list[dict[str, Any]],
    *,
    skill_ids: list[str] | None = None,
    source: str | None = None,
    audience: str | None = None,
    package: str | None = None,
    activation: str | None = None,
    include_blocked: bool = False,
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


def review_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    by_audience = Counter()
    by_risk = Counter()
    by_trust = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    for skill in skills:
        source = skill.get("source", {}).get("type") or "unknown"
        audience = skill.get("audience_guess", {}).get("audience") or "unknown"
        risk = skill.get("scan", {}).get("risk") or "unknown"
        by_source[source][risk] += 1
        by_audience[audience] += 1
        by_risk[risk] += 1
        by_trust[skill.get("trust", "discovered")] += 1
        families[_family_key(skill)].add(skill.get("content_hash") or skill["id"])
    family_count = len(families)
    variant_family_count = sum(1 for variants in families.values() if len(variants) > 1)
    return {
        "total": len(skills),
        "families": {"total": family_count, "with_variants": variant_family_count},
        "by_source": {source: dict(counts) for source, counts in sorted(by_source.items())},
        "by_audience": dict(sorted(by_audience.items())),
        "by_risk": dict(sorted(by_risk.items())),
        "by_trust": dict(sorted(by_trust.items())),
    }


def _family_key(skill: dict[str, Any]) -> str:
    name = skill["id"].rsplit("/", 1)[-1]
    for suffix in ("-vibespatial-claude", "-claude", "-codex"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def apply_review_action(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    accept_low: bool = False,
    yolo: bool = False,
    trust_state: str | None = None,
    block_high: bool = False,
    preserve_user_installed: bool = True,
) -> dict[str, Any]:
    changed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for skill in skills:
        risk = skill.get("scan", {}).get("risk")
        if preserve_user_installed and skill.get("trust_reason") == "user-installed":
            skipped.append({"skill_id": skill["id"], "reason": "already trusted as user-installed native skill"})
            continue
        if block_high and risk == "high":
            record = set_trust(state_root, skill["id"], "blocked", skill["content_hash"], skill["source"])
            changed.append({"skill_id": skill["id"], "state": record["state"]})
            continue
        if yolo:
            record = set_trust(state_root, skill["id"], "reviewed", skill["content_hash"], skill["source"])
            changed.append({"skill_id": skill["id"], "state": record["state"]})
            continue
        if trust_state:
            if risk == "high" and trust_state in {"trusted", "pinned"}:
                skipped.append({"skill_id": skill["id"], "reason": "high-risk skills require individual review"})
                continue
            record = set_trust(state_root, skill["id"], trust_state, skill["content_hash"], skill["source"])
            changed.append({"skill_id": skill["id"], "state": record["state"]})
            continue
        if accept_low:
            if risk == "low":
                record = set_trust(state_root, skill["id"], "reviewed", skill["content_hash"], skill["source"])
                changed.append({"skill_id": skill["id"], "state": record["state"]})
            else:
                skipped.append({"skill_id": skill["id"], "reason": f"risk is {risk}"})
    return {"changed": changed, "skipped": skipped}


def setup_environment(
    state_root: Path,
    *,
    paths: list[Path] | None = None,
    include_packages: bool = True,
    source: str | None = None,
    audience: str | None = None,
    package: str | None = None,
    activation: str | None = None,
    skill_ids: list[str] | None = None,
    include_blocked: bool = False,
    include_global: bool = False,
    extra_skills: list[dict[str, Any]] | None = None,
    fresh: bool = False,
    accept_low: bool = False,
    trust_state: str | None = None,
    block_high: bool = False,
    yolo: bool = False,
) -> dict[str, Any]:
    data = build_index(state_root, paths, include_packages=include_packages)
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    skipped_global = 0
    if not include_global and source is None:
        skipped_global = sum(1 for skill in data.get("skills", []) if skill.get("source", {}).get("type") == "global")
    skills = selected_skills(
        data.get("skills", []),
        skill_ids=skill_ids,
        source=source,
        audience=audience,
        package=package,
        activation=activation,
        include_blocked=include_blocked,
        include_global=include_global,
    )
    fresh_reset = 0
    if fresh:
        fresh_reset = clear_trust(state_root, [skill["id"] for skill in skills])
        data = load_index(state_root)
        if extra_skills:
            data["skills"] = [*data.get("skills", []), *extra_skills]
        skills = selected_skills(
            data.get("skills", []),
            skill_ids=skill_ids,
            source=source,
            audience=audience,
            package=package,
            activation=activation,
            include_blocked=include_blocked,
            include_global=include_global,
        )
    action = apply_review_action(
        state_root,
        skills,
        accept_low=accept_low,
        yolo=yolo,
        trust_state=trust_state,
        block_high=block_high,
        preserve_user_installed=True,
    )
    refreshed = load_index(state_root)
    if extra_skills:
        extra_skills = _refresh_extra_skill_trust(state_root, extra_skills)
        refreshed["skills"] = [*refreshed.get("skills", []), *extra_skills]
    selected = selected_skills(
        refreshed.get("skills", []),
        skill_ids=skill_ids,
        source=source,
        audience=audience,
        package=package,
        activation=activation,
        include_blocked=include_blocked or block_high,
        include_global=include_global,
    )
    return {
        "indexed": len(data.get("skills", [])),
        "skipped_global": skipped_global,
        "fresh_reset": fresh_reset,
        "errors": data.get("errors", []),
        "selected": selected,
        "summary": review_summary(selected),
        "action": action,
    }


def _refresh_extra_skill_trust(state_root: Path, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed = []
    for skill in skills:
        item = dict(skill)
        if item.get("id") and item.get("content_hash"):
            item["trust"] = trust_state(state_root, item["id"], item["content_hash"])
        refreshed.append(item)
    return refreshed
