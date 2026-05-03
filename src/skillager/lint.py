from __future__ import annotations

from typing import Any

RULE_KEYS = {
    "assumptions_env_invalid": "assumptions_env_invalid:v1",
    "audience_both": "audience_both:v1",
    "control_chars": "control_chars:v1",
    "derived_id_invalid": "derived_id_invalid:v1",
    "domain_violation": "domain_violation:v1",
    "entrypoint_invalid": "entrypoint_invalid:v1",
    "generic_description": "generic_description:v1",
    "parallel_subagents_invalid": "parallel_subagents_invalid:v1",
    "schema_violation": "schema_violation:v1",
    "target_package_invalid": "target_package_invalid:v1",
    "unknown_key": "unknown_key:v1",
    "warning_for_undeclared": "warning_for_undeclared:v1",
}


def finding(code: str, severity: str, field: str, detail: str, *, rule_key: str | None = None) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "field": field,
        "detail": detail,
        "rule_key": rule_key or _rule_key(code),
    }


def _rule_key(code: str) -> str:
    return RULE_KEYS.get(code, f"{code or 'lint'}:v1")


def lint_status(findings: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "block" for item in findings):
        return "blocked"
    if findings:
        return "warned"
    return "ok"


def lint_report(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": lint_status(findings), "findings": findings}


def lint_skill(skill: Any) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    audience = list(getattr(skill, "audience", []) or [])
    if set(audience) == {"user", "dev"}:
        findings.append(finding("audience_both", "warn", "audience", "declares both user and developer audiences"))
    summary = str(getattr(skill, "summary", "") or "").strip().lower()
    if summary in {"", "skill", "use this skill", "use this guidance", "guidance"}:
        findings.append(finding("generic_description", "warn", "SKILL.md.description", "description or first paragraph is generic"))
    compatibility = getattr(skill, "compatibility", {}) or {}
    warnings = compatibility.get("warnings") if isinstance(compatibility, dict) else {}
    incompatible = set(compatibility.get("incompatible_with", [])) if isinstance(compatibility, dict) else set()
    if isinstance(warnings, dict):
        for agent in warnings:
            if agent not in incompatible and agent != "any":
                findings.append(finding("warning_for_undeclared", "warn", f"compatibility.warnings.{agent}", "warning is advisory only"))
    return lint_report(findings)


def safe_finding_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("code") or ""), str(item.get("field") or ""), str(item.get("rule_key") or ""))


def blocking_findings(lint: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(lint, dict):
        return []
    return [item for item in lint.get("findings", []) if item.get("severity") == "block"]


def valid_lint_override(record: dict[str, Any] | None, lint: dict[str, Any] | None) -> bool:
    current = {safe_finding_identity(item) for item in blocking_findings(lint)}
    if not current:
        return True
    if not record:
        return False
    override = record.get("lint_override")
    if not isinstance(override, dict):
        return False
    accepted = {
        safe_finding_identity(item)
        for item in override.get("findings", [])
        if isinstance(item, dict) and item.get("severity") == "block"
    }
    return current.issubset(accepted)
