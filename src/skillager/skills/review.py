from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..families import agent_variant_family_key, duplicate_content_family_key
from ..index import build_index, load_index
from ..review_gates import approval_state, review_gates
from ..selection import select_visible_skills
from ..trust import clear_trust, make_lint_override, set_trust, trust_state
from .audience import audience_bucket


APPROVED_REVIEW_STATES = {"reviewed", "trusted", "pinned"}
YOLO_LINT_OVERRIDE_REASON = "accepted by --yolo/--trust-all trusted-source shortcut"


def review_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    by_audience: Counter[str] = Counter()
    by_risk: Counter[str] = Counter()
    by_trust: Counter[str] = Counter()
    by_approval: Counter[str] = Counter()
    by_review_availability: Counter[str] = Counter()
    by_signature: Counter[str] = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    for skill in skills:
        source = skill.get("source", {}).get("type") or "unknown"
        audience = audience_bucket(skill)
        risk = skill.get("scan", {}).get("risk") or "unknown"
        gates = skill.get("review_gates") or review_gates(skill)
        by_source[source][risk] += 1
        by_audience[audience] += 1
        by_risk[risk] += 1
        by_trust[skill.get("trust", "discovered")] += 1
        by_approval[skill.get("approval") or approval_state(skill)] += 1
        by_review_availability[gates.get("availability", "unknown")] += 1
        by_signature[gates.get("signature", "unknown")] += 1
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
        "by_approval": dict(sorted(by_approval.items())),
        "by_review_availability": dict(sorted(by_review_availability.items())),
        "by_signature": dict(sorted(by_signature.items())),
        "duplicate_content": duplicate_content_summary(skills),
    }


def _family_key(skill: dict[str, Any]) -> str:
    return agent_variant_family_key(skill)


def annotate_duplicate_content(
    skills: list[dict[str, Any]],
    *,
    context: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    annotated = [dict(skill) for skill in skills]
    selected_ids = {id(skill) for skill in annotated}
    selected_signatures = {_skill_signature(skill) for skill in annotated}
    context_items = [
        dict(skill)
        for skill in (context or [])
        if _skill_signature(skill) not in selected_signatures
    ]
    for _, _, group in _duplicate_content_group_entries([*annotated, *context_items]):
        approved_ids = _approved_duplicate_ids(group)
        review_needed_ids = _review_needed_duplicate_ids(group)
        if not approved_ids or not review_needed_ids:
            continue
        group_ids = _skill_ids(group)
        for skill in group:
            if id(skill) not in selected_ids:
                continue
            if skill.get("trust") != "discovered":
                continue
            skill["duplicate_of_reviewed"] = {
                "family_key": duplicate_content_family_key(skill),
                "content_hash": skill.get("content_hash"),
                "approved_ids": approved_ids,
                "approved_count": len(approved_ids),
                "group_ids": group_ids,
                "source_key_approval_required": True,
                "message": "same content already approved under another source key; review records approval for this source",
            }
    return annotated


def duplicate_content_summary(skills: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _summary_duplicate_content_groups(skills)
    review_needed_ids = sorted(
        {
            skill_id
            for group in groups
            for skill_id in group.get("review_needed_ids", [])
            if group.get("approved_ids")
        }
    )
    return {
        "groups": len(groups),
        "skill_count": sum(int(group.get("count") or 0) for group in groups),
        "approved_overlap_groups": sum(1 for group in groups if group.get("approved_ids") and group.get("review_needed_ids")),
        "source_key_approval_required": len(review_needed_ids),
        "review_needed": len(review_needed_ids),
        "review_needed_ids": review_needed_ids,
        "groups_detail": groups,
    }


def _summary_duplicate_content_groups(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Start with duplicate content visible in the current scope, then supplement
    # from annotations that reference approved duplicates outside filtered scope.
    groups_by_key = {
        (str(group.get("family_key") or ""), str(group.get("content_hash") or "")): dict(group)
        for group in duplicate_content_groups(skills)
    }
    for skill in skills:
        duplicate = skill.get("duplicate_of_reviewed") or {}
        family_key = str(duplicate.get("family_key") or "")
        content_hash_value = str(duplicate.get("content_hash") or "")
        if not family_key or not content_hash_value:
            continue
        key = (family_key, content_hash_value)
        group = groups_by_key.setdefault(
            key,
            {
                "family_key": family_key,
                "content_hash": content_hash_value,
                "count": len(duplicate.get("group_ids") or []),
                "ids": list(duplicate.get("group_ids") or []),
                "approved": 0,
                "approved_ids": [],
                "review_needed": 0,
                "review_needed_ids": [],
                "source_key_approval_required": False,
            },
        )
        group["ids"] = sorted(set(group.get("ids") or []) | set(duplicate.get("group_ids") or []))
        group["approved_ids"] = sorted(set(group.get("approved_ids") or []) | set(duplicate.get("approved_ids") or []))
        if skill.get("id"):
            group["review_needed_ids"] = sorted(set(group.get("review_needed_ids") or []) | {str(skill["id"])})
        group["approved"] = len(group.get("approved_ids") or [])
        group["review_needed"] = len(group.get("review_needed_ids") or [])
        group["count"] = max(int(group.get("count") or 0), len(group.get("ids") or []))
        group["source_key_approval_required"] = bool(group.get("approved_ids") and group.get("review_needed_ids"))
    return [
        groups_by_key[key]
        for key in sorted(groups_by_key)
    ]


def duplicate_content_groups(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for family_key, content_hash_value, group in _duplicate_content_group_entries(skills):
        approved_ids = _approved_duplicate_ids(group)
        review_needed_ids = _review_needed_duplicate_ids(group)
        groups.append(
            {
                "family_key": family_key,
                "content_hash": content_hash_value,
                "count": len(group),
                "ids": _skill_ids(group),
                "approved": len(approved_ids),
                "approved_ids": approved_ids,
                "review_needed": len(review_needed_ids),
                "review_needed_ids": review_needed_ids,
                "source_key_approval_required": bool(approved_ids and review_needed_ids),
            }
        )
    return groups


def duplicate_content_group_entries(skills: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    return _duplicate_content_group_entries(skills)


def _duplicate_content_group_entries(skills: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for skill in skills:
        content_hash_value = skill.get("content_hash")
        if not isinstance(content_hash_value, str) or not content_hash_value:
            continue
        groups[(duplicate_content_family_key(skill), content_hash_value)].append(skill)
    return [
        (family_key, content_hash_value, sorted(group, key=_duplicate_skill_sort_key))
        for (family_key, content_hash_value), group in sorted(groups.items())
        if len(group) > 1
    ]


def _duplicate_skill_sort_key(skill: dict[str, Any]) -> tuple[int, str, str]:
    source_type = str((skill.get("source") or {}).get("type") or "")
    source_rank = {"project": 0, "collection": 1, "python-package": 2, "npm-package": 2, "cargo-package": 2, "environment": 3, "global": 4}.get(source_type, 5)
    return (source_rank, str(skill.get("id") or ""), str(skill.get("entrypoint") or ""))


def _skill_signature(skill: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(skill.get("id") or ""),
        str(skill.get("content_hash") or ""),
        str(skill.get("entrypoint") or ""),
    )


def _approved_duplicate_ids(skills: list[dict[str, Any]]) -> list[str]:
    return _skill_ids([skill for skill in skills if skill.get("trust") in APPROVED_REVIEW_STATES])


def _review_needed_duplicate_ids(skills: list[dict[str, Any]]) -> list[str]:
    return _skill_ids([skill for skill in skills if skill.get("trust") == "discovered"])


def _skill_ids(skills: list[dict[str, Any]]) -> list[str]:
    return sorted(str(skill.get("id")) for skill in skills if skill.get("id"))


def apply_review_action(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    approval_root: Path | None = None,
    global_scope: bool = False,
    accept_low: bool = False,
    yolo: bool = False,
    trust_state: str | None = None,
    block_high: bool = False,
    override_lint: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    if override_lint and not (reason or "").strip():
        raise ValueError("--reason is required with --override-lint")
    changed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    override_lint_only = override_lint and not any((accept_low, yolo, trust_state, block_high))
    for skill in skills:
        risk = skill.get("scan", {}).get("risk")
        lint_override = None
        if skill.get("trust") == "lint_blocked":
            if override_lint or yolo:
                lint_override = make_lint_override(reason or YOLO_LINT_OVERRIDE_REASON, skill.get("lint") or {})
            elif not block_high:
                skipped.append({"skill_id": skill["id"], "reason": "lint-blocked; fix source or use --override-lint --reason"})
                continue
        if block_high and risk == "high":
            record = set_trust(state_root, skill["id"], "blocked", skill["content_hash"], skill["source"], lint=skill.get("lint"))
            changed.append(_review_action_item(skill, record))
            continue
        if yolo:
            record = _set_review_trust(
                state_root,
                skill,
                "reviewed",
                lint_override=lint_override,
                approval_root=approval_root,
                global_scope=global_scope,
            )
            changed.append(_review_action_item(skill, record))
            continue
        if trust_state:
            if risk == "high" and trust_state in {"trusted", "pinned"}:
                skipped.append({"skill_id": skill["id"], "reason": "high-risk skills require individual review"})
                continue
            record = _set_review_trust(
                state_root,
                skill,
                trust_state,
                lint_override=lint_override,
                approval_root=approval_root,
                global_scope=global_scope,
            )
            changed.append(_review_action_item(skill, record))
            continue
        if override_lint_only and lint_override:
            record = _set_review_trust(
                state_root,
                skill,
                "reviewed",
                lint_override=lint_override,
                approval_root=approval_root,
                global_scope=global_scope,
            )
            changed.append(_review_action_item(skill, record))
            continue
        if accept_low:
            if risk == "low":
                record = _set_review_trust(
                    state_root,
                    skill,
                    "reviewed",
                    lint_override=lint_override,
                    approval_root=approval_root,
                    global_scope=global_scope,
                )
                changed.append(_review_action_item(skill, record))
            else:
                skipped.append({"skill_id": skill["id"], "reason": f"risk is {risk}"})
    return {"changed": changed, "skipped": skipped}


def _review_action_item(skill: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    item = {"skill_id": skill["id"], "state": record["state"], "scope": record.get("scope", "project")}
    duplicate = skill.get("duplicate_of_reviewed")
    if duplicate:
        item["duplicate_of_reviewed"] = duplicate
    return item


def _set_review_trust(
    state_root: Path,
    skill: dict[str, Any],
    state: str,
    *,
    lint_override: dict[str, Any] | None,
    approval_root: Path | None,
    global_scope: bool,
) -> dict[str, Any]:
    return set_trust(
        state_root,
        skill["id"],
        state,
        skill["content_hash"],
        skill["source"],
        lint=skill.get("lint"),
        lint_override=lint_override,
        approval_key=skill.get("approval_key"),
        approval_root=approval_root,
        global_scope=global_scope,
    )


def setup_environment(
    state_root: Path,
    *,
    paths: list[Path] | None = None,
    extra_paths: list[Path] | None = None,
    include_packages: bool = True,
    source: str | None = None,
    audience: str | None = None,
    package: str | None = None,
    activation: str | None = None,
    skill_ids: list[str] | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    include_global: bool = False,
    extra_skills: list[dict[str, Any]] | None = None,
    fresh: bool = False,
    fresh_project: bool = False,
    accept_low: bool = False,
    trust_state: str | None = None,
    block_high: bool = False,
    yolo: bool = False,
    override_lint: bool = False,
    reason: str | None = None,
    approval_root: Path | None = None,
    global_scope: bool = False,
) -> dict[str, Any]:
    review_include_lint_blocked = include_lint_blocked or override_lint or yolo
    data = build_index(state_root, paths, include_packages=include_packages, approval_root=approval_root, extra_paths=extra_paths)
    if extra_skills:
        data["skills"] = [*data.get("skills", []), *extra_skills]
    skipped_global = 0
    if not include_global and source is None:
        skipped_global = sum(1 for skill in data.get("skills", []) if skill.get("source", {}).get("type") == "global")
    skills = select_visible_skills(
        data.get("skills", []),
        skill_ids=skill_ids,
        source=source,
        audience=audience,
        package=package,
        activation=activation,
        include_blocked=include_blocked,
        include_lint_blocked=review_include_lint_blocked,
        include_global=include_global,
    )
    skills = annotate_duplicate_content(skills)
    fresh_reset = 0
    global_reset = 0
    if fresh or fresh_project:
        fresh_reset = clear_trust(state_root, [skill["id"] for skill in skills])
        data = load_index(state_root, approval_root=approval_root)
        if extra_skills:
            extra_skills = _refresh_extra_skill_trust(state_root, extra_skills, approval_root=approval_root)
            data["skills"] = [*data.get("skills", []), *extra_skills]
        skills = select_visible_skills(
            data.get("skills", []),
            skill_ids=skill_ids,
            source=source,
            audience=audience,
            package=package,
            activation=activation,
            include_blocked=include_blocked,
            include_lint_blocked=review_include_lint_blocked,
            include_global=include_global,
        )
        skills = annotate_duplicate_content(skills)
    action = apply_review_action(
        state_root,
        skills,
        accept_low=accept_low,
        yolo=yolo,
        trust_state=trust_state,
        block_high=block_high,
        override_lint=override_lint,
        reason=reason,
        approval_root=approval_root,
        global_scope=global_scope,
    )
    refreshed = load_index(state_root, approval_root=approval_root)
    if extra_skills:
        extra_skills = _refresh_extra_skill_trust(state_root, extra_skills, approval_root=approval_root)
        refreshed["skills"] = [*refreshed.get("skills", []), *extra_skills]
    selected = select_visible_skills(
        refreshed.get("skills", []),
        skill_ids=skill_ids,
        source=source,
        audience=audience,
        package=package,
        activation=activation,
        include_blocked=include_blocked or block_high,
        include_lint_blocked=review_include_lint_blocked,
        include_global=include_global,
    )
    selected = annotate_duplicate_content(selected)
    return {
        "indexed": len(data.get("skills", [])),
        "skipped_global": skipped_global,
        "fresh_reset": fresh_reset,
        "global_reset": global_reset,
        "global_approved": sum(1 for skill in selected if skill.get("trust_reason") == "global-approval"),
        "errors": data.get("errors", []),
        "selected": selected,
        "summary": review_summary(selected),
        "action": action,
    }


def _refresh_extra_skill_trust(
    state_root: Path,
    skills: list[dict[str, Any]],
    *,
    approval_root: Path | None = None,
) -> list[dict[str, Any]]:
    refreshed = []
    for skill in skills:
        item = dict(skill)
        if item.get("id") and item.get("content_hash"):
            item["trust"] = trust_state(
                state_root,
                item["id"],
                item["content_hash"],
                lint=item.get("lint"),
                approval_key=item.get("approval_key"),
                approval_root=approval_root,
            )
        refreshed.append(item)
    return refreshed
