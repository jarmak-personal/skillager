from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_user_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"refusing to read symlinked Skillager state file: {path}")
    if not path.exists():
        return dict(default)
    _assert_user_owned_regular_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else dict(default)


def write_user_json(path: Path, data: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        _assert_user_owned_regular_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _assert_user_owned_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to read symlinked Skillager state file: {path}")
    if not path.is_file():
        raise ValueError(f"refusing to read non-file Skillager state path: {path}")
    if hasattr(os, "geteuid"):
        stat = path.stat()
        if stat.st_uid != os.geteuid():
            raise ValueError(f"refusing to read Skillager state file owned by another user: {path}")
