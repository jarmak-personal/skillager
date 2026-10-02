"""Read-only acceptance observation and standalone, verified native Full export."""
from __future__ import annotations

import contextlib
import os
import re
import stat
from pathlib import Path
from typing import Any

from ..catalog.impl import select_collection_skills
from ..compatibility import compatibility_problem
from ..exposure.impl import prepare_direct_candidate, _verify_materialized_projection
from ..exposure.target_state import target_state_manifest
from ..skills.tree import iter_content_files
from ..state import approvals
from ..trust import APPROVED_TRUST_STATES, approval_key_for, content_hash
from .metadata import load_library_provenance
from .model import normalize_skill_name
from .service import _require_library_identity


class ExportRefusal(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _observe(catalog: Path, skill_id: str, version: str, agent: str) -> tuple[dict[str, Any], dict[str, Any]]:
    registration, identity = _require_library_identity(catalog)
    name = normalize_skill_name(skill_id)
    source = registration.layout.skill_root(name)
    skills = select_collection_skills(catalog, "lib", trust_root=catalog, approval_root=catalog,
                                     include_blocked=True, include_lint_blocked=True)
    skill = next((item for item in skills if item["id"] == skill_id), None)
    if skill is None:
        raise ExportRefusal("skill_not_found", "the owned library skill does not exist")
    key = approval_key_for(skill_id, source, skill["source"], entrypoint=skill.get("entrypoint"))
    assert key is not None
    record = approvals.get_record(catalog, "global_approvals", key) or {}
    local = approvals.get_record(catalog, "skills", skill_id)
    if skill.get("trust") == "blocked":
        raise ExportRefusal("blocked_content", "the selected skill is blocked")
    if skill.get("trust") == "lint_blocked":
        raise ExportRefusal("lint_blocked_content", "the selected skill is lint quarantined")
    accepted = record.get("content_hash") if record.get("state") in APPROVED_TRUST_STATES else None
    if version == skill["content_hash"] and (accepted != version or skill.get("trust") not in APPROVED_TRUST_STATES):
        raise ExportRefusal("pending_content", "the current skill has not been accepted")
    if not approvals.version_was_accepted(catalog, key, version):
        raise ExportRefusal("version_not_accepted", "the requested version has no accepted-version evidence")
    if skill["content_hash"] != version:
        code = "changed_content" if version == accepted else "version_content_mismatch"
        raise ExportRefusal(code, "the current source bytes do not match the requested accepted version")
    if accepted != version or skill.get("trust") not in APPROVED_TRUST_STATES:
        raise ExportRefusal("pending_content", "the current version does not have a current accepted decision")
    if compatibility_problem(skill, agent):
        raise ExportRefusal("incompatible_agent", "the selected skill explicitly excludes this agent")
    return skill, {"registration": registration.to_mapping(), "identity": identity.to_mapping(),
                   "provenance": load_library_provenance(registration.layout), "approval": record,
                   "local_approval": local,
                   "files": {path.relative_to(source).as_posix(): stat.S_IMODE(path.stat().st_mode)
                             for path in iter_content_files(source)}}


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _open_directory(path: Path, *, identities: set[tuple[int, int]] | None = None) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if identities is not None:
            identities.add(_identity(os.fstat(descriptor)))
        for name in path.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
            if identities is not None:
                identities.add(_identity(os.fstat(descriptor)))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _remove_owned(directory_fd: int, entries: dict[str, tuple[int, int]]) -> None:
    # Never follow a replaced parent or remove an entry with a different identity.
    for relative, expected in sorted(entries.items(), key=lambda item: len(Path(item[0]).parts), reverse=True):
        descriptor = os.dup(directory_fd)
        try:
            parts = Path(relative).parts
            for index, name in enumerate(parts[:-1]):
                key = Path(*parts[:index + 1]).as_posix()
                next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                if _identity(os.fstat(next_fd)) != entries.get(key):
                    os.close(next_fd)
                    raise OSError("export cleanup parent changed")
                os.close(descriptor)
                descriptor = next_fd
            current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
            if _identity(current) == expected:
                if stat.S_ISDIR(current.st_mode):
                    os.rmdir(parts[-1], dir_fd=descriptor)
                else:
                    os.unlink(parts[-1], dir_fd=descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def export_skill(catalog: Path, skill_id: str, *, version: str, agent: str, destination: Path) -> dict[str, Any]:
    try:
        canonical_id = f"lib/{normalize_skill_name(skill_id)}"
    except ValueError as error:
        raise ExportRefusal("invalid_request", "export requires one canonical owned library skill ID") from error
    if skill_id != canonical_id or not re.fullmatch(r"[0-9a-f]{64}", version) or agent not in {"codex", "claude"}:
        raise ExportRefusal("invalid_request", "export requires a canonical lib/skill ID, full content hash, and explicit agent")
    if ".." in destination.parts or destination == Path(destination.anchor):
        raise ExportRefusal("unsafe_destination", "the destination must be a contained directory path without parent traversal")
    destination = destination.expanduser().absolute()
    catalog = catalog.resolve()
    skill, observed = _observe(catalog, skill_id, version, agent)
    library = Path(observed["registration"]["library_root"])
    for protected in (catalog, library):
        if destination == protected or destination in protected.parents or protected in destination.parents:
            raise ExportRefusal("unsafe_destination", "the export destination must not overlap library or catalog state")
    protected_roots: set[tuple[int, int]] = set()
    protected_ancestors: set[tuple[int, int]] = set()
    for protected in (catalog, library):
        protected_fd = _open_directory(protected, identities=protected_ancestors)
        try:
            protected_roots.add(_identity(os.fstat(protected_fd)))
        finally:
            os.close(protected_fd)
    parent_ancestors: set[tuple[int, int]] = set()
    try:
        parent_fd = _open_directory(destination.parent, identities=parent_ancestors)
    except OSError as error:
        raise ExportRefusal("unsafe_destination", "the existing parent must have only non-symlink directory components") from error
    destination_fd = None
    destination_identity = None
    created_destination = False
    published: dict[str, tuple[int, int]] = {}
    completed = False
    try:
        if parent_ancestors & protected_roots:
            raise ExportRefusal("unsafe_destination", "the export destination must not overlap library or catalog state")
        try:
            os.mkdir(destination.name, dir_fd=parent_fd)
            created_destination = True
        except FileExistsError:
            pass
        try:
            destination_fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as error:
            raise ExportRefusal("unsafe_destination", "the destination must be a non-symlink directory") from error
        destination_identity = _identity(os.fstat(destination_fd))
        if destination_identity in protected_ancestors:
            raise ExportRefusal("unsafe_destination", "the export destination must not overlap library or catalog state")
        if os.listdir(destination_fd):
            raise ExportRefusal("destination_not_empty", "the existing destination must be empty")

        def require_destination() -> None:
            try:
                check_fd = _open_directory(destination)
                try:
                    if _identity(os.fstat(check_fd)) != destination_identity:
                        raise OSError("directory identity changed")
                finally:
                    os.close(check_fd)
            except OSError as error:
                raise ExportRefusal("destination_changed", "the destination changed during export") from error

        require_destination()
        prepare_direct_candidate(skill, candidate=destination, target=destination, agent=agent,
                                 scope="export", mode="native", decisions={},
                                 candidate_fd=destination_fd, owned_entries=published)
        require_destination()
        manifest = target_state_manifest(destination)
        if set(manifest) != set(published):
            raise ExportRefusal("destination_changed", "another writer changed the empty destination")
        fresh, current = _observe(catalog, skill_id, version, agent)
        if current != observed or fresh["content_hash"] != version:
            raise ExportRefusal("source_changed", "source identity or acceptance changed during preparation")
        require_destination()
        _verify_materialized_projection(destination, expected_hash=version)
        final_manifest = target_state_manifest(destination)
        if final_manifest != manifest or content_hash(destination) != version:
            raise ExportRefusal("verification_failed", "the exported bytes, modes, or provenance changed")
        if any(_identity(os.stat(relative, dir_fd=destination_fd, follow_symlinks=False)) != expected
               for relative, expected in published.items()):
            raise ExportRefusal("destination_changed", "created destination objects were replaced during export")
        _fresh, current = _observe(catalog, skill_id, version, agent)
        if current != observed:
            raise ExportRefusal("source_changed", "source identity or acceptance changed before completion")
        require_destination()
        completed = True
        return {"schema": "skillager.export.v1", "status": "exported", "id": skill_id,
                "library_id": observed["identity"]["library_id"], "agent": agent, "scope": "export",
                "destination": str(destination), "content_hash": version,
                "files": [{"path": relative, "mode": item["mode"], "size": item["size"], "sha256": item["sha256"]}
                          for relative, item in sorted(final_manifest.items()) if item["type"] == "file"]}
    except ExportRefusal:
        raise
    except (OSError, ValueError) as error:
        raise ExportRefusal("export_changed", "source, destination, or prepared payload changed during export") from error
    finally:
        if not completed and destination_fd is not None:
            _remove_owned(destination_fd, published)
        if destination_fd is not None:
            os.close(destination_fd)
            if created_destination and not completed:
                with contextlib.suppress(OSError):
                    if destination_identity is not None and _identity(os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)) == destination_identity:
                        os.rmdir(destination.name, dir_fd=parent_fd)
        os.close(parent_fd)
