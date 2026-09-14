"""The closed, bounded public request for one local exposure lifecycle action."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from ..library.model import normalize_library_id, normalize_skill_name


REQUEST_SCHEMA = "skillager.exposure-request.v1"
PLAN_SCHEMA = "skillager.exposure-plan.v1"
REQUEST_BYTES = 64 * 1024
MAX_MEMBERS = 64
MAX_TARGETS = 128
MAX_EFFECTS = 2048
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_STAGED_BYTES = 128 * 1024 * 1024
MAX_REASON_BYTES = 32 * 1024


class PlanRefusal(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)



def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def encode_plan_output(value: Any) -> str:
    """One public serializer, including the emitted newline, for admission and CLI."""
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def public_refusal_reason(error: Exception) -> str:
    if isinstance(error, PlanRefusal) and len(json.dumps(str(error)).encode("utf-8")) <= MAX_REASON_BYTES:
        return str(error)
    return "The selected source or target state could not be verified; inspect its metadata and request a fresh preview."


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise PlanRefusal("invalid-request", "Exposure request fields do not match the selected action")
    return value


def _text(value: Any, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value):
        raise PlanRefusal("invalid-request", "Exposure identity is missing, oversized or contains control characters")
    return value


def exposure_id(value: Any) -> str:
    text = _text(value)
    if text.startswith("-") or text in {".", ".."} or any(c.isspace() or c in "/\\" for c in text):
        raise PlanRefusal("invalid-request", "Exposure identity must be one safe path segment")
    return text


def _origin_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise PlanRefusal("invalid-request", "Native origin must be an exact public occurrence identity")
    return value


def library_skill(value: Any) -> str:
    text = _text(value, 68)
    try:
        normalized = normalize_skill_name(text)
    except ValueError as error:
        raise PlanRefusal("invalid-request", str(error)) from error
    if not text.startswith("lib/") or text != f"lib/{normalized}":
        raise PlanRefusal("invalid-request", "Member must be an exact canonical lib/ skill identity")
    return text


def _library_id(value: Any) -> str:
    text = _text(value, 36)
    try:
        normalized = normalize_library_id(text)
    except ValueError as error:
        raise PlanRefusal("invalid-request", str(error)) from error
    if normalized != text:
        raise PlanRefusal("invalid-request", "Library identity must be a canonical UUID")
    return text


def _members(value: Any) -> None:
    if not isinstance(value, list) or len(value) > MAX_MEMBERS:
        raise PlanRefusal("request-limit", "Exposure actions support at most 64 members")
    ids = [library_skill(item) for item in value]
    if len(ids) != len(set(ids)):
        raise PlanRefusal("invalid-request", "Duplicate router members are not allowed")


def _replacements(value: Any) -> None:
    if not isinstance(value, list) or len(value) > MAX_TARGETS:
        raise PlanRefusal("request-limit", "Exposure actions support at most 128 selected targets")
    for item in value:
        if not isinstance(item, dict) or len(item) != 1:
            raise PlanRefusal("invalid-request", "A replacement selects one native origin or managed exposure")
        if "origin_id" in item:
            _origin_id(item["origin_id"])
        elif "exposure_id" in item:
            exposure_id(item["exposure_id"])
        else:
            raise PlanRefusal("invalid-request", "Unknown replacement selector")
    if len({canonical_json(item) for item in value}) != len(value):
        raise PlanRefusal("invalid-request", "Duplicate replacement selectors are not allowed")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanRefusal("invalid-request", "Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def parse_request(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8", errors="surrogatepass")) > REQUEST_BYTES:
        raise PlanRefusal("request-limit", "Exposure request exceeds 64 KiB")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, RecursionError) as error:
        raise PlanRefusal("invalid-request", "Exposure request must be valid bounded JSON") from error
    if not isinstance(value, dict) or value.get("schema") != REQUEST_SCHEMA:
        raise PlanRefusal("unsupported-request", "Unsupported exposure request schema")
    common = {"schema", "action"}
    action = _text(value.get("action"))
    if action in {"adopt-native", "remove-native"}:
        _object(value, common | {"origin_id", "source"} | ({"mode"} if action == "adopt-native" else set()))
        _origin_id(value["origin_id"])
        source = _object(value["source"], {"library_id", "skill_id"})
        _library_id(source["library_id"])
        library_skill(source["skill_id"])
        if action == "adopt-native" and _text(value["mode"]) not in {"native", "stub"}:
            raise PlanRefusal("invalid-request", "Native adoption requires native or stub mode")
    elif action in {"group", "set-members"}:
        _object(value, common | {"members", "library_id", "replace"} | ({"name"} if action == "group" else {"router_id", "departures"}))
        _library_id(value["library_id"])
        _members(value["members"])
        _replacements(value["replace"])
        if action == "group":
            _text(value["name"], 128)
            if not value["name"].strip() or not value["members"]:
                raise PlanRefusal("invalid-request", "A new router needs a name and at least one member")
        else:
            exposure_id(value["router_id"])
            departures = value["departures"]
            if not isinstance(departures, list) or len(departures) > MAX_MEMBERS:
                raise PlanRefusal("request-limit", "Exposure actions support at most 64 departures")
            for item in departures:
                _object(item, {"skill_id", "mode"})
                library_skill(item["skill_id"])
                if _text(item["mode"]) not in {"native", "stub", "remove"}:
                    raise PlanRefusal("invalid-request", "A departure must choose native, stub or remove")
            if len({item["skill_id"] for item in departures}) != len(departures):
                raise PlanRefusal("invalid-request", "Duplicate departing members are not allowed")
    elif action == "ungroup":
        _object(value, common | {"router_id", "mode"})
        exposure_id(value["router_id"])
        if _text(value["mode"]) not in {"native", "stub"}:
            raise PlanRefusal("invalid-request", "Ungroup requires native or stub mode")
    else:
        raise PlanRefusal("unsupported-request", "Unsupported exposure lifecycle action")
    return value
