"""Bounded metadata observation of the existing canonical acceptance tree."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ..skills.tree import iter_content_files
from ..trust import approval_key_for, content_hash_entries, trust_info
from .candidate import index_library_candidate
from .model import LibraryIdentity, LibraryRegistration


REVIEW_SCHEMA = "skillager.library-review-manifest.v1"
REVIEW_LIMITS = {"files": 512, "file_bytes": 2 * 1024 * 1024,
                 "tree_bytes": 32 * 1024 * 1024, "response_bytes": 256 * 1024}


class ReviewRefusal(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def limit_refusal() -> ReviewRefusal:
    return ReviewRefusal("review_limit_exceeded", "the complete review exceeds finite limits; reduce or split the skill tree and preview again")


def capture_review_tree(root: Path) -> tuple[dict[str, Any], list[tuple[str, bytes, bool]]]:
    """Hash bounded bytes once using the existing approval-identity owner."""
    try:
        paths = iter_content_files(root)
        if len(paths) > REVIEW_LIMITS["files"]:
            raise limit_refusal()
        files = []
        entries: list[tuple[str, bytes, bool]] = []
        total = 0
        for path in paths:
            remaining = min(REVIEW_LIMITS["file_bytes"], REVIEW_LIMITS["tree_bytes"] - total)
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as reader:
                before = os.fstat(reader.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise OSError("review file is no longer regular")
                if before.st_size > remaining:
                    raise limit_refusal()
                payload = reader.read(remaining + 1)
                after = os.fstat(reader.fileno())
            if len(payload) > remaining:
                raise limit_refusal()
            attributes = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, name) != getattr(after, name) for name in attributes) or len(payload) != before.st_size:
                raise OSError("review file changed during capture")
            relative = path.relative_to(root).as_posix()
            executable = bool(before.st_mode & 0o111)
            files.append({"path": relative, "size": len(payload),
                          "sha256": hashlib.sha256(payload).hexdigest(), "executable": executable})
            entries.append((relative, payload, executable))
            total += len(payload)
        return ({"working_hash": content_hash_entries(entries), "files": files,
                 "file_count": len(files), "total_bytes": total}, entries)
    except ReviewRefusal:
        raise
    except (OSError, ValueError) as error:
        raise ReviewRefusal("review_changed", "the review tree could not be captured consistently; preview again") from error


def review_library_skill(catalog: Path, registration: LibraryRegistration, identity: LibraryIdentity,
                         name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run existing scanner/lint policy on private, bounded, captured bytes."""
    root = registration.layout.skill_root(name)
    captured, entries = capture_review_tree(root)
    with tempfile.TemporaryDirectory(prefix="skillager-review-") as temporary:
        candidate = (Path(temporary) / name).resolve()
        candidate.mkdir()
        for relative, payload, executable in entries:
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o755 if executable else 0o644)
        skill = index_library_candidate(candidate, registration.layout, identity.library_id, name)
        if skill["content_hash"] != captured["working_hash"]:
            raise ReviewRefusal("review_changed", "the captured tree does not reproduce its approval identity; preview again")
        for field in ("root", "entrypoint", "manifest_path"):
            if skill.get(field):
                skill[field] = str(root / Path(skill[field]).relative_to(candidate))
        for finding in skill["scan"]["findings"]:
            if finding.get("path"):
                finding["path"] = str(root / Path(finding["path"]).relative_to(candidate))
    key = approval_key_for(skill["id"], root, skill["source"], entrypoint=skill.get("entrypoint"))
    skill["trust"] = trust_info(catalog, skill["id"], captured["working_hash"], lint=skill.get("lint"),
                                approval_key=key, approval_root=catalog).get("state", "discovered")
    return skill, captured
