from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..state.database import connect_database
from ..state.trust import APPROVED_TRUST_STATES, content_hash

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
    "including",
    "large",
    "may",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "project",
    "relevant",
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
    "work",
    "working",
    "workflow",
    "workflows",
    "would",
    "you",
    "your",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
BODY_SEARCH_CHAR_LIMIT = 50_000
BODY_ONLY_STOPWORDS = {
    "data",
    "including",
    "large",
    "python",
    "relevant",
    "scale",
    "work",
    "working",
}


def search(
    skills: list[dict[str, Any]],
    query: str,
    *,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    include_untrusted: bool = True,
    cache_path: Path | None = None,
) -> list[dict[str, Any]]:
    candidates = [
        skill
        for skill in skills
        if _included(skill, include_blocked=include_blocked, include_lint_blocked=include_lint_blocked, include_untrusted=include_untrusted)
    ]
    try:
        return _fts5_search(candidates, query, cache_path=cache_path)
    except (sqlite3.Error, RuntimeError, OSError):
        return _fallback_search(candidates, query, verify_content=cache_path is not None)


def _fts5_search(skills: list[dict[str, Any]], query: str, *, cache_path: Path | None = None) -> list[dict[str, Any]]:
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

    conn = connect_database(cache_path, writable=True) if cache_path is not None else sqlite3.connect(":memory:")
    matched_fields: dict[int, dict[str, set[str]]] = {}
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS search_entries(rowid INTEGER PRIMARY KEY, cache_key TEXT NOT NULL UNIQUE)")
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
                name, summary, audience, package, targets, source, tags, body,
                tokenize = 'unicode61 remove_diacritics 2'
            )""")
            conn.execute("CREATE TABLE IF NOT EXISTS entry_terms(entry_rowid INTEGER NOT NULL, field TEXT NOT NULL, term TEXT NOT NULL, PRIMARY KEY(entry_rowid, field, term)) WITHOUT ROWID")
            conn.execute("CREATE INDEX IF NOT EXISTS terms_lookup ON entry_terms(term, entry_rowid)")
            conn.execute("CREATE TEMP TABLE query_terms(term TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO query_terms VALUES (?)", [(term,) for term in terms])
            conn.execute("CREATE TEMP TABLE candidates(position INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, entry_rowid INTEGER)")
            columns = [_search_columns(skill) for skill in skills]
            keys = [_search_cache_key(skill, fields) for skill, fields in zip(skills, columns)]
            conn.executemany("INSERT INTO candidates(position, cache_key) VALUES (?, ?)", enumerate(keys))
            conn.execute("UPDATE candidates SET entry_rowid = (SELECT rowid FROM search_entries WHERE search_entries.cache_key = candidates.cache_key)")
            missing = conn.execute("SELECT position FROM candidates WHERE entry_rowid IS NULL").fetchall()
            for (position,) in missing:
                skill = skills[position]
                body = _verified_body_text(skill) if cache_path is not None else _body_text(skill)
                # An unreadable/changing source must never poison a hash's cached body.
                if body is None:
                    continue
                conn.execute("INSERT OR IGNORE INTO search_entries(cache_key) VALUES (?)", (keys[position],))
                rowid = conn.execute("SELECT rowid FROM search_entries WHERE cache_key = ?", (keys[position],)).fetchone()[0]
                conn.execute("INSERT OR REPLACE INTO skill_fts(rowid, name, summary, audience, package, targets, source, tags, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (rowid, *columns[position], body))
                conn.executemany("INSERT OR IGNORE INTO entry_terms VALUES (?, ?, ?)",
                                 [(rowid, field, term) for field, tokens in _token_fields(skill, body_text=body).items() for term in tokens])
                conn.execute("UPDATE candidates SET entry_rowid = ? WHERE position = ?", (rowid, position))
            conn.execute("CREATE INDEX candidates_rowid ON candidates(entry_rowid)")
            match = " OR ".join(f'"{term}"' for term in terms)
            rows = conn.execute("""SELECT candidates.position
                FROM skill_fts JOIN candidates ON candidates.entry_rowid = skill_fts.rowid
                WHERE skill_fts MATCH ?""", (match,)).fetchall()
            # Persist the existing ASCII scoring tokens as well as the Unicode FTS
            # index. This preserves ranking/reasons without loading matching bodies.
            for position, field, term in conn.execute("""SELECT candidates.position, entry_terms.field, entry_terms.term
                FROM query_terms JOIN entry_terms ON entry_terms.term = query_terms.term
                JOIN candidates ON candidates.entry_rowid = entry_terms.entry_rowid"""):
                fields = matched_fields.setdefault(position, _empty_token_fields())
                fields[field].add(term)
    finally:
        conn.close()

    by_id: dict[str, dict[str, Any]] = {}
    if exact:
        exact_fields = matched_fields.get(skills.index(exact), _empty_token_fields())
        by_id[exact["id"]] = _with_score(
            exact,
            100.0 + _score_boost(exact, terms, body_text="", fields=exact_fields),
            ["id:exact", *_reasons(exact, terms, body_text="", fields=exact_fields)],
        )
    for (position,) in rows:
        skill = skills[position]
        fields = matched_fields.get(position, _empty_token_fields())
        reasons = _reasons(skill, terms, body_text="", fields=fields)
        if not reasons or _only_weak_provenance_matches(reasons, terms):
            continue
        item = _with_score(
            skill,
            _score_boost(skill, terms, body_text="", fields=fields),
            reasons,
        )
        by_id.setdefault(skill["id"], item)
    return sorted(by_id.values(), key=lambda item: (-float(item["score"]), _visibility_rank(item), item["id"]))


def _search_columns(skill: dict[str, Any]) -> tuple[str, ...]:
    return (
        skill.get("name") or "", skill.get("summary") or "", _audience_text(skill),
        _package_text(skill), _target_text(skill), _source_text(skill),
        " ".join(str(tag) for tag in skill.get("tags", [])),
    )


def _search_cache_key(skill: dict[str, Any], columns: tuple[str, ...]) -> str:
    payload = (
        "skillager.search.v1", "scoring-tokens-v1", BODY_SEARCH_CHAR_LIMIT, skill.get("approval_key"),
        skill.get("root"), skill.get("entrypoint"), skill.get("content_hash"),
        skill.get("trust") in APPROVED_TRUST_STATES, columns,
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _verified_body_text(skill: dict[str, Any]) -> str | None:
    if skill.get("trust") not in APPROVED_TRUST_STATES:
        return ""
    root, expected = skill.get("root"), skill.get("content_hash")
    if not root or not expected:
        return None
    try:
        if content_hash(Path(root)) != expected:
            return None
        entrypoint = skill.get("entrypoint")
        if not entrypoint:
            return ""
        with Path(entrypoint).open(encoding="utf-8", errors="replace") as handle:
            body = handle.read(BODY_SEARCH_CHAR_LIMIT)
        return body if content_hash(Path(root)) == expected else None
    except (OSError, ValueError):
        return None


def _fallback_search(skills: list[dict[str, Any]], query: str, *, verify_content: bool = False) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    exact = _exact_id_match(skills, query)
    if _looks_like_skill_id(query) and not exact:
        return []
    body_texts = {skill["id"]: (_verified_body_text(skill) or "") if verify_content else _body_text(skill) for skill in skills} if terms else {}
    results: list[dict[str, Any]] = []
    for skill in skills:
        reasons: list[str] = ["id:exact"] if exact and skill["id"] == exact["id"] else []
        if terms:
            reasons.extend(_reasons(skill, terms, body_text=body_texts[skill["id"]]))
        if "id:exact" not in reasons and _only_weak_provenance_matches(reasons, terms):
            continue
        if reasons or (not terms and not query.strip()):
            body_text = body_texts.get(skill["id"], "")
            score = (100.0 if "id:exact" in reasons else 0.0) + _score_boost(skill, terms, body_text=body_text)
            results.append(_with_score(skill, score, reasons))
    return sorted(results, key=lambda item: (-item["score"], _visibility_rank(item), item["id"]))


def _only_weak_provenance_matches(reasons: list[str], terms: list[str]) -> bool:
    if len(terms) <= 1 or not reasons:
        return False
    fields = {reason.partition(":")[0] for reason in reasons}
    return fields == {"source"}


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


def _score_boost(skill: dict[str, Any], terms: list[str], *, body_text: str, fields: dict[str, set[str]] | None = None) -> float:
    if not terms:
        return 0.0
    if fields is None:
        fields = _token_fields(skill, body_text=body_text)
    score = 0.0
    for term in terms:
        if term in fields["name"]:
            score += 8.0
        if term in fields["tags"]:
            score += 2.0
        if term in fields["package"]:
            score += 5.0
        if term in fields["targets"]:
            score += 5.0
        if term in fields["source"]:
            score += 0.1
        if term in fields["summary"]:
            score += 3.0
        if term in fields["body"] and term not in BODY_ONLY_STOPWORDS:
            score += 0.2
        if term in fields["audience"]:
            score += 0.5
    if " ".join(terms) == " ".join(_tokens(skill.get("name") or "")):
        score += 25.0
    return score


def _reasons(skill: dict[str, Any], terms: list[str], *, body_text: str, fields: dict[str, set[str]] | None = None) -> list[str]:
    if fields is None:
        fields = _token_fields(skill, body_text=body_text)
    reasons: list[str] = []
    for term in terms:
        for field in ("name", "tags", "package", "targets", "source", "summary", "body", "audience"):
            if field == "body" and term in BODY_ONLY_STOPWORDS:
                continue
            if term in fields[field]:
                reasons.append(f"{field}:{term}")
                break
    return sorted(set(reasons))


def _empty_token_fields() -> dict[str, set[str]]:
    return {field: set() for field in ("name", "summary", "audience", "package", "targets", "source", "tags", "body")}


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
    targets = skill.get("targets", {})
    if not isinstance(targets, dict):
        return ""
    for target_group in (targets.get("python_packages", []), targets.get("npm_packages", []), targets.get("cargo_packages", [])):
        if not isinstance(target_group, list):
            continue
        for target in target_group:
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
    if skill.get("source", {}).get("type") in {"python-package", "npm-package", "cargo-package"}:
        return 7
    return 8
