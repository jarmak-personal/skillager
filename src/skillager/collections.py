from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audience import classify_audience
from .scan import scan_path
from .schema import SchemaError, Skill, load_skill_from_dir
from .search import search as search_skills
from .trust import content_hash, trust_state


def collections_path(state_root: Path) -> Path:
    return state_root / "collections.json"


def collection_index_dir(state_root: Path) -> Path:
    return state_root / "collections"


def tags_path(state_root: Path) -> Path:
    return state_root / "tags.json"


def project_tags_path(state_root: Path) -> Path:
    return state_root / "project_tags.json"


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
    skills, errors = _index_collection_skills(state_root, name, root)
    data = {"schema": "skillager.collection-index.v1", "name": name, "path": str(root), "skills": skills, "errors": errors}
    collection_index_dir(state_root).mkdir(parents=True, exist_ok=True)
    _collection_index_path(state_root, name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def collection_skills(state_root: Path, name: str | None = None, *, trust_root: Path | None = None) -> list[dict[str, Any]]:
    names = [_slug(name)] if name else sorted(load_collections(state_root).get("collections", {}))
    trust_root = trust_root or state_root
    skills: list[dict[str, Any]] = []
    for collection_name in names:
        data = _load_or_refresh_collection_index(state_root, collection_name)
        for skill in data.get("skills", []):
            skill = dict(skill)
            if skill.get("id") and skill.get("content_hash"):
                skill["trust"] = trust_state(trust_root, skill["id"], skill["content_hash"])
            skills.append(skill)
    return skills


def search_collection(
    state_root: Path,
    name: str,
    query: str,
    *,
    include_blocked: bool = False,
    trust_root: Path | None = None,
) -> list[dict[str, Any]]:
    return search_skills(collection_skills(state_root, name, trust_root=trust_root), query, include_blocked=include_blocked)


def find_collection_skill(state_root: Path, skill_id: str, *, trust_root: Path | None = None) -> dict[str, Any]:
    for skill in collection_skills(state_root, trust_root=trust_root):
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


def add_tag_skill(state_root: Path, tag: str, skill_id: str) -> dict[str, Any]:
    tag = _slug(tag)
    find_collection_skill(state_root, skill_id)
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
) -> dict[str, Any]:
    tag = _slug(tag)
    valid_ids = {skill["id"] for skill in collection_skills(state_root)}
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


def tag_skills(state_root: Path, tag: str, *, trust_root: Path | None = None) -> list[dict[str, Any]]:
    tag = _slug(tag)
    ids = set(load_tags(state_root).get("tags", {}).get(tag, []))
    return [skill for skill in collection_skills(state_root, trust_root=trust_root) if skill.get("id") in ids]


def load_project_tags(state_root: Path) -> dict[str, Any]:
    path = project_tags_path(state_root)
    if not path.exists():
        return {"attached_tags": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_project_tags(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    project_tags_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def attached_tag_skills(state_root: Path, *, catalog_root: Path | None = None) -> list[dict[str, Any]]:
    catalog_root = catalog_root or state_root
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in load_project_tags(state_root).get("attached_tags", []):
        for skill in tag_skills(catalog_root, tag, trust_root=state_root):
            if skill["id"] in seen:
                continue
            item = dict(skill)
            item["tags"] = sorted(set(item.get("tags", [])) | {tag})
            result.append(item)
            seen.add(skill["id"])
    return result


def _index_collection_skills(state_root: Path, name: str, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    skills: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for skill_dir in _skill_dirs(root):
        try:
            source = {"type": "collection", "collection": name, "path": str(root)}
            skill = _collection_skill(load_skill_from_dir(skill_dir, source), collection=name)
            digest = content_hash(skill.root)
            scan = scan_path(skill.root, allow_tools=bool(skill.safety.get("allow_tools", False)))
            trust = trust_state(state_root, skill.id, digest)
            entry = skill.to_index(digest, scan, trust)
            entry["audience_guess"] = classify_audience(skill)
            skills.append(entry)
        except (SchemaError, OSError, ValueError) as exc:
            errors.append({"path": str(skill_dir), "error": str(exc)})
    skills.sort(key=lambda item: item["id"])
    return skills, errors


def _collection_skill(skill: Skill, *, collection: str) -> Skill:
    leaf = skill.id.rsplit("/", 1)[-1]
    source = dict(skill.source)
    source["collection"] = collection
    return replace(skill, id=f"{collection}/{leaf}", source=source, package=collection)


def _load_or_refresh_collection_index(state_root: Path, name: str) -> dict[str, Any]:
    path = _collection_index_path(state_root, name)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return refresh_collection(state_root, name)


def _collection_index_path(state_root: Path, name: str) -> Path:
    return collection_index_dir(state_root) / f"{_slug(name)}.json"


def _skill_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if (root / "SKILL.md").exists():
        return [root]
    return sorted(path.parent for path in root.rglob("SKILL.md") if not (path.parent / "skillager.materialized.yaml").exists())


def _slug(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one alphanumeric character")
    return slug
