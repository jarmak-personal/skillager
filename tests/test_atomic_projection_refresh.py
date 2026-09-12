from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from skillager.exposure.impl import (
    materialize_one,
    materialize_router_one,
    materialize_stub_one,
    materialize_working_skill_one,
    write_materialized_sidecar,
)
from skillager.exposure.target_state import target_state_hash
from skillager.commands.exposure_confirmation import source_revalidator
from skillager.trust import content_hash


class AtomicProjectionRefreshTests(unittest.TestCase):
    def test_confirmed_direct_exposure_rechecks_approval_after_candidate_preparation(self) -> None:
        for materialize in (materialize_one, materialize_stub_one):
            with self.subTest(materialize=materialize.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                source = project / "source"
                source.mkdir()
                (source / "SKILL.md").write_text("---\nname: demo\ndescription: Fixture guidance.\n---\n\nRead the fixture.\n", encoding="utf-8")
                skill = {"id": "project/demo", "root": str(source), "source": {"type": "project"}, "content_hash": content_hash(source), "trust": "reviewed"}
                target = project / ".agents" / "skills" / "project-demo"
                preview = materialize(skill, target=target, agent="codex", scope="project", dry_run=True, bound_preview=True, project_dir=project)
                current = dict(skill)

                def revoke_while_preparing(path, data):
                    write_materialized_sidecar(path, data)
                    current["trust"] = "blocked"

                with patch("skillager.exposure.impl.write_materialized_sidecar", side_effect=revoke_while_preparing):
                    with self.assertRaisesRegex(ValueError, "approval changed"):
                        materialize(
                            skill, target=target, agent="codex", scope="project",
                            confirmation=preview["preview"]["confirmation_token"],
                            project_dir=project, revalidate_source=source_revalidator(lambda: [current]),
                        )
                self.assertFalse(target.exists())
                self.assertFalse(any(target.parent.glob(".skillager-expose-*")))

    def test_clean_generated_projection_survives_each_refresh_failure_stage(self) -> None:
        for family in ("native", "stub", "router", "working"):
            for failure_stage in ("candidate_write", "install", "final_verification"):
                with self.subTest(family=family, failure_stage=failure_stage):
                    self._assert_refresh_failure_preserves_target(family, failure_stage)

    def test_concurrent_edits_survive_preparation_and_detachment_even_with_force(self) -> None:
        for family in ("native", "stub", "router", "working"):
            for stage in ("preparation", "detachment", "after_install"):
                for force in (False, True):
                    with self.subTest(family=family, stage=stage, force=force), tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / ".agents" / "skills" / f"test-{family}"
                        self._materialize(family, target, refreshed=False)
                        sidecar = (target / "skillager.materialized.yaml").read_bytes()
                        original_replace = os.replace
                        edited = []

                        def save_edit(directory: Path) -> None:
                            (directory / "SKILL.md").write_text("Concurrent local edit\n", encoding="utf-8")
                            (directory / "notes.tmp").write_text("Unsaved notes\n", encoding="utf-8")
                            edited.append(True)

                        def write_candidate(path, data):
                            if stage == "preparation":
                                save_edit(target)
                            write_materialized_sidecar(path, data)

                        def replace(source, destination):
                            original_replace(source, destination)
                            if Path(source) == target and stage == "detachment":
                                save_edit(Path(destination))
                            if Path(source).name == "candidate" and stage == "after_install":
                                save_edit(Path(source).parent / "previous")

                        with (
                            patch("skillager.exposure.impl.write_materialized_sidecar", side_effect=write_candidate),
                            patch("skillager.exposure.impl.os.replace", side_effect=replace),
                            patch("skillager.exposure.impl.render_working_skill", return_value=
                                  "---\nname: changed-working\ndescription: Changed protocol.\n---\n\n# Changed\n"),
                        ):
                            with self.assertRaisesRegex(ValueError, "changed during"):
                                self._materialize(family, target, refreshed=True, force=force)
                        self.assertTrue(edited)
                        self.assertEqual((target / "SKILL.md").read_text(), "Concurrent local edit\n")
                        self.assertEqual((target / "notes.tmp").read_text(), "Unsaved notes\n")
                        self.assertEqual((target / "skillager.materialized.yaml").read_bytes(), sidecar)

    def test_target_created_during_preparation_is_not_overwritten(self) -> None:
        for family in ("native", "stub", "router", "working"):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / ".agents" / "skills" / f"test-{family}"

                def write_candidate(path, data):
                    target.mkdir()
                    (target / "SKILL.md").write_text("Another writer's skill\n", encoding="utf-8")
                    write_materialized_sidecar(path, data)

                with patch("skillager.exposure.impl.write_materialized_sidecar", side_effect=write_candidate):
                    with self.assertRaisesRegex(ValueError, "changed during"):
                        self._materialize(family, target, refreshed=False)
                self.assertEqual((target / "SKILL.md").read_text(), "Another writer's skill\n")
                self.assertFalse((target / "skillager.materialized.yaml").exists())

    def test_reappearing_target_preserves_both_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".agents" / "skills" / "test-stub"
            self._materialize("stub", target, refreshed=False)
            previous = target_state_hash(target)
            original_replace = os.replace

            def replace(source, destination):
                original_replace(source, destination)
                if Path(source) == target:
                    target.mkdir()
                    (target / "SKILL.md").write_text("Another writer's skill\n", encoding="utf-8")

            with patch("skillager.exposure.impl.os.replace", side_effect=replace):
                with self.assertRaisesRegex(ValueError, "previous target preserved at"):
                    self._materialize("stub", target, refreshed=True)
            self.assertEqual((target / "SKILL.md").read_text(), "Another writer's skill\n")
            recovered = list(target.parent.glob(".skillager-recovery-*/previous"))
            self.assertEqual(len(recovered), 1)
            self.assertEqual(target_state_hash(recovered[0]), previous)

    def test_edit_to_installed_candidate_is_preserved_on_verification_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".agents" / "skills" / "test-stub"
            self._materialize("stub", target, refreshed=False)
            previous = target_state_hash(target)

            def edit_installed(installed, **kwargs):
                (installed / "SKILL.md").write_text("Edit to installed skill\n", encoding="utf-8")
                raise ValueError("verification failed after concurrent edit")

            with patch("skillager.exposure.impl._verify_installed_projection", side_effect=edit_installed):
                with self.assertRaisesRegex(ValueError, "previous target preserved at"):
                    self._materialize("stub", target, refreshed=True)
            self.assertEqual((target / "SKILL.md").read_text(), "Edit to installed skill\n")
            recovered = list(target.parent.glob(".skillager-recovery-*/previous"))
            self.assertEqual(len(recovered), 1)
            self.assertEqual(target_state_hash(recovered[0]), previous)

    def test_writer_populating_install_reservation_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".agents" / "skills" / "test-stub"
            original_replace = os.replace

            def replace(source, destination):
                if Path(source).name == "candidate":
                    (target / "SKILL.md").write_text("Another writer's skill\n", encoding="utf-8")
                original_replace(source, destination)

            with patch("skillager.exposure.impl.os.replace", side_effect=replace):
                with self.assertRaises(OSError):
                    self._materialize("stub", target, refreshed=False)
            self.assertEqual((target / "SKILL.md").read_text(), "Another writer's skill\n")

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

    def _materialize(self, family: str, target: Path, *, refreshed: bool, force: bool = False) -> None:
        if family == "native":
            source = target.parents[2] / ".skills" / "demo"
            source.mkdir(parents=True, exist_ok=True)
            (source / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Native guidance.\n---\n\n"
                + ("Changed guidance.\n" if refreshed else "Original guidance.\n"),
                encoding="utf-8",
            )
            materialize_one(
                {"id": "project/demo", "root": str(source), "source": {"type": "project"},
                 "content_hash": content_hash(source), "trust": "reviewed"},
                target=target, agent="codex", scope="project", force=force,
            )
            return
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
                force=force,
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
                force=force,
            )
            return
        materialize_working_skill_one(
            target=target,
            agent="codex",
            scope="project",
            force=force,
        )


if __name__ == "__main__":
    unittest.main()
