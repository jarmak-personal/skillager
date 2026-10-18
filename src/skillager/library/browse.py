"""Incremental library metadata observations, never content or approval authority."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..schema import skill_frontmatter_metadata
from ..catalog import impl as catalog, storage
from ..signing import signature_info
from ..skills.tree import metadata_tree_receipt
from ..state.paths import cache_root
from ..trust import content_hash
from .service import _require_library_identity


def library_metadata_index(catalog_root: Path, name: str, collection: dict[str, Any], *, require_complete: bool = True) -> dict[str, Any]:
    # Keep this path opt-in to metadata browsing. Exact collection selection,
    # body gates, acceptance and mutation must never consult these receipts.
    registration, _identity = _require_library_identity(catalog_root)
    current = registration.to_mapping()
    if any(collection.get(key) != current[key] for key in ("path", "library_id", "library_root")):
        raise ValueError("personal library registration changed during metadata observation")
    root = registration.layout.skills
    identity = {"path": str(root), "library_id": collection["library_id"],
                "provenance_hash": catalog._library_provenance_hash(collection)}
    location = cache_root() / "library-browse-v1" / hashlib.sha256(str(catalog_root.resolve()).encode()).hexdigest()
    try:
        cached = storage.read_collection(location, name)
        if cached is not None and (not isinstance(cached, dict) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("root"), str)
            or not all(isinstance(entry.get(key), str) for key in ("id", "content_hash"))
            or not isinstance(entry.get("_browse_declared"), dict)
            or not isinstance(entry.get("_browse_errors"), list)
            or any(not isinstance(error, dict) or not all(isinstance(error.get(key), str) for key in ("path", "error"))
                   for error in entry.get("_browse_errors", []))
            for entry in cached.get("skills", [])
        )):
            cached = None
    except (OSError, ValueError, TypeError):
        cached = None
    previous = {entry["root"]: entry for entry in cached.get("skills", [])} if cached and all(
        cached.get(key) == value for key, value in identity.items()
    ) else {}
    errors: list[dict[str, str]] = []

    def unavailable(path: Path, error: Exception) -> None:
        if require_complete:
            raise ValueError("personal library metadata could not be observed completely") from error
        errors.append({"path": str(path), "error": str(error)})

    discovered = catalog._skill_dirs(root, require_complete=require_complete)
    receipts = {}
    for path in discovered:
        try:
            receipts[str(path)] = metadata_tree_receipt(path)
        except (OSError, ValueError) as exc:
            unavailable(path, exc)
    roots = [path for path in discovered if str(path) in receipts]
    changed = [path for path in roots if not previous.get(str(path))
               or previous[str(path)].get("_browse_receipt") != receipts[str(path)]]
    # A first browse can seed its derived cache from the existing collection
    # index, but only after exact tree verification. Stat receipts are never
    # transferred from a different cache or inferred from an approval record.
    seed = catalog._load_collection_index(catalog_root, name) if cached is None else None
    seeded = {entry["root"]: entry for entry in seed.get("skills", [])} if seed and not seed.get("errors") and all(
        seed.get(key) == value for key, value in identity.items()
    ) else {}
    reused = []
    for path in changed:
        try:
            if str(path) in seeded and content_hash(path) == seeded[str(path)].get("content_hash"):
                seed_entry = dict(seeded[str(path)])
                # Evidence is outside the accepted hash. Verified instructions
                # cannot establish that their signature metadata is current.
                seed_entry.pop("signature", None)
                signature = signature_info(path)
                if signature:
                    seed_entry["signature"] = signature
                reused.append(seed_entry)
        except OSError as exc:
            unavailable(path, exc)
    reused_roots = {entry["root"] for entry in reused}
    failed_roots = {error["path"] for error in errors}
    refresh = [path for path in changed if str(path) not in reused_roots | failed_roots]
    refreshed, refresh_errors = catalog._index_collection_skills(catalog_root, name, root, collection=collection,
                                                        skill_dirs=refresh, require_complete=require_complete) if refresh else ([], [])
    errors.extend(refresh_errors)
    refreshed.extend(reused)
    by_root = {entry["root"]: entry for entry in refreshed}
    error_roots = {error["path"] for error in errors}
    if require_complete and error_roots - by_root.keys():
        raise ValueError("personal library metadata could not be observed completely")
    for path in changed:
        key = str(path)
        entry = by_root.get(key)
        if entry is None:
            continue
        try:
            entry["_browse_declared"] = skill_frontmatter_metadata((path / "SKILL.md").read_text(encoding="utf-8"))
        except UnicodeError as exc:
            entry["_browse_declared"] = {}
            errors.append({"path": key, "error": str(exc)})
        except OSError as exc:
            unavailable(path, exc)
            del by_root[key]
            continue
        # A receipt must describe the same observation as the freshly read bytes.
        # Refuse a racing refresh instead of persisting its metadata as unchanged.
        try:
            if metadata_tree_receipt(path) != receipts[key]:
                raise ValueError("personal library changed during metadata observation")
        except (OSError, ValueError) as exc:
            unavailable(path, exc)
            del by_root[key]
            continue
        entry["_browse_receipt"] = receipts[key]
        entry["_browse_errors"] = [error for error in errors if error["path"] == key]
    changed_roots = {str(path) for path in changed}
    entries = [by_root[str(path)] if str(path) in changed_roots else previous[str(path)]
               for path in roots if str(path) not in changed_roots or str(path) in by_root]
    data = {"schema": "skillager.collection-index.v1", "name": name, **identity,
            "skills": entries, "errors": errors + [error for entry in entries
                if entry["root"] not in changed_roots for error in entry.get("_browse_errors", [])]}
    if changed or set(previous) != set(receipts) or cached is None:
        try:
            storage.write_collection(location, name, data)
        except (OSError, ValueError):
            # Derived storage is optional. The live observation is still usable.
            pass
    return data
