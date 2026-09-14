"""Complete, bounded destination effects for local exposure lifecycle plans."""
from __future__ import annotations

import hashlib
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..simple_yaml import dumps, load_mapping
from .preview import GENERATED_METADATA
from .target_state import MATERIALIZED_SIDECAR, matches_materialized_target, target_state_manifest
from .plan_request import MAX_EFFECTS, MAX_METADATA_BYTES, MAX_STAGED_BYTES, PlanRefusal, canonical_json


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bounded_metadata(value: Any) -> None:
    if len(canonical_json(value).encode("utf-8")) > MAX_METADATA_BYTES:
        raise PlanRefusal("metadata-limit", "Exposure metadata exceeds 64 KiB")


def tree_state(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise PlanRefusal("unsafe-target", "Exposure targets must not be symlinks")
    if not path.exists():
        return None
    if not path.is_dir():
        raise PlanRefusal("unsafe-target", "Exposure target must be a directory")
    entries = target_state_manifest(path, max_entries=MAX_EFFECTS, max_bytes=MAX_STAGED_BYTES)
    if any(item["type"] not in {"file", "directory"} for item in entries.values()):
        raise PlanRefusal("unsafe-target", "Exposure target contains a symlink or special entry")
    return {"mode": stat.S_IMODE(path.stat().st_mode), "entries": entries}


def file_state(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise PlanRefusal("unsafe-target", "Exposure metadata must not be a symlink")
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
        raise PlanRefusal("metadata-limit", "Exposure metadata must be a regular file of at most 64 KiB")
    raw = path.read_bytes()
    return {"type": "file", "mode": stat.S_IMODE(info.st_mode), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def directory_state(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise PlanRefusal("unsafe-target", "Exposure ancestors must be ordinary project directories")
    return {"type": "directory", "mode": stat.S_IMODE(path.stat().st_mode)} if path.exists() else None


def managed_data(path: Path, *, agent: str) -> dict[str, Any]:
    tree_state(path)
    file_state(path / MATERIALIZED_SIDECAR)
    data = load_mapping(path / MATERIALIZED_SIDECAR)
    bounded_metadata(data)
    if data.get("schema") not in {"skillager.materialized.v1", "skillager.router.v1"} or data.get("agent") != agent or data.get("scope") != "project":
        raise PlanRefusal("target-identity", "The selected target has a different managed identity")
    if not matches_materialized_target(path, data):
        raise PlanRefusal("modified-target", "The selected managed target contains local changes")
    return data


def require_managed_metadata(before: dict[str, Any] | None, data: dict[str, Any]) -> None:
    """Do not mix displayed managed identity with a different captured sidecar."""
    expected = hashlib.sha256(dumps(data).encode("utf-8")).hexdigest()
    actual = (before or {}).get("entries", {}).get(MATERIALIZED_SIDECAR, {}).get("sha256")
    if actual != expected:
        raise PlanRefusal("target-changed", "Managed identity changed during planning; request a fresh preview")


def result_identity(target: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in target.items() if key not in {"file_effects", "before", "after"}}


@dataclass
class PlanTarget:
    path: Path
    kind: str
    before: dict[str, Any] | None
    candidate: Path | None = None
    metadata: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    keep: bool = False
    parent_mode: int = 0o755
    prepared_state: dict[str, Any] | None = None

    def observe(self, path: Path | None = None) -> dict[str, Any] | None:
        selected = path or self.path
        if self.kind == "parent":
            return directory_state(selected)
        if self.kind == "tags":
            return file_state(selected)
        return tree_state(selected)

    def after(self, *, deterministic: bool = True) -> dict[str, Any] | None:
        if self.keep:
            return self.before
        if self.kind == "parent":
            return {"type": "directory", "mode": self.parent_mode}
        if self.candidate is None:
            return None
        state = deepcopy(self.prepared_state) if self.prepared_state is not None else self.observe(self.candidate)
        if deterministic and self.metadata is not None:
            assert state is not None
            bounded_metadata(self.metadata)
            if self.kind == "tags":
                state = {"type": "file", "mode": state["mode"], "metadata": self.metadata,
                         "generated_fields": {f"tags.{(self.identity or {})['tag']}.updated_at": "UTC membership change time"}}
            else:
                state["entries"][MATERIALIZED_SIDECAR] = {
                    "type": "file", "mode": state["entries"][MATERIALIZED_SIDECAR]["mode"],
                    "metadata": {key: value for key, value in self.metadata.items() if key not in GENERATED_METADATA},
                    "generated_fields": GENERATED_METADATA,
                }
        return state

    def public(self) -> dict[str, Any]:
        after = self.after()
        before = self.before
        if self.kind in {"parent", "tags"}:
            old = {".": before} if before is not None else {}
            new = {".": after} if after is not None else {}
        else:
            old = before["entries"] if before else {}
            new = after["entries"] if after else {}
        effects = [{"path": name, "action": "keep" if old.get(name) == new.get(name) else "remove" if name not in new else "replace" if name in old else "create",
                    "before": old.get(name), "after": new.get(name)} for name in sorted(old.keys() | new.keys())]
        return {"target_id": digest({"kind": self.kind, "path": str(self.path)}), "kind": self.kind, "path": str(self.path),
                **(self.identity or {}), "action": "keep" if self.keep else "remove" if after is None else "replace" if before is not None else "create",
                "before": {"state_hash": digest(before), "mode": before["mode"]} if before is not None else None,
                "after": {"state_hash": digest(after), "mode": after["mode"]} if after is not None else None,
                "file_effects": effects}

    def revalidate(self) -> None:
        if self.observe() != self.before:
            raise PlanRefusal("target-changed", "A selected target or its permissions changed; request a fresh preview")
