from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any, Iterator

from ..skills.tree import iter_content_files
from ..trust import content_hash


MATERIALIZED_SIDECAR = "skillager.materialized.yaml"
TARGET_STATE_SCHEMA = "skillager.exposure-target-state.v1"


def target_state_hash(root: Path, *, include_sidecar: bool = True) -> str:
    """Hash every entry in an exposure target without following symlinks.

    This is deliberately separate from the canonical skill content hash. Canonical
    hashing excludes caches and transient files so source identity stays stable;
    exposure replacement and removal must still notice those local target entries.
    """

    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise OSError(f"exposure target is not a readable directory: {root}")
    digest = hashlib.sha256()
    digest.update(TARGET_STATE_SCHEMA.encode("ascii"))
    digest.update(b"\0")
    for relative, path, entry_stat in _walk_entries(root):
        if not include_sidecar and relative == MATERIALIZED_SIDECAR:
            continue
        encoded_relative = os.fsencode(relative)
        digest.update(encoded_relative)
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(entry_stat.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISDIR(entry_stat.st_mode):
            digest.update(b"directory\0")
        elif stat.S_ISREG(entry_stat.st_mode):
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        elif stat.S_ISLNK(entry_stat.st_mode):
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(path)))
            digest.update(b"\0")
        else:
            digest.update(b"special\0")
            digest.update(str(entry_stat.st_rdev).encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def matches_materialized_target(root: Path, sidecar: dict[str, Any]) -> bool:
    """Return whether all locally materialized target entries remain unchanged."""

    expected_target_hash = sidecar.get("materialized_target_hash")
    if isinstance(expected_target_hash, str):
        try:
            return target_state_hash(root, include_sidecar=False) == expected_target_hash
        except OSError:
            return False
    if expected_target_hash is not None:
        return False

    # Legacy sidecars predate the full target hash. Their materializer only wrote
    # canonical files, their parent directories, and the root sidecar, so reject any
    # other entry while retaining compatibility with otherwise unchanged targets.
    expected_content_hash = sidecar.get("materialized_hash")
    if not isinstance(expected_content_hash, str):
        return False
    try:
        if content_hash(root) != expected_content_hash:
            return False
        return not _legacy_target_has_untracked_entries(root)
    except OSError:
        return False


def target_has_entries(target: Path) -> bool:
    """Return whether an existing target contains anything that could be lost."""

    if target.is_symlink() or not target.is_dir():
        return target.exists() or target.is_symlink()
    try:
        next(target.iterdir())
    except StopIteration:
        return False
    except OSError:
        return True
    return True


def _legacy_target_has_untracked_entries(root: Path) -> bool:
    canonical_files = {
        path.relative_to(root.resolve()).as_posix()
        for path in iter_content_files(root)
    }
    canonical_directories: set[str] = set()
    for relative in canonical_files:
        parent = Path(relative).parent
        while parent != Path("."):
            canonical_directories.add(parent.as_posix())
            parent = parent.parent

    for relative, _path, entry_stat in _walk_entries(root.absolute()):
        if relative == MATERIALIZED_SIDECAR and stat.S_ISREG(entry_stat.st_mode):
            continue
        if stat.S_ISREG(entry_stat.st_mode) and relative in canonical_files:
            continue
        if stat.S_ISDIR(entry_stat.st_mode) and relative in canonical_directories:
            continue
        return True
    return False


def _walk_entries(root: Path) -> Iterator[tuple[str, Path, os.stat_result]]:
    def visit(directory: Path, parts: tuple[str, ...]) -> Iterator[tuple[str, Path, os.stat_result]]:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        for entry in entries:
            path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            relative_parts = (*parts, entry.name)
            relative = Path(*relative_parts).as_posix()
            yield relative, path, entry_stat
            if stat.S_ISDIR(entry_stat.st_mode):
                yield from visit(path, relative_parts)

    yield from visit(root, ())


__all__ = [
    "MATERIALIZED_SIDECAR",
    "TARGET_STATE_SCHEMA",
    "matches_materialized_target",
    "target_has_entries",
    "target_state_hash",
]
