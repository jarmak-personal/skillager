from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from ..state.library_approval import derive_library_approvals, public_approval
from ..state.locking import resource_locks
from ..skills.tree import ContentTreeLimits, iter_content_files
from .candidate import prepare_library_candidate
from .git import commit_paths, path_changes, repository_status, verified_version_references
from .importing import _source_key
from .metadata import load_library_provenance, write_library_provenance
from .service import _require_safe_git_mutation
from .sync_lineage import canonical_approval_key, canonical_target_state, identity


COPY_LIMITS = ContentTreeLimits(files=512, file_bytes=8 * 1024 * 1024, tree_bytes=32 * 1024 * 1024)
CHUNK_SKILLS = 128
CHUNK_BYTES = 256 * 1024 * 1024


def _publish(candidate: Path, target: Path, backup: Path, previous: dict[str, Any] | None) -> None:
    if previous is not None:
        if canonical_target_state(target) != previous:
            raise ValueError("sync destination changed during preparation")
        os.replace(target, backup)
        if canonical_target_state(backup) != previous:
            raise ValueError("sync detached destination changed")
    elif target.exists() or target.is_symlink():
        raise ValueError("sync destination appeared during preparation")
    # Reuse the established exposure reservation mechanics: never replace a
    # populated target created while the previous directory was detached.
    target.mkdir()
    try:
        os.replace(candidate, target)
    except Exception:
        try:
            target.rmdir()
        except OSError:
            pass
        raise


def _restore(backup: Path, target: Path) -> bool:
    if not backup.exists():
        return True
    try:
        target.mkdir()
        os.replace(backup, target)
        return True
    except OSError:
        return False


def apply_sync_chunks(
    selected: list[dict[str, Any]], project: Path, catalog: Path, binding,
    provenance: dict[str, Any], records: dict[Path, dict[str, Any]], *,
    expected_library_id: str, expected_library_root: Path, deadline: float,
) -> None:
    from .sync import library_binding
    registration, library_identity = binding
    layout = registration.layout
    offset = 0
    while offset < len(selected):
        if time.monotonic() >= deadline:
            for value in selected[offset:]:
                value["item"].update(reason_code="time-limit")
            break
        chunk = selected[offset:offset + CHUNK_SKILLS]
        offset += len(chunk)
        resources = [catalog / "library-mutation", *(catalog / f"library-skill-{item['name']}" for item in chunk)]
        with resource_locks(resources):
            library_binding(catalog, expected_library_id, expected_library_root)
            if load_library_provenance(layout) != provenance:
                for value in chunk:
                    value["item"].update(outcome="conflict", reason_code="provenance-changed", repair="resolve-conflict")
                continue
            git = repository_status(layout.root, mode=library_identity.git_mode)
            _require_safe_git_mutation(git, allow_target_staged=False)
            if library_identity.git_mode == "system" and any(path_changes(git, layout.root, layout.provenance_path).values()):
                raise ValueError("library sync preserves modified provenance")
            temp_root = Path(tempfile.mkdtemp(prefix=".skillager-sync-", dir=layout.root.parent))
            retain = False
            ready: list[dict[str, Any]] = []
            staged_bytes = 0
            try:
                for value in chunk:
                    item, name = value["item"], value["name"]
                    candidate = temp_root / name
                    try:
                        previous_bytes = sum(path.stat().st_size for path in iter_content_files(layout.skill_root(name))) if value["previous"] else 0
                        if ready and staged_bytes + previous_bytes + COPY_LIMITS.tree_bytes > CHUNK_BYTES:
                            offset -= len(chunk) - chunk.index(value)
                            break
                        copied, entry = prepare_library_candidate(Path(value["source"]["root"]), candidate,
                            layout, registration.library_id, name, value["source"]["content_hash"], limits=COPY_LIMITS)
                        size = sum((candidate / path).stat().st_size for path in copied)
                        staged_bytes += size + previous_bytes
                        state = canonical_target_state(candidate)
                        key = canonical_approval_key(registration.library_id, name)
                        lineage = {"schema": "skillager.library-sync-lineage.v1",
                            "lineage_id": identity("lineage", value["item"]["source_identity"], registration.library_id, name),
                            "source_identity": item["source_identity"], "source_key": _source_key(value["source"]),
                            "source_approval": public_approval(value["witness"]), "origins": value["origins"], "target_state": state}
                        item.update(phase="prepared", lineage_id=lineage["lineage_id"])
                        value.update(candidate=entry, candidate_path=candidate, target=layout.skill_root(name),
                                     backup=temp_root / f"{name}.previous", approval_key=key, lineage=lineage,
                                     previous_target_state=provenance["skills"].get(name, {}).get("sync", {}).get("target_state"),
                                     previous_protections={authority: records[authority].get("skills", {}).get(f"lib/{name}") for authority in {project, catalog}},
                                     previous_approval=records[catalog].get("global_approvals", {}).get(key))
                        ready.append(value)
                    except (OSError, ValueError):
                        item.update(outcome="failed", reason_code="candidate-refused", repair="observe")
                        try:
                            if candidate.exists():
                                shutil.rmtree(candidate)
                        except OSError:
                            retain = True
                            item.update(outcome="uncertain", phase="prepared", reason_code="staging-cleanup-incomplete", recovery_path=str(candidate))
                            offset -= len(chunk) - chunk.index(value) - 1
                            break
                if not ready:
                    if retain:
                        for value in selected[offset:]:
                            value["item"].update(reason_code="recovery-required", repair="observe")
                        return
                    continue
                def publish_verified_chunk() -> None:
                    nonlocal provenance
                    library_binding(catalog, expected_library_id, expected_library_root)
                    if load_library_provenance(layout) != provenance:
                        raise ValueError("library sync provenance changed during preparation")
                    updated = {**provenance, "skills": {**provenance["skills"]}}
                    for value in ready:
                        _publish(value["candidate_path"], value["target"], value["backup"], value["previous_target_state"])
                        value["item"]["phase"] = "published"
                        updated["skills"][value["name"]] = {"imported_from": {
                            "skill_id": value["source"]["id"], "content_hash": value["source"]["content_hash"],
                            "source_type": value["source"]["source"].get("type", "unknown")}, "sync": value["lineage"]}
                    write_library_provenance(layout, updated)
                    provenance = updated
                    if library_identity.git_mode == "system":
                        commit_paths(layout.root, [*(value["target"] for value in ready), layout.provenance_path],
                                     f"Sync {len(ready)} approved library skills")
                        for value in ready:
                            value["item"]["phase"] = "committed"
                    versions = verified_version_references(layout.root,
                        [(value["target"], value["approval_key"], value["source"]["content_hash"]) for value in ready], mode=library_identity.git_mode)
                    library_binding(catalog, expected_library_id, expected_library_root)
                    for value in ready:
                        value["version"] = versions[value["approval_key"]]
                        if canonical_target_state(value["target"]) != value["lineage"]["target_state"]:
                            raise ValueError("published sync target changed before acceptance")
                derive_library_approvals(project, catalog, ready, publish_verified_chunk)
                for value in ready:
                    value["item"].update(outcome="updated" if value["previous"] else "created", phase="accepted",
                                         accepted_hash=value["source"]["content_hash"], reason_code=None)
            except Exception:
                for value in ready:
                    item = value["item"]
                    if item["phase"] == "accepted":
                        continue
                    item.update(outcome="failed", reason_code="sync-incomplete",
                                repair="accept-pending" if item["phase"] in {"published", "committed"} else "observe")
                    if value["backup"].exists():
                        if item["phase"] == "prepared" and _restore(value["backup"], value["target"]):
                            continue
                        if item["phase"] == "prepared":
                            item.update(outcome="uncertain", phase="unknown", repair="observe")
                        retain = True
                        item["recovery_path"] = str(value["backup"])
            finally:
                for value in ready:
                    if value["item"]["phase"] == "accepted" and value["backup"].exists():
                        try:
                            if canonical_target_state(value["backup"]) != value["previous_target_state"]:
                                raise ValueError("detached previous copy changed after publication")
                        except (OSError, ValueError):
                            retain = True
                            value["item"].update(outcome="conflict", reason_code="previous-copy-changed", repair="resolve-conflict")
                            value["item"]["recovery_path"] = str(value["backup"])
                if not retain:
                    try:
                        shutil.rmtree(temp_root)
                    except OSError:
                        for value in ready:
                            value["item"].update(recovery_path=str(temp_root), repair="observe")
                        raise
        if retain:
            for value in selected[offset:]:
                value["item"].update(reason_code="recovery-required", repair="observe")
            break
