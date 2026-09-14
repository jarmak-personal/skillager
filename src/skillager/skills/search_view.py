"""Known-skill search presentation; identities never establish approval or recency."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..exposure.identity import library_id, source_key
from ..library.model import normalize_skill_name
from ..library.sync_lineage import canonical_approval_key, identity, lineage_is_bound, source_identity


SEARCH_VIEW_SCHEMA = "skillager.search.v1"
INSTALLED_SCHEMA = "skillager.search-installed.v1"
SEARCH_INVENTORY_LIMIT = 10_000
SEARCH_RESULT_LIMIT = 50
SEARCH_RESULT_BYTES = 4 * 1024 * 1024
INSTALLED_INPUT_BYTES = 2 * 1024 * 1024


def installed_keys(payload: Any) -> set[str]:
    """Caller-supplied presentation exclusions contain identities, never paths/grants."""
    if not isinstance(payload, dict) or set(payload) != {"schema", "identities"} or payload["schema"] != INSTALLED_SCHEMA:
        raise ValueError("invalid installed identity input")
    values = payload["identities"]
    if not isinstance(values, list) or len(values) > SEARCH_INVENTORY_LIMIT:
        raise ValueError("invalid installed identity count")
    result: set[str] = set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {"library_id", "skill_id"}:
            raise ValueError("installed input accepts only canonical identities")
        identifier, skill_id = item["library_id"], item["skill_id"]
        if library_id(identifier) is None or not isinstance(skill_id, str) or not skill_id.startswith("lib/"):
            raise ValueError("invalid installed canonical identity")
        if skill_id != f"lib/{normalize_skill_name(skill_id)}":
            raise ValueError("invalid installed skill ID")
        key = source_key(skill_id, identifier)
        assert key is not None
        if key in result:
            raise ValueError("duplicate installed canonical identity")
        result.add(key)
    return result


def lineage_relations(provenance: Any, identifier: str | None, approvals: dict[str, Any]) -> tuple[dict[str, str], bool]:
    """Validate persisted CLI lineage without interpreting names or matching hashes."""
    if identifier is None:
        return {}, True
    if not isinstance(provenance, dict) or provenance.get("schema") != "skillager.library-provenance.v1":
        return {}, False
    entries = provenance.get("skills")
    if not isinstance(entries, dict) or len(entries) > SEARCH_INVENTORY_LIMIT:
        return {}, False
    relations: dict[str, str] = {}
    conflicts: set[str] = set()
    complete = True
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            complete = False
            continue
        stored = entry.get("sync")
        if stored is None:
            # Historical import prose lacks source identity; do not infer a relation.
            continue
        if not isinstance(stored, dict):
            complete = False
            continue
        key, origin_key = stored.get("source_identity"), stored.get("source_key")
        try:
            valid_name = isinstance(name, str) and normalize_skill_name(name) == name
        except ValueError:
            valid_name = False
        if (not valid_name
                or not isinstance(origin_key, str) or not origin_key or len(origin_key) > 16_384
                or key != identity("source", origin_key)
                or stored.get("schema") != "skillager.library-sync-lineage.v1"
                or stored.get("lineage_id") != identity("lineage", key, identifier, name)
                or not lineage_is_bound(stored, approvals.get(canonical_approval_key(identifier, name)))):
            complete = False
            continue
        canonical = source_key(f"lib/{name}", identifier)
        assert canonical is not None
        if key in relations:
            conflicts.add(key)
            complete = False
        else:
            relations[key] = canonical
    for key in conflicts:
        relations.pop(key, None)
    return relations, complete


@dataclass(frozen=True)
class Occurrence:
    skill: dict[str, Any]
    group: str
    occurrence: dict[str, Any]


class SearchView:
    """One bounded, per-request identity map and lazy ranked occurrence projection."""
    def __init__(self, skills: list[dict[str, Any]], *, relations: dict[str, str],
                 exposures: list[dict[str, Any]], present: set[str], installed_complete: bool,
                 project: Path | None) -> None:
        if len(skills) > SEARCH_INVENTORY_LIMIT or len(exposures) > SEARCH_INVENTORY_LIMIT:
            raise ValueError("search inventory limit exceeded")
        self.groups: dict[str, list[Occurrence]] = {}
        self.sources: dict[int, Occurrence] = {}
        self.present = set(present)
        self.installed_complete = installed_complete
        self.project = project
        self.relations = relations
        self.canonical: dict[str, Occurrence] = {}
        self.occurrence_count = 0
        for skill in skills:
            if skill.get("identity_collision"):
                continue
            source = skill.get("source") or {}
            owned = source.get("ownership") == "library"
            group = source_key(skill["id"], source.get("library_id")) if owned else None
            source_id = None if owned else source_identity(skill)
            if not group:
                group = relations.get(source_id or "", f"source:{source_id}")
            native = skill.get("native") or {}
            is_project = source.get("type") == "project" and project is not None
            path = str(Path(skill["root"]).resolve())
            occurrence = {
                "id": identity("search-occurrence", group, path, str(native.get("agent") or source.get("agent") or "")),
                "kind": "library" if owned else "project-original" if is_project else "source",
                "path": path, "entrypoint": str(Path(skill["entrypoint"]).resolve()),
                "agent": native.get("agent") or source.get("agent"),
                "source_identity": source_id,
            }
            row = Occurrence(skill, group, occurrence)
            self.sources[id(skill)] = row
            self._add(row)
            if owned:
                self.canonical[group] = row
            if is_project and not native.get("managed"):
                self.present.add(group)
                if source_id not in relations:
                    # A native source may be an original or a copy whose sidecar
                    # was removed. Its own identity is known, its relation to a
                    # canonical skill is not; never infer canonical absence.
                    self.installed_complete = False
        # Exposure membership is independent of current source hash/approval.
        for exposure in exposures:
            if exposure["mode"] == "router":
                members = exposure.get("member_sources") or []
                if not members:
                    self.installed_complete = False
            else:
                members = [{"skill_id": exposure["skill_id"], "source_library_id": exposure.get("source_library_id")}]
            for member in members:
                key = source_key(member["skill_id"], member.get("source_library_id"))
                if key is None:
                    self.installed_complete = False
                    continue
                # Non-library sidecars lack an origin qualifier. A discovered
                # row with the same public ID does not prove this relationship.
                if not member["skill_id"].startswith("lib/"):
                    self.installed_complete = False
                    continue
                self.present.add(key)
                sources = self.groups.get(key)
                if not sources:
                    continue
                source_row = self.canonical.get(key) or sources[0]
                occurrence = {
                    "id": identity("search-exposure", key, exposure["target"], exposure["agent"], exposure["exposure_id"]),
                    "kind": "router-member" if exposure["mode"] == "router" else "full" if exposure["mode"] == "native" else "stub",
                    "path": exposure["target"], "entrypoint": str(Path(exposure["target"]) / "SKILL.md"),
                    "agent": exposure["agent"], "source_identity": None,
                    "exposure": {name: exposure[name] for name in
                                 ("schema", "exposure_id", "agent", "scope", "target", "mode", "router_slug", "router_kind", "tag")
                                 if name in exposure},
                }
                self._add(Occurrence(source_row.skill, key, occurrence))

    def _add(self, row: Occurrence) -> None:
        self.occurrence_count += 1
        if self.occurrence_count > SEARCH_INVENTORY_LIMIT:
            raise ValueError("search occurrence limit exceeded")
        self.groups.setdefault(row.group, []).append(row)

    def candidates(self, include_installed: bool) -> list[dict[str, Any]]:
        return [row.skill for row in self.sources.values()
                if include_installed or row.group not in self.present]

    def results(self, ranked: list[dict[str, Any]], *, copies: bool, limit: int,
                current: Callable[[dict[str, Any]], bool], public: Callable[[dict[str, Any]], dict[str, Any]],
                agent: str | None) -> list[dict[str, Any]]:
        by_identity = {(row.skill["id"], row.skill["root"]): row for row in self.sources.values()}
        checked: dict[tuple[str, str], bool] = {}
        def available(skill: dict[str, Any]) -> bool:
            key = (skill["id"], skill["root"])
            if key not in checked:
                checked[key] = current(skill)
            return checked[key]
        emitted: set[str] = set()
        matched_groups: set[str] = set()
        result = []
        for match in ranked:
            row = by_identity[(match["id"], match["root"])]
            if not available(match):
                continue
            if row.group in matched_groups:
                continue
            matched_groups.add(row.group)
            if copies:
                selected = self.groups[row.group].copy()
            else:
                preferred = self.canonical.get(row.group)
                selected = [preferred if preferred and available(preferred.skill) else row]
            selected.sort(key=lambda item: (item.occurrence["agent"] != agent, item.occurrence["id"]))
            for item in selected:
                if not available(item.skill):
                    if "exposure" not in item.occurrence:
                        continue
                    item = Occurrence(row.skill, item.group, item.occurrence)
                token = item.occurrence["id"] if copies else row.group
                if token in emitted:
                    continue
                emitted.add(token)
                payload = public(item.skill)
                payload["score"] = match["score"]
                payload["reasons"] = match["reasons"] if item.occurrence["id"] == row.occurrence["id"] else []
                payload["search"] = {
                    "group_id": identity("search-group", row.group), "occurrence": item.occurrence,
                    "canonical": ({"library_id": row.group.split("\0")[0].removeprefix("library:"),
                                   "skill_id": row.group.split("\0")[1]} if row.group.startswith("library:") else None),
                    "group_occurrences": len(self.groups[row.group]),
                    "installed": True if row.group in self.present else False if self.installed_complete else None,
                    "match": {"occurrence_id": row.occurrence["id"], "skill_id": match["id"],
                              "occurrence": row.occurrence,
                              "content_hash": match["content_hash"], "score": match["score"], "reasons": match["reasons"]},
                }
                result.append(payload)
                if len(result) >= limit:
                    return result
        return result
