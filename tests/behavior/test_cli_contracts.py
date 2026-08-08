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

            working = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(working, 0)
            self.assert_body_not_exposed(working)
            working_data = working.json()
            self.assertEqual(working_data["status"], "review-needed")
            self.assertEqual(working_data["pending_owner_review_count"], 1)
            self.assertFalse(working_data["can_proceed"])

            doctor = cli.run("doctor", "--agent", "codex", "--no-packages", "--json")
            self.assert_code(doctor, 10)
            self.assert_body_not_exposed(doctor)
            self.assertEqual(doctor.json()["status"], "review-needed")

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

            expose = cli.run("expose", "project/gis-domain", "--mode", "stub", "--agent", "codex", "--json")
            self.assert_code(expose, 0)
            self.assert_body_not_exposed(expose)
            exposed = {item["skill_id"]: item for item in expose.json()}
            self.assertEqual(exposed["project/gis-domain"]["status"], "exposed")
            self.assertEqual(exposed["project/gis-domain"]["exposure_id"], "project-gis-domain")
            self.assertEqual(exposed["project/gis-domain"]["mode"], "stub")
            self.assertTrue(exposed["project/gis-domain"]["restart_required"])
            self.assertNotIn("materialized", expose.stdout)

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

    def test_working_plain_ready_output_is_compact_and_body_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project, cli = self.make_workspace(Path(tmp_name))
            self.write_skill(project)

            setup = cli.run(
                "setup",
                "--source",
                "project",
                "--accept-low",
                "--agent",
                "codex",
                "--no-packages",
                "--summary-json",
            )
            self.assert_code(setup, 0)
            self.assert_body_not_exposed(setup)

            working = cli.run("working", "--agent", "codex")
            self.assert_code(working, 0)
            self.assert_body_not_exposed(working)
            self.assertIn("Skillager ready.", working.stdout)
            self.assertIn("1 available source entry -> 1 Codex-ready choice(s)", working.stdout)
            self.assertIn("Tell your agent what you plan to do", working.stdout)

    def test_agent_search_is_monotonic_by_displayed_fractional_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project, cli = self.make_workspace(Path(tmp_name))
            lower = project / ".skills" / "lower"
            higher = project / ".skills" / "higher"
            lower.mkdir(parents=True)
            higher.mkdir(parents=True)
            (lower / "SKILL.md").write_text(
                "# Lower\n\nUse alpha beta guidance.\n\nApply gamma.\n",
                encoding="utf-8",
            )
            (higher / "SKILL.md").write_text(
                "# Higher\n\nUse alpha beta guidance.\n\nApply gamma and delta.\n",
                encoding="utf-8",
            )
            setup = cli.run("setup", "--source", "project", "--accept-low", "--no-packages", "--summary-json")
            self.assert_code(setup, 0)
            exposed = cli.run("expose", "project/lower", "--mode", "native", "--agent", "codex", "--json")
            self.assert_code(exposed, 0)

            search = cli.run(
                "search",
                "alpha beta gamma delta",
                "--agent",
                "codex",
                "--json",
                "--limit",
                "0",
            )

            self.assert_code(search, 0)
            self.assert_body_not_exposed(search)
            results = search.json()
            self.assertEqual([item["id"] for item in results[:2]], ["project/higher", "project/lower"])
            self.assertGreater(float(results[0]["score"]), float(results[1]["score"]))

    def test_working_prefers_existing_router_over_repeated_curation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project, cli = self.make_workspace(Path(tmp_name))
            self.write_skill(project)
            setup = cli.run(
                "setup",
                "--source",
                "project",
                "--accept-low",
                "--agent",
                "codex",
                "--no-packages",
                "--summary-json",
            )
            self.assert_code(setup, 0)
            tagged = cli.run("tag", "add", "gis", "project/gis-domain")
            self.assert_code(tagged, 0)
            exposed = cli.run("expose", "--tag", "gis", "--mode", "router", "--agent", "codex", "--scope", "project")
            self.assert_code(exposed, 0)

            plain = cli.run("working", "--agent", "codex")
            self.assert_code(plain, 0)
            self.assert_body_not_exposed(plain)
            self.assertIn("Use the existing router tag(s) first: gis.", plain.stdout)
            self.assertNotIn("Tell your agent what you plan to do", plain.stdout)

            working = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(working, 0)
            self.assertFalse(working.json()["curation"]["recommended"])
            self.assertEqual(working.json()["curation"]["existing_router_tags"], ["gis"])


if __name__ == "__main__":
    unittest.main()
