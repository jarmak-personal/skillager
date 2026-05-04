from __future__ import annotations

import re
import unicodedata
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from ..compatibility import KNOWN_AGENTS, WARNING_CODES, normalize_compatibility
from ..lint import finding, lint_report
from ..simple_yaml import YamlError, load_manifest_mapping

SCHEMA = "skillager.skill.v1"
AUDIENCES = {"user", "dev"}
ACTIVATION_MODES = {"always", "suggested", "manual", "session"}
TRUST_STATES = {"discovered", "reviewed", "trusted", "pinned", "blocked", "lint_blocked"}
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PACKAGE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_TARGET_PACKAGES = 16
MAX_ENV_NAMES = 16
MAX_SPECIFIER_LENGTH = 128


class SchemaError(ValueError):
    def __init__(self, message: str, *, findings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.findings = findings or [finding("schema_violation", "block", "skillager.yaml", message)]


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
    compatibility: dict[str, Any]
    targets: dict[str, Any]
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
            "compatibility": self.compatibility,
            "targets": self.targets,
            "package": self.package,
            "version": self.version,
            "content_hash": content_hash,
            "scan": scan,
            "trust": trust,
            "inferred": self.inferred,
        }


@dataclass(frozen=True)
class QuarantinedSkill:
    id: str
    root: Path
    entrypoint: Path | None
    manifest_path: Path | None
    source: dict[str, Any]
    lint: dict[str, Any]

    def to_index(self, content_hash: str, scan: dict[str, Any], trust: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": str(self.root),
            "entrypoint": str(self.entrypoint) if self.entrypoint else None,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "source": self.source,
            "content_hash": content_hash,
            "scan": scan,
            "trust": trust,
            "lint": self.lint,
            "quarantined": True,
            "inferred": False,
        }


def load_skill_from_dir(root: Path, source: dict[str, Any]) -> Skill:
    manifest = _find_manifest(root)
    if manifest:
        try:
            raw = load_manifest_mapping(manifest)
        except (YamlError, OSError, UnicodeError) as exc:
            raise SchemaError("invalid skillager.yaml", findings=[finding("schema_violation", "block", "skillager.yaml", _safe_error(exc))]) from exc
        return parse_skill(raw, root=root, manifest_path=manifest, source_default=source, inferred=False)
    return infer_skill(root, source)


def quarantine_skill_from_dir(root: Path, source: dict[str, Any], exc: BaseException) -> QuarantinedSkill | None:
    if not (root / "SKILL.md").exists() and not (root / "SKILL.md").is_symlink():
        return None
    root_resolved = root.resolve()
    manifest = _find_manifest(root)
    try:
        skill_id = _infer_id(root_resolved, source)
    except SchemaError as id_exc:
        skill_id = _fallback_quarantine_id(root_resolved, source)
        findings = list(_schema_findings(id_exc))
    else:
        findings = []
    findings.extend(_schema_findings(exc))
    return QuarantinedSkill(
        id=skill_id,
        root=root_resolved,
        entrypoint=(root / "SKILL.md").resolve() if (root / "SKILL.md").exists() else None,
        manifest_path=manifest.resolve() if manifest else None,
        source=source,
        lint=lint_report(findings),
    )


def parse_skill(
    raw: dict[str, Any],
    *,
    root: Path,
    manifest_path: Path | None,
    source_default: dict[str, Any],
    inferred: bool,
) -> Skill:
    _check_allowed_keys(raw, {"schema", "audience", "activation", "compatibility", "targets"}, "skillager.yaml")
    _check_no_control_chars(raw, "skillager.yaml")
    if raw.get("schema") != SCHEMA:
        raise SchemaError(f"schema must be {SCHEMA}", findings=[finding("schema_violation", "block", "schema", f"expected {SCHEMA}")])
    audience = _enum_list(raw.get("audience"), "audience", AUDIENCES, required=True)
    activation_raw = _required_mapping(raw.get("activation"), "activation")
    _check_allowed_keys(activation_raw, {"default"}, "activation")
    activation = activation_raw.get("default")
    if activation not in ACTIVATION_MODES:
        raise SchemaError(
            "activation.default is invalid",
            findings=[finding("schema_violation", "block", "activation.default", "expected activation enum")],
        )
    entrypoint = _canonical_entrypoint(root)
    text = entrypoint.read_text(encoding="utf-8", errors="replace")
    skill_id = _infer_id(root, source_default)
    name, summary = _identity_from_skill_md(root, text)
    compatibility = normalize_compatibility(_compatibility(raw.get("compatibility")), text=text, root=root)
    targets = _targets(raw.get("targets"))
    source = dict(source_default)
    return Skill(
        id=skill_id,
        name=name,
        summary=summary,
        entrypoint=entrypoint,
        manifest_path=manifest_path.resolve() if manifest_path else None,
        root=root.resolve(),
        source=source,
        audience=audience,
        activation=activation,
        compatibility=compatibility,
        targets=targets,
        package=source.get("package"),
        version=source.get("version"),
        inferred=inferred,
    )


def infer_skill(root: Path, source: dict[str, Any]) -> Skill:
    entrypoint = _canonical_entrypoint(root)
    text = entrypoint.read_text(encoding="utf-8", errors="replace")
    name, summary = _identity_from_skill_md(root, text)
    skill_id = _infer_id(root, source)
    return Skill(
        id=skill_id,
        name=name,
        summary=summary,
        entrypoint=entrypoint,
        manifest_path=None,
        root=root.resolve(),
        source=source,
        audience=["user"],
        activation="manual",
        compatibility=normalize_compatibility(None, text=text, root=root),
        targets={},
        package=source.get("package"),
        version=source.get("version"),
        inferred=True,
    )


def manifest_for_skill(skill: Skill) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "audience": skill.audience,
        "activation": {"default": skill.activation},
    }


def _canonical_entrypoint(root: Path) -> Path:
    entrypoint = root / "SKILL.md"
    root_resolved = root.resolve()
    if entrypoint.is_symlink():
        raise SchemaError(
            "entrypoint must be a regular file",
            findings=[finding("entrypoint_invalid", "block", "SKILL.md", "SKILL.md must not be a symlink")],
        )
    if not entrypoint.exists() or not entrypoint.is_file():
        raise SchemaError(
            "missing SKILL.md",
            findings=[finding("entrypoint_invalid", "block", "SKILL.md", "SKILL.md is required")],
        )
    resolved = entrypoint.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise SchemaError(
            "entrypoint must stay inside the skill directory",
            findings=[finding("entrypoint_invalid", "block", "SKILL.md", "SKILL.md must stay inside skill root")],
        )
    return resolved


def _find_manifest(root: Path) -> Path | None:
    path = root / "skillager.yaml"
    return path if path.exists() else None


def _identity_from_skill_md(root: Path, text: str) -> tuple[str, str]:
    frontmatter = _frontmatter(text)
    heading = frontmatter.get("name") or _first_heading(text) or root.name.replace("-", " ").replace("_", " ").title()
    summary = frontmatter.get("description") or _first_sentence(text, heading)
    return heading, summary


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{field} must be a mapping", findings=[finding("schema_violation", "block", field, "expected mapping")])
    return value


def _optional_mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SchemaError(f"{field} must be a mapping", findings=[finding("schema_violation", "block", field, "expected mapping")])
    return value


def _check_allowed_keys(raw: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown_count = sum(1 for key in raw if key not in allowed)
    if unknown_count:
        detail = "contains unknown manifest field" if unknown_count == 1 else f"contains {unknown_count} unknown manifest fields"
        raise SchemaError("unknown manifest key", findings=[finding("unknown_key", "block", field, detail)])


def _check_no_control_chars(value: Any, field: str) -> None:
    if isinstance(value, str):
        for char in value:
            category = unicodedata.category(char)
            if category in {"Cf", "Cc"} and char not in {"\n", "\r", "\t"}:
                raise SchemaError("manifest string contains control characters", findings=[finding("control_chars", "block", field, "contains hidden/control characters")])
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _check_no_control_chars(key, field)
            _check_no_control_chars(item, field)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_no_control_chars(item, f"{field}[{index}]")


def _enum_list(value: Any, field: str, choices: set[str], *, required: bool = False, maximum: int | None = None) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not value:
        raise SchemaError(f"{field} must be a non-empty list", findings=[finding("schema_violation", "block", field, "expected non-empty enum list")])
    if maximum is not None and len(value) > maximum:
        raise SchemaError(f"{field} has too many entries", findings=[finding("domain_violation", "block", field, f"maximum entries is {maximum}")])
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in choices:
            raise SchemaError(f"{field} contains invalid enum", findings=[finding("schema_violation", "block", field, "expected known enum value")])
        if item in result:
            raise SchemaError(f"{field} contains duplicates", findings=[finding("domain_violation", "block", field, "duplicate values are not allowed")])
        result.append(item)
    return result


def _compatibility(value: Any) -> dict[str, Any]:
    data = _optional_mapping(value, "compatibility")
    _check_allowed_keys(data, {"exclusive_to", "incompatible_with", "assumptions", "warnings"}, "compatibility")
    result: dict[str, Any] = {}
    if "exclusive_to" in data:
        result["exclusive_to"] = _agent(data["exclusive_to"], "compatibility.exclusive_to")
    if "incompatible_with" in data:
        result["incompatible_with"] = _enum_list(data["incompatible_with"], "compatibility.incompatible_with", KNOWN_AGENTS)
    assumptions = _assumptions(data.get("assumptions"))
    if assumptions:
        result["assumptions"] = assumptions
    warnings = _warnings(data.get("warnings"))
    if warnings:
        result["warnings"] = warnings
    return result


def _agent(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in KNOWN_AGENTS:
        raise SchemaError(f"{field} contains invalid agent", findings=[finding("schema_violation", "block", field, "expected known agent enum")])
    return value


def _assumptions(value: Any) -> dict[str, Any]:
    data = _optional_mapping(value, "compatibility.assumptions")
    _check_allowed_keys(data, {"parallel_subagents", "writes_files", "env"}, "compatibility.assumptions")
    result: dict[str, Any] = {}
    if "parallel_subagents" in data:
        parallel = _required_mapping(data["parallel_subagents"], "compatibility.assumptions.parallel_subagents")
        _check_allowed_keys(parallel, {"required", "preferred"}, "compatibility.assumptions.parallel_subagents")
        normalized: dict[str, Any] = {}
        if "required" in parallel:
            if not isinstance(parallel["required"], bool):
                raise SchemaError(
                    "parallel_subagents.required must be bool",
                    findings=[finding("parallel_subagents_invalid", "block", "compatibility.assumptions.parallel_subagents.required", "expected bool")],
                )
            normalized["required"] = parallel["required"]
        if "preferred" in parallel:
            if not isinstance(parallel["preferred"], int) or not 1 <= parallel["preferred"] <= 16:
                raise SchemaError(
                    "parallel_subagents.preferred is invalid",
                    findings=[finding("parallel_subagents_invalid", "block", "compatibility.assumptions.parallel_subagents.preferred", "expected integer 1..16")],
                )
            normalized["preferred"] = parallel["preferred"]
        result["parallel_subagents"] = normalized
    if "writes_files" in data:
        if not isinstance(data["writes_files"], bool):
            raise SchemaError("writes_files must be bool", findings=[finding("schema_violation", "block", "compatibility.assumptions.writes_files", "expected bool")])
        result["writes_files"] = data["writes_files"]
    if "env" in data:
        result["env"] = _env_list(data["env"])
    return result


def _env_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SchemaError("env must be a list", findings=[finding("assumptions_env_invalid", "block", "compatibility.assumptions.env", "expected env name list")])
    if len(value) > MAX_ENV_NAMES:
        raise SchemaError("env list too long", findings=[finding("assumptions_env_invalid", "block", "compatibility.assumptions.env", f"maximum entries is {MAX_ENV_NAMES}")])
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not ENV_RE.fullmatch(item):
            raise SchemaError("env name invalid", findings=[finding("assumptions_env_invalid", "block", "compatibility.assumptions.env", "expected env identifier")])
        if item in result:
            raise SchemaError("env contains duplicates", findings=[finding("assumptions_env_invalid", "block", "compatibility.assumptions.env", "duplicate values are not allowed")])
        result.append(item)
    return result


def _warnings(value: Any) -> dict[str, str]:
    data = _optional_mapping(value, "compatibility.warnings")
    result: dict[str, str] = {}
    for key, item in data.items():
        if key not in KNOWN_AGENTS or not isinstance(item, str) or item not in WARNING_CODES:
            raise SchemaError("compatibility warning invalid", findings=[finding("schema_violation", "block", "compatibility.warnings", "expected warning-code enum")])
        result[key] = item
    return result


def _targets(value: Any) -> dict[str, Any]:
    data = _optional_mapping(value, "targets")
    _check_allowed_keys(data, {"python_packages"}, "targets")
    if "python_packages" not in data:
        return {}
    packages = data["python_packages"]
    if not isinstance(packages, list) or len(packages) > MAX_TARGET_PACKAGES:
        raise SchemaError("targets.python_packages invalid", findings=[finding("target_package_invalid", "block", "targets.python_packages", f"expected list with at most {MAX_TARGET_PACKAGES} entries")])
    result = []
    seen: set[tuple[str, str | None]] = set()
    for index, item in enumerate(packages):
        field = f"targets.python_packages[{index}]"
        package = _required_mapping(item, field)
        _check_allowed_keys(package, {"name", "versions"}, field)
        name = package.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SchemaError("package name invalid", findings=[finding("target_package_invalid", "block", f"{field}.name", "expected package name")])
        canonical = canonicalize_name(name)
        if not PACKAGE_RE.fullmatch(canonical):
            raise SchemaError("package name invalid", findings=[finding("target_package_invalid", "block", f"{field}.name", "expected PEP 503 package name")])
        versions = None
        if "versions" in package:
            raw_spec = package["versions"]
            if not isinstance(raw_spec, str) or len(raw_spec) > MAX_SPECIFIER_LENGTH:
                raise SchemaError("package version specifier invalid", findings=[finding("target_package_invalid", "block", f"{field}.versions", "expected PEP 440 specifier")])
            try:
                versions = str(SpecifierSet(raw_spec))
            except InvalidSpecifier as exc:
                raise SchemaError(
                    "package version specifier invalid",
                    findings=[finding("target_package_invalid", "block", f"{field}.versions", "expected PEP 440 specifier")],
                ) from exc
        key = (canonical, versions)
        if key in seen:
            raise SchemaError("duplicate package target", findings=[finding("target_package_invalid", "block", field, "duplicate package target")])
        seen.add(key)
        target: dict[str, Any] = {"name": canonical}
        if versions is not None:
            target["versions"] = versions
        result.append(target)
    return {"python_packages": result}


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


def _infer_id(root: Path, source: dict[str, Any]) -> str:
    prefix_value = source.get("package") or source.get("collection") or source.get("type") or "skill"
    prefix = _id_part(str(prefix_value), "source")
    name = _id_part(root.name, "path")
    return f"{prefix}/{name}"


def _id_part(value: str, field: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    if not normalized or len(normalized) > 64:
        raise SchemaError(
            "derived ID component invalid",
            findings=[finding("derived_id_invalid", "block", field, "path/source component cannot form a bounded slug")],
        )
    return normalized


def _fallback_quarantine_id(root: Path, source: dict[str, Any]) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "-", str(source.get("type") or "skill").lower()).strip("-") or "skill"
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}/lint-blocked-{digest}"


def _schema_findings(exc: BaseException) -> list[dict[str, Any]]:
    if isinstance(exc, SchemaError):
        return list(exc.findings)
    return [finding("schema_violation", "block", "skillager.yaml", _safe_error(exc))]


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, UnicodeError):
        return "skillager.yaml must be valid UTF-8"
    if isinstance(exc, YamlError):
        return "skillager.yaml failed strict manifest parsing"
    if isinstance(exc, OSError):
        return "skillager.yaml could not be read"
    return "skillager.yaml could not be parsed"
