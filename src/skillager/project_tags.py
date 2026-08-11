from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from .state.locking import resource_lock
from .state.statefiles import read_user_json, write_user_json


PROJECT_TAGS_SCHEMA = "skillager.project-tags.v1"
MutationResult = TypeVar("MutationResult")


def tags_path(project_dir: Path) -> Path:
    return project_dir.expanduser().resolve() / ".skillager" / "tags.json"


def load_tags(project_dir: Path) -> dict[str, Any]:
    path = _validated_tags_path(project_dir)
    if not path.exists():
        return _empty_tags()
    return normalize_tags(read_user_json(path, _empty_tags()))


def save_tags(project_dir: Path, data: dict[str, Any]) -> None:
    replacement = normalize_tags(data)

    def mutation(current: dict[str, Any]) -> None:
        current.clear()
        current.update(replacement)

    mutate_tags(project_dir, mutation)


def mutate_tags(
    project_dir: Path,
    mutation: Callable[[dict[str, Any]], MutationResult],
) -> MutationResult:
    """Apply one atomic project-tag mutation under a project-contained lock."""

    project_root = project_dir.expanduser().resolve()
    path = _validated_tags_path(project_root)
    with resource_lock(path):
        path = _validated_tags_path(project_root)
        current = normalize_tags(read_user_json(path, _empty_tags()))
        previous = normalize_tags(current)
        result = mutation(current)
        normalized = normalize_tags(current)
        if normalized != previous:
            path = _validated_tags_path(project_root, create_parent=True)
            write_user_json(path, normalized)
        return result


def normalize_tags(data: dict[str, Any]) -> dict[str, Any]:
    tags = data.get("tags") if isinstance(data, dict) else {}
    normalized: dict[str, Any] = {"schema": PROJECT_TAGS_SCHEMA, "tags": {}}
    if isinstance(data, dict):
        for key in ("catalog_state_dir",):
            value = data.get(key)
            if isinstance(value, str) and value:
                normalized[key] = value
    if not isinstance(tags, dict):
        return normalized
    for raw_tag, raw_entry in tags.items():
        try:
            tag = normalize_tag(str(raw_tag))
        except ValueError:
            continue
        if isinstance(raw_entry, list):
            skills = _normalize_skill_ids(raw_entry)
            entry: dict[str, Any] = {"skills": skills}
        elif isinstance(raw_entry, dict):
            entry = dict(raw_entry)
            entry["skills"] = _normalize_skill_ids(entry.get("skills") or [])
            imported_from = entry.get("imported_from")
            if imported_from is not None and not isinstance(imported_from, list):
                entry.pop("imported_from", None)
        else:
            continue
        normalized["tags"][tag] = entry
    return normalized


def create_tag(project_dir: Path, tag: str, *, catalog_state_dir: Path | None = None) -> dict[str, Any]:
    tag = normalize_tag(tag)

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        _remember_catalog_state(data, catalog_state_dir)
        data.setdefault("tags", {}).setdefault(tag, {"skills": []})
        _touch_tag(data["tags"][tag])
        return {"tag": tag, "skills": data["tags"][tag]["skills"]}

    return mutate_tags(project_dir, mutation)


def set_tag_skills(
    project_dir: Path,
    tag: str,
    skill_ids: list[str],
    *,
    sync: bool = False,
    source_collection: str | None = None,
    catalog_state_dir: Path | None = None,
) -> dict[str, Any]:
    tag = normalize_tag(tag)

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        _remember_catalog_state(data, catalog_state_dir)
        tags = data.setdefault("tags", {})
        current = tags.setdefault(tag, {"skills": []})
        if sync:
            skills = sorted(dict.fromkeys(skill_ids))
        else:
            skills = sorted(set(current.get("skills") or []) | set(skill_ids))
        current["skills"] = skills
        if source_collection:
            collections = set(current.get("source_collections") or [])
            collections.add(normalize_tag(source_collection))
            current["source_collections"] = sorted(collections)
            current["managed_by"] = "collection"
        _touch_tag(current)
        return {"tag": tag, "skills": skills}

    return mutate_tags(project_dir, mutation)


def add_tag_skills(
    project_dir: Path,
    tag: str,
    skill_ids: list[str],
    *,
    catalog_state_dir: Path | None = None,
) -> dict[str, Any]:
    return set_tag_skills(project_dir, tag, skill_ids, catalog_state_dir=catalog_state_dir)


def remove_tag_skills(project_dir: Path, tag: str, skill_ids: list[str]) -> dict[str, Any]:
    tag = normalize_tag(tag)

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        tags = data.setdefault("tags", {})
        entry = tags.setdefault(tag, {"skills": []})
        remove = set(skill_ids)
        entry["skills"] = [skill_id for skill_id in entry.get("skills", []) if skill_id not in remove]
        _touch_tag(entry)
        return {"tag": tag, "skills": entry["skills"]}

    return mutate_tags(project_dir, mutation)


def delete_tag(project_dir: Path, tag: str) -> dict[str, Any]:
    tag = normalize_tag(tag)

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        removed = data.setdefault("tags", {}).pop(tag, None)
        return {"tag": tag, "removed": removed is not None, "tags": sorted(data.get("tags", {}))}

    return mutate_tags(project_dir, mutation)


def clear_tags(project_dir: Path) -> int:
    project_root = project_dir.expanduser().resolve()
    path = _validated_tags_path(project_root)
    if not path.exists():
        return 0
    with resource_lock(path):
        path = _validated_tags_path(project_root)
        if not path.exists():
            return 0
        count = len(normalize_tags(read_user_json(path, _empty_tags())).get("tags") or {})
        path.unlink()
        return count


def tag_names(project_dir: Path) -> list[str]:
    return sorted(load_tags(project_dir).get("tags", {}))


def tag_skills(project_dir: Path, tag: str) -> list[str]:
    tag = normalize_tag(tag)
    entry = load_tags(project_dir).get("tags", {}).get(tag) or {}
    return list(entry.get("skills") or [])


def normalize_tag(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one alphanumeric character")
    return slug


def _normalize_skill_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted(dict.fromkeys(str(value) for value in values if isinstance(value, str) and value))


def _touch_tag(entry: dict[str, Any]) -> None:
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()


def _remember_catalog_state(data: dict[str, Any], catalog_state_dir: Path | None) -> None:
    if catalog_state_dir is not None:
        data["catalog_state_dir"] = str(catalog_state_dir.expanduser().resolve())


def _empty_tags() -> dict[str, Any]:
    return {"schema": PROJECT_TAGS_SCHEMA, "tags": {}}


def _validated_tags_path(project_dir: Path, *, create_parent: bool = False) -> Path:
    project_root = project_dir.expanduser().resolve()
    parent = project_root / ".skillager"
    if parent.is_symlink():
        raise ValueError(f"refusing symlinked project tag state directory: {parent}")
    if parent.exists() and not parent.is_dir():
        raise ValueError(f"project tag state parent is not a directory: {parent}")
    if create_parent and not parent.exists():
        parent.mkdir(mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError(f"refusing unsafe project tag state directory: {parent}")
    path = parent / "tags.json"
    if path.is_symlink():
        raise ValueError(f"refusing symlinked project tags file: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"refusing non-file project tags path: {path}")
    return path
