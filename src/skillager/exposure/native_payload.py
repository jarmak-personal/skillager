"""Descriptor-bound native payload copying for standalone export destinations."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from ..skills.tree import iter_content_files


def copy_native_tree_to_fd(source: Path, destination_fd: int, *, owned_entries: dict[str, tuple[int, int]] | None = None) -> None:
    """Preserve native file bytes/modes while refusing redirected target components."""
    source = source.resolve()
    directories: dict[tuple[str, ...], tuple[int, int]] = {}
    for path in iter_content_files(source):
        parts = path.relative_to(source).parts
        parent_fd = os.dup(destination_fd)
        try:
            for index, name in enumerate(parts[:-1]):
                key = parts[:index + 1]
                if key not in directories:
                    os.mkdir(name, dir_fd=parent_fd)
                next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                value = os.fstat(next_fd)
                identity = (value.st_dev, value.st_ino)
                if key in directories and directories[key] != identity:
                    os.close(next_fd)
                    raise ValueError("native payload destination directory changed")
                directories[key] = identity
                if owned_entries is not None:
                    owned_entries[Path(*key).as_posix()] = identity
                os.close(parent_fd)
                parent_fd = next_fd
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as reader:
                metadata = os.fstat(reader.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("native payload source is no longer a regular file")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                with os.fdopen(os.open(parts[-1], flags, 0o600, dir_fd=parent_fd), "wb") as writer:
                    created = os.fstat(writer.fileno())
                    if owned_entries is not None:
                        owned_entries[Path(*parts).as_posix()] = (created.st_dev, created.st_ino)
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fchmod(writer.fileno(), stat.S_IMODE(metadata.st_mode))
                    os.utime(writer.fileno(), ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        finally:
            os.close(parent_fd)
