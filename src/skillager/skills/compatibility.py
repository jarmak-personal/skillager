from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KNOWN_AGENTS = {"codex", "claude", "any"}
WARNING_CODES = {
    "parallel_subagents_unsupported",
    "claude_only_paths",
    "codex_only_paths",
    "assumes_writes_files",
    "assumes_shell",
    "assumes_subagents",
}
WARNING_MESSAGES = {
    "parallel_subagents_unsupported": "Mentions Claude Agent Teams; requires a workflow with parallel subagents.",
    "claude_only_paths": "References Claude skill paths; adapt paths before following file-relative instructions.",
    "codex_only_paths": "References Codex skill paths; adapt paths before following file-relative instructions.",
    "assumes_writes_files": "Assumes the agent can write files.",
    "assumes_shell": "Assumes the agent can run shell commands.",
    "assumes_subagents": "Uses or benefits from parallel subagents.",
}


def normalize_compatibility(raw: Any = None, *, text: str = "", root: Path | None = None) -> dict[str, Any]:
    """Return negative-only compatibility metadata.

    Skills are assumed compatible by default. This metadata only records explicit
    exclusions plus advisory assumptions/warnings inferred from inert text.
    """
    data = _mapping(raw)
    exclusive_to = _optional_agent(data.get("exclusive_to"))
    incompatible_with = _agent_list(data.get("incompatible_with"))
    assumptions = _mapping(data.get("assumptions") or data.get("requires"))
    warnings = _warning_map(data.get("warning") or data.get("warnings"))

    inferred = infer_compatibility(text, root=root)
    inferred_assumptions = inferred.get("assumptions", {})
    merged_assumptions = {**inferred_assumptions, **assumptions}
    merged_warnings = dict(inferred.get("warnings", {}))
    merged_warnings.update(warnings)

    return {
        "exclusive_to": None if exclusive_to == "any" else exclusive_to,
        "incompatible_with": incompatible_with,
        "assumptions": merged_assumptions,
        "warnings": merged_warnings,
        "inferred": inferred.get("signals", []),
    }


def infer_compatibility(text: str, *, root: Path | None = None) -> dict[str, Any]:
    haystack = text.lower()
    root_text = str(root or "").lower()
    signals: list[str] = []
    assumptions: dict[str, Any] = {}
    warnings: dict[str, str] = {}

    if "claude_code_experimental_agent_teams" in haystack or "agent teams" in haystack:
        signals.append("mentions Claude Agent Teams")
        assumptions["parallel_subagents"] = {"required": True}
        warnings["codex"] = "parallel_subagents_unsupported"
    elif re.search(r"\b(parallel|background)\s+(subagents?|agents?)\b", haystack) or "agent tool" in haystack:
        signals.append("mentions parallel agents")
        assumptions["parallel_subagents"] = {"required": False}
        warnings["any"] = "assumes_subagents"

    if "${claude_skill_dir}" in haystack or ".claude/skills" in haystack or "/.claude/skills" in root_text:
        signals.append("mentions Claude skill paths")
        warnings.setdefault("codex", "claude_only_paths")

    if ".agents/skills" in haystack or ".agents/codex/skills" in haystack or "/.agents/skills" in root_text:
        signals.append("mentions Codex skill paths")
        warnings.setdefault("claude", "codex_only_paths")

    env_vars = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{4,}\b", text)))
    env_vars = [name for name in env_vars if name.startswith(("CLAUDE_", "CODEX_", "SKILLAGER_"))]
    if env_vars:
        assumptions["env"] = env_vars[:8]
        signals.append("mentions agent environment variables")

    if re.search(r"\b(write|save|create|edit)\b.{0,60}\b(file|files|directory|directories|folder|folders)\b", haystack):
        assumptions["writes_files"] = True
        signals.append("mentions writing files")
        warnings.setdefault("any", "assumes_writes_files")

    if re.search(r"\b(run|execute)\b.{0,40}\b(shell|bash|sh|command)\b", haystack):
        signals.append("mentions shell commands")
        warnings.setdefault("any", "assumes_shell")

    return {"assumptions": assumptions, "warnings": warnings, "signals": signals}


def compatibility_problem(skill: dict[str, Any], agent: str | None) -> str | None:
    if not agent:
        return None
    agent = agent.lower()
    compatibility = skill.get("compatibility") or {}
    exclusive_to = compatibility.get("exclusive_to")
    if exclusive_to and str(exclusive_to).lower() not in {agent, "any"}:
        return f"exclusive to {exclusive_to}"
    incompatible = {str(item).lower() for item in compatibility.get("incompatible_with", [])}
    if "any" in incompatible or agent in incompatible:
        return f"incompatible with {agent}"
    return None


def compatibility_warnings(skill: dict[str, Any], agent: str | None = None) -> list[str]:
    compatibility = skill.get("compatibility") or {}
    warnings = compatibility.get("warnings") or {}
    result: list[str] = []
    if isinstance(warnings, dict):
        general = warnings.get("any")
        if isinstance(general, str) and general:
            result.append(_warning_message(general))
        if agent:
            value = warnings.get(agent.lower())
            if isinstance(value, str) and value:
                result.append(_warning_message(value))
    assumptions = compatibility.get("assumptions") or {}
    if isinstance(assumptions, dict):
        parallel = assumptions.get("parallel_subagents")
        if isinstance(parallel, dict) and parallel.get("required") is True:
            result.append("Requires a workflow with parallel subagents.")
        elif parallel is True:
            result.append("Uses or benefits from parallel subagents.")
    return _dedupe(result)


def is_explicitly_incompatible(skill: dict[str, Any], agent: str | None) -> bool:
    return compatibility_problem(skill, agent) is not None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_agent(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _agent_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _warning_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).lower(): str(code)
        for key, code in value.items()
        if str(key).lower() in KNOWN_AGENTS and isinstance(code, str) and code in WARNING_CODES
    }


def _append_unique(values: Any, value: str) -> list[str]:
    result = list(values) if isinstance(values, list) else []
    if value not in result:
        result.append(value)
    return result


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _warning_message(code: str) -> str:
    return WARNING_MESSAGES.get(code, code)
