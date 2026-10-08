from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from skillager.exposure import impl
from skillager.exposure.plan import ExposurePlan
from skillager.exposure.plan_apply import apply_plan
from skillager.exposure.plan_request import PlanRefusal
from skillager.simple_yaml import load_mapping
from tests.behavior import test_exposure_plan as fixtures
from tests.support import chdir


class Clock:
    value = datetime(2026, 10, 8, 12, 0, 0, 123456, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.value


class ExposurePlanClockTests(unittest.TestCase):
    def test_unchanged_native_stub_and_group_intent_survives_timestamp_precision_boundary(self):
        for mode in ("native", "stub", "group"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture = fixtures.ExposurePlanBehaviorTests()
                project, cli, source, origin, relation = fixture.fixture(root)
                request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native",
                           "origin_id": origin, "source": relation, "mode": mode}
                if mode == "group":
                    request = {"schema": "skillager.exposure-request.v1", "action": "group", "name": "Clock",
                               "members": [relation["skill_id"]], "library_id": relation["library_id"],
                               "replace": [{"origin_id": origin}]}
                before = fixture.snapshot(project)
                plans = []
                with chdir(project), patch.dict(os.environ, cli.env), \
                        patch("skillager.exposure.impl.datetime", Clock), patch("skillager.project_tags.datetime", Clock):
                    for index, micros in enumerate((123456, 0)):
                        Clock.value = datetime(2026, 10, 8, 12, 0, index, micros, tzinfo=timezone.utc)
                        scratch = root / f"scratch-{index}"
                        scratch.mkdir()
                        plans.append(ExposurePlan(request, state=root / "state/project", catalog=root / "state/catalog",
                                                  project=project, agent="codex", scratch=scratch))
                    self.assertEqual(fixture.snapshot(project), before)
                    self.assertEqual(plans[0].payload, plans[1].payload)
                    self.assertEqual(plans[0].token, plans[1].token)
                    for plan in plans:
                        actual_bytes = sum(target.prepared_state["size"] if target.kind == "tags" else
                                           sum(entry.get("size", 0) for entry in target.prepared_state["entries"].values())
                                           for target in plan.targets if target.candidate is not None)
                        self.assertEqual(plan.staging["candidate_bytes"], actual_bytes)
                    sidecars = [load_mapping(next(target.candidate for target in plan.targets if target.kind in {"direct", "router"})
                                             / "skillager.materialized.yaml") for plan in plans]
                    self.assertNotEqual(sidecars[0]["materialized_at"], sidecars[1]["materialized_at"])
                    for index, micros in enumerate((123456, 0)):
                        Clock.value = datetime(2026, 10, 8, 12, 0, index, micros, tzinfo=timezone.utc)
                        scratch = root / f"bounded-{index}"
                        scratch.mkdir()
                        with patch("skillager.exposure.plan.MAX_STAGED_BYTES", plans[0].staging["peak_bytes"] - 1), \
                                self.assertRaises(PlanRefusal) as refused:
                            ExposurePlan(request, state=root / "state/project", catalog=root / "state/catalog",
                                         project=project, agent="codex", scratch=scratch)
                        self.assertEqual(refused.exception.code, "payload-limit")
                        self.assertEqual(fixture.snapshot(project), before)
                    result, code = apply_plan(plans[1], plans[0].token)
                self.assertEqual((code, result["status"]), (0, "applied"), result)
                fixture.assert_complete_effects(plans[0].preview())
                if mode == "native":
                    self.assertTrue((source / "support/helper.sh").is_file())

    def test_real_generated_body_changes_still_invalidate_token_without_project_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = fixtures.ExposurePlanBehaviorTests()
            project, cli, _, origin, relation = fixture.fixture(root)
            request = {"schema": "skillager.exposure-request.v1", "action": "adopt-native",
                       "origin_id": origin, "source": relation, "mode": "stub"}
            render = impl.render_stub_skill
            before = fixture.snapshot(project)
            plans = []
            with chdir(project), patch.dict(os.environ, cli.env), patch("skillager.exposure.impl.datetime", Clock):
                for index in range(2):
                    Clock.value = datetime(2026, 10, 8, 12, 0, index, 123456 if index == 0 else 0, tzinfo=timezone.utc)
                    scratch = root / f"scratch-{index}"
                    scratch.mkdir()
                    with patch.object(impl, "render_stub_skill", side_effect=lambda *args, **kwargs: render(*args, **kwargs) + "\nChanged reviewed effect.\n" * index):
                        plans.append(ExposurePlan(request, state=root / "state/project", catalog=root / "state/catalog",
                                                  project=project, agent="codex", scratch=scratch))
                self.assertNotEqual(plans[0].token, plans[1].token)
                self.assertNotEqual(plans[0].payload["targets"], plans[1].payload["targets"])
                self.assertNotEqual(plans[0].staging["candidate_bytes"], plans[1].staging["candidate_bytes"])
                with self.assertRaises(PlanRefusal) as refused:
                    apply_plan(plans[1], plans[0].token)
                self.assertEqual(refused.exception.code, "stale-plan")
            self.assertEqual(fixture.snapshot(project), before)
