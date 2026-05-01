from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from .paths import environment_roots, find_project_root, venv_site_packages
from .schema import SchemaError, Skill, load_skill_from_dir


def project_skill_roots(root: Path, source: dict[str, str]) -> list[tuple[Path, dict[str, str]]]:
    return [
        (root / ".skills", source),
        (root / "skills", source),
        (root / ".agents" / "skills", source),
        (root / ".agents" / "codex" / "skills", {**source, "agent": "codex"}),
        (root / ".agents" / "claude" / "skills", {**source, "agent": "claude"}),
        (root / ".codex" / "skills", {**source, "agent": "codex"}),
        (root / ".claude" / "skills", {**source, "agent": "claude"}),
    ]


def default_source_roots(project_root: Path | None = None) -> list[tuple[Path, dict[str, str]]]:
    roots: list[tuple[Path, dict[str, str]]] = []
    project_root = project_root or find_project_root()
    if project_root:
        roots.extend(project_skill_roots(project_root, {"type": "project"}))
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


def discover(paths: Iterable[Path] | None = None, *, include_packages: bool = True) -> tuple[list[Skill], list[dict[str, str]]]:
    skills: list[Skill] = []
    errors: list[dict[str, str]] = []
    if paths is None:
        roots = default_source_roots()
    else:
        roots = [(path, {"type": "path"}) for path in paths]
    for root, source in roots:
        for skill_dir in _skill_dirs(root):
            try:
                skills.append(load_skill_from_dir(skill_dir, source))
            except (SchemaError, OSError, ValueError) as exc:
                errors.append({"path": str(skill_dir), "error": str(exc)})
    if include_packages:
        package_skills, package_errors = discover_package_skills()
        skills.extend(package_skills)
        errors.extend(package_errors)
    return _dedupe(skills), errors


def discover_package_skills() -> tuple[list[Skill], list[dict[str, str]]]:
    skills: list[Skill] = []
    errors: list[dict[str, str]] = []
    seen_dirs: set[Path] = set()
    project_root = find_project_root()
    for dist, environment in _package_distributions(project_root):
        name = dist.metadata.get("Name", "unknown")
        version = dist.version
        files = dist.files or []
        candidates = sorted({dist.locate_file(file).parent for file in files if _is_packaged_skill_file(file.parts, file.name)})
        for skill_dir in candidates:
            skill_dir = Path(skill_dir).resolve()
            seen_dirs.add(skill_dir)
            try:
                skills.append(load_skill_from_dir(skill_dir, {"type": "python-package", "package": name, "version": version, "environment": environment}))
            except (SchemaError, OSError, ValueError) as exc:
                errors.append({"path": str(skill_dir), "error": str(exc)})
        editable_root = _editable_source_root(dist)
        if editable_root:
            source = {
                "type": "python-package",
                "package": name,
                "version": version,
                "environment": environment,
                "editable": "true",
            }
            for root, root_source in project_skill_roots(editable_root, source):
                for skill_dir in _skill_dirs(root):
                    skill_dir = skill_dir.resolve()
                    if skill_dir in seen_dirs:
                        continue
                    seen_dirs.add(skill_dir)
                    try:
                        skills.append(load_skill_from_dir(skill_dir, root_source))
                    except (SchemaError, OSError, ValueError) as exc:
                        errors.append({"path": str(skill_dir), "error": str(exc)})
    for site_packages in _site_package_paths(project_root):
        for skill_dir in _package_skill_dirs(site_packages):
            if skill_dir in seen_dirs:
                continue
            package = _package_name_from_skill_dir(site_packages, skill_dir)
            try:
                skills.append(load_skill_from_dir(skill_dir, {"type": "python-package", "package": package, "version": None, "environment": str(site_packages)}))
            except (SchemaError, OSError, ValueError) as exc:
                errors.append({"path": str(skill_dir), "error": str(exc)})
    return skills, errors


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


def _dedupe(skills: list[Skill]) -> list[Skill]:
    seen_paths: set[Path] = set()
    seen_ids: set[str] = set()
    result: list[Skill] = []
    for skill in skills:
        if skill.entrypoint in seen_paths:
            continue
        seen_paths.add(skill.entrypoint)
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


def _source_suffix(skill: Skill) -> str:
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
