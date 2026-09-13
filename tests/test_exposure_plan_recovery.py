from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.exposure.plan import ExposurePlan
from skillager.exposure.plan_apply import apply_plan
from skillager.exposure import plan_apply
from skillager.exposure.plan_targets import tree_state
from tests.behavior import test_exposure_plan as fixtures


class ExposurePlanRecoveryTests(unittest.TestCase):
    def test_failures_preserve_originals_and_report_actual_per_target_outcomes(self):
        for fault in ("tag-install", "concurrent-original", "concurrent-installed", "detached-mode", "disposal"):
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
                try:
                    os.chdir(project)
                    with patch.dict(os.environ, cli.env), patch.object(plan_apply, "_install", side_effect=install), patch.object(plan_apply.os, "replace", side_effect=replace), patch.object(plan_apply, "_delete", side_effect=delete):
                        plan = ExposurePlan(request, state=Path(cli.env["SKILLAGER_STATE_DIR"]), catalog=Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), project=project, agent="codex", scratch=scratch)
                        result, code = apply_plan(plan, plan.token)
                finally:
                    os.chdir(previous)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["status"], "partial", result)
                self.assertEqual({item["target_id"] for item in result["results"]}, {item["target_id"] for item in result["targets"]})
                origin_result = next(item for item in result["results"] if item["path"] == str(source))
                if fault == "concurrent-original":
                    self.assertEqual((source / "local.txt").read_text(), "Concurrent local source")
                    self.assertEqual(origin_result["status"], "recovery_required")
                    self.assertEqual(tree_state(Path(origin_result["recovery_path"])), original)
                elif fault == "disposal":
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
