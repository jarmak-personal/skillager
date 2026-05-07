from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .support import BODY_SENTINEL, CliResult, SkillagerCli, make_basic_workspace, write_basic_skill


class SkillagerCliBehaviorTests(unittest.TestCase):
    def make_workspace(self, tmp: Path) -> tuple[Path, SkillagerCli]:
        return make_basic_workspace(tmp)

    def write_skill(self, project: Path, slug: str = "gis-domain") -> Path:
        return write_basic_skill(project, slug)

    def assert_code(self, result: CliResult, expected: int) -> None:
        self.assertEqual(
            result.code,
            expected,
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def assert_body_not_exposed(self, result: CliResult) -> None:
        self.assertNotIn(BODY_SENTINEL, result.stdout)
        self.assertNotIn(BODY_SENTINEL, result.stderr)

    def test_metadata_commands_do_not_expose_unreviewed_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project, cli = self.make_workspace(Path(tmp_name))
            self.write_skill(project)

            status = cli.run("status", "--no-packages", "--json")
            self.assert_code(status, 0)
            self.assert_body_not_exposed(status)
            status_data = status.json()
            self.assertTrue(status_data["needs_setup"])
            self.assertEqual(status_data["pending_owner_review"], 1)
            self.assertEqual(status_data["available"], 0)

            search = cli.run("search", "spatial", "--no-session-record", "--json")
            self.assert_code(search, 0)
            self.assert_body_not_exposed(search)
            self.assertEqual(search.json(), [])

            show = cli.run("show", "project/gis-domain", "--json")
            self.assert_code(show, 2)
            self.assert_body_not_exposed(show)
            self.assertIn("not available", show.stderr)

            show_content = cli.run("show", "project/gis-domain", "--content")
            self.assert_code(show_content, 2)
            self.assert_body_not_exposed(show_content)
            self.assertIn("not available", show_content.stderr)

            activate = cli.run("activate", "project/gis-domain", "--no-session-record")
            self.assert_code(activate, 2)
            self.assert_body_not_exposed(activate)
            self.assertIn("not available", activate.stderr)

    def test_reviewed_project_skill_can_be_stubbed_and_guarded_activation_emits_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project, cli = self.make_workspace(Path(tmp_name))
            self.write_skill(project)

            setup = cli.run("setup", "--source", "project", "--accept-low", "--no-packages", "--summary-json")
            self.assert_code(setup, 0)
            self.assert_body_not_exposed(setup)
            setup_data = setup.json()
            self.assertEqual(setup_data["approved"], 1)
            self.assertEqual(setup_data["review_needed"], 0)
            self.assertEqual(setup_data["selected_ids"], ["project/gis-domain"])

            search = cli.run("search", "spatial", "--no-session-record", "--json")
            self.assert_code(search, 0)
            self.assert_body_not_exposed(search)
            search_data = search.json()
            self.assertEqual(search_data[0]["id"], "project/gis-domain")
            self.assertTrue(search_data[0]["available"])
            self.assertNotIn("trust", search_data[0])

            materialize = cli.run("materialize", "project/gis-domain", "--mode", "stub", "--agent", "codex", "--json")
            self.assert_code(materialize, 0)
            self.assert_body_not_exposed(materialize)
            materialized = {item["skill_id"]: item for item in materialize.json()}
            self.assertEqual(materialized["project/gis-domain"]["status"], "materialized")

            stub = project / ".agents" / "skills" / "project-gis-domain" / "SKILL.md"
            working = project / ".agents" / "skills" / "skillager-working" / "SKILL.md"
            note = project / "AGENTS.md"
            self.assertTrue(stub.exists())
            self.assertFalse(working.exists())
            self.assertFalse(note.exists())
            stub_text = stub.read_text(encoding="utf-8")
            self.assertNotIn(BODY_SENTINEL, stub_text)
            self.assertIn("skillager activate project/gis-domain --from-stub project-gis-domain", stub_text)

            wrong_stub = cli.run("activate", "project/gis-domain", "--from-stub", "wrong-stub", "--no-session-record")
            self.assert_code(wrong_stub, 2)
            self.assert_body_not_exposed(wrong_stub)

            activated = cli.run(
                "activate",
                "project/gis-domain",
                "--from-stub",
                "project-gis-domain",
                "--no-session-record",
            )
            self.assert_code(activated, 0)
            self.assertIn("# GIS Domain", activated.stdout)
            self.assertIn(BODY_SENTINEL, activated.stdout)


if __name__ == "__main__":
    unittest.main()
