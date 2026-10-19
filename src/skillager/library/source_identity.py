"""Stable external source and physical occurrence identity shared by import and sync."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..state.trust import approval_key_for


def source_key(skill: dict[str, Any]) -> str:
    key = skill.get("approval_key") or approval_key_for(
        str(skill["id"]),
        skill.get("root"),
        skill.get("source") or {},
        entrypoint=skill.get("entrypoint"),
    )
    if isinstance(key, str) and key:
        return key
    return f"path:{Path(skill['root']).resolve().as_posix()}"


def identity(kind: str, *parts: str) -> str:
    digest = hashlib.sha256(f"skillager.sync-{kind}.v1\0".encode())
    digest.update(json.dumps(parts, separators=(",", ":")).encode())
    return digest.hexdigest()


def source_identity(skill: dict[str, Any]) -> str:
    return identity("source", source_key(skill))


def origin(skill: dict[str, Any], project: Path | None, *, source_id: str) -> dict[str, Any]:
    source = skill.get("source") or {}
    root = str(Path(skill["root"]).resolve())
    entrypoint = str(Path(skill["entrypoint"]).resolve())
    native = skill.get("native")
    return {
        "origin_id": identity("origin", source_id, root, entrypoint),
        "skill_id": skill["id"], "source_type": source.get("type", "unknown"),
        "path": root, "entrypoint": entrypoint,
        "native": ({"agent": native["agent"], "scope": native["scope"],
                    "project_root": str(project.resolve()) if project and native["scope"] == "project" else None} if native else None),
        "provenance": {name: (source[name] in {True, "true", "True"} if name == "editable" else source[name])
                       for name in ("collection", "package", "version", "editable") if name in source},
    }
