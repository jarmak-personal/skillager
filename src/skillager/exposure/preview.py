from __future__ import annotations

from pathlib import Path
from typing import Any

from ..library.confirmation import confirmation_token
from .target_state import MATERIALIZED_SIDECAR, target_state_manifest


PREVIEW_SCHEMA = "skillager.exposure-preview.v1"
REMOVE_PREVIEW_SCHEMA = "skillager.exposure-remove-preview.v1"
GENERATED_METADATA = {
    "materialized_at": "UTC installation time",
    "materialized_fingerprint": "advisory fingerprint of installed file metadata",
    "materialized_sidecar_hash": "integrity hash of the complete generated sidecar",
}


def exposure_source_state(skill: dict[str, Any]) -> dict[str, Any]:
    """The source identity and eligibility state a confirmed exposure may consume."""
    return {
        key: skill.get(key)
        for key in ("id", "root", "entrypoint", "content_hash", "trust", "source", "compatibility")
    }


def exposure_preview(
    skill: dict[str, Any],
    *,
    candidate: Path,
    target: Path,
    previous_target_hash: str | None,
    sidecar: dict[str, Any],
    agent: str,
    mode: str,
    project_dir: Path,
) -> dict[str, Any]:
    before = target_state_manifest(target) if previous_target_hash is not None else {}
    after = target_state_manifest(candidate)
    metadata = {key: value for key, value in sidecar.items() if key not in GENERATED_METADATA}
    after[MATERIALIZED_SIDECAR] = {
        "type": "file",
        "mode": after[MATERIALIZED_SIDECAR]["mode"],
        "metadata": metadata,
        "generated_fields": GENERATED_METADATA,
    }
    effects = [
        {
            "path": path,
            "action": "remove" if path not in after else "replace" if path in before else "create",
            "before": before.get(path),
            "after": after.get(path),
        }
        for path in sorted(before.keys() | after.keys())
    ]
    state = {
        "schema": PREVIEW_SCHEMA,
        "source": exposure_source_state(skill),
        "project": str(project_dir.resolve()),
        "agent": agent,
        "scope": "project",
        "mode": mode,
        "target": str(target.absolute()),
        "target_state_hash": previous_target_hash,
        "target_directory": {
            "before_mode": target.stat().st_mode & 0o7777 if previous_target_hash is not None else None,
            "after_mode": candidate.stat().st_mode & 0o7777,
        },
        "file_effects": effects,
    }
    return {**state, "confirmation_token": confirmation_token("exposure", **state)}


def removal_file_effects(target: Path, *, target_hash: str) -> dict[str, Any]:
    """Describe the entire removed tree, including its root and deployment sidecar."""
    entries = target_state_manifest(target)
    return {
        "schema": REMOVE_PREVIEW_SCHEMA,
        "target_state_hash": target_hash,
        "target_directory": {
            "before_mode": target.stat().st_mode & 0o7777,
            "after_mode": None,
        },
        "file_effects": [
            {"path": path, "action": "remove", "before": entries[path], "after": None}
            for path in sorted(entries)
        ],
    }


__all__ = ["PREVIEW_SCHEMA", "REMOVE_PREVIEW_SCHEMA", "exposure_preview", "exposure_source_state", "removal_file_effects"]
