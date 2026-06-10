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
from skillager.trust import content_hash, set_trust


class SkillagerDoctorTests(unittest.TestCase):

    def test_doctor_clean_empty_project_without_agent_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertEqual(data["exit_code"], 0)
            self.assertTrue(data["readiness"]["ready"])
            self.assertTrue(data["readiness"]["can_proceed"])
            self.assertTrue(data["readiness"]["artifacts_ready"])
            self.assertIsNone(data["readiness"]["reason_code"])
            self.assertEqual(data["state"]["lint_overrides"], {"count": 0, "ids": []})

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
            self.assertEqual(fix_data["fix"]["reason"], "selected next action is not a first-party working artifact repair")
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
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "agent-required")
            self.assertIsNone(data["next"]["command"])
            self.assertEqual(data["next"]["next_commands"], ["skillager doctor --agent codex", "skillager doctor --agent claude"])
            self.assertNotIn("<codex|claude>", json.dumps(data["next"]))
            self.assertNotIn("handoff", json.dumps(data["readiness"]))

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

    def test_doctor_explains_same_content_duplicate_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            project_skill = root / ".skills" / "mapping"
            package_skill = root / ".venv" / "lib" / "python3.13" / "site-packages" / "demo_pkg" / ".skills" / "mapping"
            project_skill.mkdir(parents=True)
            package_skill.mkdir(parents=True)
            body = "# Mapping\n\nUse GIS domain concepts.\n"
            (project_skill / "SKILL.md").write_text(body, encoding="utf-8")
            (package_skill / "SKILL.md").write_text(body, encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--json"]), 10)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "review-needed")
            self.assertIn("source-key approval", data["message"])
            self.assertEqual(data["state"]["duplicate_content"]["review_needed_ids"], ["demo-pkg/mapping"])

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

    def test_doctor_missing_working_skill_exits_eleven_and_suggests_fix(self) -> None:
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
            self.assertEqual(data["next"]["command"], "skillager doctor --agent codex --fix")
            self.assertEqual(data["readiness"]["artifacts"]["reason_code"], "working_missing")
            self.assertFalse(data["readiness"]["artifacts_ready"])
            self.assertNotIn("handoff", json.dumps(data["readiness"]))

    def test_doctor_does_not_write_session_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance body sentinel.\n", encoding="utf-8")
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
            self.assertFalse((state / "sessions").exists())

    def test_doctor_no_session_record_is_accepted_as_noop(self) -> None:
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
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--no-session-record", "--json"]), 10)
            self.assertFalse((state / "sessions").exists())

    def test_doctor_fix_repairs_first_party_working_artifacts(self) -> None:
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
            self.assertEqual([artifact["status"] for artifact in data["fix"]["artifacts"]], ["written", "written"])
            self.assertEqual(data["fix"]["summary"]["by_status"], {"written": 2})
            self.assertNotIn('"materialized"', output.getvalue())
            self.assertTrue((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())
            self.assertIn("skillager working", (root / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertFalse((state / "sessions").exists())

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
            self.assertEqual(data["next"]["command"], "skillager doctor --agent codex --fix")
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
            data = json.loads(output.getvalue())
            self.assertFalse(data["fix"]["applied"])
            self.assertEqual(data["state"]["review"]["needed"], 1)
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
            self.assertEqual(data["next"]["command"], "skillager review --include-lint-blocked --summary")
            self.assertIn(
                'skillager review approve project/linted --override-lint --reason "<why>"',
                data["next"]["next_commands"],
            )

    def test_doctor_reports_active_lint_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "linted"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Linted\n\nUse linted guidance.\n", encoding="utf-8")
            (skill_dir / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\nsummary: lint bait\naudience:\n  - user\nactivation:\n  default: manual\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--no-packages", "--override-lint", "--reason", "fixture override", "--json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 11)
                human = StringIO()
                with redirect_stdout(human):
                    self.assertEqual(main(["doctor", "--no-packages"]), 11)
            data = json.loads(output.getvalue())
            self.assertEqual(data["state"]["lint_overrides"], {"count": 1, "ids": ["project/linted"]})
            self.assertIn("Lint overrides in effect: 1 (project/linted)", human.getvalue())

    def test_doctor_tracks_lint_override_across_collection_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "collection"
            skill_dir = collection / "lintbait"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Lint Bait\n\nUse linted guidance.\n", encoding="utf-8")
            (skill_dir / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\nsummary: lint bait\naudience:\n  - user\nactivation:\n  default: manual\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "oldcol"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--override-lint", "--reason", "fixture override", "--json"]), 0)
                    self.assertEqual(main(["collection", "remove", "oldcol"]), 0)
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "newcol"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    main(["doctor", "--no-packages", "--json"])
            data = json.loads(output.getvalue())
            self.assertEqual(data["state"]["lint_overrides"], {"count": 1, "ids": ["newcol/lintbait"]})

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
            self.assertEqual(data["next"]["command"], "skillager doctor --migration-details")

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
            self.assertEqual(data["next"]["command"], "skillager doctor --ack-migration")

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
            self.assertEqual(data["readiness"]["artifacts"]["reason_code"], "working_unmanaged")
            self.assertFalse(fix_data["fix"]["applied"])
            self.assertEqual(fix_data["fix"]["reason"], "selected next action is not a first-party working artifact repair")
            self.assertEqual(fix_data["fix"]["reason_code"], "working_unmanaged")

    def test_doctor_fix_does_not_overwrite_customized_working_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            target = root / ".agents" / "skills" / "skillager-working"
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                materialize_working_skill(agents=["codex"], project_dir=root)
                with (target / "SKILL.md").open("a", encoding="utf-8") as handle:
                    handle.write("\n# Local customization\n")
                before = (target / "SKILL.md").read_text(encoding="utf-8")
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--agent", "codex", "--no-packages", "--fix", "--json"]), 14)
                after = (target / "SKILL.md").read_text(encoding="utf-8")

            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "manual-artifact-repair-needed")
            self.assertFalse(data["fix"]["applied"])
            self.assertEqual(data["fix"]["reason_code"], "working_local_customization")
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
