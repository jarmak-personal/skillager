from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LintFinding:
    code: str
    severity: str
    field: str
    detail: str
    rule_key: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "detail": self.detail,
            "rule_key": self.rule_key,
        }


@dataclass(frozen=True)
class LintReport:
    status: str
    findings: tuple[LintFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "findings": [item.to_dict() for item in self.findings]}


@dataclass(frozen=True)
class ValidatedSkillMetadata:
    skill_id: str
    name: str
    summary: str
    root: Path
    entrypoint: Path
    manifest_path: Path | None
    audience: tuple[str, ...]
    activation: str
    compatibility: dict[str, Any]
    targets: dict[str, Any]
    inferred: bool

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "root": str(self.root),
            "entrypoint": str(self.entrypoint),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "audience": list(self.audience),
            "activation": self.activation,
            "compatibility": self.compatibility,
            "targets": self.targets,
            "inferred": self.inferred,
        }


@dataclass(frozen=True)
class LintResult:
    path: Path
    manifest_path: Path | None
    skill_id: str | None
    lint: LintReport
    metadata: ValidatedSkillMetadata | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "skill_id": self.skill_id,
            "lint": self.lint.to_dict(),
        }
