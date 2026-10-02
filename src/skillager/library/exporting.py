"""Read-only acceptance observation and standalone, verified native Full export."""
from __future__ import annotations

import contextlib
import os
import re
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..catalog.impl import select_collection_skills
from ..compatibility import compatibility_problem
from ..exposure.impl import prepare_direct_candidate, _verify_materialized_projection
from ..exposure.native_payload import copy_native_tree_to_fd
from ..exposure.target_state import target_state_manifest, write_materialized_sidecar
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
    if accepted is None and version == skill["content_hash"]:
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


def _open_directory(path: Path) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
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
    try:
        parent_fd = _open_directory(destination.parent)
    except OSError as error:
        raise ExportRefusal("unsafe_destination", "the existing parent must have only non-symlink directory components") from error
    destination_fd = stage_fd = payload_fd = None
    destination_identity = stage_identity = payload_identity = None
    created_destination = False
    stage_name = f".skillager-export-{uuid4().hex}"
    staged: dict[str, tuple[int, int]] = {}
    published: dict[str, tuple[int, int]] = {}
    completed = False
    try:
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

        os.mkdir(stage_name, mode=0o700, dir_fd=destination_fd)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination_fd)
        stage_identity = _identity(os.fstat(stage_fd))
        os.mkdir("payload", dir_fd=stage_fd)
        payload_fd = os.open("payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
        payload_identity = _identity(os.fstat(payload_fd))
        payload = destination / stage_name / "payload"
        require_destination()
        metadata = prepare_direct_candidate(skill, candidate=payload, target=destination, agent=agent,
                                            scope="export", mode="native", decisions={},
                                            candidate_fd=payload_fd, owned_entries=staged)
        require_destination()
        _verify_materialized_projection(payload, expected_hash=version)
        manifest = target_state_manifest(payload)
        fresh, current = _observe(catalog, skill_id, version, agent)
        if current != observed or fresh["content_hash"] != version:
            raise ExportRefusal("source_changed", "source identity or acceptance changed during preparation")
        if os.listdir(destination_fd) != [stage_name]:
            raise ExportRefusal("destination_changed", "another writer changed the empty destination")
        require_destination()
        # Publication uses the same descriptor-bound native copier. Exclusive file
        # creation preserves anything another writer adds, including directories.
        copy_native_tree_to_fd(payload, destination_fd, owned_entries=published)
        write_materialized_sidecar(destination / "skillager.materialized.yaml", metadata,
                                   directory_fd=destination_fd, owned_entries=published)
        require_destination()
        _remove_owned(payload_fd, staged)
        if _identity(os.stat("payload", dir_fd=stage_fd, follow_symlinks=False)) == payload_identity:
            os.rmdir("payload", dir_fd=stage_fd)
        if _identity(os.stat(stage_name, dir_fd=destination_fd, follow_symlinks=False)) == stage_identity:
            os.rmdir(stage_name, dir_fd=destination_fd)
        require_destination()
        _verify_materialized_projection(destination, expected_hash=version)
        final_manifest = target_state_manifest(destination)
        if final_manifest != manifest or content_hash(destination) != version:
            raise ExportRefusal("verification_failed", "the exported bytes, modes, or provenance changed")
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
        if payload_fd is not None:
            _remove_owned(payload_fd, staged)
            os.close(payload_fd)
        if stage_fd is not None:
            with contextlib.suppress(OSError):
                if payload_identity is not None and _identity(os.stat("payload", dir_fd=stage_fd, follow_symlinks=False)) == payload_identity:
                    os.rmdir("payload", dir_fd=stage_fd)
            os.close(stage_fd)
        if destination_fd is not None:
            with contextlib.suppress(OSError):
                if stage_identity is not None and _identity(os.stat(stage_name, dir_fd=destination_fd, follow_symlinks=False)) == stage_identity:
                    os.rmdir(stage_name, dir_fd=destination_fd)
            os.close(destination_fd)
            if created_destination and not completed:
                with contextlib.suppress(OSError):
                    if destination_identity is not None and _identity(os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)) == destination_identity:
                        os.rmdir(destination.name, dir_fd=parent_fd)
        os.close(parent_fd)
