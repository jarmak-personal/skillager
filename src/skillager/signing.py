from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

OMS_SIGNATURE = "skill.oms.sig"
SKILL_CARD_NAMES = ("skill-card.md", "Skill Card.md", "card.yaml", "card.yml", "SKILLCARD.yaml", "SKILLCARD.yml")
EVIDENCE_FILE_NAMES = frozenset((OMS_SIGNATURE, *SKILL_CARD_NAMES))


def signature_info(root: Path) -> dict[str, Any] | None:
    root = root.resolve()
    signature_path = root / OMS_SIGNATURE
    if not signature_path.is_file():
        return None
    info: dict[str, Any] = {
        "format": "oms",
        "path": str(signature_path),
        "filename": OMS_SIGNATURE,
        "signature_hash": _file_sha256(signature_path),
        "verification": {"status": "not_checked"},
        "card": {"present": False},
    }
    card = _skill_card_path(root)
    if card:
        info["card"] = {"present": True, "path": str(card), "filename": card.name, "hash": _file_sha256(card)}
    parsed = _parse_oms_signature(signature_path)
    if parsed:
        info.update(parsed)
    return info


def verify_oms_signature(
    root: Path,
    *,
    certificate_chains: list[Path],
    ignore_unsigned_files: bool = False,
    verifier: str = "model_signing",
) -> dict[str, Any]:
    root = root.resolve()
    signature_path = root / OMS_SIGNATURE
    if not signature_path.is_file():
        return {
            "verified": False,
            "status": "missing_signature",
            "root": str(root),
            "signature_path": str(signature_path),
            "message": f"{OMS_SIGNATURE} is not present at the skill root",
        }
    executable = shutil.which(verifier)
    if executable is None:
        return {
            "verified": False,
            "status": "verifier_missing",
            "root": str(root),
            "signature_path": str(signature_path),
            "message": "model_signing is not installed; install the optional verifier with `pip install model-signing`",
        }
    missing_certs = [str(path) for path in certificate_chains if not path.is_file()]
    if missing_certs:
        return {
            "verified": False,
            "status": "missing_certificate",
            "root": str(root),
            "signature_path": str(signature_path),
            "missing_certificates": missing_certs,
            "message": f"certificate chain file not found: {missing_certs[0]}",
        }
    command = [
        executable,
        "verify",
        "certificate",
        str(root),
        "--signature",
        str(signature_path),
    ]
    for certificate in certificate_chains:
        command.extend(["--certificate-chain", str(certificate.resolve())])
    if ignore_unsigned_files:
        command.append("--ignore-unsigned-files")
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    verified = completed.returncode == 0
    return {
        "verified": verified,
        "status": "verified" if verified else "failed",
        "root": str(root),
        "signature_path": str(signature_path),
        "certificate_chains": [str(path.resolve()) for path in certificate_chains],
        "strict": not ignore_unsigned_files,
        "returncode": completed.returncode,
        "message": stdout or stderr,
    }


def _skill_card_path(root: Path) -> Path | None:
    for name in SKILL_CARD_NAMES:
        path = root / name
        if path.is_file():
            return path.resolve()
    return None


def is_evidence_file(relative: Path) -> bool:
    return len(relative.parts) == 1 and relative.name in EVIDENCE_FILE_NAMES


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_oms_signature(signature_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(signature_path.read_text(encoding="utf-8"))
        payload = data.get("dsseEnvelope", {}).get("payload")
        if not isinstance(payload, str):
            return None
        statement = json.loads(base64.b64decode(payload))
    except Exception:
        return None
    predicate = statement.get("predicate") if isinstance(statement, dict) else None
    resources = predicate.get("resources") if isinstance(predicate, dict) else None
    subjects = statement.get("subject") if isinstance(statement, dict) else None
    result: dict[str, Any] = {}
    if isinstance(resources, list):
        result["signed_resource_count"] = len(resources)
        algorithms = sorted({str(item.get("algorithm")) for item in resources if isinstance(item, dict) and item.get("algorithm")})
        if algorithms:
            result["signed_resource_algorithms"] = algorithms
    if isinstance(subjects, list):
        safe_subjects = []
        for subject in subjects[:10]:
            if not isinstance(subject, dict):
                continue
            item: dict[str, Any] = {}
            if isinstance(subject.get("name"), str):
                item["name"] = subject["name"]
            digest = subject.get("digest")
            if isinstance(digest, dict):
                item["digest"] = {str(key): str(value) for key, value in digest.items() if isinstance(value, str)}
            if item:
                safe_subjects.append(item)
        if safe_subjects:
            result["subjects"] = safe_subjects
    return result or None


__all__ = [
    "EVIDENCE_FILE_NAMES",
    "OMS_SIGNATURE",
    "SKILL_CARD_NAMES",
    "is_evidence_file",
    "signature_info",
    "verify_oms_signature",
]
