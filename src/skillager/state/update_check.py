from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

PYPI_URL = "https://pypi.org/pypi/{package}/json"
UPDATE_SCHEMA = "skillager.update-check.v1"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


def check_for_update(
    cache_dir: Path,
    *,
    current_version: str,
    package: str = "skillager",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    timeout_seconds: float = 1.5,
    now: float | None = None,
) -> dict[str, Any]:
    if os.environ.get("SKILLAGER_NO_UPDATE_CHECK"):
        return _result(current_version=current_version, checked=False, enabled=False)
    now = time.time() if now is None else now
    cached = _load_cache(cache_dir)
    if cached and now - float(cached.get("checked_at", 0)) < ttl_seconds:
        return _result_from_record(cached, current_version=current_version, cached=True)
    try:
        latest = _fetch_latest_version(package, timeout_seconds=timeout_seconds)
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        _save_cache(cache_dir, {"schema": UPDATE_SCHEMA, "checked_at": now, "latest_version": None})
        if cached:
            return _result_from_record(cached, current_version=current_version, cached=True)
        return _result(current_version=current_version, checked=False, cached=False)
    record = {
        "schema": UPDATE_SCHEMA,
        "checked_at": now,
        "latest_version": latest,
    }
    _save_cache(cache_dir, record)
    return _result_from_record(record, current_version=current_version, cached=False)


def is_newer_version(latest: str | None, current: str) -> bool:
    if not latest:
        return False
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        return _simple_version_key(latest) > _simple_version_key(current)


def _result_from_record(record: dict[str, Any], *, current_version: str, cached: bool) -> dict[str, Any]:
    latest = record.get("latest_version")
    return _result(
        current_version=current_version,
        latest_version=latest,
        available=is_newer_version(latest, current_version),
        checked=True,
        cached=cached,
    )


def _result(
    *,
    current_version: str,
    latest_version: str | None = None,
    available: bool = False,
    checked: bool = False,
    cached: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "checked": checked,
        "cached": cached,
        "available": available,
        "current_version": current_version,
        "latest_version": latest_version,
        "command": "uv tool upgrade skillager" if available else None,
    }


def _fetch_latest_version(package: str, *, timeout_seconds: float) -> str:
    with urlopen(PYPI_URL.format(package=package), timeout=timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))
    version = data.get("info", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("PyPI response did not include a version")
    return version


def _load_cache(cache_dir: Path) -> dict[str, Any] | None:
    path = _cache_path(cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema") != UPDATE_SCHEMA:
        return None
    return data


def _save_cache(cache_dir: Path, record: dict[str, Any]) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / "update-check.json"


def _simple_version_key(version: str) -> tuple[int, ...]:
    parts = []
    for value in version.replace("-", ".").split("."):
        digits = ""
        for char in value:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or 0))
    return tuple(parts)
