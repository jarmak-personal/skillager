from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..catalog.impl import refresh_collection
from ..skills.tree import iter_content_files, require_canonical_content_tree
from ..state.locking import resource_locks
from ..trust import approval_key_for, content_hash, content_hash_entries, set_trust
from .candidate import index_library_candidate
from .git import (
    GitTreeFile,
    LibraryGitError,
    commit_paths,
    git_path_history,
    git_tree_files,
    repository_status,
)
from .model import LIBRARY_NAMESPACE, normalize_skill_name
from .service import (
    _acceptance_overrides,
    _compact_lint,
    _compact_scan,
    _history_availability,
    _require_library_identity,
    _require_safe_git_mutation,
    library_where,
)


LIBRARY_HISTORY_SCHEMA = "skillager.library-history.v1"
LIBRARY_DIFF_SCHEMA = "skillager.library-diff.v1"
LIBRARY_RESTORE_SCHEMA = "skillager.library-restore.v1"
HASH_PREFIX_PATTERN = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class _TreeEndpoint:
    kind: str
    label: str
    content_hash: str | None
    files: tuple[GitTreeFile, ...]
    commit: str | None = None


def library_history(
    catalog_root: Path,
    skill_name: str,
    *,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration, identity = _require_library_identity(catalog_root)
    where = library_where(catalog_root, skill_name, project_dir=project_dir)["skill"]
    git = repository_status(registration.layout.root, mode=identity.git_mode)
    availability = _history_availability(identity, git, registration.layout)
    if not availability["available"]:
        return {
            "schema": LIBRARY_HISTORY_SCHEMA,
            "status": "unavailable",
            "available": False,
            "reason": availability["reason"],
            "skill": _history_skill(where),
            "versions": [],
        }
    versions = _verified_history_versions(registration.layout.root, Path(where["path"]), where)
    return {
        "schema": LIBRARY_HISTORY_SCHEMA,
        "status": "ready",
        "available": True,
        "reason": None,
        "skill": _history_skill(where),
        "versions": versions,
    }


def resolve_history_version(versions: list[dict[str, Any]], value: str) -> dict[str, Any]:
    prefix = value.strip().lower()
    if not prefix or len(prefix) > 64 or HASH_PREFIX_PATTERN.fullmatch(prefix) is None:
        raise ValueError("version must be a Skillager content-hash prefix")
    matches = [version for version in versions if str(version["content_hash"]).startswith(prefix)]
    if not matches:
        raise ValueError(f"historical Skillager content hash not found: {value}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous Skillager content-hash prefix: {value}")
    return matches[0]


def library_diff(
    catalog_root: Path,
    skill_name: str,
    *,
    from_hash: str | None = None,
    to_hash: str | None = None,
    stat_only: bool = False,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration, _identity = _require_library_identity(catalog_root)
    history = library_history(catalog_root, skill_name, project_dir=project_dir)
    _require_available_history(history)
    versions = history["versions"]
    target = Path(history["skill"]["path"])
    if to_hash is None:
        to_endpoint = _working_endpoint(target, str(history["skill"]["working_hash"]))
    else:
        to_version = resolve_history_version(versions, to_hash)
        to_endpoint = _historical_endpoint(registration.layout.root, target, to_version)
    if from_hash is not None:
        from_version = resolve_history_version(versions, from_hash)
        from_endpoint = _historical_endpoint(registration.layout.root, target, from_version)
    elif to_hash is None:
        head_versions = [version for version in versions if version["head"]]
        if not head_versions:
            raise ValueError("library skill has no recoverable version at Git HEAD")
        from_endpoint = _historical_endpoint(registration.layout.root, target, head_versions[0])
    else:
        selected = resolve_history_version(versions, to_hash)
        index = versions.index(selected)
        from_endpoint = (
            _historical_endpoint(registration.layout.root, target, versions[index + 1])
            if index + 1 < len(versions)
            else _TreeEndpoint(kind="empty", label="empty", content_hash=None, files=())
        )
    stat = _tree_stat(from_endpoint, to_endpoint)
    return {
        "schema": LIBRARY_DIFF_SCHEMA,
        "status": "ready",
        "skill": history["skill"],
        "content_bearing": not stat_only,
        "from": _endpoint_metadata(from_endpoint),
        "to": _endpoint_metadata(to_endpoint),
        "stat": stat,
        "diff": None if stat_only else _unified_tree_diff(from_endpoint, to_endpoint),
    }


def library_restore_preview(
    catalog_root: Path,
    skill_name: str,
    to_hash: str,
    *,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    registration, _identity = _require_library_identity(catalog_root)
    history = library_history(catalog_root, skill_name, project_dir=project_dir)
    _require_available_history(history)
    version = resolve_history_version(history["versions"], to_hash)
    name = normalize_skill_name(skill_name)
    target = registration.layout.skill_root(name)
    require_canonical_content_tree(target, action="library restore")
    endpoint = _historical_endpoint(registration.layout.root, target, version)
    with tempfile.TemporaryDirectory(prefix="skillager-restore-preview-", dir=registration.layout.root.parent) as tmp:
        candidate = Path(tmp) / name
        _materialize_tree(endpoint.files, candidate)
        entry = index_library_candidate(candidate, registration.layout, registration.library_id, name)
        if entry["content_hash"] != version["content_hash"]:
            raise ValueError("historical candidate does not reproduce its verified Skillager content hash")
        lint = _compact_lint(entry.get("lint"))
        scan = _compact_scan(entry.get("scan"))
    current = _working_endpoint(target, str(history["skill"]["working_hash"]))
    already_current = current.content_hash == version["content_hash"]
    return {
        "schema": LIBRARY_RESTORE_SCHEMA,
        "status": "already-current" if already_current else "preview",
        "skill": history["skill"],
        "selected_version": version,
        "current_hash": current.content_hash,
        "_current_tree_fingerprint": _tree_fingerprint(current.files),
        "lint": lint,
        "scan": scan,
        "requires_override": lint["blocking_count"] > 0 or scan["risk"] == "high",
        "stat": _tree_stat(current, endpoint),
    }


def restore_library_skill(
    catalog_root: Path,
    skill_name: str,
    *,
    expected_hash: str,
    expected_commit: str,
    expected_current_hash: str,
    expected_current_fingerprint: str,
    override_lint: bool = False,
    reason: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    name = normalize_skill_name(skill_name)
    resources = [catalog_root / "library-mutation", catalog_root / f"library-skill-{name}"]
    with resource_locks(resources):
        registration, identity = _require_library_identity(catalog_root)
        history = library_history(catalog_root, name, project_dir=project_dir)
        _require_available_history(history)
        version = resolve_history_version(history["versions"], expected_hash)
        if version["content_hash"] != expected_hash or version["commit"] != expected_commit:
            raise ValueError("historical version changed since preview; review the restore again")
        current = library_where(catalog_root, name, project_dir=project_dir)["skill"]
        if current["working_hash"] != expected_current_hash:
            raise ValueError("library skill tree changed since restore preview; review it again")
        if current["working_hash"] == expected_hash:
            raise ValueError("library skill already matches the selected historical version")
        target = registration.layout.skill_root(name)
        require_canonical_content_tree(target, action="library restore")
        current_endpoint = _working_endpoint(target, expected_current_hash)
        if _tree_fingerprint(current_endpoint.files) != expected_current_fingerprint:
            raise ValueError("library skill tree changed since restore preview; review it again")
        git = repository_status(registration.layout.root, mode=identity.git_mode)
        availability = _history_availability(identity, git, registration.layout)
        if not availability["available"]:
            raise ValueError(f"library history is unavailable: {availability['reason']}")
        _require_safe_git_mutation(git, allow_target_staged=False)

        candidate_entry: dict[str, Any]
        with tempfile.TemporaryDirectory(prefix="skillager-restore-", dir=registration.layout.root.parent) as tmp:
            temp_root = Path(tmp)
            candidate = temp_root / name
            backup = temp_root / f"{name}.previous"
            files = git_tree_files(registration.layout.root, target, expected_commit)
            if _files_content_hash(files) != expected_hash:
                raise ValueError("historical version no longer reproduces the previewed Skillager content hash")
            _materialize_tree(tuple(files), candidate)
            candidate_entry = index_library_candidate(candidate, registration.layout, registration.library_id, name)
            if candidate_entry["content_hash"] != expected_hash or content_hash(candidate) != expected_hash:
                raise ValueError("restored candidate does not reproduce the selected Skillager content hash")
            lint_override, risk_override = _acceptance_overrides(
                candidate_entry,
                override_lint=override_lint,
                reason=reason,
            )
            require_canonical_content_tree(target, action="library restore")
            final_current = _working_endpoint(target, expected_current_hash)
            if _tree_fingerprint(final_current.files) != expected_current_fingerprint:
                raise ValueError("library skill changed while restore was being prepared; no files were written")
            os.replace(target, backup)
            try:
                os.replace(candidate, target)
            except Exception:
                os.replace(backup, target)
                raise

        try:
            commit = commit_paths(
                registration.layout.root,
                [target],
                f"Restore library skill {name} to {expected_hash[:12]}",
            )
        except LibraryGitError as exc:
            raise ValueError(
                f"{exc}; restored content remains pending. Fix Git, then run "
                f"`skillager library accept lib/{name} --json` and execute its returned command"
            ) from exc
        approval_key = approval_key_for(
            f"{LIBRARY_NAMESPACE}/{name}",
            target,
            candidate_entry["source"],
            entrypoint=target / "SKILL.md",
        )
        if not approval_key:
            raise ValueError("restored library skill is missing a stable approval key")
        try:
            record = set_trust(
                catalog_root,
                f"{LIBRARY_NAMESPACE}/{name}",
                "reviewed",
                expected_hash,
                candidate_entry["source"],
                lint=candidate_entry.get("lint"),
                lint_override=lint_override,
                risk_override=risk_override,
                reason=(reason or "").strip() or None,
                approval_key=approval_key,
                approval_root=catalog_root,
                global_scope=True,
            )
        except Exception as exc:
            raise ValueError(
                f"restored content is committed but pending acceptance: {exc}; repair with "
                f"`skillager library accept lib/{name} --json` and execute its returned command"
            ) from exc
        refresh_collection(catalog_root, LIBRARY_NAMESPACE)
        where = library_where(catalog_root, name, project_dir=project_dir)["skill"]
        restored_versions = _verified_history_versions(registration.layout.root, target, where)
        restored_version = next(
            (item for item in restored_versions if item.get("commit") == commit.get("commit")),
            None,
        )
        if restored_version is None:
            raise ValueError("restored content was committed but its new HEAD version could not be verified")
        return {
            "schema": LIBRARY_RESTORE_SCHEMA,
            "status": "restored",
            "skill": where,
            "restored_version": restored_version,
            "commit": commit,
            "approval": {
                "state": record["state"],
                "scope": record["scope"],
                "content_hash": record["content_hash"],
                "lint_override": record.get("lint_override"),
                "risk_override": record.get("risk_override"),
            },
        }


def _verified_history_versions(root: Path, target: Path, where: dict[str, Any]) -> list[dict[str, Any]]:
    commits = git_path_history(root, target)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for commit in commits:
        files = git_tree_files(root, target, commit["commit"])
        if not files:
            continue
        if not any(file.path == "SKILL.md" for file in files):
            raise ValueError(f"historical library version has no regular SKILL.md: {commit['commit']}")
        digest = _files_content_hash(files)
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(
            {
                "content_hash": digest,
                "commit": commit["commit"],
                "committed_at": commit["committed_at"],
                "operation": _known_operation(commit["subject"]),
                "file_count": len(files),
                "head": digest == where.get("head_hash"),
                "current": digest == where.get("working_hash"),
                "accepted": digest == where.get("accepted_hash"),
            }
        )
    short_hashes = _unique_short_hashes([str(version["content_hash"]) for version in unique])
    for version in unique:
        version["short_hash"] = short_hashes[str(version["content_hash"])]
    return unique


def _unique_short_hashes(hashes: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for digest in hashes:
        width = 12
        while width < len(digest) and sum(other.startswith(digest[:width]) for other in hashes) > 1:
            width += 1
        result[digest] = digest[:width]
    return result


def _known_operation(subject: str) -> str | None:
    prefixes = (
        ("Add library skill ", "created"),
        ("Accept library skill ", "accepted"),
        ("Import library skill ", "imported"),
        ("Import reconciled exposure as library skill ", "imported"),
        ("Promote exposure to library skill ", "promoted"),
        ("Fork library skill ", "forked"),
        ("Restore library skill ", "restored"),
    )
    for prefix, operation in prefixes:
        if subject.startswith(prefix):
            return operation
    return None


def _history_skill(where: dict[str, Any]) -> dict[str, Any]:
    return {
        key: where.get(key)
        for key in (
            "id",
            "name",
            "summary",
            "path",
            "status",
            "acceptance",
            "working_hash",
            "accepted_hash",
            "head_hash",
        )
    }


def _require_available_history(history: dict[str, Any]) -> None:
    if not history["available"]:
        raise ValueError(f"library history is unavailable: {history['reason']}")


def _historical_endpoint(root: Path, target: Path, version: dict[str, Any]) -> _TreeEndpoint:
    files = tuple(git_tree_files(root, target, str(version["commit"])))
    if _files_content_hash(files) != version["content_hash"]:
        raise ValueError("historical tree does not reproduce its verified Skillager content hash")
    return _TreeEndpoint(
        kind="history",
        label=str(version["short_hash"]),
        content_hash=str(version["content_hash"]),
        files=files,
        commit=str(version["commit"]),
    )


def _working_endpoint(target: Path, expected_hash: str) -> _TreeEndpoint:
    files = tuple(
        GitTreeFile(
            path=path.relative_to(target.resolve()).as_posix(),
            mode="100755" if path.stat().st_mode & 0o111 else "100644",
            content=path.read_bytes(),
        )
        for path in iter_content_files(target)
    )
    digest = _files_content_hash(files)
    if digest != expected_hash:
        raise ValueError("library working tree changed during version inspection; retry the command")
    return _TreeEndpoint(kind="working", label="working", content_hash=digest, files=files)


def _files_content_hash(files: tuple[GitTreeFile, ...] | list[GitTreeFile]) -> str:
    return content_hash_entries((file.path, file.content, file.mode) for file in files)


def _tree_fingerprint(files: tuple[GitTreeFile, ...]) -> str:
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(file.mode.encode("ascii"))
        digest.update(b"\0")
        digest.update(file.content)
        digest.update(b"\0")
    return digest.hexdigest()


def _materialize_tree(files: tuple[GitTreeFile, ...], destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"historical tree destination already exists: {destination}")
    destination.mkdir(parents=True)
    for file in files:
        relative = Path(file.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"historical file path is unsafe: {file.path}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.content)
        target.chmod(0o755 if file.mode == "100755" else 0o644)


def _tree_stat(before: _TreeEndpoint, after: _TreeEndpoint) -> dict[str, Any]:
    before_files = {file.path: file for file in before.files}
    after_files = {file.path: file for file in after.files}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before_files) | set(after_files)):
        old = before_files.get(path)
        new = after_files.get(path)
        if old is not None and new is not None and old.content == new.content and old.mode == new.mode:
            continue
        if old is None:
            status = "added"
        elif new is None:
            status = "deleted"
        elif old.content == new.content:
            status = "mode-changed"
        else:
            status = "changed"
        additions, deletions, binary = _line_change_counts(old.content if old else b"", new.content if new else b"")
        changes.append(
            {
                "path": path,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "binary": binary,
            }
        )
    return {
        "files_changed": len(changes),
        "additions": sum(change["additions"] or 0 for change in changes),
        "deletions": sum(change["deletions"] or 0 for change in changes),
        "files": changes,
    }


def _line_change_counts(before: bytes, after: bytes) -> tuple[int | None, int | None, bool]:
    try:
        before_lines = before.decode("utf-8").splitlines()
        after_lines = after.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None, None, True
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            deletions += first_end - first_start
        if tag in {"replace", "insert"}:
            additions += second_end - second_start
    return additions, deletions, False


def _unified_tree_diff(before: _TreeEndpoint, after: _TreeEndpoint) -> str:
    before_files = {file.path: file for file in before.files}
    after_files = {file.path: file for file in after.files}
    chunks: list[str] = []
    for path in sorted(set(before_files) | set(after_files)):
        old = before_files.get(path)
        new = after_files.get(path)
        if old is not None and new is not None and old.content == new.content and old.mode == new.mode:
            continue
        if old is not None and new is not None and old.content == new.content:
            chunks.append(f"mode change {old.mode} => {new.mode} {path}\n")
            continue
        old_content = old.content if old else b""
        new_content = new.content if new else b""
        try:
            old_lines = old_content.decode("utf-8").splitlines(keepends=True)
            new_lines = new_content.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            chunks.append(f"Binary files a/{path} and b/{path} differ\n")
            continue
        from_file = f"a/{path}" if old is not None else "/dev/null"
        to_file = f"b/{path}" if new is not None else "/dev/null"
        chunks.extend(difflib.unified_diff(old_lines, new_lines, fromfile=from_file, tofile=to_file))
    return "".join(chunks)


def _endpoint_metadata(endpoint: _TreeEndpoint) -> dict[str, Any]:
    return {
        "kind": endpoint.kind,
        "label": endpoint.label,
        "content_hash": endpoint.content_hash,
        "commit": endpoint.commit,
    }


__all__ = [
    "LIBRARY_DIFF_SCHEMA",
    "LIBRARY_HISTORY_SCHEMA",
    "LIBRARY_RESTORE_SCHEMA",
    "library_diff",
    "library_history",
    "library_restore_preview",
    "resolve_history_version",
    "restore_library_skill",
]
