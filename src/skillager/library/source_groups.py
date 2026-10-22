"""Library browse provenance. These labels never establish trust or identity relations."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


GROUP_ID = re.compile(r"source:[0-9a-f]{64}")
GROUP_KINDS = {"project", "collection", "package", "directory", "personal", "unknown", "multiple"}


def _group(kind: str, label: str, detail: str | None, *identity: str) -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(["skillager.library-source.v1", kind, *identity], separators=(",", ":")).encode()).hexdigest()
    return {"id": f"source:{digest}", "label": label[:256], "kind": kind, "detail": detail}


def _absolute(value: Any) -> str | None:
    # Stored origins remain useful after the original disappears. Do not stat,
    # resolve, discover, or open that external location during library browsing.
    return value if isinstance(value, str) and len(value) <= 4096 and Path(value).is_absolute() else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() and len(value) <= 256 else None


def _directory(root: str) -> str:
    parent = Path(root).parent
    if parent.name in {"skills", ".skills"}:
        parent = parent.parent
    if parent.name in {".agents", ".codex", ".claude", ".skillager"}:
        parent = parent.parent
    return str(parent)


def source_group(source: dict[str, Any], project: Path | None) -> dict[str, Any]:
    """Capture presentation provenance from the exact source selected by import/sync."""
    metadata = source.get("source") or {}
    return _origin_group({"path": source.get("root"), "source_type": metadata.get("type"),
                          "native": source.get("native"), "provenance": metadata},
                         str(project.resolve()) if project else None)


def _origin_group(origin: dict[str, Any], project: str | None = None) -> dict[str, Any]:
    metadata = origin.get("provenance")
    metadata = metadata if isinstance(metadata, dict) else {}
    root = _absolute(origin.get("path"))
    container = _absolute(metadata.get("path")) or (_directory(root) if root else None)
    collection, package = _text(metadata.get("collection")), _text(metadata.get("package"))
    if collection and container:
        return _group("collection", collection, container, collection, container)
    if package and container:
        return _group("package", package, container, str(origin.get("source_type")), package, container)
    native = origin.get("native")
    native = native if isinstance(native, dict) else {}
    project = _absolute(native.get("project_root")) or _absolute(project)
    if project is None and origin.get("source_type") == "project":
        project = container
    if project and (origin.get("source_type") == "project" or native.get("scope") == "project"):
        return _group("project", Path(project).name or project, project, project)
    if container:
        return _group("directory", Path(container).name or container, container, container)
    return _group("unknown", "Unknown source", None)


def _stored_group(value: Any) -> dict[str, Any] | None:
    if (isinstance(value, dict) and isinstance(value.get("id"), str) and GROUP_ID.fullmatch(value["id"])
            and isinstance(value.get("kind"), str) and value["kind"] in GROUP_KINDS and _text(value.get("label"))
            and (value.get("detail") is None or isinstance(value["detail"], str) and len(value["detail"]) <= 4096)):
        return {key: value.get(key) for key in ("id", "label", "kind", "detail")}
    return None


def library_source_group(provenance: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
    """Use persisted browse facts first, then historical exact source records."""
    if not isinstance(provenance, dict):
        return _group("unknown", "Unknown source", None)
    stored = _stored_group(provenance.get("source_group"))
    if stored:
        return stored
    for field in ("import_source", "import_preservation"):
        relation = approval.get(field)
        if isinstance(relation, dict) and isinstance(relation.get("origin"), dict):
            return _origin_group(relation["origin"], relation.get("project_root"))
    sync = provenance.get("sync")
    if isinstance(sync, dict):
        origins = sync.get("origins")
        groups = {_origin_group(origin)["id"]: _origin_group(origin) for origin in origins if isinstance(origin, dict)} if isinstance(origins, list) else {}
        if len(groups) == 1:
            return next(iter(groups.values()))
        if groups:
            ordered = sorted(groups.values(), key=lambda group: (group["label"].casefold(), group["detail"] or "", group["id"]))
            label = " + ".join(group["label"] for group in ordered)
            detail = " · ".join(f"{group['label']}: {group['detail']}" if group["detail"] else group["label"] for group in ordered)
            return _group("multiple", label[:253] + "..." if len(label) > 256 else label, detail[:4096], *sorted(groups))
    if provenance:
        return _group("unknown", "Unknown source", None)
    return _group("personal", "Personal", None)


def group_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = row["source_group"]
        item = groups.setdefault(group["id"], {**group, "count": 0})
        item["count"] += 1
    return sorted(groups.values(), key=lambda group: (group["label"].casefold(), group["detail"] or "", group["id"]))


def filter_source_group(rows: list[dict[str, Any]], identifier: str | None) -> list[dict[str, Any]]:
    if identifier is None:
        return rows
    if not GROUP_ID.fullmatch(identifier):
        raise ValueError("--source-group requires an opaque group ID returned by list --group-by source")
    return [row for row in rows if (row.get("source_group") or {}).get("id") == identifier]
