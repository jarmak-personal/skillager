from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def find_project_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for path in (current, *current.parents):
        if (path / ".git").exists() or (path / "pyproject.toml").exists():
            return path
    return current


def state_root(start: Path | None = None) -> Path:
    explicit = os.environ.get("SKILLAGER_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    project = find_project_root(start)
    if project:
        return project_state_root(project)
    return state_home() / "skillager"


def project_state_root(project: Path) -> Path:
    project = project.expanduser().resolve()
    digest = hashlib.sha256(str(project).encode("utf-8")).hexdigest()
    return state_home() / "skillager" / "projects" / digest


def state_home() -> Path:
    explicit = os.environ.get("XDG_STATE_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path.home() / ".local" / "state"


def legacy_project_state_root(start: Path | None = None) -> Path | None:
    project = find_project_root(start)
    return project / ".skillager" if project else None


def catalog_state_root() -> Path:
    explicit = os.environ.get("SKILLAGER_CATALOG_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "skillager"


def cache_root() -> Path:
    explicit = os.environ.get("SKILLAGER_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "skillager"


def current_venv() -> Path | None:
    env = os.environ.get("VIRTUAL_ENV")
    if env:
        return Path(env).expanduser().resolve()
    prefix = Path(sys.prefix).resolve()
    if prefix != Path(sys.base_prefix).resolve():
        return prefix
    return None


def project_venv(project_root: Path | None = None) -> Path | None:
    project = project_root or find_project_root()
    if not project:
        return None
    for name in (".venv", "venv"):
        candidate = (project / name).resolve()
        if candidate.exists():
            return candidate
    return None


def venv_site_packages(venv: Path) -> list[Path]:
    candidates = []
    candidates.extend(sorted((venv / "lib").glob("python*/site-packages")))
    candidates.append(venv / "Lib" / "site-packages")
    return [path.resolve() for path in candidates if path.exists()]


def environment_roots(project_root: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    project = project_root or find_project_root()
    active = current_venv()
    project_env = project_venv(project)
    if project_env:
        roots.append(project_env)
    if active and active not in roots and _is_relevant_venv(active, project):
        roots.append(active)
    return roots


def _is_relevant_venv(venv: Path, project_root: Path | None) -> bool:
    if not project_root:
        return False
    try:
        venv.relative_to(project_root.resolve())
    except ValueError:
        return False
    return True


def git_root(start: Path | None = None) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start or Path.cwd()),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return find_project_root(start)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return find_project_root(start)
