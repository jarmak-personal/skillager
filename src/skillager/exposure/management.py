"""Managed exposure metadata shared by listing, removal and lifecycle selection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..simple_yaml import load_mapping
from .impl import _project_agent_bases
from .identity import library_id, router_member_sources


def _exposure_records(project_dir: Path, *, agents: list[str], scope: str, errors: list[str] | None = None,
                      observed_targets: set[Path] | None = None,
                      inventory_errors: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    observing = errors is not None or inventory_errors is not None

    def failed(code: str, path: Path) -> None:
        if errors is not None:
            errors.append(code)
        if inventory_errors is not None:
            inventory_errors.append({"code": code, "path": str(path)})

    for agent, roots in _exposure_roots(project_dir, agents=agents, scope=scope).items():
        for root_path in roots:
            if not root_path.is_dir():
                if observing and (root_path.exists() or root_path.is_symlink()):
                    failed("unreadable-target-root", root_path)
                continue
            if observing:
                try:
                    # Path.glob can suppress directory permission errors. Check
                    # actual enumeration before claiming a complete observation.
                    next(root_path.iterdir(), None)
                except OSError:
                    failed("unreadable-target-root", root_path)
                    continue
            for sidecar in sorted(root_path.glob("*/skillager.materialized.yaml")):
                try:
                    resolved = sidecar.resolve()
                    if observed_targets is not None:
                        observed_targets.add(sidecar.parent.resolve())
                except OSError:
                    failed("unreadable-target", sidecar.parent)
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                if observing and (sidecar.is_symlink() or sidecar.parent.is_symlink()
                        or (scope == "project" and not resolved.is_relative_to(project_dir.resolve()))
                        or (inventory_errors is not None and (sidecar.parent / "SKILL.md").is_symlink())
                        or not (sidecar.parent / "SKILL.md").is_file()):
                    failed("unreadable-target", sidecar.parent)
                    continue
                try:
                    data = load_mapping(sidecar)
                except Exception:
                    failed("unreadable-target", sidecar.parent)
                    continue
                try:
                    record = _exposure_record(sidecar, data, fallback_agent=agent, fallback_scope=scope)
                except (TypeError, ValueError):
                    if inventory_errors is None:
                        raise
                    failed("unknown-target", sidecar.parent)
                    continue
                working = data.get("source_type") == "skillager-working"
                if inventory_errors is not None:
                    working = working and data.get("schema") == "skillager.materialized.v1"
                    if record is not None and (record["agent"] != agent or record["scope"] != scope):
                        failed("unknown-target", sidecar.parent)
                        continue
                if record is not None:
                    records.append(record)
                elif observing and not working:
                    failed("unknown-target", sidecar.parent)
    return sorted(records, key=lambda item: (item["agent"], item["scope"], item["exposure_id"]))


def _exposure_roots(project_dir: Path, *, agents: list[str], scope: str) -> dict[str, list[Path]]:
    if scope == "project":
        project_roots = {agent: _project_agent_bases(project_dir, agent) for agent in agents}
        return {agent: project_roots.get(agent, []) for agent in agents}
    if scope == "global":
        roots: dict[str, list[Path]] = {}
        for agent in agents:
            if agent == "codex":
                roots[agent] = [Path.home() / ".agents" / "skills", Path.home() / ".codex" / "skills"]
            elif agent == "claude":
                roots[agent] = [Path.home() / ".claude" / "skills"]
            else:
                roots[agent] = [Path.home() / ".skillager" / "agents" / agent / "skills"]
        return roots
    raise ValueError("scope must be project or global")


def _exposure_record(sidecar: Path, data: dict[str, Any], *, fallback_agent: str, fallback_scope: str) -> dict[str, Any] | None:
    if data.get("schema") not in {"skillager.materialized.v1", "skillager.router.v1"}:
        return None
    source_type = data.get("source_type")
    if source_type == "skillager-working":
        return None
    if source_type == "skillager-router":
        mode = "router"
        skill_id = data.get("source_id") or data.get("id")
    elif source_type == "skillager-stub":
        mode = "stub"
        skill_id = data.get("source_id") or data.get("id")
    else:
        mode = "native"
        skill_id = data.get("source_id") or data.get("id")
    if not skill_id:
        return None
    target = sidecar.parent
    record: dict[str, Any] = {
        "schema": "skillager.exposure.v1",
        "exposure_id": target.name,
        "skill_id": str(skill_id),
        "mode": mode,
        "agent": str(data.get("agent") or fallback_agent),
        "scope": str(data.get("scope") or fallback_scope),
        "target": str(target),
        "status": "exposed",
        "reason": None,
        "restart_required": True,
    }
    if mode == "router":
        record["member_sources"] = router_member_sources(data)
    else:
        record["source_library_id"] = library_id(data.get("source_library_id"))
    if data.get("tag"):
        record["tag"] = data.get("tag")
    for key in ("router_kind", "selection_kind", "router_slug"):
        if data.get(key):
            record[key] = data.get(key)
    if data.get("skill_ids"):
        record["skill_ids"] = list(data.get("skill_ids") or [])
    return record
