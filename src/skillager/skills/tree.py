from __future__ import annotations

import fnmatch
import hashlib
import shutil
from pathlib import Path

from ..signing import is_evidence_file


CONTENT_TREE_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "skillager.materialized.yaml",
}
TRANSIENT_PATTERNS = ("*.tmp", "*.swp", "*~")
TREE_FINGERPRINT_SCHEMA = "skillager.tree-fingerprint.v2"


def iter_content_files(root: Path) -> list[Path]:
    """Return regular, agent-visible files below a skill root in hash order."""

    root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root)
        if content_path_excluded(relative):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def content_path_excluded(relative: Path) -> bool:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"content tree path must be relative and contained: {relative}")
    if is_evidence_file(relative):
        return True
    for part in relative.parts:
        if part in CONTENT_TREE_EXCLUDES:
            return True
        if part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return any(fnmatch.fnmatch(relative.as_posix(), pattern) for pattern in TRANSIENT_PATTERNS)


def copy_content_tree(source: Path, destination: Path) -> list[str]:
    """Copy the canonical content tree without following symlinks."""

    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"skill source is not a directory: {source}")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"skill destination already exists: {destination}")
    destination.mkdir(parents=True)
    copied: list[str] = []
    for source_path in iter_content_files(source):
        relative = source_path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        copied.append(relative.as_posix())
    return copied


def content_tree_manifest(root: Path) -> dict[str, str]:
    """Return per-file hashes suitable for metadata-only tree comparisons."""

    root = root.resolve()
    result: dict[str, str] = {}
    for path in iter_content_files(root):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        result[relative] = digest.hexdigest()
    return result


def content_tree_fingerprint(root: Path) -> str:
    """Return a cheap advisory fingerprint for the canonical content tree.

    The fingerprint intentionally uses metadata rather than file bytes. It is a cache
    invalidation hint only; content hashes remain authoritative for approval and
    mutation decisions.
    """

    root = root.resolve()
    digest = hashlib.sha256()
    digest.update(TREE_FINGERPRINT_SCHEMA.encode("utf-8"))
    digest.update(b"\0")
    for path in iter_content_files(root):
        relative = path.relative_to(root).as_posix()
        stat = path.stat(follow_symlinks=False)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\0")
        digest.update(b"x" if stat.st_mode & 0o111 else b"-")
        digest.update(b"\0")
    return digest.hexdigest()


def require_canonical_content_tree(root: Path, *, action: str = "mutation") -> None:
    """Refuse symlinks and files deliberately excluded from the authoritative tree."""

    root = root.resolve()
    canonical = {path.relative_to(root).as_posix() for path in iter_content_files(root)}
    noncanonical: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or (path.is_file() and relative not in canonical):
            noncanonical.append(relative)
    if noncanonical:
        visible = ", ".join(sorted(noncanonical)[:5])
        remainder = len(noncanonical) - 5
        suffix = f" (and {remainder} more)" if remainder > 0 else ""
        raise ValueError(
            f"{action} refuses symlinks or files outside the canonical content tree; "
            f"preserve or remove them first: {visible}{suffix}"
        )


__all__ = [
    "CONTENT_TREE_EXCLUDES",
    "TREE_FINGERPRINT_SCHEMA",
    "TRANSIENT_PATTERNS",
    "content_path_excluded",
    "content_tree_fingerprint",
    "content_tree_manifest",
    "copy_content_tree",
    "iter_content_files",
    "require_canonical_content_tree",
]
