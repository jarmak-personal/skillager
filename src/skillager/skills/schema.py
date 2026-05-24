from __future__ import annotations

import hashlib as _hashlib
import re as _re
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path
from typing import Any as _Any

from skillager_linter import validators as _validators
from skillager_linter.findings import finding as _finding
from skillager_linter.findings import lint_report as _lint_report
from skillager_linter.models import ValidatedSkillMetadata as _ValidatedSkillMetadata
from skillager_linter.simple_yaml import YamlError as _YamlError
from skillager_linter.simple_yaml import load_manifest_mapping as _load_manifest_mapping

SCHEMA = _validators.SCHEMA
AUDIENCES = _validators.AUDIENCES
ACTIVATION_MODES = _validators.ACTIVATION_MODES
TRUST_STATES = {"discovered", "reviewed", "trusted", "pinned", "blocked", "lint_blocked"}
ENV_RE = _validators.ENV_RE
PACKAGE_RE = _validators.PACKAGE_RE
NPM_PACKAGE_RE = _validators.NPM_PACKAGE_RE
MAX_TARGET_PACKAGES = _validators.MAX_TARGET_PACKAGES
MAX_ENV_NAMES = _validators.MAX_ENV_NAMES
MAX_SPECIFIER_LENGTH = _validators.MAX_SPECIFIER_LENGTH
SchemaError = _validators.ManifestValidationError
canonical_npm_package_name = _validators.canonical_npm_package_name


@_dataclass(frozen=True)
class Skill:
    id: str
    name: str
    summary: str
    entrypoint: _Path
    manifest_path: _Path | None
    root: _Path
    source: dict[str, _Any]
    audience: list[str]
    activation: str
    compatibility: dict[str, _Any]
    targets: dict[str, _Any]
    package: str | None
    version: str | None
    inferred: bool = False

    def to_index(self, content_hash: str, scan: dict[str, _Any], trust: str) -> dict[str, _Any]:
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


@_dataclass(frozen=True)
class QuarantinedSkill:
    id: str
    root: _Path
    entrypoint: _Path | None
    manifest_path: _Path | None
    source: dict[str, _Any]
    lint: dict[str, _Any]

    def to_index(self, content_hash: str, scan: dict[str, _Any], trust: str) -> dict[str, _Any]:
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


def load_skill_from_dir(root: _Path, source: dict[str, _Any]) -> Skill:
    manifest = _validators.find_manifest(root)
    if manifest:
        try:
            raw = _load_manifest_mapping(manifest)
        except (_YamlError, OSError, UnicodeError) as exc:
            raise SchemaError(
                "invalid skillager.yaml",
                findings=[_finding("schema_violation", "block", "skillager.yaml", _validators.safe_error(exc))],
            ) from exc
        return parse_skill(raw, root=root, manifest_path=manifest, source_default=source, inferred=False)
    return infer_skill(root, source)


def quarantine_skill_from_dir(root: _Path, source: dict[str, _Any], exc: BaseException) -> QuarantinedSkill | None:
    if not (root / "SKILL.md").exists() and not (root / "SKILL.md").is_symlink():
        return None
    root_resolved = root.resolve()
    manifest = _validators.find_manifest(root)
    try:
        skill_id = _validators.infer_id(root_resolved, source)
    except SchemaError as id_exc:
        skill_id = _fallback_quarantine_id(root_resolved, source)
        findings = list(_validators.schema_findings(id_exc))
    else:
        findings = []
    findings.extend(_validators.schema_findings(exc))
    findings = _dedupe_findings(findings)
    return QuarantinedSkill(
        id=skill_id,
        root=root_resolved,
        entrypoint=(root / "SKILL.md").resolve() if (root / "SKILL.md").exists() else None,
        manifest_path=manifest.resolve() if manifest else None,
        source=source,
        lint=_lint_report(findings),
    )


def parse_skill(
    raw: dict[str, _Any],
    *,
    root: _Path,
    manifest_path: _Path | None,
    source_default: dict[str, _Any],
    inferred: bool,
) -> Skill:
    metadata = _validators.validate_skill_metadata(
        raw,
        root=root,
        manifest_path=manifest_path,
        source=source_default,
        inferred=inferred,
    )
    return _skill_from_metadata(metadata, source_default)


def infer_skill(root: _Path, source: dict[str, _Any]) -> Skill:
    metadata = _validators.validate_skill_metadata(
        None,
        root=root,
        manifest_path=None,
        source=source,
        inferred=True,
    )
    return _skill_from_metadata(metadata, source)


def manifest_for_skill(skill: Skill) -> dict[str, _Any]:
    return _validators.manifest_for_metadata(skill.audience, skill.activation)


def _skill_from_metadata(metadata: _ValidatedSkillMetadata, source_default: dict[str, _Any]) -> Skill:
    source = dict(source_default)
    return Skill(
        id=metadata.skill_id,
        name=metadata.name,
        summary=metadata.summary,
        entrypoint=metadata.entrypoint,
        manifest_path=metadata.manifest_path,
        root=metadata.root,
        source=source,
        audience=list(metadata.audience),
        activation=metadata.activation,
        compatibility=metadata.compatibility,
        targets=metadata.targets,
        package=source.get("package"),
        version=source.get("version"),
        inferred=metadata.inferred,
    )


def _fallback_quarantine_id(root: _Path, source: dict[str, _Any]) -> str:
    prefix = _re.sub(r"[^a-z0-9]+", "-", str(source.get("type") or "skill").lower()).strip("-") or "skill"
    digest = _hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}/lint-blocked-{digest}"


def _dedupe_findings(findings: list[dict[str, _Any]]) -> list[dict[str, _Any]]:
    result: list[dict[str, _Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in findings:
        key = (
            str(item.get("code") or ""),
            str(item.get("severity") or ""),
            str(item.get("field") or ""),
            str(item.get("detail") or ""),
            str(item.get("rule_key") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


__all__ = [
    "ACTIVATION_MODES",
    "AUDIENCES",
    "ENV_RE",
    "MAX_ENV_NAMES",
    "MAX_SPECIFIER_LENGTH",
    "MAX_TARGET_PACKAGES",
    "NPM_PACKAGE_RE",
    "PACKAGE_RE",
    "SCHEMA",
    "TRUST_STATES",
    "QuarantinedSkill",
    "SchemaError",
    "Skill",
    "canonical_npm_package_name",
    "infer_skill",
    "load_skill_from_dir",
    "manifest_for_skill",
    "parse_skill",
    "quarantine_skill_from_dir",
]
