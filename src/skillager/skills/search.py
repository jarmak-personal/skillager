from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ..state.trust import APPROVED_TRUST_STATES

STOPWORDS = {
    "a",
    "about",
    "after",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "for",
    "from",
    "guidance",
    "have",
    "help",
    "how",
    "i",
    "if",
    "in",
    "including",
    "into",
    "is",
    "it",
    "may",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "project",
    "should",
    "skill",
    "skills",
    "that",
    "the",
    "their",
    "then",
    "this",
    "to",
    "use",
    "using",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "workflow",
    "would",
    "you",
    "your",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
BODY_SEARCH_CHAR_LIMIT = 50_000


def search(
    skills: list[dict[str, Any]],
    query: str,
    *,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    include_untrusted: bool = True,
) -> list[dict[str, Any]]:
    candidates = [
        skill
        for skill in skills
        if _included(skill, include_blocked=include_blocked, include_lint_blocked=include_lint_blocked, include_untrusted=include_untrusted)
    ]
    try:
        return _fts5_search(candidates, query)
    except (sqlite3.Error, RuntimeError):
        return _fallback_search(candidates, query)


def _fts5_search(skills: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    exact = _exact_id_match(skills, query)
    if _looks_like_skill_id(query) and not exact:
        return []
    if not terms:
        if exact:
            return [_with_score(exact, 100.0, ["id:exact"])]
        if query.strip():
            return []
        return [_with_score(skill, 0.0, []) for skill in sorted(skills, key=lambda item: (_visibility_rank(item), item["id"]))]

    body_texts = {skill["id"]: _body_text(skill) for skill in skills}
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE skill_fts USING fts5(
                name,
                summary,
                audience,
                package,
                targets,
                source,
                tags,
                body,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        for rowid, skill in enumerate(skills, start=1):
            conn.execute(
                "INSERT INTO skill_fts(rowid, name, summary, audience, package, targets, source, tags, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rowid,
                    skill.get("name") or "",
                    skill.get("summary") or "",
                    _audience_text(skill),
                    _package_text(skill),
                    _target_text(skill),
                    _source_text(skill),
                    " ".join(str(tag) for tag in skill.get("tags", [])),
                    body_texts[skill["id"]],
                ),
            )
        match = " OR ".join(f'"{term}"' for term in terms)
        rows = conn.execute(
            """
            SELECT rowid, bm25(skill_fts, 8.0, 3.0, 0.5, 4.0, 4.0, 3.0, 6.0, 0.2) AS rank
            FROM skill_fts
            WHERE skill_fts MATCH ?
            ORDER BY rank
            """,
            (match,),
        ).fetchall()
    finally:
        conn.close()

    by_id: dict[str, dict[str, Any]] = {}
    if exact:
        exact_body = body_texts.get(exact["id"], "")
        by_id[exact["id"]] = _with_score(
            exact,
            100.0 + _score_boost(exact, terms, body_text=exact_body),
            ["id:exact", *_reasons(exact, terms, body_text=exact_body)],
        )
    for rowid, _rank in rows:
        skill = skills[int(rowid) - 1]
        body_text = body_texts[skill["id"]]
        item = _with_score(
            skill,
            _score_boost(skill, terms, body_text=body_text),
            _reasons(skill, terms, body_text=body_text),
        )
        by_id.setdefault(skill["id"], item)
    return sorted(by_id.values(), key=lambda item: (-float(item["score"]), _visibility_rank(item), item["id"]))


def _fallback_search(skills: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    exact = _exact_id_match(skills, query)
    if _looks_like_skill_id(query) and not exact:
        return []
    body_texts = {skill["id"]: _body_text(skill) for skill in skills} if terms else {}
    results: list[dict[str, Any]] = []
    for skill in skills:
        reasons: list[str] = ["id:exact"] if exact and skill["id"] == exact["id"] else []
        if terms:
            reasons.extend(_reasons(skill, terms, body_text=body_texts[skill["id"]]))
        if reasons or (not terms and not query.strip()):
            body_text = body_texts.get(skill["id"], "")
            score = (100.0 if "id:exact" in reasons else 0.0) + _score_boost(skill, terms, body_text=body_text)
            results.append(_with_score(skill, score, reasons))
    return sorted(results, key=lambda item: (-item["score"], _visibility_rank(item), item["id"]))


def _included(
    skill: dict[str, Any],
    *,
    include_blocked: bool,
    include_lint_blocked: bool,
    include_untrusted: bool,
) -> bool:
    if skill.get("trust") == "blocked" and not include_blocked:
        return False
    if skill.get("trust") == "lint_blocked" and not include_lint_blocked:
        return False
    return not (skill.get("trust") == "discovered" and not include_untrusted)


def _exact_id_match(skills: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    normalized = query.strip().lower()
    if not normalized:
        return None
    return next((skill for skill in skills if str(skill.get("id", "")).lower() == normalized), None)


def _looks_like_skill_id(query: str) -> bool:
    value = query.strip()
    return "/" in value and not any(char.isspace() for char in value)


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in _tokens(query):
        if term in STOPWORDS or len(term) < 2:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def _score_boost(skill: dict[str, Any], terms: list[str], *, body_text: str) -> float:
    if not terms:
        return 0.0
    fields = _token_fields(skill, body_text=body_text)
    score = 0.0
    for term in terms:
        if term in fields["name"]:
            score += 8.0
        if term in fields["tags"]:
            score += 7.0
        if term in fields["package"]:
            score += 5.0
        if term in fields["targets"]:
            score += 5.0
        if term in fields["source"]:
            score += 4.0
        if term in fields["summary"]:
            score += 2.0
        if term in fields["body"]:
            score += 0.2
        if term in fields["audience"]:
            score += 0.5
    if " ".join(terms) == " ".join(_tokens(skill.get("name") or "")):
        score += 25.0
    return score


def _reasons(skill: dict[str, Any], terms: list[str], *, body_text: str) -> list[str]:
    fields = _token_fields(skill, body_text=body_text)
    reasons: list[str] = []
    for term in terms:
        for field in ("name", "tags", "package", "targets", "source", "summary", "body", "audience"):
            if term in fields[field]:
                reasons.append(f"{field}:{term}")
                break
    return sorted(set(reasons))


def _token_fields(skill: dict[str, Any], *, body_text: str) -> dict[str, set[str]]:
    return {
        "name": set(_tokens(skill.get("name") or "")),
        "summary": set(_tokens(skill.get("summary") or "")),
        "audience": set(_tokens(_audience_text(skill))),
        "package": set(_tokens(_package_text(skill))),
        "targets": set(_tokens(_target_text(skill))),
        "source": set(_tokens(_source_text(skill))),
        "tags": set(_tokens(" ".join(str(tag) for tag in skill.get("tags", [])))),
        "body": set(_tokens(body_text)),
    }


def _audience_text(skill: dict[str, Any]) -> str:
    return " ".join(str(item) for item in skill.get("audience", []))


def _package_text(skill: dict[str, Any]) -> str:
    source = skill.get("source") or {}
    return " ".join(str(item) for item in (skill.get("package"), source.get("package")) if item)


def _target_text(skill: dict[str, Any]) -> str:
    parts: list[str] = []
    targets = skill.get("targets", {}).get("python_packages", []) if isinstance(skill.get("targets"), dict) else []
    for target in targets:
        if isinstance(target, dict):
            name = target.get("name")
            if name:
                parts.append(str(name))
    return " ".join(parts)


def _source_text(skill: dict[str, Any]) -> str:
    source = skill.get("source") or {}
    return " ".join(
        str(item)
        for item in (
            source.get("type"),
            source.get("collection"),
            source.get("package"),
            source.get("agent"),
        )
        if item
    )


def _body_text(skill: dict[str, Any]) -> str:
    if skill.get("trust") not in APPROVED_TRUST_STATES:
        return ""
    entrypoint = skill.get("entrypoint")
    if not entrypoint:
        return ""
    try:
        text = Path(entrypoint).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:BODY_SEARCH_CHAR_LIMIT]


def _with_score(skill: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    item = dict(skill)
    capped_score = max(0.0, min(100.0, score))
    item["score"] = round(capped_score, 3)
    item["reasons"] = sorted(set(reasons))
    item["score_detail"] = _score_detail(capped_score, item["reasons"])
    return item


def _score_detail(score: float, reasons: list[str]) -> dict[str, Any]:
    fields = sorted({reason.split(":", 1)[0] for reason in reasons if ":" in reason})
    return {
        "scale": "0-100 weighted match; exact id matches score 100; body-only matches are intentionally weak",
        "matched_fields": fields,
        "body_only": bool(fields) and fields == ["body"],
        "rounded": round(score, 3),
    }


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
