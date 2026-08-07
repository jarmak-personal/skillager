from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Iterator


DEFAULT_LOCK_TIMEOUT = 5.0
LOCK_POLL_INTERVAL = 0.025


class ResourceLockTimeout(TimeoutError):
    """Raised when a Skillager resource cannot be locked within the deadline."""

    def __init__(self, resource: Path, timeout: float) -> None:
        self.resource = resource
        self.timeout = timeout
        super().__init__(f"timed out after {timeout:.3f}s waiting for Skillager resource lock: {resource}")


def lock_path_for(resource: Path) -> Path:
    canonical = resource.expanduser().resolve()
    label = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in canonical.name)[:48] or "resource"
    digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:16]
    return canonical.parent / ".skillager-locks" / f"{label}-{digest}.lock"


@contextlib.contextmanager
def resource_lock(resource: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    canonical = resource.expanduser().resolve()
    lock_path = lock_path_for(canonical)
    lock_dir = lock_path.parent
    _ensure_private_lock_dir(lock_dir)
    with _open_lock_file(lock_path) as handle:
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            if _try_lock(handle):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResourceLockTimeout(canonical, timeout)
            time.sleep(min(LOCK_POLL_INTERVAL, remaining))
        try:
            yield
        finally:
            _unlock(handle)


@contextlib.contextmanager
def resource_locks(resources: list[Path], *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    canonical = sorted({resource.expanduser().resolve() for resource in resources}, key=str)
    deadline = time.monotonic() + max(timeout, 0.0)
    with ExitStack() as stack:
        for resource in canonical:
            remaining = max(0.0, deadline - time.monotonic())
            stack.enter_context(resource_lock(resource, timeout=remaining))
        yield


def _ensure_private_lock_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"refusing unsafe Skillager lock directory: {path}")
    with contextlib.suppress(OSError):
        path.chmod(0o700)


@contextlib.contextmanager
def _open_lock_file(path: Path):
    if path.is_symlink():
        raise ValueError(f"refusing symlinked Skillager lock file: {path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"refusing non-file Skillager lock path: {path}")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise ValueError(f"refusing Skillager lock file owned by another user: {path}")
        with os.fdopen(descriptor, "r+b", closefd=True) as handle:
            descriptor = -1
            if info.st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            yield handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _try_lock(handle) -> bool:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI.
        import msvcrt

        handle.seek(0)
        try:
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        except OSError:
            return False
        return True

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - unsupported platform guard.
        raise RuntimeError("Skillager resource locking is unavailable on this platform") from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(handle) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI.
        import msvcrt

        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "DEFAULT_LOCK_TIMEOUT",
    "ResourceLockTimeout",
    "lock_path_for",
    "resource_lock",
    "resource_locks",
]
