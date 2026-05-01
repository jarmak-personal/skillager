from __future__ import annotations

import json
import fnmatch
import hashlib
from pathlib import Path
from typing import Any

from .schema import TRUST_STATES

DEFAULT_HASH_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "skillager.materialized.yaml",
}


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    path = path.resolve()
    if path.is_dir():
        for file_path in _hashable_files(path):
            relative = file_path.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hashable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _excluded(relative):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _excluded(relative: Path) -> bool:
    for part in relative.parts:
        if part in DEFAULT_HASH_EXCLUDES:
            return True
        if part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return any(fnmatch.fnmatch(relative.as_posix(), pattern) for pattern in ("*.tmp", "*.swp", "*~"))


def trust_path(state_root: Path) -> Path:
    return state_root / "trust.json"


def load_trust(state_root: Path) -> dict[str, Any]:
    path = trust_path(state_root)
    if not path.exists():
        return {"skills": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_trust(state_root: Path, data: dict[str, Any]) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    trust_path(state_root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def trust_state(state_root: Path, skill_id: str, current_hash: str) -> str:
    record = load_trust(state_root).get("skills", {}).get(skill_id)
    if not record:
        return "discovered"
    state = record.get("state", "discovered")
    if state == "blocked":
        return "blocked"
    if record.get("content_hash") and record.get("content_hash") != current_hash:
        return "discovered"
    return state if state in TRUST_STATES else "discovered"


def set_trust(state_root: Path, skill_id: str, state: str, current_hash: str, source: dict[str, Any]) -> dict[str, Any]:
    if state not in TRUST_STATES - {"discovered"}:
        raise ValueError(f"invalid trust state: {state}")
    data = load_trust(state_root)
    data.setdefault("skills", {})[skill_id] = {
        "state": state,
        "content_hash": current_hash,
        "source": source,
    }
    save_trust(state_root, data)
    return data["skills"][skill_id]


def clear_trust(state_root: Path, skill_ids: list[str]) -> int:
    data = load_trust(state_root)
    skills = data.setdefault("skills", {})
    removed = 0
    for skill_id in skill_ids:
        if skill_id in skills:
            del skills[skill_id]
            removed += 1
    if removed:
        save_trust(state_root, data)
    return removed
