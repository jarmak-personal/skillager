from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

IGNORED_DIR_NAMES = {
    ".git",
    ".cargo",
    ".conda",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "target",
}


def iter_lint_targets(path: Path, *, recursive: bool = True) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    if (path / "SKILL.md").exists():
        yield path
        return
    if not recursive:
        if (path / "skillager.yaml").exists():
            yield path / "skillager.yaml"
        return

    seen_roots: set[Path] = set()
    for current, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIR_NAMES]
        root = Path(current)
        if "skillager.materialized.yaml" in filenames:
            dirnames.clear()
            continue
        if "SKILL.md" in filenames:
            resolved = root.resolve()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                yield root
            dirnames.clear()
            continue
        if "skillager.yaml" in filenames:
            resolved = root.resolve()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                yield root / "skillager.yaml"
