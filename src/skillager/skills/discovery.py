from __future__ import annotations

import json
import hashlib
import os
import sys
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, TypeAlias
from urllib.parse import unquote, urlparse

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

from ..paths import environment_roots, find_project_root, venv_site_packages
from .schema import (
    CARGO_PACKAGE_RE,
    NPM_PACKAGE_RE,
    QuarantinedSkill,
    SchemaError,
    Skill,
    canonical_cargo_package_name,
    canonical_npm_package_name,
    load_skill_from_dir,
    quarantine_skill_from_dir,
)

IGNORED_CHILD_REPO_DIR_NAMES = {
    ".cache",
    ".conda",
    ".cargo",
    ".git",
    ".gradle",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "target",
    "venv",
}


def project_skill_roots(root: Path, source: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    return [
        (root / ".skills", source),
        (root / "skills", source),
        (root / ".agents" / "skills", source),
        (root / ".agents" / "codex" / "skills", {**source, "agent": "codex"}),
        (root / ".agents" / "claude" / "skills", {**source, "agent": "claude"}),
        (root / ".codex" / "skills", {**source, "agent": "codex"}),
        (root / ".claude" / "skills", {**source, "agent": "claude"}),
    ]


def default_source_roots(project_root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    roots: list[tuple[Path, dict[str, Any]]] = []
    project_root = project_root or find_project_root() or Path.cwd()
    if project_root:
        roots.extend(project_skill_roots(project_root, {"type": "project"}))
        roots.extend(project_child_skill_repo_roots(project_root))
    roots.extend(
        [
            (Path.home() / ".codex" / "skills", {"type": "global", "agent": "codex"}),
            (Path.home() / ".claude" / "skills", {"type": "global", "agent": "claude"}),
            (Path.home() / ".skillager" / "skills", {"type": "global", "agent": "skillager"}),
        ]
    )
    for venv in environment_roots(project_root):
        roots.append((venv / ".skillager" / "skills", {"type": "environment", "environment": str(venv)}))
    return roots


def project_child_skill_repo_roots(project_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    try:
        if not project_root.exists():
            return []
        children = sorted(project_root.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    roots: list[tuple[Path, dict[str, Any]]] = []
    for child in children:
        try:
            if not child.is_dir() or child.name in IGNORED_CHILD_REPO_DIR_NAMES:
                continue
            child_path = str(child.resolve())
        except OSError:
            continue
        source = {
            "type": "collection",
            "collection": _slug(child.name) or "local",
            "path": child_path,
            "local": "true",
        }
        for skill_root, root_source in project_skill_roots(child, source):
            try:
                if skill_root.is_dir():
                    roots.append((skill_root, root_source))
            except OSError:
                continue
    return roots


IndexableSkill: TypeAlias = Skill | QuarantinedSkill


def discover(
    paths: Iterable[Path] | None = None,
    *,
    include_packages: bool = True,
    extra_paths: Iterable[Path] | None = None,
) -> tuple[list[IndexableSkill], list[dict[str, str]]]:
    skills: list[IndexableSkill] = []
    errors: list[dict[str, str]] = []
    roots: list[tuple[Path, dict[str, Any]]] = []
    if extra_paths:
        roots.extend((path, {"type": "path"}) for path in extra_paths)
    if paths is None:
        roots.extend(default_source_roots())
    else:
        roots.extend((path, {"type": "path"}) for path in paths)
    for root, source in roots:
        try:
            skill_dirs = _skill_dirs(root)
        except OSError as exc:
            errors.append({"path": str(root), "error": str(exc)})
            continue
        for skill_dir in skill_dirs:
            try:
                skills.append(load_skill_from_dir(skill_dir, source))
            except (SchemaError, OSError, ValueError) as exc:
                quarantined = quarantine_skill_from_dir(skill_dir, source, exc)
                if quarantined:
                    skills.append(quarantined)
                errors.append({"path": str(skill_dir), "error": str(exc)})
    if include_packages:
        package_skills, package_errors = discover_package_skills()
        skills = [*package_skills, *skills]
        errors.extend(package_errors)
    return _dedupe(skills), errors


def discover_package_skills() -> tuple[list[IndexableSkill], list[dict[str, str]]]:
    skills: list[IndexableSkill] = []
    errors: list[dict[str, str]] = []
    seen_dirs: set[Path] = set()
    project_root = find_project_root()
    for dist, environment in _package_distributions(project_root):
        name = dist.metadata["Name"] if "Name" in dist.metadata else "unknown"
        version = dist.version
        files = dist.files or []
        candidates = sorted({dist.locate_file(file).parent for file in files if _is_packaged_skill_file(file.parts, file.name)})
        for skill_dir in candidates:
            skill_dir = Path(skill_dir).resolve()
            seen_dirs.add(skill_dir)
            try:
                skills.append(load_skill_from_dir(skill_dir, {"type": "python-package", "package": name, "version": version, "environment": environment}))
            except (SchemaError, OSError, ValueError) as exc:
                quarantined = quarantine_skill_from_dir(skill_dir, {"type": "python-package", "package": name, "version": version, "environment": environment}, exc)
                if quarantined:
                    skills.append(quarantined)
                errors.append({"path": str(skill_dir), "error": str(exc)})
        editable_root = _editable_source_root(dist)
        if editable_root:
            source = {
                "type": "python-package",
                "package": name,
                "version": version,
                "editable": "true",
                "path": str(editable_root),
            }
            if environment is not None:
                source["environment"] = environment
            for root, root_source in project_skill_roots(editable_root, source):
                try:
                    skill_dirs = _skill_dirs(root)
                except OSError as exc:
                    errors.append({"path": str(root), "error": str(exc)})
                    continue
                for skill_dir in skill_dirs:
                    skill_dir = skill_dir.resolve()
                    if skill_dir in seen_dirs:
                        continue
                    seen_dirs.add(skill_dir)
                    try:
                        skills.append(load_skill_from_dir(skill_dir, root_source))
                    except (SchemaError, OSError, ValueError) as exc:
                        quarantined = quarantine_skill_from_dir(skill_dir, root_source, exc)
                        if quarantined:
                            skills.append(quarantined)
                        errors.append({"path": str(skill_dir), "error": str(exc)})
    for site_packages in _site_package_paths(project_root):
        try:
            skill_dirs = _package_skill_dirs(site_packages)
        except OSError as exc:
            errors.append({"path": str(site_packages), "error": str(exc)})
            continue
        for skill_dir in skill_dirs:
            if skill_dir in seen_dirs:
                continue
            package = _package_name_from_skill_dir(site_packages, skill_dir)
            try:
                skills.append(load_skill_from_dir(skill_dir, {"type": "python-package", "package": package, "version": None, "environment": str(site_packages)}))
            except (SchemaError, OSError, ValueError) as exc:
                package_source: dict[str, Any] = {"type": "python-package", "package": package, "version": None, "environment": str(site_packages)}
                quarantined = quarantine_skill_from_dir(skill_dir, package_source, exc)
                if quarantined:
                    skills.append(quarantined)
                errors.append({"path": str(skill_dir), "error": str(exc)})
    npm_skills, npm_errors = discover_npm_package_skills(project_root)
    skills.extend(npm_skills)
    errors.extend(npm_errors)
    cargo_skills, cargo_errors = discover_cargo_package_skills(project_root)
    skills.extend(cargo_skills)
    errors.extend(cargo_errors)
    return skills, errors


def discover_npm_package_skills(project_root: Path | None = None) -> tuple[list[IndexableSkill], list[dict[str, str]]]:
    skills: list[IndexableSkill] = []
    errors: list[dict[str, str]] = []
    seen_dirs: set[Path] = set()
    project_root = project_root or find_project_root()
    if not project_root:
        return skills, errors

    for node_modules in _node_modules_paths(project_root):
        for package_root, package_name, version in _npm_package_roots(node_modules):
            source = {
                "type": "npm-package",
                "package": package_name,
                "version": version,
                "environment": str(node_modules),
                "package_root": str(package_root),
            }
            for root, root_source in project_skill_roots(package_root, source):
                try:
                    skill_dirs = _skill_dirs(root)
                except OSError as exc:
                    errors.append({"path": str(root), "error": str(exc)})
                    continue
                for skill_dir in skill_dirs:
                    skill_dir = skill_dir.resolve()
                    if skill_dir in seen_dirs:
                        continue
                    seen_dirs.add(skill_dir)
                    try:
                        skills.append(load_skill_from_dir(skill_dir, root_source))
                    except (SchemaError, OSError, ValueError) as exc:
                        quarantined = quarantine_skill_from_dir(skill_dir, root_source, exc)
                        if quarantined:
                            skills.append(quarantined)
                        errors.append({"path": str(skill_dir), "error": str(exc)})
    return skills, errors


def _node_modules_paths(project_root: Path) -> list[Path]:
    path = project_root / "node_modules"
    return [path.resolve()] if path.exists() else []


def _npm_package_roots(node_modules: Path) -> list[tuple[Path, str, str | None]]:
    packages: list[tuple[Path, str, str | None]] = []
    seen_roots: set[Path] = set()
    try:
        children = sorted(node_modules.iterdir(), key=lambda path: path.name)
    except OSError:
        return packages
    for child in children:
        if child.name.startswith("@"):
            for scoped_child in _npm_scoped_package_dirs(child):
                _append_npm_package(packages, seen_roots, node_modules, scoped_child)
        elif not child.name.startswith("."):
            _append_npm_package(packages, seen_roots, node_modules, child)
    return packages


def _npm_scoped_package_dirs(scope_dir: Path) -> list[Path]:
    try:
        if not scope_dir.is_dir():
            return []
        return sorted((child for child in scope_dir.iterdir() if not child.name.startswith(".")), key=lambda path: path.name)
    except OSError:
        return []


def _append_npm_package(
    packages: list[tuple[Path, str, str | None]],
    seen_roots: set[Path],
    node_modules: Path,
    package_dir: Path,
) -> None:
    try:
        if not package_dir.is_dir():
            return
        package_root = package_dir.resolve()
    except OSError:
        return
    if package_root in seen_roots:
        return
    if not _has_package_skill_root(package_dir):
        return
    if not (package_dir / "package.json").is_file():
        return
    package_name, version = _npm_package_metadata(package_dir, node_modules)
    seen_roots.add(package_root)
    packages.append((package_root, package_name, version))


def _has_package_skill_root(package_dir: Path) -> bool:
    for skill_root, _ in project_skill_roots(package_dir, {}):
        try:
            if skill_root.is_dir():
                return True
        except OSError:
            continue
    return False


def _npm_package_metadata(package_dir: Path, node_modules: Path) -> tuple[str, str | None]:
    data: dict[str, Any] = {}
    try:
        raw = json.loads((package_dir / "package.json").read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        raw = {}
    if isinstance(raw, dict):
        data = raw
    fallback_name = _npm_package_name_from_path(node_modules, package_dir)
    raw_name = data.get("name")
    package_name = canonical_npm_package_name(raw_name) if isinstance(raw_name, str) else fallback_name
    if not NPM_PACKAGE_RE.fullmatch(package_name):
        package_name = fallback_name
    raw_version = data.get("version")
    version = raw_version.strip() if isinstance(raw_version, str) and raw_version.strip() else None
    return package_name, version


def _npm_package_name_from_path(node_modules: Path, package_dir: Path) -> str:
    try:
        relative = package_dir.relative_to(node_modules)
    except ValueError:
        return canonical_npm_package_name(package_dir.name) or "unknown"
    parts = relative.parts
    if len(parts) >= 2 and parts[0].startswith("@"):
        name = f"{parts[0]}/{parts[1]}"
    elif parts:
        name = parts[0]
    else:
        name = package_dir.name
    canonical = canonical_npm_package_name(name)
    return canonical if NPM_PACKAGE_RE.fullmatch(canonical) else "unknown"


def discover_cargo_package_skills(project_root: Path | None = None) -> tuple[list[IndexableSkill], list[dict[str, str]]]:
    skills: list[IndexableSkill] = []
    errors: list[dict[str, str]] = []
    seen_dirs: set[Path] = set()
    project_root = project_root or find_project_root()
    if not project_root:
        return skills, errors

    for package_root, package_name, version, environment in _cargo_package_roots(project_root):
        source = {
            "type": "cargo-package",
            "package": package_name,
            "version": version,
            "environment": str(environment),
            "package_root": str(package_root),
        }
        for root, root_source in project_skill_roots(package_root, source):
            try:
                skill_dirs = _skill_dirs(root)
            except OSError as exc:
                errors.append({"path": str(root), "error": str(exc)})
                continue
            for skill_dir in skill_dirs:
                skill_dir = skill_dir.resolve()
                if skill_dir in seen_dirs:
                    continue
                seen_dirs.add(skill_dir)
                try:
                    skills.append(load_skill_from_dir(skill_dir, root_source))
                except (SchemaError, OSError, ValueError) as exc:
                    quarantined = quarantine_skill_from_dir(skill_dir, root_source, exc)
                    if quarantined:
                        skills.append(quarantined)
                    errors.append({"path": str(skill_dir), "error": str(exc)})
    return skills, errors


def _cargo_package_roots(project_root: Path) -> list[tuple[Path, str, str | None, Path]]:
    lock_path = project_root / "Cargo.lock"
    if not lock_path.is_file():
        return []
    lock_packages = _cargo_lock_packages(lock_path)
    if not lock_packages:
        return []
    cargo_home = _cargo_home()
    packages: list[tuple[Path, str, str | None, Path]] = []
    seen_roots: set[Path] = set()
    local_packages: dict[str, list[tuple[str, str]]] = {}
    git_packages: dict[str, list[tuple[str, str]]] = {}

    for package in lock_packages:
        raw_name = package.get("name")
        raw_version = package.get("version")
        if not isinstance(raw_name, str) or not isinstance(raw_version, str):
            continue
        name = canonical_cargo_package_name(raw_name)
        version = raw_version.strip()
        if not name or not version or not CARGO_PACKAGE_RE.fullmatch(name):
            continue
        source = package.get("source")
        if isinstance(source, str) and source.startswith(("registry+", "sparse+")):
            for root, environment in _cargo_registry_package_dirs(cargo_home, name, version):
                _append_cargo_package(packages, seen_roots, root, name, version, environment)
        elif isinstance(source, str) and source.startswith("git+"):
            git_packages.setdefault(name, []).append((version, source))
        elif source is None:
            local_packages.setdefault(name, []).append((version, "local"))

    if git_packages:
        for root, name, version, environment in _cargo_manifest_package_roots(cargo_home / "git" / "checkouts", git_packages):
            _append_cargo_package(packages, seen_roots, root, name, version, environment)
    if local_packages:
        for root, name, version, environment in _cargo_manifest_package_roots(project_root, local_packages):
            _append_cargo_package(packages, seen_roots, root, name, version, environment)
    return packages


def _cargo_home() -> Path:
    raw = os.environ.get("CARGO_HOME")
    if raw:
        try:
            return Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            return Path(raw).expanduser()
    try:
        return (Path.home() / ".cargo").resolve()
    except (OSError, RuntimeError):
        return Path(".cargo").resolve()


def _cargo_registry_package_dirs(cargo_home: Path, name: str, version: str) -> list[tuple[Path, Path]]:
    registry_src = cargo_home / "registry" / "src"
    try:
        source_roots = sorted((path for path in registry_src.iterdir() if path.is_dir()), key=lambda path: path.name)
    except OSError:
        return []
    package_dir_name = f"{name}-{version}"
    result: list[tuple[Path, Path]] = []
    for source_root in source_roots:
        package_root = source_root / package_dir_name
        try:
            if package_root.is_dir():
                result.append((package_root, source_root))
        except OSError:
            continue
    return result


def _append_cargo_package(
    packages: list[tuple[Path, str, str | None, Path]],
    seen_roots: set[Path],
    package_dir: Path,
    package_name: str,
    version: str,
    environment: Path,
) -> None:
    try:
        if not package_dir.is_dir():
            return
        package_root = package_dir.resolve()
    except OSError:
        return
    if package_root in seen_roots:
        return
    if not _has_package_skill_root(package_dir):
        return
    seen_roots.add(package_root)
    packages.append((package_root, package_name, version, environment))


def _cargo_manifest_package_roots(root: Path, locked: dict[str, list[tuple[str, str]]]) -> list[tuple[Path, str, str, Path]]:
    result: list[tuple[Path, str, str, Path]] = []
    for manifest in _cargo_manifest_paths(root):
        package_root = manifest.parent
        if not _has_package_skill_root(package_root):
            continue
        name, version = _cargo_manifest_metadata(manifest)
        match = _matching_locked_cargo_package(name, version, locked)
        if not match:
            continue
        package_name, package_version = match
        result.append((package_root, package_name, package_version, root))
    return sorted(result, key=lambda item: item[0].as_posix())


def _matching_locked_cargo_package(name: str | None, version: str | None, locked: dict[str, list[tuple[str, str]]]) -> tuple[str, str] | None:
    if not name:
        return None
    canonical = canonical_cargo_package_name(name)
    if not CARGO_PACKAGE_RE.fullmatch(canonical):
        return None
    candidates = locked.get(canonical, [])
    if version:
        normalized_version = version.strip()
        for candidate_version, _source in candidates:
            if candidate_version == normalized_version:
                return canonical, candidate_version
        return None
    if len(candidates) == 1:
        return canonical, candidates[0][0]
    return None


def _cargo_manifest_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    manifests: list[Path] = []
    ignored = set(IGNORED_CHILD_REPO_DIR_NAMES)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirname for dirname in dirnames if dirname not in ignored)
        if "Cargo.toml" in filenames:
            manifests.append(Path(dirpath) / "Cargo.toml")
    return sorted(manifests, key=lambda path: path.as_posix())


def _cargo_manifest_metadata(manifest_path: Path) -> tuple[str | None, str | None]:
    data = _load_toml(manifest_path)
    if not data:
        return None, None
    package = data.get("package")
    if not isinstance(package, dict):
        return None, None
    raw_name = package.get("name")
    raw_version = package.get("version")
    name = raw_name if isinstance(raw_name, str) and raw_name.strip() else None
    version = raw_version if isinstance(raw_version, str) and raw_version.strip() else None
    return name, version


def _cargo_lock_packages(lock_path: Path) -> list[dict[str, str | None]]:
    data = _load_toml(lock_path)
    if not data:
        return []
    packages: list[dict[str, str | None]] = []
    raw_packages = data.get("package")
    if not isinstance(raw_packages, list):
        return []
    for raw_package in raw_packages:
        if not isinstance(raw_package, dict):
            continue
        raw_name = raw_package.get("name")
        raw_version = raw_package.get("version")
        if not isinstance(raw_name, str) or not isinstance(raw_version, str):
            continue
        package: dict[str, str | None] = {
            "name": raw_name,
            "version": raw_version,
            "source": None,
        }
        raw_source = raw_package.get("source")
        if isinstance(raw_source, str):
            package["source"] = raw_source
        packages.append(package)
    return [package for package in packages if package.get("name") and package.get("version")]


def _load_toml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _package_distributions(project_root: Path | None = None) -> list[tuple[metadata.Distribution, str | None]]:
    distributions: list[tuple[metadata.Distribution, str | None]] = []
    for site_packages in _site_package_paths(project_root):
        distributions.extend((dist, str(site_packages)) for dist in metadata.distributions(path=[str(site_packages)]))
    return distributions


def _site_package_paths(project_root: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    for venv in environment_roots(project_root):
        for path in venv_site_packages(venv):
            if path not in paths:
                paths.append(path)
    return paths


def _package_skill_dirs(site_packages: Path) -> list[Path]:
    if not site_packages.exists():
        return []
    return sorted({path.parent.resolve() for path in site_packages.rglob("SKILL.md") if _is_packaged_skill_file(path.parts, path.name)})


def _package_name_from_skill_dir(site_packages: Path, skill_dir: Path) -> str:
    try:
        relative = skill_dir.relative_to(site_packages)
    except ValueError:
        return "unknown"
    if not relative.parts:
        return "unknown"
    if relative.parts[0] == ".skills":
        return "unknown"
    if relative.parts[0] == "skills":
        return "unknown"
    return relative.parts[0].replace("_", "-")


def _is_packaged_skill_file(parts: tuple[str, ...], name: str) -> bool:
    return name == "SKILL.md" and (".skills" in parts or "skills" in parts)


def _editable_source_root(dist: metadata.Distribution) -> Path | None:
    try:
        text = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not data.get("dir_info", {}).get("editable"):
        return None
    url = data.get("url")
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    path = Path(unquote(parsed.path)).resolve()
    return path if path.exists() else None


def _skill_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if (root / "SKILL.md").exists():
        if _is_materialized_skill(root):
            return []
        return [root]
    return sorted(path.parent for path in root.rglob("SKILL.md") if not _is_materialized_skill(path.parent))


def _is_materialized_skill(root: Path) -> bool:
    return (root / "skillager.materialized.yaml").exists()


def _dedupe(skills: list[IndexableSkill]) -> list[IndexableSkill]:
    seen_paths: set[Path] = set()
    seen_ids: set[str] = set()
    result: list[IndexableSkill] = []
    for skill in skills:
        unique_path = skill.entrypoint or skill.root
        if unique_path in seen_paths:
            continue
        seen_paths.add(unique_path)
        skill_id = skill.id
        if skill_id in seen_ids:
            base_id = _with_source_suffix(skill.id, _source_suffix(skill))
            skill_id = base_id
            count = 2
            while skill_id in seen_ids:
                skill_id = f"{base_id}-{count}"
                count += 1
            skill = replace(skill, id=skill_id)
        seen_ids.add(skill_id)
        result.append(skill)
    return result


def _with_source_suffix(skill_id: str, suffix: str) -> str:
    if "/" not in skill_id:
        return f"{skill_id}-{suffix}"
    namespace, name = skill_id.rsplit("/", 1)
    return f"{namespace}/{name}-{suffix}"


def _source_suffix(skill: IndexableSkill) -> str:
    parts = []
    if skill.source.get("package"):
        parts.append(skill.source["package"])
    if skill.source.get("agent"):
        parts.append(skill.source["agent"])
    if not parts and skill.source.get("type"):
        parts.append(skill.source["type"])
    suffix = "-".join(_slug(part) for part in parts if part)
    if suffix:
        return suffix
    return hashlib.sha1(str(skill.root).encode("utf-8")).hexdigest()[:8]


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
