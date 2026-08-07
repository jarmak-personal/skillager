from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..trust import content_hash_entries


FALLBACK_GIT_NAME = "Skillager"
FALLBACK_GIT_EMAIL = "skillager@localhost"
CONFLICT_CODES = {"AA", "AU", "DD", "DU", "UA", "UD", "UU"}


class LibraryGitError(RuntimeError):
    """Raised when a library Git operation cannot complete safely."""


def git_available() -> bool:
    return shutil.which("git") is not None


def initialize_repository(root: Path) -> bool:
    """Ensure root is its own Git worktree and return whether it was created."""

    _require_git()
    root = root.resolve()
    existing_root = _repository_root(root)
    if existing_root is not None:
        if existing_root != root:
            raise LibraryGitError(
                f"library path is inside another Git working tree ({existing_root}); choose a separate path or use --no-git"
            )
        _require_safe_existing_status(root)
        return False
    result = _run_git(root, "init", "--quiet")
    if result.returncode != 0:
        raise LibraryGitError(_git_error("could not initialize library Git repository", result))
    return True


def commit_paths(root: Path, paths: list[Path], message: str, *, allow_staged_paths: bool = False) -> dict[str, Any]:
    """Commit only the selected library-relative paths."""

    root = root.resolve()
    relative_paths = [_relative_path(root, path) for path in paths]
    status = repository_status(root, mode="system")
    if status["conflicts"]:
        raise LibraryGitError("library Git repository has unresolved conflicts")
    if status["operation"]:
        raise LibraryGitError(f"library Git repository has an in-progress {status['operation']} operation")
    unrelated_staged = [path for path in status["staged"] if not _matches_any_path(path, relative_paths)]
    if status["staged"] and (not allow_staged_paths or unrelated_staged):
        raise LibraryGitError("library Git repository has staged changes; commit or unstage them before continuing")
    added = _run_git(root, "add", "--", *relative_paths)
    if added.returncode != 0:
        raise LibraryGitError(_git_error("could not stage library metadata", added))
    name = _config_value(root, "user.name")
    email = _config_value(root, "user.email")
    command = []
    if not name:
        command.extend(["-c", f"user.name={FALLBACK_GIT_NAME}"])
    if not email:
        command.extend(["-c", f"user.email={FALLBACK_GIT_EMAIL}"])
    command.extend(["commit", "--quiet", "-m", message, "--", *relative_paths])
    committed = _run_git(root, *command)
    if committed.returncode != 0:
        raise LibraryGitError(_git_error("could not commit library metadata", committed))
    return {
        "commit": _head_commit(root),
        "identity": _identity_report(name, email),
    }


def repository_status(root: Path, *, mode: str) -> dict[str, Any]:
    root = root.resolve()
    available = git_available()
    if mode == "disabled":
        return {
            "mode": mode,
            "available": available,
            "repository": (root / ".git").exists(),
            "clean": None,
            "branch": None,
            "head": None,
            "operation": None,
            "conflicts": [],
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "remote": None,
            "commit_identity": None,
        }
    if not available:
        return {
            "mode": mode,
            "available": False,
            "repository": (root / ".git").exists(),
            "clean": None,
            "branch": None,
            "head": None,
            "operation": None,
            "conflicts": [],
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "remote": None,
            "commit_identity": None,
            "error": "git executable is unavailable",
        }
    repository_root = _repository_root(root)
    if repository_root != root:
        return {
            "mode": mode,
            "available": True,
            "repository": False,
            "clean": None,
            "branch": None,
            "head": None,
            "operation": None,
            "conflicts": [],
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "remote": None,
            "commit_identity": None,
            "error": "library path is not its own Git working tree",
        }
    porcelain = _run_git(root, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")
    if porcelain.returncode != 0:
        raise LibraryGitError(_git_error("could not read library Git status", porcelain))
    paths = _parse_porcelain(porcelain.stdout)
    name = _config_value(root, "user.name")
    email = _config_value(root, "user.email")
    remote = _config_value(root, "remote.origin.url")
    return {
        "mode": mode,
        "available": True,
        "repository": True,
        "clean": not any(paths.values()),
        "branch": _branch(root),
        "head": _head_commit(root),
        "operation": _repository_operation(root),
        **paths,
        "remote": remote,
        "commit_identity": _identity_report(name, email),
    }


def _require_safe_existing_status(root: Path) -> None:
    status = repository_status(root, mode="system")
    if status["conflicts"]:
        raise LibraryGitError("library Git repository has unresolved conflicts")
    if status["operation"]:
        raise LibraryGitError(f"library Git repository has an in-progress {status['operation']} operation")
    if status["staged"]:
        raise LibraryGitError("library Git repository has staged changes; commit or unstage them before initializing")


def head_content_hash(root: Path, path: Path) -> str | None:
    """Return the Skillager content hash for one directory as stored at Git HEAD."""

    root = root.resolve()
    relative = _relative_path(root, path)
    listed = _run_git_bytes(root, "ls-tree", "-r", "-z", "HEAD", "--", relative)
    if listed.returncode != 0:
        return None
    tree_entries = [item for item in listed.stdout.split(b"\0") if item]
    if not tree_entries:
        return None
    prefix = f"{relative.rstrip('/')}/"
    entries: list[tuple[str, bytes]] = []
    for tree_entry in tree_entries:
        metadata, separator, raw_name = tree_entry.partition(b"\t")
        if not separator:
            continue
        mode = metadata.split(b" ", maxsplit=1)[0]
        if mode in {b"120000", b"160000"}:
            continue
        name = raw_name.decode("utf-8", errors="surrogateescape")
        if not name.startswith(prefix):
            continue
        shown = _run_git_bytes(root, "show", f"HEAD:{name}")
        if shown.returncode != 0:
            raise LibraryGitError(_git_bytes_error(f"could not read {name} from library HEAD", shown))
        entries.append((name[len(prefix) :], shown.stdout))
    return content_hash_entries(entries) if entries else None


def path_changes(status: dict[str, Any], root: Path, path: Path) -> dict[str, list[str]]:
    relative = _relative_path(root.resolve(), path).rstrip("/")
    prefix = f"{relative}/"
    return {
        key: [item for item in status.get(key, []) if item == relative or item.startswith(prefix)]
        for key in ("conflicts", "staged", "unstaged", "untracked")
    }


def _parse_porcelain(output: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"conflicts": [], "staged": [], "unstaged": [], "untracked": []}
    for line in output.splitlines():
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            result["untracked"].append(path)
            continue
        if code in CONFLICT_CODES or "U" in code:
            result["conflicts"].append(path)
            continue
        if code[0] != " ":
            result["staged"].append(path)
        if code[1] != " ":
            result["unstaged"].append(path)
    return result


def _repository_root(root: Path) -> Path | None:
    if not git_available():
        return None
    result = _run_git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    try:
        return Path(result.stdout.strip()).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _repository_operation(root: Path) -> str | None:
    result = _run_git(root, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return None
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    markers = (
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    )
    for marker, operation in markers:
        if (git_dir / marker).exists():
            return operation
    return None


def _branch(root: Path) -> str | None:
    result = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return result.stdout.strip() or None if result.returncode == 0 else None


def _head_commit(root: Path) -> str | None:
    result = _run_git(root, "rev-parse", "--verify", "HEAD")
    return result.stdout.strip() or None if result.returncode == 0 else None


def _config_value(root: Path, key: str) -> str | None:
    result = _run_git(root, "config", "--get", key)
    return result.stdout.strip() or None if result.returncode == 0 else None


def _identity_report(name: str | None, email: str | None) -> dict[str, Any]:
    return {
        "source": "configured" if name and email else "skillager-fallback",
        "name": name or FALLBACK_GIT_NAME,
        "email": email or FALLBACK_GIT_EMAIL,
    }


def _relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise LibraryGitError(f"Git path escapes the library: {path}") from exc


def _matches_any_path(candidate: str, allowed: list[str]) -> bool:
    return any(candidate == path or candidate.startswith(f"{path.rstrip('/')}/") for path in allowed)


def _require_git() -> None:
    if not git_available():
        raise LibraryGitError("git executable is unavailable; install Git or rerun with --no-git")


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _run_git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_error(prefix: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return f"{prefix}: {detail}"


def _git_bytes_error(prefix: str, result: subprocess.CompletedProcess[bytes]) -> str:
    detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip() or f"exit {result.returncode}"
    return f"{prefix}: {detail}"


__all__ = [
    "FALLBACK_GIT_EMAIL",
    "FALLBACK_GIT_NAME",
    "LibraryGitError",
    "commit_paths",
    "git_available",
    "head_content_hash",
    "initialize_repository",
    "path_changes",
    "repository_status",
]
