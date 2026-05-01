from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compatibility import normalize_compatibility
from .simple_yaml import load_mapping

SCHEMA = "skillager.skill.v1"
ACTIVATION_MODES = {"always", "suggested", "manual", "session"}
TRUST_STATES = {"discovered", "reviewed", "trusted", "pinned", "blocked"}


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    summary: str
    entrypoint: Path
    manifest_path: Path | None
    root: Path
    source: dict[str, Any]
    audience: list[str]
    activation: str
    triggers: dict[str, Any]
    domains: list[str]
    context: dict[str, Any]
    safety: dict[str, Any]
    compatibility: dict[str, Any]
    references: list[str]
    tools: list[str]
    package: str | None
    version: str | None
    inferred: bool = False

    def to_index(self, content_hash: str, scan: dict[str, Any], trust: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "entrypoint": str(self.entrypoint),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "root": str(self.root),
            "source": self.source,
            "audience": self.audience,
            "activation": self.activation,
            "triggers": self.triggers,
            "domains": self.domains,
            "context": self.context,
            "safety": self.safety,
            "compatibility": self.compatibility,
            "references": self.references,
            "tools": self.tools,
            "package": self.package,
            "version": self.version,
            "content_hash": content_hash,
            "scan": scan,
            "trust": trust,
            "inferred": self.inferred,
        }


def load_skill_from_dir(root: Path, source: dict[str, Any]) -> Skill:
    manifest = _find_manifest(root)
    entrypoint = root / "SKILL.md"
    if manifest:
        raw = load_mapping(manifest)
        return parse_skill(raw, root=root, manifest_path=manifest, source_default=source, inferred=False)
    return infer_skill(root, source)


def parse_skill(
    raw: dict[str, Any],
    *,
    root: Path,
    manifest_path: Path | None,
    source_default: dict[str, Any],
    inferred: bool,
) -> Skill:
    schema = raw.get("schema")
    if schema != SCHEMA:
        raise SchemaError(f"unsupported schema {schema!r}; expected {SCHEMA}")
    skill_id = _required_str(raw, "id")
    name = _required_str(raw, "name")
    summary = _required_str(raw, "summary")
    entrypoint_name = _required_str(raw, "entrypoint")
    source = _mapping(raw.get("source")) or source_default
    audience = _string_list(raw.get("audience"), "audience")
    activation_raw = _mapping(raw.get("activation"))
    activation = activation_raw.get("default")
    if activation not in ACTIVATION_MODES:
        raise SchemaError("activation.default must be one of: " + ", ".join(sorted(ACTIVATION_MODES)))
    root_resolved = root.resolve()
    entrypoint = (root / entrypoint_name).resolve()
    if not entrypoint.is_relative_to(root_resolved):
        raise SchemaError(f"entrypoint must stay inside the skill directory: {entrypoint_name}")
    if (root / entrypoint_name).is_symlink():
        raise SchemaError(f"entrypoint must be a regular file, not a symlink: {entrypoint_name}")
    if not entrypoint.exists():
        raise SchemaError(f"entrypoint does not exist: {entrypoint_name}")
    text = entrypoint.read_text(encoding="utf-8", errors="replace")
    return Skill(
        id=skill_id,
        name=name,
        summary=summary,
        entrypoint=entrypoint,
        manifest_path=manifest_path,
        root=root_resolved,
        source=source,
        audience=audience,
        activation=activation,
        triggers=_mapping(raw.get("triggers")),
        domains=_string_list(raw.get("domains", []), "domains"),
        context=_mapping(raw.get("context")),
        safety=_mapping(raw.get("safety")),
        compatibility=normalize_compatibility(raw.get("compatibility"), text=text, root=root),
        references=_string_list(raw.get("references", []), "references"),
        tools=_string_list(raw.get("tools", []), "tools"),
        package=raw.get("package"),
        version=raw.get("version"),
        inferred=inferred,
    )


def infer_skill(root: Path, source: dict[str, Any]) -> Skill:
    entrypoint = root / "SKILL.md"
    root_resolved = root.resolve()
    if entrypoint.is_symlink():
        raise SchemaError(f"entrypoint must be a regular file, not a symlink: {entrypoint.name}")
    if not entrypoint.exists():
        raise SchemaError(f"missing SKILL.md in {root}")
    if not entrypoint.resolve().is_relative_to(root_resolved):
        raise SchemaError(f"entrypoint must stay inside the skill directory: {entrypoint.name}")
    text = entrypoint.read_text(encoding="utf-8", errors="replace")
    frontmatter = _frontmatter(text)
    heading = frontmatter.get("name") or _first_heading(text) or root.name.replace("-", " ").replace("_", " ").title()
    summary = frontmatter.get("description") or _first_sentence(text, heading)
    skill_id = _infer_id(root, source)
    return Skill(
        id=skill_id,
        name=heading,
        summary=summary,
        entrypoint=entrypoint.resolve(),
        manifest_path=None,
        root=root_resolved,
        source=source,
        audience=["user"],
        activation="manual",
        triggers={"keywords": _keywords_from_text(heading + " " + summary)},
        domains=[],
        context={"budget": "medium"},
        safety={"min_trust": "reviewed", "allow_tools": False},
        compatibility=normalize_compatibility(frontmatter.get("compatibility"), text=text, root=root),
        references=[],
        tools=[],
        package=source.get("package"),
        version=source.get("version"),
        inferred=True,
    )


def manifest_for_skill(skill: Skill) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "id": skill.id,
        "name": skill.name,
        "summary": skill.summary,
        "source": skill.source,
        "audience": skill.audience,
        "activation": {"default": skill.activation},
        "triggers": skill.triggers,
        "context": skill.context,
        "entrypoint": skill.entrypoint.name,
        "safety": skill.safety,
        "compatibility": skill.compatibility,
    }


def _find_manifest(root: Path) -> Path | None:
    for name in ("skillager.yaml", "skillager.yml", "skillager.json"):
        path = root / name
        if path.exists():
            return path
    return None


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{key} is required")
    return value.strip()


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SchemaError("expected mapping")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SchemaError(f"{name} must be a list of strings")
    return value


def _first_heading(text: str) -> str | None:
    for line in _body_without_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _first_sentence(text: str, fallback: str) -> str:
    for line in _body_without_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped != "---":
            return stripped[:180]
    return fallback


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip().strip("\"'")
        if key.strip() in {"name", "description"} and value:
            result[key.strip()] = value
    return result


def _body_without_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def _keywords_from_text(text: str) -> list[str]:
    words: list[str] = []
    for word in text.lower().replace("/", " ").replace("-", " ").split():
        clean = "".join(ch for ch in word if ch.isalnum())
        if len(clean) >= 4 and clean not in words:
            words.append(clean)
    return words[:12]


def _infer_id(root: Path, source: dict[str, Any]) -> str:
    prefix = source.get("package") or source.get("type") or "skill"
    return f"{prefix}/{root.name}".replace(" ", "-").lower()
