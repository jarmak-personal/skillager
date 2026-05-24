from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def find_project_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for path in (current, *current.parents):
        if path != current and path in _unsafe_project_parent_roots():
            break
        if (path / ".git").exists() or (path / "pyproject.toml").exists():
            return path
    return current


def _unsafe_project_parent_roots() -> set[Path]:
    roots = {Path(tempfile.gettempdir()).resolve()}
    cache_home = os.environ.get("XDG_CACHE_HOME")
    roots.add(Path(cache_home).expanduser().resolve() if cache_home else (Path.home() / ".cache").resolve())
    return roots


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


def current_conda_env() -> Path | None:
    env = os.environ.get("CONDA_PREFIX")
    default_env = os.environ.get("CONDA_DEFAULT_ENV")
    if not env or not default_env or default_env == "base":
        return None
    return Path(env).expanduser().resolve()


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


def project_conda_envs(project_root: Path | None = None) -> list[Path]:
    project = project_root or find_project_root()
    if not project:
        return []
    root = (project / ".conda").resolve()
    if not root.exists():
        return []

    envs: list[Path] = []
    if _looks_like_conda_env(root):
        envs.append(root)

    envs_dir = root / "envs"
    try:
        project_is_home = project.resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        project_is_home = False
    # Avoid treating a home-directory ~/.conda/envs tree as project-local inventory.
    if envs_dir.is_dir() and not project_is_home:
        try:
            candidates = sorted(envs_dir.iterdir(), key=lambda path: path.name)
        except OSError:
            candidates = []
        for candidate in candidates:
            try:
                if candidate.is_dir() and _looks_like_conda_env(candidate):
                    envs.append(candidate.resolve())
            except OSError:
                continue
    return envs


def environment_roots(project_root: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    project = project_root or find_project_root()
    project_env = project_venv(project)
    if project_env:
        roots.append(project_env)
    for conda_env in project_conda_envs(project):
        if conda_env not in roots:
            roots.append(conda_env)
    for active in (current_venv(), current_conda_env()):
        if active and active not in roots and _is_relevant_env(active, project):
            roots.append(active)
    return roots


def _looks_like_conda_env(path: Path) -> bool:
    return (
        (path / "conda-meta").is_dir()
        or bool(venv_site_packages(path))
        or (path / ".skillager" / "skills").is_dir()
    )


def _is_relevant_env(env: Path, project_root: Path | None) -> bool:
    if not project_root:
        return False
    try:
        env.relative_to(project_root.resolve())
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
