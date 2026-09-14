from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from skillager.exposure.plan import ExposurePlan
from skillager.exposure.plan_apply import apply_plan
from skillager.exposure import plan_apply
from skillager.exposure.plan_targets import PlanTarget, digest, tree_state
from skillager.state.locking import lock_path_for
from tests.behavior import test_exposure_plan as fixtures


class ExposurePlanRecoveryTests(unittest.TestCase):
    def test_failures_preserve_originals_and_report_actual_per_target_outcomes(self):
        for fault in ("tag-install", "concurrent-original", "concurrent-installed", "detached-mode", "disposal", "observation"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ExposurePlanBehaviorTests()
                project, cli, source, origin, relation = fixture.fixture(root)
                original = tree_state(source)
                request = {"schema": "skillager.exposure-request.v1", "action": "group", "name": "Recovery", "members": [relation["skill_id"]], "library_id": relation["library_id"], "replace": [{"origin_id": origin}]}
                scratch = root / "scratch"
                scratch.mkdir()
                previous = Path.cwd()
                real_install, real_replace, real_delete = plan_apply._install, os.replace, plan_apply._delete
                real_observe = PlanTarget.observe
                verified_backups = 0
                router = source.parent / "skillager-recovery"
                def install(candidate, target):
                    if target.kind == "tags":
                        if fault == "concurrent-original":
                            source.mkdir()
                            (source / "local.txt").write_text("Concurrent local source")
                        if fault == "concurrent-installed":
                            (router / "local.txt").write_text("Concurrent router edit")
                        if fault in {"tag-install", "concurrent-original", "concurrent-installed"}:
                            raise OSError("injected metadata publication failure")
                    return real_install(candidate, target)
                def replace(old, new):
                    result = real_replace(old, new)
                    if fault == "detached-mode" and Path(old) == source and Path(new).name == "previous":
                        (Path(new) / "support/helper.sh").chmod(0o600)
                    return result
                def delete(path):
                    if fault == "disposal" and path.name == "previous":
                        raise OSError("injected original disposal failure")
                    return real_delete(path)
                def observe(target, path=None):
                    nonlocal verified_backups
                    if fault == "observation" and target.path == source:
                        if path is not None and path.name == "previous":
                            verified_backups += 1
                        elif path is None and verified_backups == 2:
                            raise OSError("injected post-publication observation failure")
                    return real_observe(target, path)
                try:
                    os.chdir(project)
                    with patch.dict(os.environ, cli.env), patch.object(plan_apply, "_install", side_effect=install), patch.object(plan_apply.os, "replace", side_effect=replace), patch.object(plan_apply, "_delete", side_effect=delete), patch.object(PlanTarget, "observe", observe):
                        plan = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                        result, code = apply_plan(plan, plan.token)
                finally:
                    os.chdir(previous)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["status"], "partial", result)
                self.assertIsNotNone(result["reason_code"])
                self.assertEqual({item["target_id"] for item in result["results"]}, {item["target_id"] for item in result["targets"]})
                origin_result = next(item for item in result["results"] if item["path"] == str(source))
                if fault == "concurrent-original":
                    self.assertEqual((source / "local.txt").read_text(), "Concurrent local source")
                    self.assertEqual(origin_result["status"], "recovery_required")
                    self.assertEqual(tree_state(Path(origin_result["recovery_path"])), original)
                elif fault in {"disposal", "observation"}:
                    self.assertEqual(result["reason_code"], f"{fault}-failed")
                    self.assertFalse(source.exists())
                    self.assertEqual(tree_state(Path(origin_result["recovery_path"])), original)
                elif fault == "detached-mode":
                    self.assertEqual((source / "support/helper.sh").stat().st_mode & 0o7777, 0o600)
                    self.assertEqual((source / "SKILL.md").read_bytes(), (root / "library/skills" / relation["skill_id"][4:] / "SKILL.md").read_bytes())
                else:
                    self.assertEqual(tree_state(source), original)
                if fault == "concurrent-installed":
                    self.assertEqual((router / "local.txt").read_text(), "Concurrent router edit")
                    self.assertEqual(next(item for item in result["results"] if item["path"] == str(router))["status"], "recovery_required")

    def test_parent_rollback_preserves_stable_locks_and_distinguishes_unknown_material(self):
        for extra in (None, "file", "unknown-lock", "parent-mode"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ExposurePlanBehaviorTests()
                project, cli, source, _, relation = fixture.fixture(root)
                request = {"schema": "skillager.exposure-request.v1", "action": "group", "name": "Parents", "members": [relation["skill_id"]], "library_id": relation["library_id"], "replace": []}
                scratch = root / "scratch"
                scratch.mkdir()
                previous = Path.cwd()
                original = tree_state(source)
                lock_inodes = {}
                real_install = plan_apply._install
                def install(candidate, target):
                    if target.kind == "tags":
                        lock_inodes.update({path: path.stat().st_ino for path in project.rglob("*.lock")})
                        if extra == "file":
                            (project / ".claude/skills/local.txt").write_text("Concurrent material")
                        elif extra == "unknown-lock":
                            (project / ".claude/skills/.skillager-locks/other.lock").write_bytes(b"\0")
                        elif extra == "parent-mode":
                            (project / ".claude").chmod(0o700)
                        raise OSError("injected tag publication failure")
                    return real_install(candidate, target)
                try:
                    os.chdir(project)
                    with patch.dict(os.environ, cli.env), patch.object(plan_apply, "_install", side_effect=install):
                        plan = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="claude", scratch=scratch)
                        result, code = apply_plan(plan, plan.token)
                finally:
                    os.chdir(previous)
                self.assertEqual(code, 2)
                self.assertFalse((project / ".skillager-locks").exists())
                self.assertFalse((project / ".claude/skills/skillager-parents").exists())
                self.assertEqual(tree_state(source), original)
                self.assertTrue(lock_inodes)
                self.assertEqual({path: path.stat().st_ino for path in lock_inodes}, lock_inodes)
                parents = [item for item in result["results"] if item["kind"] == "parent" and item["action"] != "keep"]
                self.assertEqual(len(parents), 3)
                for item in parents:
                    path = Path(item["path"])
                    actual = {"type": "directory", "mode": path.stat().st_mode & 0o7777}
                    self.assertEqual(item["observed_state_hash"], digest(actual))
                    ambiguous = extra is not None and path != project / ".skillager" and (extra != "parent-mode" or path == project / ".claude")
                    self.assertEqual(item["status"], "recovery_required" if ambiguous else "applied", item)
                    self.assertEqual(item["reason_code"], "parent-retained" if ambiguous else "coordination-retained", item)
                    self.assertEqual(item["recovery_path"], str(path) if ambiguous else None)
                if extra is None:
                    self.assertFalse(any(item["status"] == "recovery_required" for item in result["results"]), result)
                    self.assertFalse(list(project.rglob(".skillager-exposure-plan-*")))

    def test_staging_admission_is_rechecked_under_existing_destination_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, source, origin, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native", "origin_id": origin, "source": relation, "mode": "stub"}
            scratch = root / "scratch"
            scratch.mkdir()
            previous = Path.cwd()
            original = tree_state(source)
            retained = source.parent / ".skillager-exposure-plan-concurrent"
            real_locks = plan_apply.resource_locks
            @contextmanager
            def locks(resources):
                with real_locks(resources):
                    if any(resource.name == ".skillager-target-allocation" for resource in resources):
                        self.assertTrue(all(lock_path_for(resource).is_file() for resource in resources))
                        retained.mkdir()
                        (retained / "preserve.txt").write_text("Retained by another action")
                    yield
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env):
                    plan = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                    with patch.object(plan_apply, "resource_locks", locks):
                        result, code = apply_plan(plan, plan.token)
            finally:
                os.chdir(previous)
            self.assertEqual((code, result["status"], result["reason_code"]), (2, "refused", "staging-present"))
            self.assertEqual(tree_state(source), original)
            self.assertEqual((retained / "preserve.txt").read_text(), "Retained by another action")
            self.assertFalse((project / ".skillager-locks").exists())

    def test_effect_and_target_admission_limits_refuse_without_project_writes(self):
        from skillager.exposure.plan_request import PlanRefusal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, _, _, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "group", "name": "Bounded", "members": [relation["skill_id"]], "library_id": relation["library_id"], "replace": []}
            previous = Path.cwd()
            scratch = root / "scratch"
            scratch.mkdir()
            before = fixture.snapshot(project)
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env):
                    first = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="claude", scratch=scratch)
                    limits = [("MAX_EFFECTS", sum(len(target["file_effects"]) for target in first.payload["targets"]) - 1, "effect-limit"),
                              ("MAX_TARGETS", len(first.targets) - 1, "target-limit")]
                    for constant, limit, reason in limits:
                        with self.subTest(limit=constant):
                            candidate = root / constant
                            candidate.mkdir()
                            with patch(f"skillager.exposure.plan.{constant}", limit), self.assertRaises(PlanRefusal) as refused:
                                ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="claude", scratch=candidate)
                            self.assertEqual(refused.exception.code, reason)
                            self.assertEqual(fixture.snapshot(project), before)
                            self.assertFalse((project / ".claude").exists())
                            self.assertFalse((project / ".skillager").exists())
            finally:
                os.chdir(previous)

    def test_candidate_cleanup_failure_reports_recovery_and_stops_later_actions(self):
        from skillager.exposure.plan_request import PlanRefusal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, _, _, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "group", "name": "Cleanup", "members": [relation["skill_id"]], "library_id": relation["library_id"], "replace": []}
            scratch = root / "scratch"
            scratch.mkdir()
            previous = Path.cwd()
            real_rmdir = Path.rmdir
            def rmdir(path):
                if path.name.startswith(".skillager-exposure-plan-"):
                    raise OSError("injected actual staging-directory cleanup failure")
                return real_rmdir(path)
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env):
                    plan = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                    with patch.object(Path, "rmdir", rmdir):
                        result, code = apply_plan(plan, plan.token)
                    self.assertEqual(code, 2)
                    self.assertEqual(result["status"], "partial")
                    recoveries = [item for item in result["results"] if item["reason_code"] == "cleanup-incomplete"]
                    self.assertTrue(recoveries)
                    for item in recoveries:
                        self.assertEqual(item["status"], "recovery_required")
                        self.assertTrue(Path(item["recovery_path"]).is_dir())
                    with self.assertRaises(PlanRefusal) as refused:
                        ExposurePlan({**request, "name": "Next"}, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                    self.assertEqual(refused.exception.code, "staging-present")
            finally:
                os.chdir(previous)

    def test_peak_staging_admission_includes_retained_originals_before_mutation(self):
        from skillager.exposure.plan_request import PlanRefusal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, source, origin, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native", "origin_id": origin, "source": relation, "mode": "native"}
            previous = Path.cwd()
            scratch = root / "scratch"
            scratch.mkdir()
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env):
                    first = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                    self.assertGreater(first.staging["retained_original_bytes"], 0)
                    limit = first.staging["peak_bytes"] - 1
                    self.assertGreaterEqual(limit, first.staging["candidate_bytes"])
                    second = root / "second-scratch"
                    second.mkdir()
                    before = tree_state(source)
                    with patch("skillager.exposure.plan.MAX_STAGED_BYTES", limit), self.assertRaises(PlanRefusal) as refused:
                        ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=second)
                    self.assertEqual(refused.exception.code, "payload-limit")
                    self.assertEqual(tree_state(source), before)
            finally:
                os.chdir(previous)

    def test_router_identity_swap_during_plan_cannot_bind_other_tree_to_old_membership(self):
        from skillager.exposure.plan_request import PlanRefusal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, source, _, relation = fixture.fixture(root)
            for name in ("First", "Other"):
                fixture.apply(cli, fixture.preview(cli, "group", name=name, members=[relation["skill_id"]], library_id=relation["library_id"], replace=[]))
            first, other = source.parent / "skillager-first", source.parent / "skillager-other"
            second_state = tree_state(other)
            original_router = ExposurePlan._router
            def swap(plan, path, tag, skills, *, data=None):
                first.rename(root / "original-router")
                other.rename(first)
                return original_router(plan, path, tag, skills, data=data)
            scratch = root / "scratch"
            scratch.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env), patch.object(ExposurePlan, "_router", swap), self.assertRaises(PlanRefusal) as refused:
                    ExposurePlan({"schema": "skillager.exposure-request.v1", "action": "set-members", "router_id": "skillager-first", "members": [relation["skill_id"]], "library_id": relation["library_id"], "replace": [], "departures": []}, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                self.assertEqual(refused.exception.code, "target-changed")
                self.assertEqual(tree_state(first), second_state)
            finally:
                os.chdir(previous)

    def test_output_admission_includes_pretty_serialization_and_all_refusal_results(self):
        from skillager.exposure.plan_request import PlanRefusal, canonical_json, encode_plan_output
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, source, origin, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native", "origin_id": origin, "source": relation, "mode": "stub"}
            previous = Path.cwd()
            scratch = root / "scratch"
            scratch.mkdir()
            try:
                os.chdir(project)
                with patch.dict(os.environ, cli.env):
                    first = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                    compact = len(canonical_json(first.preview()).encode())
                    emitted = encode_plan_output(first.preview()).encode()
                    self.assertTrue(emitted.endswith(b"\n"))
                    self.assertGreater(len(emitted), compact)
                    # Compact admission alone would accept this complete preview.
                    # The actual emitted preview and full refusal envelope cannot fit.
                    second = root / "second-scratch"
                    second.mkdir()
                    before = tree_state(source)
                    with patch("skillager.exposure.plan.MAX_OUTPUT_BYTES", compact + 1), self.assertRaises(PlanRefusal) as refused:
                        ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=second)
                    self.assertEqual(refused.exception.code, "output-limit")
                    self.assertEqual(tree_state(source), before)
            finally:
                os.chdir(previous)
