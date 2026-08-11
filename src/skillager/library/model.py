from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


LIBRARY_SCHEMA = "skillager.library.v1"
LIBRARY_NAMESPACE = "lib"
LIBRARY_COLLECTION_KIND = "library"
LIBRARY_GIT_MODES = {"system", "disabled"}


def normalize_skill_name(value: str) -> str:
    candidate = value.strip()
    prefix = f"{LIBRARY_NAMESPACE}/"
    if candidate.startswith(prefix):
        candidate = candidate[len(prefix) :]
    if "/" in candidate or "\\" in candidate:
        raise ValueError("library skill names must contain exactly one path component")
    slug = "".join(char if char.isalnum() else "-" for char in candidate.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    if not slug:
        raise ValueError("library skill name must contain at least one alphanumeric character")
    if len(slug) > 64:
        raise ValueError("library skill name must be 64 characters or fewer")
    return slug


def normalize_library_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("library_id must be a UUID") from exc


@dataclass(frozen=True)
class LibraryLayout:
    root: Path

    @classmethod
    def from_root(cls, root: Path) -> LibraryLayout:
        resolved = root.expanduser().resolve()
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"library root is not a directory: {resolved}")
        return cls(root=resolved)

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def metadata(self) -> Path:
        return self.root / ".skillager"

    @property
    def identity_path(self) -> Path:
        return self.metadata / "library.json"

    @property
    def provenance_path(self) -> Path:
        return self.metadata / "provenance.json"

    def skill_root(self, name: str) -> Path:
        target = self.skills / normalize_skill_name(name)
        if self.skills.is_symlink() or (self.skills.exists() and not self.skills.is_dir()):
            raise ValueError(f"library skills path must be a non-symlinked directory: {self.skills}")
        if target.is_symlink():
            resolved = target.resolve()
            try:
                resolved.relative_to(self.skills.resolve())
            except ValueError as exc:
                raise ValueError(f"library skill path escapes the library: {target}") from exc
            raise ValueError(f"library skill path must not be a symlink alias: {target}")
        if target.exists() and not target.is_dir():
            raise ValueError(f"library skill path must be a directory: {target}")
        try:
            target.resolve().relative_to(self.skills.resolve())
        except ValueError as exc:  # Defensive guard if name validation changes.
            raise ValueError(f"library skill path escapes the library: {target}") from exc
        return target


@dataclass(frozen=True)
class LibraryIdentity:
    library_id: str
    git_mode: str
    created_at: str
    namespace: str = LIBRARY_NAMESPACE
    schema: str = LIBRARY_SCHEMA

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> LibraryIdentity:
        if value.get("schema") != LIBRARY_SCHEMA:
            raise ValueError(f"unsupported library schema: {value.get('schema')!r}")
        if value.get("namespace") != LIBRARY_NAMESPACE:
            raise ValueError(f"library namespace must be {LIBRARY_NAMESPACE!r}")
        git = value.get("git")
        git_mode = git.get("mode") if isinstance(git, dict) else None
        if git_mode not in LIBRARY_GIT_MODES:
            raise ValueError(f"unsupported library git mode: {git_mode!r}")
        created_at = value.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("library created_at must be a non-empty string")
        return cls(
            library_id=normalize_library_id(value.get("library_id")),
            git_mode=git_mode,
            created_at=created_at,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "library_id": self.library_id,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "git": {"mode": self.git_mode},
        }


@dataclass(frozen=True)
class LibraryRegistration:
    library_id: str
    layout: LibraryLayout

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> LibraryRegistration:
        if value.get("name") != LIBRARY_NAMESPACE or value.get("kind") != LIBRARY_COLLECTION_KIND:
            raise ValueError("catalog entry is not the reserved Skillager library")
        library_id = normalize_library_id(value.get("library_id"))
        raw_root = value.get("library_root")
        raw_skills = value.get("path")
        if not isinstance(raw_root, str) or not isinstance(raw_skills, str):
            raise ValueError("library registration requires library_root and path")
        layout = LibraryLayout.from_root(Path(raw_root))
        registered_skills = Path(raw_skills).expanduser().resolve()
        if registered_skills != layout.skills.resolve():
            raise ValueError("library registration path must be <library_root>/skills")
        return cls(library_id=library_id, layout=layout)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": LIBRARY_NAMESPACE,
            "path": str(self.layout.skills.resolve()),
            "kind": LIBRARY_COLLECTION_KIND,
            "library_id": self.library_id,
            "library_root": str(self.layout.root),
        }
