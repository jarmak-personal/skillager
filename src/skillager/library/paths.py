from __future__ import annotations

from pathlib import Path

from ..catalog.impl import load_collections
from .model import LIBRARY_NAMESPACE, LibraryRegistration


def default_library_root() -> Path:
    return (Path.home() / ".skillager" / "library").resolve()


def load_library_registration(catalog_root: Path) -> LibraryRegistration | None:
    value = load_collections(catalog_root).get("collections", {}).get(LIBRARY_NAMESPACE)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid reserved library registration")
    return LibraryRegistration.from_mapping(value)


def require_library_registration(catalog_root: Path) -> LibraryRegistration:
    registration = load_library_registration(catalog_root)
    if registration is None:
        raise ValueError("personal skill library is not initialized; run `skillager library init`")
    return registration
