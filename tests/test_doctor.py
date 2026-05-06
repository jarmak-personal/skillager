from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main
from skillager.materialize import materialize_working_skill
from skillager.session import append_event, end_session, start_session
from skillager.trust import content_hash, set_trust


class SkillagerDoctorTests(unittest.TestCase):

    def test_doctor_clean_project_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 0)
                fix_output = StringIO()
                with redirect_stdout(fix_output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 0)
            data = json.loads(output.getvalue())
            fix_data = json.loads(fix_output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertEqual(data["exit_code"], 0)
            self.assertTrue(data["readiness"]["ready"])
            self.assertFalse(fix_data["fix"]["applied"])
            self.assertEqual(fix_data["fix"]["reason"], "selected next action is not a first-party bootstrap repair")
            self.assertIsNone(fix_data["fix"]["reason_code"])

    def test_doctor_agent_required_lists_runnable_agent_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 11)
                status_output = StringIO()
                with redirect_stdout(status_output):
                    self.assertEqual(main(["status", "--no-packages"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "agent-required")
            self.assertIsNone(data["next"]["command"])
            self.assertEqual(data["next"]["next_commands"], ["skillager doctor --agent codex", "skillager doctor --agent claude"])
            self.assertNotIn("<codex|claude>", json.dumps(data["next"]))
            status_text = status_output.getvalue()
            self.assertIn("skillager status --agent codex", status_text)
            self.assertIn("skillager status --agent claude", status_text)
            self.assertNotIn("<codex|claude>", status_text)

    def test_doctor_unreviewed_skill_exits_ten_and_suggests_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 10)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "review-needed")
            self.assertEqual(data["next"]["command"], "skillager setup --agent codex")
            self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())

    def test_doctor_unreviewed_skill_without_agent_lists_setup_agent_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 10)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "review-needed")
            self.assertIsNone(data["next"]["command"])
            self.assertEqual(data["next"]["next_commands"], ["skillager setup --agent codex", "skillager setup --agent claude"])
            self.assertNotIn("skillager setup\"", json.dumps(data["next"]))

    def test_doctor_missing_working_skill_exits_eleven_and_suggests_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 11)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "artifact-attention-needed")
            self.assertEqual(data["next"]["command"], "skillager bootstrap --agent codex")
            self.assertEqual(data["readiness"]["handoff"]["reason_code"], "working_missing")

    def test_doctor_fix_repairs_first_party_bootstrap_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertTrue(data["fix"]["applied"])
            self.assertIsNone(data["fix"]["reason"])
            self.assertEqual(data["fix"]["reason_code"], "working_missing")
            self.assertTrue((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())
            self.assertIn("skillager handoff", (root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_doctor_fix_requires_explicit_agent_even_when_env_detects_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {
                        "SKILLAGER_STATE_DIR": str(state),
                        "SKILLAGER_CATALOG_STATE_DIR": str(state),
                        "NO_COLOR": "1",
                        "CODEX_SESSION_ID": "codex-test",
                    },
                ),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--no-packages", "--fix", "--json"]), 11)
            data = json.loads(output.getvalue())
            self.assertEqual(data["agent"], "codex")
            self.assertEqual(data["next"]["command"], "skillager bootstrap --agent codex")
            self.assertFalse(data["fix"]["applied"])
            self.assertEqual(data["fix"]["reason"], "pass --agent to apply mutating repairs")
            self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())

    def test_doctor_fix_does_not_approve_or_materialize_third_party_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 10)
                status_output = StringIO()
                with redirect_stdout(status_output):
                    self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            status = json.loads(status_output.getvalue())
            self.assertFalse(data["fix"]["applied"])
            self.assertEqual(status["review_needed"], 1)
            self.assertFalse((root / ".agents" / "skills" / "project-demo" / "SKILL.md").exists())
            self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())

    def test_doctor_lint_blocked_exit_code_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            reviewed_dir = root / ".skills" / "reviewed"
            review_needed_dir = root / ".skills" / "todo"
            lint_dir = root / ".skills" / "linted"
            reviewed_dir.mkdir(parents=True)
            review_needed_dir.mkdir(parents=True)
            lint_dir.mkdir(parents=True)
            (reviewed_dir / "SKILL.md").write_text("# Reviewed\n\nUse reviewed guidance.\n", encoding="utf-8")
            (review_needed_dir / "SKILL.md").write_text("# Todo\n\nUse todo guidance.\n", encoding="utf-8")
            (lint_dir / "SKILL.md").write_text("# Linted\n\nUse linted guidance.\n", encoding="utf-8")
            (lint_dir / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\nsummary: lint bait\naudience:\n  - user\nactivation:\n  default: manual\n",
                encoding="utf-8",
            )
            set_trust(state, "project/reviewed", "reviewed", content_hash(reviewed_dir), {"type": "project", "path": str(reviewed_dir)})
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 12)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "lint-blocked")
            self.assertEqual(data["next"]["command"], "skillager lint")

    def test_doctor_migration_review_exits_thirteen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            python = collection / "python" / "foo"
            writing = collection / "writing" / "foo"
            python.mkdir(parents=True)
            writing.mkdir(parents=True)
            (python / "SKILL.md").write_text("# Python Foo\n\nUse python foo.\n", encoding="utf-8")
            (writing / "SKILL.md").write_text("# Writing Foo\n\nUse writing foo.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [
                                {"id": "personal/foo", "root": str(python), "content_hash": content_hash(python)},
                                {"id": "personal/foo", "root": str(writing), "content_hash": content_hash(writing)},
                            ],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (state / "tags.json").write_text(json.dumps({"tags": {"foo": ["personal/foo"]}}, indent=2) + "\n", encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 13)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "migration-review-needed")
            self.assertEqual(data["next"]["command"], "skillager status --migration-details")

    def test_doctor_migration_ack_exits_thirteen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            python = collection / "python" / "foo"
            python.mkdir(parents=True)
            (python / "SKILL.md").write_text("# Python Foo\n\nUse python foo.\n", encoding="utf-8")
            digest = content_hash(python)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [{"id": "personal/foo", "root": str(python), "content_hash": digest}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                set_trust(state, "personal/foo", "reviewed", digest, {"type": "collection", "collection": "personal"})
                (state / "tags.json").write_text(json.dumps({"tags": {"foo": ["personal/foo"]}}, indent=2) + "\n", encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 13)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "migration-ack-needed")
            self.assertEqual(data["next"]["command"], "skillager status --ack-migration")

    def test_doctor_lookback_pending_exits_fifteen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            first = start_session(state, agent="codex")
            append_event(state, first["session_id"], "skill_activated", {"skill_id": "community/gis"})
            end_session(state, agent="codex")
            second = start_session(state, agent="codex")
            append_event(state, second["session_id"], "skill_activated", {"skill_id": "community/gis"})
            end_session(state, agent="codex")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 15)
                fix_output = StringIO()
                with redirect_stdout(fix_output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 15)
            data = json.loads(output.getvalue())
            fix_data = json.loads(fix_output.getvalue())
            self.assertEqual(data["status"], "lookback-pending")
            self.assertEqual(data["next"]["command"], "skillager lookback")
            self.assertFalse(fix_data["fix"]["applied"])
            self.assertEqual(fix_data["fix"]["reason"], "selected next action is not a first-party bootstrap repair")
            self.assertIsNone(fix_data["fix"]["reason_code"])

    def test_doctor_reports_unmanaged_working_skill_as_manual_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                materialize_working_skill(agents=["codex"], project_dir=root)
                (root / ".agents" / "skills" / "skillager-working" / "skillager.materialized.yaml").unlink()
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--json"]), 14)
                fix_output = StringIO()
                with redirect_stdout(fix_output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 14)
            data = json.loads(output.getvalue())
            fix_data = json.loads(fix_output.getvalue())
            self.assertEqual(data["status"], "manual-artifact-repair-needed")
            self.assertIsNone(data["next"]["command"])
            self.assertEqual(data["readiness"]["handoff"]["reason_code"], "working_unmanaged")
            self.assertFalse(fix_data["fix"]["applied"])
            self.assertEqual(fix_data["fix"]["reason"], "selected next action is not a first-party bootstrap repair")
            self.assertEqual(fix_data["fix"]["reason_code"], "working_unmanaged")


if __name__ == "__main__":
    unittest.main()
