from __future__ import annotations

import fnmatch
import hashlib
import configparser
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..lint import blocking_findings, valid_lint_override
from ..schema import TRUST_STATES
from ..signing import is_evidence_file
from ..statefiles import read_user_json, write_user_json

DEFAULT_HASH_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "skillager.materialized.yaml",
}
APPROVED_TRUST_STATES = {"reviewed", "trusted", "pinned"}


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
    if is_evidence_file(relative):
        return True
    for part in relative.parts:
        if part in DEFAULT_HASH_EXCLUDES:
            return True
        if part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return any(fnmatch.fnmatch(relative.as_posix(), pattern) for pattern in ("*.tmp", "*.swp", "*~"))


def trust_path(state_root: Path) -> Path:
    return state_root / "trust.json"


def load_trust(state_root: Path) -> dict[str, Any]:
    return read_user_json(trust_path(state_root), {"skills": {}})


def save_trust(state_root: Path, data: dict[str, Any]) -> None:
    write_user_json(trust_path(state_root), data)


def trust_state(
    state_root: Path,
    skill_id: str,
    current_hash: str,
    *,
    lint: dict[str, Any] | None = None,
    approval_key: str | None = None,
    approval_root: Path | None = None,
) -> str:
    return trust_info(
        state_root,
        skill_id,
        current_hash,
        lint=lint,
        approval_key=approval_key,
        approval_root=approval_root,
    ).get("state", "discovered")


def trust_info(
    state_root: Path,
    skill_id: str,
    current_hash: str,
    *,
    lint: dict[str, Any] | None = None,
    approval_key: str | None = None,
    approval_root: Path | None = None,
) -> dict[str, Any]:
    lint_blocked = bool(blocking_findings(lint))
    record = load_trust(state_root).get("skills", {}).get(skill_id)
    info = _record_trust_info(record, current_hash, lint=lint, scope="project")
    if info:
        return info
    if approval_key and approval_root:
        approval = load_trust(approval_root).get("global_approvals", {}).get(approval_key)
        info = _record_trust_info(approval, current_hash, lint=lint, scope="global")
        if info:
            if info.get("state") in APPROVED_TRUST_STATES:
                info["reason"] = "global-approval"
            return info
    return {"state": "lint_blocked" if lint_blocked else "discovered"}


def _record_trust_info(
    record: dict[str, Any] | None,
    current_hash: str,
    *,
    lint: dict[str, Any] | None,
    scope: str,
) -> dict[str, Any] | None:
    if not record:
        return None
    state = record.get("state", "discovered")
    if record.get("content_hash") and record.get("content_hash") != current_hash:
        return None
    lint_blocked = bool(blocking_findings(lint))
    if lint_blocked and not valid_lint_override(record, lint):
        return None
    if state in TRUST_STATES - {"discovered", "lint_blocked"}:
        info = {
            "state": state,
            "scope": scope,
            "source": record.get("source"),
        }
        if record.get("reason"):
            info["reason"] = record["reason"]
        return info
    return None


def set_trust(
    state_root: Path,
    skill_id: str,
    state: str,
    current_hash: str,
    source: dict[str, Any],
    *,
    lint: dict[str, Any] | None = None,
    lint_override: dict[str, Any] | None = None,
    reason: str | None = None,
    approval_key: str | None = None,
    approval_root: Path | None = None,
    global_scope: bool = False,
) -> dict[str, Any]:
    if state not in TRUST_STATES - {"discovered", "lint_blocked"}:
        raise ValueError(f"invalid trust state: {state}")
    if state in {"reviewed", "trusted", "pinned"} and blocking_findings(lint) and not lint_override:
        raise ValueError("lint-blocked skills require --override-lint --reason")
    use_global = bool(global_scope and approval_key and approval_root)
    target_root: Path = approval_root if use_global and approval_root is not None else state_root
    data = load_trust(target_root)
    record: dict[str, Any] = {
        "state": state,
        "content_hash": current_hash,
        "source": source,
        "scope": "global" if use_global else "project",
    }
    if approval_key:
        record["approval_key"] = approval_key
    if lint_override:
        record["lint_override"] = lint_override
    if reason:
        record["reason"] = reason
    if use_global:
        record["skill_id"] = skill_id
        data.setdefault("global_approvals", {})[approval_key] = record
        save_trust(target_root, data)
        return data["global_approvals"][approval_key]
    data.setdefault("skills", {})[skill_id] = record
    save_trust(target_root, data)
    return data["skills"][skill_id]


def approval_key_for(
    skill_id: str,
    root: str | Path | None,
    source: dict[str, Any] | None,
    *,
    entrypoint: str | Path | None = None,
) -> str | None:
    source = source or {}
    root_path = _existing_path(root)
    entrypoint_path = _existing_path(entrypoint)
    package = str(source.get("package") or "").strip()
    if package:
        relative = _source_relative_path(root_path, source) or _marker_relative_path(root_path) or _skill_tail(skill_id)
        return f"package:{_canonical_package(package, source)}#{relative}"
    identity_path = root_path or entrypoint_path
    if identity_path:
        git_root = _find_git_root(identity_path)
        if git_root:
            relative = _relative_to(root_path or identity_path, git_root)
            remote = _git_origin_url(git_root)
            if remote:
                return f"git:{remote}#{relative}"
            return f"git-local:{git_root.as_posix()}#{relative}"
    source_path = _existing_path(source.get("path"))
    if source_path and root_path:
        relative = _relative_to(root_path, source_path)
        return f"path:{source_path.as_posix()}#{relative}"
    return None


def _existing_path(value: str | Path | Any | None) -> Path | None:
    if value is None:
        return None
    try:
        return Path(str(value)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _find_git_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate.resolve()
    return None


def _git_origin_url(git_root: Path) -> str | None:
    config_path = _git_config_path(git_root)
    if not config_path or not config_path.exists():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return None
    section = 'remote "origin"'
    if not parser.has_section(section):
        return None
    try:
        raw = parser.get(section, "url")
    except (configparser.Error, OSError):
        return None
    return _normalize_git_url(raw)


def _git_config_path(git_root: Path) -> Path | None:
    marker = git_root / ".git"
    if marker.is_dir():
        return marker / "config"
    if marker.is_file():
        try:
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if text.startswith("gitdir:"):
            git_dir = Path(text.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = (git_root / git_dir).resolve()
            return git_dir / "config"
    return None


def _normalize_git_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    scp_like = re.fullmatch(r"[^@\s]+@([^:\s]+):(.+)", value)
    if scp_like:
        host, path = scp_like.groups()
        return _normalize_git_host_path(host, path)
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        return _normalize_git_host_path(parsed.hostname, parsed.path)
    return None


def _normalize_git_host_path(host: str, path: str) -> str:
    host = host.lower()
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if host in {"github.com", "www.github.com"}:
        normalized_path = normalized_path.lower()
    return f"{host}/{normalized_path}"


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _source_relative_path(root: Path | None, source: dict[str, Any]) -> str | None:
    if not root:
        return None
    for key in ("package_root", "environment", "path"):
        raw = source.get(key)
        if not raw:
            continue
        base = _existing_path(raw)
        if not base:
            continue
        try:
            return root.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


def _marker_relative_path(root: Path | None) -> str | None:
    if not root:
        return None
    parts = root.parts
    markers = (
        (".agents", "skills"),
        (".agents", "codex", "skills"),
        (".agents", "claude", "skills"),
        (".codex", "skills"),
        (".claude", "skills"),
        (".skillager", "skills"),
        ("skills",),
    )
    for marker in markers:
        width = len(marker)
        for index in range(0, len(parts) - width + 1):
            if tuple(parts[index : index + width]) == marker:
                return Path(*parts[index:]).as_posix()
    return root.name


def _skill_tail(skill_id: str) -> str:
    return skill_id.rsplit("/", 1)[-1]


def _canonical_package(value: str, source: dict[str, Any] | None = None) -> str:
    if source and source.get("type") == "npm-package":
        return value.strip().lower()
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def make_lint_override(reason: str, lint: dict[str, Any]) -> dict[str, Any]:
    reason = reason.strip()
    if not reason:
        raise ValueError("--reason is required with --override-lint")
    return {
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
        "findings": blocking_findings(lint),
    }


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


def clear_global_approvals(approval_root: Path, approval_keys: list[str]) -> int:
    data = load_trust(approval_root)
    approvals = data.setdefault("global_approvals", {})
    removed = 0
    for approval_key in approval_keys:
        if approval_key in approvals:
            del approvals[approval_key]
            removed += 1
    if removed:
        save_trust(approval_root, data)
    return removed
