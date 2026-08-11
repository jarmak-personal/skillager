from __future__ import annotations

import hashlib
import json
from typing import Any


def confirmation_token(operation: str, **state: Any) -> str:
    """Return an opaque, deterministic token bound to one mutation preview."""
    payload = {"operation": operation, "state": state}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_confirmation_token(provided: str | None, expected: str, *, operation: str) -> None:
    if not provided:
        raise ValueError(
            f"{operation} requires the confirmation token from its current preview; "
            "rerun without --yes and execute the returned command"
        )
    if provided != expected:
        raise ValueError(
            f"{operation} preview is stale or does not match this command; "
            "review the current preview and execute its returned command"
        )


__all__ = ["confirmation_token", "require_confirmation_token"]
