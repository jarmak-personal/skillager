from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audience import classify_audience
from .lint import lint_skill
from .scan import scan_path
from .schema import QuarantinedSkill, SchemaError, Skill, load_skill_from_dir, quarantine_skill_from_dir
from .search import search as search_skills
from .selection import select_visible_skills
from .trust import approval_key_for, content_hash, load_trust, save_trust, trust_info

COLLECTION_MIGRATIONS_SCHEMA = "skillager.collection-migrations.v1"
IGNORED_SKILL_DIR_NAMES = {
    ".cache",
    ".git",
    ".gradle",
    ".next",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}
WRAPPER_ID_PARTS = {".agents", ".claude", ".codex", ".skills", "agent-skills", "skills"}


def collections_path(state_root: Path) -> Path:
    return state_root / "collections.json"


def collection_index_dir(state_root: Path) -> Path:
    return state_root / "collections"


def tags_path(state_root: Path) -> Path:
    return state_root / "tags.json"


def project_tags_path(state_root: Path) -> Path:
    return state_root / "project_tags.json"


def collection_migrations_path(state_root: Path) -> Path:
    return state_root / "collection_migrations.json"


def load_collections(state_root: Path) -> dict[str, Any]:
    path = collections_path(state_root)
    if not path.exists():
        return {"collections": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_collections(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    collections_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def add_collection(state_root: Path, name: str, path: Path) -> dict[str, Any]:
    name = _slug(name)
    root = path.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"collection path does not exist: {root}")
    data = load_collections(state_root)
    data.setdefault("collections", {})[name] = {"name": name, "path": str(root)}
    save_collections(state_root, data)
    index = refresh_collection(state_root, name)
    return {"collection": data["collections"][name], "indexed": len(index.get("skills", [])), "errors": index.get("errors", [])}


def remove_collection(state_root: Path, name: str) -> bool:
    name = _slug(name)
    data = load_collections(state_root)
    removed = data.setdefault("collections", {}).pop(name, None) is not None
    if removed:
        save_collections(state_root, data)
        index_path = _collection_index_path(state_root, name)
        if index_path.exists():
            index_path.unlink()
    return removed


def refresh_collection(state_root: Path, name: str) -> dict[str, Any]:
    name = _slug(name)
    collection = load_collections(state_root).get("collections", {}).get(name)
    if not collection:
        raise KeyError(f"collection not found: {name}")
    root = Path(collection["path"]).expanduser().resolve()
    old_index = _load_collection_index(state_root, name)
    skills, errors = _index_collection_skills(state_root, name, root)
    data = {"schema": "skillager.collection-index.v1", "name": name, "path": str(root), "skills": skills, "errors": errors}
    _migrate_collection_references(state_root, name, old_index, data)
    collection_index_dir(state_root).mkdir(parents=True, exist_ok=True)
    _collection_index_path(state_root, name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def select_collection_skills(
    state_root: Path,
    name: str | None = None,
    *,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
) -> list[dict[str, Any]]:
    return select_visible_skills(
        _collection_skills(state_root, name, trust_root=trust_root, approval_root=approval_root),
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    )


def _collection_skills(
    state_root: Path,
    name: str | None = None,
    *,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
) -> list[dict[str, Any]]:
    names = [_slug(name)] if name else sorted(load_collections(state_root).get("collections", {}))
    trust_root = trust_root or state_root
    approval_root = approval_root or trust_root
    skills: list[dict[str, Any]] = []
    for collection_name in names:
        data = _load_or_refresh_collection_index(state_root, collection_name)
        for skill in data.get("skills", []):
            skill = dict(skill)
            if skill.get("id") and skill.get("content_hash"):
                approval_key = skill.get("approval_key") or approval_key_for(
                    skill["id"],
                    skill.get("root"),
                    skill.get("source") or {},
                    entrypoint=skill.get("entrypoint"),
                )
                trust = trust_info(
                    trust_root,
                    skill["id"],
                    skill["content_hash"],
                    lint=skill.get("lint"),
                    approval_key=approval_key,
                    approval_root=approval_root,
                )
                _apply_approval_metadata(skill, approval_key, trust)
            skills.append(skill)
    return skills


def search_collection(
    state_root: Path,
    name: str,
    query: str,
    *,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
) -> list[dict[str, Any]]:
    return search_skills(
        select_collection_skills(
            state_root,
            name,
            trust_root=trust_root,
            approval_root=approval_root,
            include_blocked=include_blocked,
            include_lint_blocked=include_lint_blocked,
        ),
        query,
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    )


def _find_collection_skill(
    state_root: Path,
    skill_id: str,
    *,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
) -> dict[str, Any]:
    for skill in _collection_skills(state_root, trust_root=trust_root, approval_root=approval_root):
        if skill.get("id") == skill_id:
            return skill
    raise KeyError(f"collection skill not found: {skill_id}")


def load_tags(state_root: Path) -> dict[str, Any]:
    path = tags_path(state_root)
    if not path.exists():
        return {"tags": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_tags(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    tags_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_tag(state_root: Path, tag: str) -> dict[str, Any]:
    tag = _slug(tag)
    data = load_tags(state_root)
    data.setdefault("tags", {}).setdefault(tag, [])
    save_tags(state_root, data)
    return {"tag": tag, "skills": data["tags"][tag]}


def add_tag_skill(state_root: Path, tag: str, skill_id: str, *, validate: bool = True) -> dict[str, Any]:
    tag = _slug(tag)
    if validate:
        _find_collection_skill(state_root, skill_id)
    data = load_tags(state_root)
    skills = data.setdefault("tags", {}).setdefault(tag, [])
    if skill_id not in skills:
        skills.append(skill_id)
        skills.sort()
    save_tags(state_root, data)
    return {"tag": tag, "skills": skills}


def set_tag_skills(
    state_root: Path,
    tag: str,
    skill_ids: list[str],
    *,
    sync: bool = False,
    source_collection: str | None = None,
    valid_ids: set[str] | None = None,
) -> dict[str, Any]:
    tag = _slug(tag)
    valid_ids = valid_ids if valid_ids is not None else {skill["id"] for skill in _collection_skills(state_root)}
    missing = sorted(skill_id for skill_id in skill_ids if skill_id not in valid_ids)
    if missing:
        hint = f" for collection {source_collection}" if source_collection else ""
        raise KeyError(f"collection skill not found{hint}: {missing[0]}")
    data = load_tags(state_root)
    if sync:
        skills = sorted(dict.fromkeys(skill_ids))
    else:
        current = data.setdefault("tags", {}).setdefault(tag, [])
        skills = sorted(set(current) | set(skill_ids))
    data.setdefault("tags", {})[tag] = skills
    if source_collection:
        metadata = data.setdefault("tag_metadata", {}).setdefault(tag, {})
        source_collections = set() if sync else set(metadata.get("source_collections") or [])
        source_collections.add(_slug(source_collection))
        metadata["source_collections"] = sorted(source_collections)
        metadata["managed_by"] = "collection"
    save_tags(state_root, data)
    return {"tag": tag, "skills": skills}


def remove_tag_skill(state_root: Path, tag: str, skill_id: str) -> dict[str, Any]:
    tag = _slug(tag)
    data = load_tags(state_root)
    skills = data.setdefault("tags", {}).setdefault(tag, [])
    if skill_id in skills:
        skills.remove(skill_id)
    save_tags(state_root, data)
    return {"tag": tag, "skills": skills}


def select_tag_skills(
    state_root: Path,
    tag: str,
    *,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
) -> list[dict[str, Any]]:
    return select_visible_skills(
        _tag_skills(state_root, tag, trust_root=trust_root, approval_root=approval_root),
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    )


def _tag_skills(
    state_root: Path,
    tag: str,
    *,
    trust_root: Path | None = None,
    approval_root: Path | None = None,
) -> list[dict[str, Any]]:
    tag = _slug(tag)
    if trust_root:
        apply_collection_trust_migrations(trust_root, state_root)
    ids = set(load_tags(state_root).get("tags", {}).get(tag, []))
    return [
        skill
        for skill in _collection_skills(state_root, trust_root=trust_root, approval_root=approval_root)
        if skill.get("id") in ids
    ]


def load_project_tags(state_root: Path) -> dict[str, Any]:
    path = project_tags_path(state_root)
    if not path.exists():
        return {"attached_tags": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_project_tags(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    project_tags_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_collection_migrations(state_root: Path) -> dict[str, Any]:
    path = collection_migrations_path(state_root)
    if not path.exists():
        return {"schema": COLLECTION_MIGRATIONS_SCHEMA, "collections": {}, "acknowledged": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("schema", COLLECTION_MIGRATIONS_SCHEMA)
    data.setdefault("collections", {})
    data.setdefault("acknowledged", [])
    return data


def save_collection_migrations(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    collection_migrations_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collection_migration_summary(state_root: Path) -> dict[str, Any]:
    data = load_collection_migrations(state_root)
    outcomes = [outcome for outcome in data.get("collections", {}).values() if outcome.get("has_changes")]
    digest = _migration_digest(outcomes)
    acknowledged = set(data.get("acknowledged", []))
    totals = {
        "collections": len(outcomes),
        "id_migrations": sum(len(outcome.get("id_migrations", [])) for outcome in outcomes),
        "trust_migrated": sum(len(outcome.get("trust_migrated", [])) for outcome in outcomes),
        "needs_review": sum(len(outcome.get("needs_review", [])) for outcome in outcomes),
        "tag_migrated": sum(len(outcome.get("tag_migrated", [])) for outcome in outcomes),
        "tag_needs_repair": sum(len(outcome.get("tag_needs_repair", [])) for outcome in outcomes),
    }
    return {
        "pending": bool(outcomes) and digest not in acknowledged,
        "hash": digest,
        "totals": totals,
        "collections": outcomes,
    }


def ack_collection_migrations(state_root: Path) -> dict[str, Any]:
    data = load_collection_migrations(state_root)
    summary = collection_migration_summary(state_root)
    if summary.get("hash"):
        acknowledged = set(data.get("acknowledged", []))
        acknowledged.add(summary["hash"])
        data["acknowledged"] = sorted(acknowledged)
        save_collection_migrations(state_root, data)
    summary["pending"] = False
    return summary


def apply_collection_trust_migrations(state_root: Path, catalog_root: Path) -> int:
    migrations = load_collection_migrations(catalog_root)
    id_migrations = [
        migration
        for outcome in migrations.get("collections", {}).values()
        for migration in outcome.get("id_migrations", [])
    ]
    if not id_migrations:
        return 0
    data = load_trust(state_root)
    skills = data.setdefault("skills", {})
    changed = 0
    for migration in id_migrations:
        old_id = migration.get("old_id")
        new_id = migration.get("new_id")
        old_hash = migration.get("old_content_hash")
        new_hash = migration.get("new_content_hash")
        if not old_id or not new_id or old_id == new_id or not old_hash or old_hash != new_hash:
            continue
        record = skills.get(old_id)
        if not record or record.get("content_hash") != old_hash:
            continue
        if skills.get(new_id) == record:
            continue
        skills[new_id] = dict(record)
        changed += 1
    if changed:
        save_trust(state_root, data)
    return changed


def attach_project_tag(state_root: Path, tag: str, *, catalog_root: Path | None = None) -> dict[str, Any]:
    tag = _slug(tag)
    catalog_root = catalog_root or state_root
    if tag not in load_tags(catalog_root).get("tags", {}):
        raise KeyError(f"tag not found: {tag}")
    data = load_project_tags(state_root)
    data["catalog_state_dir"] = str(catalog_root.expanduser().resolve())
    tags = data.setdefault("attached_tags", [])
    if tag not in tags:
        tags.append(tag)
        tags.sort()
    save_project_tags(state_root, data)
    return data


def detach_project_tag(state_root: Path, tag: str) -> dict[str, Any]:
    tag = _slug(tag)
    data = load_project_tags(state_root)
    tags = data.setdefault("attached_tags", [])
    if tag in tags:
        tags.remove(tag)
    save_project_tags(state_root, data)
    return data


def select_attached_tag_skills(
    state_root: Path,
    *,
    catalog_root: Path | None = None,
    approval_root: Path | None = None,
    include_blocked: bool = False,
    include_lint_blocked: bool = False,
) -> list[dict[str, Any]]:
    return select_visible_skills(
        _attached_tag_skills(state_root, catalog_root=catalog_root, approval_root=approval_root),
        include_blocked=include_blocked,
        include_lint_blocked=include_lint_blocked,
    )


def _attached_tag_skills(
    state_root: Path,
    *,
    catalog_root: Path | None = None,
    approval_root: Path | None = None,
) -> list[dict[str, Any]]:
    catalog_root = catalog_root or state_root
    apply_collection_trust_migrations(state_root, catalog_root)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in load_project_tags(state_root).get("attached_tags", []):
        for skill in _tag_skills(catalog_root, tag, trust_root=state_root, approval_root=approval_root):
            if skill["id"] in seen:
                continue
            item = dict(skill)
            item["tags"] = sorted(set(item.get("tags", [])) | {tag})
            result.append(item)
            seen.add(skill["id"])
    return result


def _apply_approval_metadata(entry: dict[str, Any], approval_key: str | None, trust: dict[str, Any]) -> None:
    entry["trust"] = trust.get("state", "discovered")
    for key in ("trust_reason", "trust_scope"):
        entry.pop(key, None)
    if approval_key:
        entry["approval_key"] = approval_key
    if trust.get("reason"):
        entry["trust_reason"] = trust["reason"]
    if trust.get("scope"):
        entry["trust_scope"] = trust["scope"]


def _index_collection_skills(state_root: Path, name: str, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    skills: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for skill_dir in _skill_dirs(root):
        try:
            source = {"type": "collection", "collection": name, "path": str(root)}
            skill = _collection_skill(load_skill_from_dir(skill_dir, source), collection=name, collection_root=root)
            digest = content_hash(skill.root)
            scan = scan_path(skill.root, allow_tools=False)
            lint = lint_skill(skill)
            approval_key = approval_key_for(skill.id, skill.root, skill.source, entrypoint=skill.entrypoint)
            trust = trust_info(state_root, skill.id, digest, lint=lint, approval_key=approval_key, approval_root=state_root)
            entry = skill.to_index(digest, scan, trust.get("state", "discovered"))
            _apply_approval_metadata(entry, approval_key, trust)
            entry["lint"] = lint
            entry["audience_guess"] = classify_audience(skill)
            skills.append(entry)
        except (SchemaError, OSError, ValueError) as exc:
            source = {"type": "collection", "collection": name, "path": str(root)}
            quarantined = quarantine_skill_from_dir(skill_dir, source, exc)
            if quarantined:
                try:
                    skill = _collection_quarantined(quarantined, collection=name, collection_root=root)
                except ValueError:
                    skill = quarantined
                digest = content_hash(skill.root)
                scan = scan_path(skill.root, allow_tools=False)
                approval_key = approval_key_for(skill.id, skill.root, skill.source, entrypoint=skill.entrypoint)
                trust = trust_info(state_root, skill.id, digest, lint=skill.lint, approval_key=approval_key, approval_root=state_root)
                entry = skill.to_index(digest, scan, trust.get("state", "discovered"))
                _apply_approval_metadata(entry, approval_key, trust)
                skills.append(entry)
            errors.append({"path": str(skill_dir), "error": str(exc)})
    skills.sort(key=lambda item: item["id"])
    return skills, errors


def _collection_skill(skill: Skill, *, collection: str, collection_root: Path) -> Skill:
    relative_id = _collection_relative_id(skill.root, collection_root, root_leaf=skill.id.rsplit("/", 1)[-1])
    source = dict(skill.source)
    source["collection"] = collection
    return replace(skill, id=f"{collection}/{relative_id}", source=source, package=collection)


def _collection_quarantined(skill: QuarantinedSkill, *, collection: str, collection_root: Path) -> QuarantinedSkill:
    relative_id = _collection_relative_id(skill.root, collection_root, root_leaf=skill.id.rsplit("/", 1)[-1])
    source = dict(skill.source)
    source["collection"] = collection
    return replace(skill, id=f"{collection}/{relative_id}", source=source)


def _load_or_refresh_collection_index(state_root: Path, name: str) -> dict[str, Any]:
    path = _collection_index_path(state_root, name)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return refresh_collection(state_root, name)


def _load_collection_index(state_root: Path, name: str) -> dict[str, Any] | None:
    path = _collection_index_path(state_root, name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _collection_index_path(state_root: Path, name: str) -> Path:
    return collection_index_dir(state_root) / f"{_slug(name)}.json"


def _skill_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if (root / "skillager.materialized.yaml").exists():
        return []
    if (root / "SKILL.md").exists():
        return [root]
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirname for dirname in dirnames if dirname not in IGNORED_SKILL_DIR_NAMES)
        current = Path(dirpath)
        if "skillager.materialized.yaml" in filenames:
            dirnames[:] = []
            continue
        if "SKILL.md" in filenames:
            result.append(current)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def _collection_relative_id(skill_root: Path, collection_root: Path, *, root_leaf: str) -> str:
    relative_path = skill_root.resolve().relative_to(collection_root.resolve())
    if relative_path.as_posix() == "." or len(relative_path.parts) == 1:
        return root_leaf
    relative = relative_path.as_posix()
    parts = []
    for part in relative.split("/"):
        if part in WRAPPER_ID_PARTS:
            continue
        clean = _id_part(part)
        if clean:
            parts.append(clean)
    if not parts:
        raise ValueError("collection skill path must be below collection root")
    return "/".join(parts)


def _id_part(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    if not slug or len(slug) > 64:
        raise ValueError("collection path component cannot form a bounded slug")
    return slug


def _migrate_collection_references(
    state_root: Path,
    collection: str,
    old_index: dict[str, Any] | None,
    new_index: dict[str, Any],
) -> dict[str, Any]:
    if not old_index:
        return {"has_changes": False}
    old_entries = [_migration_entry(skill) for skill in old_index.get("skills", [])]
    new_by_root = {
        entry["root"]: entry
        for entry in (_migration_entry(skill) for skill in new_index.get("skills", []))
        if entry.get("root")
    }
    id_migrations = []
    for old in old_entries:
        new = new_by_root.get(old.get("root"))
        if not new or old.get("id") == new.get("id"):
            continue
        id_migrations.append(
            {
                "old_id": old["id"],
                "new_id": new["id"],
                "root": old["root"],
                "old_content_hash": old.get("content_hash"),
                "new_content_hash": new.get("content_hash"),
            }
        )
    if not id_migrations:
        return {"has_changes": False}
    trust_result = _migrate_trust(state_root, old_entries, new_by_root)
    tag_result = _migrate_tags(state_root, old_entries, new_by_root)
    outcome = {
        "schema": "skillager.collection-migration.v1",
        "collection": collection,
        "has_changes": True,
        "id_migrations": id_migrations,
        **trust_result,
        **tag_result,
    }
    data = load_collection_migrations(state_root)
    data.setdefault("collections", {})[collection] = outcome
    save_collection_migrations(state_root, data)
    return outcome


def _migrate_trust(state_root: Path, old_entries: list[dict[str, Any]], new_by_root: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    data = load_trust(state_root)
    skills = data.setdefault("skills", {})
    migrated = []
    needs_review = []
    changed = False
    for old_id, record in list(skills.items()):
        record_hash = record.get("content_hash")
        if not record_hash:
            continue
        candidates = [entry for entry in old_entries if entry.get("id") == old_id and entry.get("content_hash") == record_hash]
        if not candidates:
            continue
        if len(candidates) > 1:
            needs_review.append({"old_id": old_id, "reason": "ambiguous old ID/content hash"})
            continue
        old = candidates[0]
        new = new_by_root.get(old.get("root"))
        if not new or new.get("id") == old_id:
            continue
        if new.get("content_hash") != record_hash:
            needs_review.append(
                {
                    "old_id": old_id,
                    "new_id": new.get("id"),
                    "old_content_hash": record_hash,
                    "new_content_hash": new.get("content_hash"),
                    "reason": "content changed since last collection refresh",
                }
            )
            continue
        skills[new["id"]] = dict(record)
        migrated.append({"old_id": old_id, "new_id": new["id"], "content_hash": record_hash})
        changed = True
    if changed:
        save_trust(state_root, data)
    return {"trust_migrated": migrated, "needs_review": needs_review}


def _migrate_tags(state_root: Path, old_entries: list[dict[str, Any]], new_by_root: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    data = load_tags(state_root)
    tags = data.setdefault("tags", {})
    migrated = []
    needs_repair = []
    changed = False
    for tag, skill_ids in list(tags.items()):
        new_skill_ids: list[str] = []
        for skill_id in skill_ids:
            old_matches = [entry for entry in old_entries if entry.get("id") == skill_id]
            if not old_matches:
                new_skill_ids.append(skill_id)
                continue
            mapped_ids = sorted(
                {
                    new_by_root[entry["root"]]["id"]
                    for entry in old_matches
                    if entry.get("root") in new_by_root
                }
            )
            if len(old_matches) == 1 and len(mapped_ids) == 1:
                mapped_id = mapped_ids[0]
                new_skill_ids.append(mapped_id)
                if mapped_id != skill_id:
                    migrated.append({"tag": tag, "old_id": skill_id, "new_id": mapped_id})
                    changed = True
                continue
            new_skill_ids.append(skill_id)
            if any(mapped_id != skill_id for mapped_id in mapped_ids):
                needs_repair.append({"tag": tag, "old_id": skill_id, "candidate_ids": mapped_ids, "reason": "ambiguous old tag member"})
        deduped = sorted(dict.fromkeys(new_skill_ids))
        if deduped != skill_ids:
            tags[tag] = deduped
            changed = True
    if changed:
        save_tags(state_root, data)
    return {"tag_migrated": migrated, "tag_needs_repair": needs_repair}


def _migration_entry(skill: dict[str, Any]) -> dict[str, Any]:
    root = skill.get("root")
    return {
        "id": skill.get("id"),
        "root": str(Path(root).expanduser().resolve()) if root else None,
        "content_hash": skill.get("content_hash"),
    }


def _migration_digest(outcomes: list[dict[str, Any]]) -> str | None:
    if not outcomes:
        return None
    significant = [
        {
            "collection": outcome.get("collection"),
            "id_migrations": outcome.get("id_migrations", []),
            "trust_migrated": outcome.get("trust_migrated", []),
            "needs_review": outcome.get("needs_review", []),
            "tag_migrated": outcome.get("tag_migrated", []),
            "tag_needs_repair": outcome.get("tag_needs_repair", []),
        }
        for outcome in sorted(outcomes, key=lambda item: str(item.get("collection")))
    ]
    raw = json.dumps(significant, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _slug(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one alphanumeric character")
    return slug


def normalize_tag(value: str) -> str:
    return _slug(value)
