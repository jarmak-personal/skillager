from __future__ import annotations

from pathlib import Path
from typing import Any

from .schema import infer_skill, manifest_for_skill
from .scan import scan_path
from .simple_yaml import dumps


def onboard_path(path: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    roots = [path] if (path / "SKILL.md").exists() else sorted(item.parent for item in path.rglob("SKILL.md"))
    results: list[dict[str, Any]] = []
    for root in roots:
        skill = infer_skill(root, {"type": "onboarded"})
        manifest = manifest_for_skill(skill)
        scan = scan_path(skill.entrypoint)
        target = root / "skillager.yaml"
        if not dry_run and not target.exists():
            target.write_text(dumps(manifest), encoding="utf-8")
        results.append({"skill_id": skill.id, "path": str(root), "manifest": str(target), "written": not dry_run and target.exists(), "scan": scan})
    return results
