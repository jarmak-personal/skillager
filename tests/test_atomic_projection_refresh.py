from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from skillager.exposure.impl import (
    materialize_router_one,
    materialize_stub_one,
    materialize_working_skill_one,
)
from skillager.exposure.target_state import target_state_hash


class AtomicProjectionRefreshTests(unittest.TestCase):
    def test_clean_generated_projection_survives_each_refresh_failure_stage(self) -> None:
        for family in ("stub", "router", "working"):
            for failure_stage in ("candidate_write", "install", "final_verification"):
                with self.subTest(family=family, failure_stage=failure_stage):
                    self._assert_refresh_failure_preserves_target(family, failure_stage)

    def _assert_refresh_failure_preserves_target(self, family: str, failure_stage: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".agents" / "skills" / f"test-{family}"
            self._materialize(family, target, refreshed=False)
            before_hash = target_state_hash(target)
            before_skill = (target / "SKILL.md").read_bytes()
            before_sidecar = (target / "skillager.materialized.yaml").read_bytes()

            original_replace = os.replace

            def fail_candidate_install(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
                if Path(source).name == "candidate" and Path(destination) == target:
                    raise OSError("injected candidate install failure")
                original_replace(source, destination)

            with ExitStack() as stack:
                if family == "working":
                    stack.enter_context(
                        patch(
                            "skillager.exposure.impl.render_working_skill",
                            return_value="---\nname: changed-working\ndescription: Changed protocol.\n---\n\n# Changed\n",
                        )
                    )
                if failure_stage == "candidate_write":
                    stack.enter_context(
                        patch(
                            "skillager.exposure.impl.write_materialized_sidecar",
                            side_effect=OSError("injected candidate metadata write failure"),
                        )
                    )
                elif failure_stage == "install":
                    stack.enter_context(
                        patch("skillager.exposure.impl.os.replace", side_effect=fail_candidate_install)
                    )
                else:
                    stack.enter_context(
                        patch(
                            "skillager.exposure.impl._verify_installed_projection",
                            side_effect=ValueError("injected final verification failure"),
                        )
                    )

                with self.assertRaises((OSError, ValueError)):
                    self._materialize(family, target, refreshed=True)

            self.assertTrue(target.is_dir())
            self.assertEqual(target_state_hash(target), before_hash)
            self.assertEqual((target / "SKILL.md").read_bytes(), before_skill)
            self.assertEqual((target / "skillager.materialized.yaml").read_bytes(), before_sidecar)
            self.assertFalse(any(target.parent.glob(f".{target.name}.skillager-*")))
            self.assertFalse(any(target.parent.glob(".skillager-*-*")))

    def _materialize(self, family: str, target: Path, *, refreshed: bool) -> None:
        if family == "stub":
            skill = {
                "id": "project/demo",
                "name": "Demo",
                "summary": "Changed guidance." if refreshed else "Original guidance.",
                "root": str(target.parents[2] / ".skills" / "demo"),
                "source": {"type": "project"},
                "content_hash": "b" * 64 if refreshed else "a" * 64,
                "trust": "reviewed",
            }
            materialize_stub_one(
                skill,
                target=target,
                agent="codex",
                scope="project",
            )
            return
        if family == "router":
            skills = [
                {
                    "id": "project/demo",
                    "name": "Demo",
                    "summary": "Changed guidance." if refreshed else "Original guidance.",
                    "content_hash": "b" * 64 if refreshed else "a" * 64,
                    "trust": "reviewed",
                }
            ]
            materialize_router_one(
                "demo",
                skills,
                target=target,
                agent="codex",
                scope="project",
            )
            return
        materialize_working_skill_one(
            target=target,
            agent="codex",
            scope="project",
        )


if __name__ == "__main__":
    unittest.main()
