"""Publish a bounded exposure plan with retained originals and explicit recovery."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..library.confirmation import require_confirmation_token
from ..state import approvals
from ..state.locking import lock_path_for, resource_locks
from .plan import ExposurePlan
from .impl import install_reserved_projection, _verify_materialized_projection
from .plan_request import PlanRefusal
from .plan_targets import PlanTarget, digest, result_identity


def apply_plan(plan: ExposurePlan, token: str) -> tuple[dict[str, Any], int]:
    """No mutation is admitted until the current whole plan matches confirmation."""
    try:
        require_confirmation_token(token, plan.token, operation="exposure plan")
    except ValueError as error:
        raise PlanRefusal("stale-plan", str(error)) from error
    source = plan.sources
    resources = [source.catalog / "library-mutation"]
    resources.extend(source.catalog / f"library-skill-{skill_id[4:]}" for skill_id in source.selected)
    with resource_locks(resources):
        with approvals.locked_records([source.state, source.catalog]) as records:
            source.revalidate(records)
            plan.require_staging_clear()
            plan.revalidate_targets()
            return _publish(plan, records)


def _publish(plan: ExposurePlan, records: dict[Path, dict[str, Any]]) -> tuple[dict[str, Any], int]:
    outcomes = [{**result_identity(target.public()), "status": "unchanged" if target.keep else "refused", "reason_code": None,
                 "observed_state_hash": digest(target.before) if target.before is not None else None,
                 "recovery_path": None} for target in plan.targets]
    backups: dict[Path, Path] = {}
    installed: dict[Path, dict[str, Any] | None] = {}
    staging: dict[Path, Path] = {}
    scratch: dict[Path, PlanTarget] = {}
    created_parents: list[PlanTarget] = []
    mutation_started = False
    failed = False
    rollback_started = False
    failure_code: str | None = None
    allocations = {target.path.parent / ".skillager-target-allocation" for target in plan.targets if target.kind not in {"parent", "tags"}}
    target_resources = [target.path for target in plan.targets if target.kind != "parent"]
    coordination_files = {lock_path_for(resource) for resource in [*allocations, *target_resources]}
    with ExitStack() as locks:
        try:
            # A token mismatch above cannot create destination ancestors. Once this
            # begins, failures return per-target partial outcomes, including parents.
            for target, result in zip(plan.targets, outcomes):
                if target.kind != "parent" or target.keep:
                    continue
                target.revalidate()
                target.path.mkdir(mode=target.parent_mode)
                target.path.chmod(target.parent_mode)
                created_parents.append(target)
                mutation_started = True
                result["status"] = "applied"
            locks.enter_context(resource_locks(list(allocations)))
            locks.enter_context(resource_locks(target_resources))
            plan.require_staging_clear()
            plan.sources.revalidate(records)
            for target in plan.targets:
                if target not in created_parents:
                    target.revalidate()
            # These are stable hidden staging directories, on each target's filesystem.
            for target in plan.targets:
                if target.keep or target.kind == "parent":
                    continue
                root = Path(tempfile.mkdtemp(prefix=".skillager-exposure-plan-", dir=target.path.parent))
                scratch[root] = target
                mutation_started = True
                backups[target.path] = root / "previous"
                if target.candidate is not None:
                    candidate = root / "candidate"
                    if target.observe(target.candidate) != target.prepared_state:
                        raise PlanRefusal("candidate-changed", "Prepared candidate changed before transfer")
                    if root.stat().st_dev == target.candidate.stat().st_dev:
                        os.replace(target.candidate, candidate)
                    else:
                        state = target.prepared_state
                        assert state is not None
                        size = state["size"] if target.kind == "tags" else sum(item.get("size", 0) for item in state["entries"].values())
                        if size > plan.staging["transfer_reserve_bytes"]:
                            raise PlanRefusal("filesystem-changed", "Destination filesystem changed; transfer was not admitted")
                        if target.kind == "tags":
                            shutil.copy2(target.candidate, candidate)
                        else:
                            shutil.copytree(target.candidate, candidate)
                        if target.observe(candidate) != target.prepared_state:
                            raise PlanRefusal("candidate-changed", "Transferred candidate changed before verification")
                        _delete(target.candidate)
                    target.candidate = candidate
                    if target.kind != "tags":
                        _verify_materialized_projection(candidate, expected_hash=str(target.metadata["materialized_hash"]) if target.metadata else "")
                    if target.observe(candidate) != target.after(deterministic=False):
                        raise PlanRefusal("candidate-changed", "Staged exposure bytes or modes changed")
                    staging[target.path] = candidate
            plan.sources.revalidate(records)
            for target in plan.targets:
                if target not in created_parents:
                    target.revalidate()
            # Content installs precede removal and tag publication; all backups stay
            # available until every target has verified the confirmed after state.
            order = sorted(enumerate(plan.targets), key=lambda pair: (pair[1].kind == "tags", pair[1].candidate is None))
            for index, target in order:
                if target.keep or target.kind == "parent":
                    continue
                target.revalidate()
                backup: Path | None = backups[target.path]
                assert backup is not None
                if target.before is not None:
                    mutation_started = True
                    os.replace(target.path, backup)
                    if target.observe(backup) != target.before:
                        raise PlanRefusal("target-changed", "Detached original changed; its actual bytes and modes were retained")
                prepared = staging.get(target.path)
                if prepared is not None:
                    if target.observe(prepared) != target.prepared_state:
                        raise PlanRefusal("candidate-changed", "Staged exposure changed before publication")
                    mutation_started = True
                    _install(prepared, target)
                    installed[target.path] = target.after(deterministic=False)
                    if target.observe() != installed[target.path]:
                        raise PlanRefusal("target-changed", "Installed exposure changed before verification")
                else:
                    installed[target.path] = None
                outcomes[index]["status"] = "applied"
            for target in plan.targets:
                expected = target.after(deterministic=False)
                if target.observe() != expected:
                    raise PlanRefusal("target-changed", "Exposure changed before aggregate verification")
            # Recheck original bytes/modes at their detached locations, without
            # substituting the recovery path for the reviewed destination identity.
            for target in plan.targets:
                backup = backups.get(target.path)
                if backup is not None and backup.exists() and target.observe(backup) != target.before:
                    raise PlanRefusal("target-changed", "Retained original changed before disposal")
        except (OSError, ValueError) as error:
            failed = True
            rollback_started = True
            failure_code = error.code if isinstance(error, PlanRefusal) else "publication-failed"
            _rollback(plan, outcomes, backups, installed, failure_code)
        finally:
            for target, result in zip(plan.targets, outcomes):
                try:
                    observed = target.observe()
                    result["observed_state_hash"] = digest(observed) if observed is not None else None
                except (OSError, ValueError):
                    result.update(status="recovery_required", reason_code="observation-failed", observed_state_hash=None)
                    failed = True
                    failure_code = failure_code or "observation-failed"
            # Never discard an original after failed rollback. Successful disposal
            # failures also have an explicit recovery outcome and path.
            for target, result in zip(plan.targets, outcomes):
                backup = backups.get(target.path)
                if backup is None or not backup.exists():
                    continue
                if failed:
                    result.update(status="recovery_required", reason_code=failure_code or "recovery-required", recovery_path=result["recovery_path"] or str(backup))
                    continue
                try:
                    if target.observe(backup) != target.before:
                        raise OSError("retained original changed before disposal")
                    _delete(backup)
                except OSError:
                    failed = True
                    failure_code = failure_code or "disposal-failed"
                    result.update(status="recovery_required", reason_code="disposal-failed", recovery_path=str(backup))
            for root, target in scratch.items():
                if (root / "previous").exists() or (root / "interrupted-current").exists():
                    continue
                try:
                    _cleanup_stage(root, target)
                except (OSError, ValueError):
                    failed = True
                    failure_code = failure_code or "cleanup-incomplete"
                    result = outcomes[plan.targets.index(target)]
                    result.update(status="recovery_required", reason_code="cleanup-incomplete", recovery_path=str(root))
            if rollback_started:
                _rollback_parents(plan, outcomes, created_parents, coordination_files, failure_code)
    status = "partial" if failed and mutation_started else "refused" if failed else "applied"
    return {**plan.payload, "status": status, "plan_hash": plan.token, "reason_code": failure_code, "results": outcomes}, 2 if failed else 0


def _cleanup_stage(root: Path, target: PlanTarget) -> None:
    """Discard only the verified, privately owned remainder; retain ambiguity."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("staging location changed")
    entries = list(root.iterdir())
    candidate = root / "candidate"
    if any(path != candidate for path in entries):
        raise ValueError("unrecognized material appeared in exposure staging")
    if entries:
        if target.observe(candidate) != target.prepared_state:
            raise ValueError("retained candidate contains concurrent changes")
        _delete(candidate)
    root.rmdir()


def _install(candidate: Path, target: PlanTarget) -> None:
    install_reserved_projection(candidate, target.path, metadata_file=target.kind == "tags")


def _rollback(plan: ExposurePlan, outcomes: list[dict[str, Any]], backups: dict[Path, Path], installed: dict[Path, dict[str, Any] | None], code: str) -> None:
    for target, result in reversed(list(zip(plan.targets, outcomes))):
        if target.kind == "parent" or target.keep:
            continue
        backup = backups.get(target.path)
        try:
            if target.path in installed:
                if target.observe() != installed[target.path]:
                    raise PlanRefusal("target-changed", "Concurrent target changes prevent rollback")
                if installed[target.path] is not None:
                    assert backup is not None
                    interrupted = backup.parent / "interrupted-current"
                    os.replace(target.path, interrupted)
                    if target.observe(interrupted) != installed[target.path]:
                        with contextlib.suppress(OSError, ValueError):
                            _install(interrupted, target)
                        raise PlanRefusal("target-changed", "Concurrent installed-copy changes were retained")
                    _delete(interrupted)
            if backup is not None and backup.exists():
                _install(backup, target)
                result.update(status="rolled_back", reason_code=code)
            elif target.path in installed:
                result.update(status="rolled_back", reason_code=code)
            else:
                result["reason_code"] = code
        except (OSError, ValueError):
            recovery = backup.parent if backup is not None and (backup.parent / "interrupted-current").exists() else backup
            result.update(status="recovery_required", reason_code=code, recovery_path=str(recovery) if recovery is not None and recovery.exists() else None)


def _rollback_parents(plan: ExposurePlan, outcomes: list[dict[str, Any]], created_parents: list[PlanTarget], coordination_files: set[Path], code: str | None) -> None:
    retained: set[Path] = set()
    for target in reversed(created_parents):
        result = outcomes[plan.targets.index(target)]
        result["observed_state_hash"] = None
        try:
            observed = target.observe()
            result["observed_state_hash"] = digest(observed) if observed is not None else None
            if observed != target.after():
                raise ValueError("created parent changed")
            entries = list(target.path.iterdir())
            if not entries:
                target.path.rmdir()
                result.update(status="rolled_back", reason_code=code, observed_state_hash=None)
            elif all(entry in retained or _coordination_directory(entry, coordination_files) for entry in entries):
                # Stable locks can be held or awaited by other processes. Keep
                # them and disclose the directory that actually remains.
                retained.add(target.path)
                result.update(status="applied", reason_code="coordination-retained", observed_state_hash=digest(observed), recovery_path=None)
            else:
                raise ValueError("created parent contains retained material")
        except (OSError, ValueError):
            result.update(status="recovery_required", reason_code="parent-retained", recovery_path=str(target.path))


def _coordination_directory(path: Path, expected_files: set[Path]) -> bool:
    if path.name != ".skillager-locks" or path.is_symlink() or not path.is_dir():
        return False
    return all(child in expected_files and not child.is_symlink() and child.is_file() for child in path.iterdir())


def _delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
