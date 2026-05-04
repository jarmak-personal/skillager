from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from ..session import append_event, ensure_session, find_sessions, list_sessions, read_events


def resolve_session_ids(state_root: Path, *, session_id: str | None = None, agent: str | None = None, external_session_id: str | None = None) -> list[str]:
    if session_id:
        return [session_id]
    matches = find_sessions(state_root, agent=agent, external_session_id=external_session_id)
    if matches:
        return matches
    current = ensure_session(state_root, agent=agent, external_session_id=external_session_id, no_create=True)
    return [current["session_id"]] if current else []


def build_lookback(
    state_root: Path,
    *,
    session_id: str | None = None,
    agent: str | None = None,
    external_session_id: str | None = None,
    recent: int = 10,
    include_active: bool = True,
) -> dict[str, Any]:
    session_ids = resolve_session_ids(state_root, session_id=session_id, agent=agent, external_session_id=external_session_id)
    candidate_sessions = resolve_candidate_session_ids(
        state_root,
        primary_session_ids=session_ids,
        agent=agent,
        recent=recent,
        include_active=include_active,
    )
    events: list[dict[str, Any]] = []
    for item in session_ids:
        events.extend(read_events(state_root, item))
    candidate_events: list[dict[str, Any]] = []
    for item in candidate_sessions:
        candidate_events.extend(read_events(state_root, item))
    counts = Counter(event["event"] for event in events)
    by_skill = _skill_counts(events)
    aggregate_by_skill = _skill_counts(candidate_events)
    skill_sessions = _skill_sessions(candidate_events)
    session_records = {item["session_id"]: item for item in list_sessions(state_root, agent=agent)}
    recommendations = _recommendations(aggregate_by_skill, skill_sessions, session_records)
    overlaps = _observed_overlaps(candidate_events, aggregate_by_skill, skill_sessions, session_records)
    first = events[0] if events else (candidate_events[0] if candidate_events else {})
    return {
        "sessions": session_ids,
        "candidate_sessions": candidate_sessions,
        "candidate_session_count": len(candidate_sessions),
        "active_candidate_sessions": sum(1 for item in candidate_sessions if session_records.get(item, {}).get("active")),
        "completed_candidate_sessions": sum(1 for item in candidate_sessions if not session_records.get(item, {}).get("active")),
        "agent": first.get("agent"),
        "external_session_id": first.get("external_session_id"),
        "started_at": first.get("started_at") or first.get("timestamp"),
        "ended_at": _last_ended(events),
        "counts": dict(counts),
        "aggregate_counts": dict(Counter(event["event"] for event in candidate_events)),
        "skills": {skill: dict(counter) for skill, counter in by_skill.items()},
        "aggregate_skills": {skill: dict(counter) for skill, counter in aggregate_by_skill.items()},
        "recommendations": recommendations,
        "observed_overlaps": overlaps,
    }


def resolve_candidate_session_ids(
    state_root: Path,
    *,
    primary_session_ids: list[str],
    agent: str | None = None,
    recent: int = 10,
    include_active: bool = True,
) -> list[str]:
    records = list_sessions(state_root, agent=agent)
    selected: list[str] = []
    for session_id in primary_session_ids:
        if session_id not in selected:
            selected.append(session_id)
    for record in records[: max(0, recent)]:
        session_id = record["session_id"]
        if session_id not in selected:
            selected.append(session_id)
    if include_active:
        for record in records:
            if record.get("active") and record["session_id"] not in selected:
                selected.append(record["session_id"])
    return selected


def _skill_counts(events: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    by_skill: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        skill_id = event.get("skill_id")
        if skill_id:
            by_skill[skill_id][event["event"]] += 1
    return by_skill


def _skill_sessions(events: list[dict[str, Any]]) -> dict[str, set[str]]:
    sessions: dict[str, set[str]] = defaultdict(set)
    for event in events:
        skill_id = event.get("skill_id")
        session_id = event.get("session_id")
        if skill_id and session_id:
            sessions[skill_id].add(session_id)
    return sessions


def _recommendations(
    by_skill: dict[str, Counter[str]],
    skill_sessions: dict[str, set[str]],
    session_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations = []
    for skill_id, skill_counts in sorted(by_skill.items()):
        activated = skill_counts.get("skill_activated", 0)
        useful = skill_counts.get("feedback_useful", 0) + skill_counts.get("feedback_materialize", 0)
        harmful = skill_counts.get("feedback_harmful", 0) + skill_counts.get("feedback_block", 0)
        route_only = (
            skill_counts.get("skill_rejected", 0)
            + skill_counts.get("feedback_not_useful", 0)
            + skill_counts.get("feedback_route-only", 0)
        )
        sessions = sorted(skill_sessions.get(skill_id, set()))
        session_count = len(sessions)
        active_sessions = sum(1 for item in sessions if session_records.get(item, {}).get("active"))
        evidence = {
            "sessions": sessions,
            "session_count": session_count,
            "active_session_count": active_sessions,
            "events": dict(skill_counts),
        }
        if harmful:
            recommendations.append(
                {
                    "skill_id": skill_id,
                    "action": "block",
                    "reason": "marked harmful or blocked in feedback",
                    **evidence,
                }
            )
        elif (session_count >= 2 and (useful or activated >= 2)) or useful >= 2 or activated >= 5:
            recommendations.append(
                {
                    "skill_id": skill_id,
                    "action": "materialize",
                    "reason": "used across sessions or repeatedly marked useful",
                    **evidence,
                }
            )
        elif useful or activated >= 3:
            recommendations.append(
                {
                    "skill_id": skill_id,
                    "action": "route-only",
                    "reason": "useful in one session; keep routed until repeated value is clear",
                    **evidence,
                }
            )
        elif route_only >= 2:
            recommendations.append(
                {
                    "skill_id": skill_id,
                    "action": "route-only",
                    "reason": "rejected or marked less useful",
                    **evidence,
                }
            )
    return recommendations


def _observed_overlaps(
    events: list[dict[str, Any]],
    by_skill: dict[str, Counter[str]],
    skill_sessions: dict[str, set[str]],
    session_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    pair_queries: dict[tuple[str, str], set[str]] = defaultdict(set)
    pair_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)
    session_skills: dict[str, set[str]] = defaultdict(set)
    materialized = {skill_id for skill_id, counts in by_skill.items() if counts.get("skill_materialized")}
    for event in events:
        session_id = event.get("session_id")
        skill_id = event.get("skill_id")
        if session_id and skill_id and event.get("event") in {"skill_activated", "skill_materialized", "feedback_useful", "feedback_not_useful"}:
            session_skills[session_id].add(skill_id)
        if event.get("event") != "skill_search":
            continue
        top_ids = [item for item in event.get("top_ids", []) if isinstance(item, str)]
        for pair in _pairs(top_ids[:5]):
            pair_counts[pair] += 1
            if session_id:
                pair_sessions[pair].add(session_id)
            preview = event.get("query_preview")
            if isinstance(preview, str) and preview:
                pair_queries[pair].add(preview)
    for session_id, skills in session_skills.items():
        for pair in _pairs(sorted(skills)):
            pair_counts[pair] += 1
            pair_sessions[pair].add(session_id)
    groups: list[dict[str, Any]] = []
    for pair, count in pair_counts.most_common(12):
        if count < 2:
            continue
        sessions = sorted(pair_sessions.get(pair, set()))
        active_sessions = sum(1 for item in sessions if session_records.get(item, {}).get("active"))
        group_skills = [
            {
                "id": skill_id,
                "events": dict(by_skill.get(skill_id, Counter())),
                "session_count": len(skill_sessions.get(skill_id, set())),
                "materialized": skill_id in materialized,
            }
            for skill_id in pair
        ]
        groups.append(
            {
                "reason": "co-occurred in searches or sessions",
                "confidence": "behavioral-hint",
                "score": count,
                "sessions": sessions,
                "session_count": len(sessions),
                "active_session_count": active_sessions,
                "skills": group_skills,
                "query_previews": sorted(pair_queries.get(pair, set()))[:3],
                "suggested_next_step": "ask user whether to pin a winner, keep route-only, stub commands, block old skills, or ignore",
            }
        )
    return groups


def render_lookback(report: dict[str, Any]) -> str:
    lines = [
        f"Skillager session: {', '.join(report.get('sessions') or []) or 'none'}",
        f"Agent: {report.get('agent') or 'unknown'}",
        f"External session: {report.get('external_session_id') or 'none'}",
        f"Started: {report.get('started_at') or 'unknown'}",
        f"Ended: {report.get('ended_at') or 'open'}",
        f"Candidate sessions: {report.get('candidate_session_count', len(report.get('sessions') or []))}"
        f" ({report.get('active_candidate_sessions', 0)} active)",
        "",
        "Events:",
    ]
    counts = report.get("counts", {})
    if counts:
        for event, count in sorted(counts.items()):
            lines.append(f"- {event}: {count}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Skills:")
    skills = report.get("skills", {})
    if skills:
        for skill_id, events in sorted(skills.items()):
            summary = ", ".join(f"{name}={count}" for name, count in sorted(events.items()))
            lines.append(f"- {skill_id}: {summary}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Recommendations:")
    recs = report.get("recommendations", [])
    if recs:
        for rec in recs:
            lines.append(
                f"- {rec['skill_id']}: {rec['action']} ({rec['reason']}; "
                f"sessions={rec.get('session_count', 0)}, active={rec.get('active_session_count', 0)})"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Observed Overlap:")
    overlaps = report.get("observed_overlaps", [])
    if overlaps:
        for group in overlaps:
            skill_ids = ", ".join(item["id"] for item in group.get("skills", []))
            lines.append(f"- {skill_ids}: {group.get('reason')} (score={group.get('score')})")
            previews = group.get("query_previews") or []
            if previews:
                lines.append(f"  queries: {', '.join(previews)}")
            lines.append("  next: ask user whether to pin, keep route-only, stub, block, or ignore")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _pairs(values: list[str]) -> list[tuple[str, str]]:
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return [(left, right) if left < right else (right, left) for left, right in combinations(unique, 2)]


def record_feedback(state_root: Path, skill_id: str, feedback: str, *, note: str | None = None) -> dict[str, Any]:
    meta = ensure_session(state_root)
    if not meta:
        raise KeyError("no current session")
    return append_event(
        state_root,
        meta["session_id"],
        f"feedback_{feedback}",
        {
            "agent": meta.get("agent"),
            "external_session_id": meta.get("external_session_id"),
            "skill_id": skill_id,
            "note": note,
        },
    )


def _last_ended(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("ended_at"):
            return event["ended_at"]
    return None
