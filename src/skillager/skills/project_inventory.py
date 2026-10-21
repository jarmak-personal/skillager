"""Metadata-only, exact-project occurrence inventory for interactive consumers."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from ..exposure.management import _exposure_records
from ..exposure.target_state import target_state_hash
from .index import build_index
from .schema import skill_frontmatter_metadata
from ..trust import APPROVED_TRUST_STATES, trust_snapshot



PROJECT_INVENTORY_LIMIT = 10_000
PROJECT_ERROR_LIMIT = 20


def observe_project(state: Path, catalog: Path, project: Path) -> dict[str, Any]:
    """Read every exact project occurrence without persistence or collection scans."""
    with trust_snapshot([state, catalog]):
        observed = build_index(state, approval_root=catalog, project_native_root=project,
                               include_packages=False, persist=False, project_inventory=True)
    errors = [_discovery_error(item) for item in observed["errors"]]
    exposure_errors: list[dict[str, str]] = []
    observed_targets: set[Path] = set()
    exposures = _exposure_records(project, agents=["codex", "claude"], scope="project", inventory_errors=exposure_errors,
                                  observed_targets=observed_targets)
    errors.extend({"code": "unrecognized-managed-copy", "path": path[:4096]}
                  for path in observed["managed_paths"] if Path(path) not in observed_targets)
    errors.extend({"code": item["code"], "path": item["path"][:4096]} for item in exposure_errors)
    # Native traversal and exposure enumeration may observe the same failed
    # path. Report that occurrence once, keeping the first precise cause.
    failures: dict[str, dict[str, Any]] = {}
    for error in errors:
        failures.setdefault(str(error["path"] or error["code"]), error)
    errors = list(failures.values())
    if len(observed["skills"]) + len(exposures) > PROJECT_INVENTORY_LIMIT or any(
        error["code"] == "inventory-limit" for error in errors
    ):
        raise InventoryUnavailable("inventory-limit")
    rows: list[dict[str, Any]] = []
    managed_state = []
    for skill in observed["skills"]:
        path = Path(skill["entrypoint"]) if skill.get("entrypoint") else Path(skill["root"]) / "SKILL.md"
        try:
            row = _row(path, project, skill["id"])
        except (OSError, UnicodeError, ValueError, RuntimeError):
            errors.append({"code": "unreadable-skill", "path": str(path)[:4096]})
            continue
        trust = skill["trust"]
        row.update(status="accepted" if trust in APPROVED_TRUST_STATES else
                   trust if trust in {"blocked", "lint_blocked"} else "pending", trust=trust,
                   kind="native", native={"scope": "project", "managed": False,
                                          "agent": (skill.get("native") or {}).get("agent")}, exposure=None)
        rows.append(row)
    for exposure in exposures:
        target = Path(exposure["target"])
        try:
            target = target.resolve(strict=True)
            exposure = {**exposure, "target": str(target)}
            row = _row(target / "SKILL.md", project, exposure["skill_id"])
            managed_state.append({"record": exposure, "hash": target_state_hash(target)})
        except (OSError, UnicodeError, ValueError, RuntimeError):
            errors.append({"code": "unreadable-managed-copy", "path": str(target)[:4096]})
            continue
        row.update(status="present", trust=None,
                   kind="full" if exposure["mode"] == "native" else exposure["mode"],
                   native={"scope": "project", "managed": True, "agent": exposure["agent"]},
                   exposure=exposure)
        rows.append(row)
    # Identity is the current canonical instruction occurrence, not a name,
    # ranking family, library lineage claim, or discovery provenance winner.
    rows = sorted({row["occurrence_id"]: row for row in rows}.values(), key=lambda row: row["skill_file"])
    observation = {"status": "partial" if errors else "complete", "errors": errors[:PROJECT_ERROR_LIMIT],
                   "error_count": len(errors)}
    return {"rows": rows, "observation": observation, "skills": observed["skills"],
            "snapshot": {"originals": observed, "managed": managed_state, "rows": rows, "errors": errors}}


class InventoryUnavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _row(path: Path, project: Path, skill_id: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(project):
        raise ValueError("instruction file is outside selected project")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("instruction file is not regular")
        # Read only bounded declared metadata. Never infer a description from the
        # skill body, including when frontmatter is missing or quarantined.
        value = stream.read(65_537)
    if len(value) > 65_536:
        # Large bodies are fine: only a complete header in the bounded prefix is
        # eligible as metadata. Incomplete/missing headers yield empty metadata.
        value = value[:65_536]
    declared = skill_frontmatter_metadata(value.decode("utf-8", errors="replace"))
    return {
        "id": skill_id, "occurrence_id": "project:" + hashlib.sha256(os.fsencode(resolved)).hexdigest(),
        "name": _text(declared.get("name") or path.parent.name, 160),
        "description": _text(declared.get("description") or "", 1000), "skill_file": str(resolved),
    }


def _text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _discovery_error(item: dict[str, Any]) -> dict[str, Any]:
    # Exceptions and linter diagnostics can embed source excerpts. Only the
    # discovery owner's fixed codes and bounded paths cross this metadata API.
    codes = {"outside-project", "inventory-limit", "invalid-root", "invalid-instruction-file", "unreadable-path"}
    return {"code": item["code"] if item.get("code") in codes else "invalid-skill",
            "path": str(item["path"])[:4096] if item.get("path") else None}
