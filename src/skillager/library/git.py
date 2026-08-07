from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


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


def commit_paths(root: Path, paths: list[Path], message: str) -> dict[str, Any]:
    """Commit only the selected library-relative paths."""

    root = root.resolve()
    status = repository_status(root, mode="system")
    if status["conflicts"]:
        raise LibraryGitError("library Git repository has unresolved conflicts")
    if status["staged"]:
        raise LibraryGitError("library Git repository has staged changes; commit or unstage them before continuing")
    relative_paths = [_relative_path(root, path) for path in paths]
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
        **paths,
        "remote": remote,
        "commit_identity": _identity_report(name, email),
    }


def _require_safe_existing_status(root: Path) -> None:
    status = repository_status(root, mode="system")
    if status["conflicts"]:
        raise LibraryGitError("library Git repository has unresolved conflicts")
    if status["staged"]:
        raise LibraryGitError("library Git repository has staged changes; commit or unstage them before initializing")


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


def _git_error(prefix: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return f"{prefix}: {detail}"


__all__ = [
    "FALLBACK_GIT_EMAIL",
    "FALLBACK_GIT_NAME",
    "LibraryGitError",
    "commit_paths",
    "git_available",
    "initialize_repository",
    "repository_status",
]
