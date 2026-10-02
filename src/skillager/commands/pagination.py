from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any


STALE_CURSOR_EXIT = 15
CURSOR_SCHEMA = "skillager.cursor.v1"


class CursorError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = STALE_CURSOR_EXIT if code == "stale_cursor" else 2

    def payload(self) -> dict[str, Any]:
        return {"schema": "skillager.error.v1", "error": {"code": self.code, "message": str(self)}}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def page_metadata(
    rows: list[dict[str, Any]], *, snapshot: str, request: dict[str, Any],
    limit: int, cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Page one verified ordered inventory; cursors carry no approval authority."""
    if limit <= 0:
        raise ValueError("--limit must be greater than 0 for paginated results")
    request_hash = fingerprint(request)
    offset = 0
    if cursor not in (None, ""):
        assert cursor is not None
        decoded = _decode_cursor(cursor)
        if decoded["request"] != request_hash:
            raise CursorError("invalid_cursor", "cursor belongs to a different request")
        if decoded["snapshot"] != snapshot:
            raise CursorError("stale_cursor", "inventory changed; restart without --cursor")
        offset = decoded["offset"]
        if offset >= len(rows):
            raise CursorError("invalid_cursor", "cursor offset is outside the inventory")
    end = offset + limit
    next_cursor = None
    if end < len(rows):
        payload = {"schema": CURSOR_SCHEMA, "request": request_hash, "snapshot": snapshot, "offset": end}
        next_cursor = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return rows[offset:end], next_cursor


def _decode_cursor(token: str) -> dict[str, Any]:
    try:
        if not token or len(token) > 4096:
            raise ValueError("invalid cursor length")
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "request", "snapshot", "offset"}
            or value["schema"] != CURSOR_SCHEMA
            or type(value["offset"]) is not int
            or value["offset"] <= 0
            or any(not isinstance(value[key], str) or len(value[key]) != 64 for key in ("request", "snapshot"))
        ):
            raise ValueError("invalid cursor shape")
        return value
    except (ValueError, UnicodeDecodeError, binascii.Error, RecursionError) as exc:
        raise CursorError("invalid_cursor", "invalid paging cursor") from exc
