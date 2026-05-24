from __future__ import annotations

from typing import Any

APPROVED_APPROVAL_STATES = {"reviewed", "trusted", "pinned"}


def apply_review_metadata(skill: dict[str, Any]) -> dict[str, Any]:
    skill["approval"] = approval_state(skill)
    skill["review_gates"] = review_gates(skill)
    return skill


def approval_state(skill: dict[str, Any]) -> str:
    trust = skill.get("trust")
    if trust in APPROVED_APPROVAL_STATES or trust == "blocked":
        return str(trust)
    return "unreviewed"


def review_gates(skill: dict[str, Any]) -> dict[str, str]:
    return {
        "scan": scan_gate(skill),
        "lint": lint_gate(skill),
        "signature": signature_gate(skill),
        "availability": availability_gate(skill),
    }


def scan_gate(skill: dict[str, Any]) -> str:
    return str((skill.get("scan") or {}).get("risk") or "unknown")


def lint_gate(skill: dict[str, Any]) -> str:
    status = (skill.get("lint") or {}).get("status")
    if status:
        return str(status)
    if skill.get("trust") == "lint_blocked":
        return "blocked"
    return "unknown"


def signature_gate(skill: dict[str, Any]) -> str:
    signature = skill.get("signature")
    if not isinstance(signature, dict):
        return "missing"
    verification = signature.get("verification") or {}
    status = str(verification.get("status") or "not_checked")
    if status == "verified":
        signer = verification.get("signer") or verification.get("issuer") or verification.get("publisher")
        if signer:
            return f"verified:{signer}"
    return status


def availability_gate(skill: dict[str, Any]) -> str:
    trust = skill.get("trust")
    if trust in APPROVED_APPROVAL_STATES:
        return "available"
    if trust == "blocked":
        return "blocked"
    if trust == "lint_blocked" or lint_gate(skill) == "blocked":
        return "blocked_until_lint_override"
    return "blocked_until_review"
