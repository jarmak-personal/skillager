from __future__ import annotations

from typing import Any

from skillager_linter.findings import (
    RULE_KEYS,
    blocking_findings,
    finding,
    lint_report,
    lint_skill,
    lint_status,
    safe_finding_identity,
)


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


__all__ = [
    "RULE_KEYS",
    "blocking_findings",
    "finding",
    "lint_report",
    "lint_skill",
    "lint_status",
    "safe_finding_identity",
    "valid_lint_override",
]
