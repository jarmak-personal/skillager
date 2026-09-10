"""Metadata candidates for search; callers must verify content before publication."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..review_gates import apply_review_metadata
from ..trust import APPROVED_TRUST_STATES, approval_key_for, trust_info, trust_record, trust_snapshot
from .impl import (
    _apply_approval_metadata,
    _index_collection_skills,
    _library_provenance_hash,
    _load_collection_index,
    _matching_id_migrations,
    _skill_dirs,
    _trust_with_collection_migration_alias,
    load_collections,
)
from ..library.model import LIBRARY_COLLECTION_KIND


def collection_search_candidates(catalog_root: Path, *, trust_root: Path, name: str | None = None) -> list[dict[str, Any]]:
    collections = load_collections(catalog_root).get("collections", {})
    names = [name] if name is not None else sorted(collections)
    result: list[dict[str, Any]] = []
    with trust_snapshot([trust_root, catalog_root]):
        for collection_name in names:
            collection = collections.get(collection_name)
            if collection is None:
                if collection_name == "lib":
                    continue
                raise KeyError(f"collection not found: {collection_name}")
            entries = _candidates(catalog_root, trust_root, collection_name, collection)
            for entry in entries:
                skill = dict(entry)
                key = skill.get("approval_key") or approval_key_for(skill["id"], skill.get("root"), skill.get("source") or {}, entrypoint=skill.get("entrypoint"))
                trust = trust_info(trust_root, skill["id"], skill["content_hash"], lint=skill.get("lint"), approval_key=key, approval_root=catalog_root)
                trust = _trust_with_collection_migration_alias(catalog_root, trust_root, skill, trust)
                _apply_approval_metadata(skill, key, trust)
                apply_review_metadata(skill)
                result.append(skill)
    return result


def _candidates(catalog_root: Path, trust_root: Path, name: str, collection: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(collection["path"]).expanduser().resolve()
    cached = _load_collection_index(catalog_root, name)
    if not cached or cached.get("path") != str(root) or cached.get("errors"):
        return _index_collection_skills(catalog_root, name, root, collection=collection)[0]
    if collection.get("kind") == LIBRARY_COLLECTION_KIND and (
        cached.get("library_id") != collection.get("library_id")
        or cached.get("provenance_hash") != _library_provenance_hash(collection)
    ):
        return _index_collection_skills(catalog_root, name, root, collection=collection)[0]

    by_root = {entry.get("root"): entry for entry in cached.get("skills", [])}
    entries: list[dict[str, Any]] = []
    refresh: list[Path] = []
    # Enumerate names to retain in-place discovery and discard deleted sources.
    # No tree bytes or mtime shortcuts are used to establish candidate approval.
    for path in _skill_dirs(root):
        entry = by_root.get(str(path))
        if not entry or not entry.get("approval_key") or not entry.get("content_hash") or not entry.get("lint"):
            refresh.append(path)
        elif _approval_needs_metadata(catalog_root, trust_root, entry):
            refresh.append(path)
        else:
            entries.append(entry)
    if refresh:
        entries.extend(_index_collection_skills(catalog_root, name, root, collection=collection, skill_dirs=refresh)[0])
    return entries


def _approval_needs_metadata(catalog_root: Path, trust_root: Path, entry: dict[str, Any]) -> bool:
    records = [trust_record(trust_root, "skills", entry["id"]), trust_record(catalog_root, "global_approvals", entry["approval_key"])]
    # Legacy aliases may carry a different current decision from the cached ID.
    # Only unresolved aliases need this lookup; ordinary approved rows stay cheap.
    if not any(record and record.get("state") in APPROVED_TRUST_STATES for record in records):
        for migration in _matching_id_migrations(catalog_root, entry["id"], entry["content_hash"]):
            if migration.get("old_id"):
                records.append(trust_record(trust_root, "skills", migration["old_id"]))
    return any(
        record and record.get("state") in APPROVED_TRUST_STATES and record.get("content_hash") != entry["content_hash"]
        for record in records
    )
