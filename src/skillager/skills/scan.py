from __future__ import annotations

import base64
import fnmatch
import re
import unicodedata
from pathlib import Path
from typing import Any

from ..signing import is_evidence_file

FINDINGS: list[dict[str, Any]] = [
    {
        "code": "instruction_override",
        "severity": "high",
        "pattern": re.compile(r"\b(ignore|override|bypass)\b.{0,40}\b(previous|system|developer|higher priority)\b", re.I | re.S),
        "explanation": "Attempts to override higher-priority agent instructions.",
        "recommendation": "Block unless this is clearly a benign example in security documentation.",
    },
    {
        "code": "system_prompt_request",
        "severity": "high",
        "pattern": re.compile(r"\b(system prompt|developer message|hidden instructions?)\b", re.I),
        "explanation": "Mentions hidden agent instructions or system prompts.",
        "recommendation": "Review manually; skills should not ask agents to reveal hidden instructions.",
    },
    {
        "code": "secret_exfiltration",
        "severity": "high",
        "pattern": re.compile(r"\b(api[_ -]?key|token|password|secret|credential)\b.{0,80}\b(print|send|upload|exfiltrate|reveal|dump)\b", re.I | re.S),
        "explanation": "Looks like a request to reveal or transmit secrets.",
        "recommendation": "Block unless the skill is a defensive scanner and never prints secret values.",
    },
    {
        "code": "credential_path",
        "severity": "high",
        "pattern": re.compile(r"(\.ssh/id_rsa|\.aws/credentials|\.gnupg|\.kube/config|\.env\b)", re.I),
        "explanation": "References common credential storage paths.",
        "recommendation": "Confirm the skill does not ask the agent to read, print, copy, or upload secrets.",
    },
    {
        "code": "download_execute",
        "severity": "high",
        "pattern": re.compile(r"\b(curl|wget)\b.{0,100}(\|\s*(bash|sh)|bash\s+-c|sh\s+-c)", re.I | re.S),
        "explanation": "Downloads content and executes it in one flow.",
        "recommendation": "Block or rewrite to require separate download, inspection, and explicit user approval.",
    },
    {
        "code": "network_callback",
        "severity": "medium",
        "pattern": re.compile(r"\b(curl|wget|http[s]?://|webhook|callback)\b.{0,80}\b(token|secret|env|credential|key)\b", re.I | re.S),
        "explanation": "Combines network callbacks with sensitive terms.",
        "recommendation": "Review target URL and data flow before approval.",
    },
    {
        "code": "shell_execution",
        "severity": "medium",
        "pattern": re.compile(r"\b(run|execute)\b.{0,40}\b(shell|bash|sh|powershell|command)\b", re.I | re.S),
        "explanation": "Asks the agent to run shell commands.",
        "recommendation": "Allow only when command execution is an expected part of the skill.",
    },
    {
        "code": "unattended_approval",
        "severity": "medium",
        "pattern": re.compile(r"\b(without asking|do not ask|don't ask|no confirmation|auto[- ]?approve|silently)\b", re.I),
        "explanation": "May be trying to bypass user approval or confirmation.",
        "recommendation": "Review for agentic autonomy that exceeds the user's expected control.",
    },
    {
        "code": "html_comment",
        "severity": "low",
        "pattern": re.compile(r"<!--.*?-->", re.S),
        "explanation": "Contains hidden markdown/HTML comment text.",
        "recommendation": "Inspect comments to ensure they are documentation, not hidden instructions.",
    },
]

SCAN_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "skill.oms.sig",
    "skillager.materialized.yaml",
}
MAX_SCAN_CHARS = 50_000
MAX_SCAN_BYTES = 200_000


def scan_path(path: Path, *, allow_tools: bool = False) -> dict[str, Any]:
    if path.is_dir():
        return scan_directory(path, allow_tools=allow_tools)
    text, truncated = _read_text_limited(path)
    return scan_text(text, path=str(path), allow_tools=allow_tools, truncated=truncated)


def scan_directory(root: Path, *, allow_tools: bool = False) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scanned_files = 0
    skipped_files = 0
    findings.extend(_symlink_findings(root))
    for path in _scan_files(root):
        if _looks_binary(path):
            skipped_files += 1
            continue
        report = scan_path(path, allow_tools=allow_tools)
        findings.extend(report["findings"])
        scanned_files += 1
    risk = _risk(findings)
    return {"risk": risk, "ok": risk != "high", "findings": findings, "scanned_files": scanned_files, "skipped_files": skipped_files}


def scan_text(text: str, *, path: str | None = None, allow_tools: bool = False, truncated: bool = False) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    original_length = len(text)
    if original_length > MAX_SCAN_CHARS:
        truncated = True
        text = text[:MAX_SCAN_CHARS]
    for rule in FINDINGS:
        code = rule["code"]
        if code == "shell_execution" and allow_tools:
            continue
        pattern = rule["pattern"]
        for match in pattern.finditer(text):
            findings.append(_finding(rule, text, match.start(), match.group(0), path))
    findings.extend(_hidden_char_findings(text, path))
    findings.extend(_encoded_payload_findings(text, path))
    if truncated:
        findings.append({"code": "oversized_skill", "severity": "medium", "line": 1, "message": "skill content is larger than 50k characters", "path": path})
    risk = _risk(findings)
    return {"risk": risk, "ok": risk != "high", "findings": findings}


def _finding(rule: dict[str, Any] | str, text: str, offset: int, snippet: str, path: str | None) -> dict[str, Any]:
    if isinstance(rule, str):
        code = rule
        severity = "low"
        explanation = None
        recommendation = None
    else:
        code = rule["code"]
        severity = rule["severity"]
        explanation = rule.get("explanation")
        recommendation = rule.get("recommendation")
    return {
        "code": code,
        "severity": severity,
        "line": text.count("\n", 0, offset) + 1,
        "message": snippet[:120].replace("\n", " "),
        "path": path,
        "explanation": explanation,
        "recommendation": recommendation,
    }


def _hidden_char_findings(text: str, path: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, char in enumerate(text):
        category = unicodedata.category(char)
        if category in {"Cf", "Cc"} and char not in {"\n", "\r", "\t"}:
            findings.append(
                {
                    "code": "hidden_control_character",
                    "severity": "medium",
                    "line": text.count("\n", 0, index) + 1,
                    "message": f"hidden/control character U+{ord(char):04X}",
                    "path": path,
                }
            )
    return findings


def _encoded_payload_findings(text: str, path: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for match in re.finditer(r"\b(?:[A-Za-z0-9+/]{20,}\s*){4,}={0,2}\b", text):
        blob = "".join(match.group(0).split())
        if len(blob) < 80:
            continue
        try:
            decoded = base64.b64decode(blob, validate=True)
        except Exception:
            continue
        if any(marker in decoded.lower() for marker in (b"ignore previous", b"system prompt", b"/.ssh/", b"api_key")):
            findings.append(
                _finding(
                    {
                        "code": "encoded_payload",
                        "severity": "high",
                        "explanation": "Base64-like content decodes to risky agent instructions or secret paths.",
                        "recommendation": "Block unless the encoded content is required and fully documented.",
                    },
                    text,
                    match.start(),
                    "base64-like hidden instruction",
                    path,
                )
            )
        else:
            findings.append(
                _finding(
                    {
                        "code": "encoded_blob",
                        "severity": "low",
                        "explanation": "Large encoded content is hard to review in plain text.",
                        "recommendation": "Decode and inspect before trusting if the blob is part of instructions.",
                    },
                    text,
                    match.start(),
                    "large base64-like blob",
                    path,
                )
            )
    return findings


def _risk(findings: list[dict[str, Any]]) -> str:
    severities = {item["severity"] for item in findings}
    if "high" in severities:
        return "high"
    if "medium" in severities:
        return "medium"
    if "low" in severities:
        return "low"
    return "low"


def _scan_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _excluded(relative):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _excluded(relative: Path) -> bool:
    if is_evidence_file(relative):
        return True
    for part in relative.parts:
        if part in SCAN_EXCLUDES:
            return True
        if part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return any(fnmatch.fnmatch(relative.as_posix(), pattern) for pattern in ("*.tmp", "*.swp", "*~"))


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    return b"\0" in chunk


def _read_text_limited(path: Path) -> tuple[str, bool]:
    with path.open("rb") as handle:
        data = handle.read(MAX_SCAN_BYTES + 1)
    truncated = len(data) > MAX_SCAN_BYTES
    if truncated:
        data = data[:MAX_SCAN_BYTES]
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_SCAN_CHARS:
        return text[:MAX_SCAN_CHARS], True
    return text, truncated


def _symlink_findings(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    root_resolved = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        relative = path.relative_to(root)
        if _excluded(relative):
            continue
        try:
            target = path.resolve(strict=False)
        except OSError:
            target = path.absolute()
        if not target.is_relative_to(root_resolved):
            findings.append(
                {
                    "code": "symlink_escape",
                    "severity": "high",
                    "line": 1,
                    "message": f"symlink leaves skill root: {relative.as_posix()} -> {target}",
                    "path": str(path),
                    "explanation": "A skill file symlink points outside the reviewed skill directory.",
                    "recommendation": "Remove the symlink or replace it with explicit reviewed content before trusting.",
                }
            )
    return findings
