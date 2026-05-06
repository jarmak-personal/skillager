from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import TtyStringIO, chdir
from skillager.cli import main
from skillager.index import build_index
from skillager.lookback import build_lookback
from skillager.materialize import materialize_working_skill
from skillager.session import append_event, end_session, prune_sessions, read_events, redact_session, start_session
from skillager.simple_yaml import loads
from skillager.trust import content_hash, set_trust


class SkillagerStatusHandoffSessionTests(unittest.TestCase):

    def test_status_reports_unreviewed_skills_without_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            output = StringIO()
            with (
                redirect_stdout(output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["status", "--no-packages"]), 0)
                self.assertEqual(main(["status", "--no-packages", "--exit-code"]), 10)
            text = output.getvalue()
            self.assertIn("review needed: 1", text)
            self.assertIn("Ask the user to run `skillager setup`", text)

    def test_status_reports_available_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            update = {
                "enabled": True,
                "checked": True,
                "cached": False,
                "available": True,
                "current_version": "0.1.0",
                "latest_version": "0.1.1",
                "command": "uv tool upgrade skillager",
            }
            output = StringIO()
            with (
                redirect_stdout(output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                patch("skillager.cli.check_for_update", return_value=update),
                chdir(root),
            ):
                self.assertEqual(main(["status", "--no-packages"]), 0)
            text = output.getvalue()
            self.assertIn("update available: skillager 0.1.1", text)
            self.assertIn("uv tool upgrade skillager", text)

    def test_session_ids_cannot_escape_session_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            with self.assertRaisesRegex(ValueError, "invalid session id"):
                read_events(state, "../outside")
            with self.assertRaisesRegex(ValueError, "invalid session id"):
                redact_session(state, "../../outside")

    def test_status_json_reports_clean_approved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertIsNone(data["agent"])
            self.assertFalse(data["needs_setup"])
            self.assertEqual(data["review_needed"], 0)
            self.assertTrue(data["readiness"]["review_ready"])
            self.assertFalse(data["readiness"]["handoff_ready"])
            self.assertFalse(data["readiness"]["ready"])
            self.assertEqual(data["readiness"]["handoff"]["kind"], "agent-required")
            self.assertEqual(data["readiness"]["handoff"]["reason_code"], "agent_required")
            self.assertIsNone(data["readiness"]["handoff"]["command"])
            self.assertIn("status --agent codex", data["message"])
            self.assertFalse(data["lookback_pending"])

    def test_status_agent_controls_handoff_readiness_target(self) -> None:
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
                    self.assertEqual(main(["status", "--agent", "claude", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["agent"], "claude")
            self.assertEqual(data["readiness"]["handoff"]["command"], "skillager bootstrap --agent claude")
            self.assertEqual(data["readiness"]["handoff"]["reason_code"], "working_missing")
            self.assertNotIn("skillager bootstrap --agent codex", data["message"])

    def test_status_and_handoff_share_readiness_for_same_fixture(self) -> None:
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
                    materialize_working_skill(agents=["codex"], project_dir=root)
                status_output = StringIO()
                with redirect_stdout(status_output):
                    self.assertEqual(main(["status", "--agent", "codex", "--no-packages", "--json"]), 0)
                handoff_output = StringIO()
                with redirect_stdout(handoff_output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            status = json.loads(status_output.getvalue())
            handoff = json.loads(handoff_output.getvalue())
            self.assertEqual(status["readiness"], handoff["readiness"])
            self.assertTrue(status["readiness"]["ready"])
            self.assertEqual(status["readiness"]["exposure"]["available_on_demand"], 1)

    def test_status_reports_pending_lookback_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            meta = start_session(state, agent="codex")
            append_event(state, meta["session_id"], "skill_activated", {"skill_id": "community/gis"})
            end_session(state, agent="codex")
            meta = start_session(state, agent="codex")
            append_event(state, meta["session_id"], "skill_activated", {"skill_id": "community/gis"})
            end_session(state, agent="codex")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
                text = StringIO()
                with redirect_stdout(text):
                    self.assertEqual(main(["status", "--no-packages"]), 0)
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["lookback", "--json"]), 0)
                reviewed_output = StringIO()
                with redirect_stdout(reviewed_output):
                    self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertTrue(data["lookback_pending"])
            self.assertEqual(data["lookback_summary"]["recommendations"], 1)
            self.assertIn("Lookback available", data["message"])
            self.assertIn("overlap hint(s) (behavioral signals, not decisions)", data["message"])
            self.assertNotIn("overlap hint(s;", data["message"])
            self.assertIn("lookback pending", text.getvalue())
            self.assertIn("overlap hint(s) (behavioral signals, not decisions)", text.getvalue())
            self.assertNotIn("overlap hint(s;", text.getvalue())
            reviewed = json.loads(reviewed_output.getvalue())
            self.assertFalse(reviewed["lookback_pending"])
            self.assertTrue(reviewed["lookback_summary"]["reviewed"])

    def test_active_session_lookback_signal_collects_without_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            meta = start_session(state, agent="codex")
            append_event(state, meta["session_id"], "skill_activated", {"skill_id": "community/gis"})
            append_event(state, meta["session_id"], "skill_activated", {"skill_id": "community/gis"})
            append_event(state, meta["session_id"], "skill_activated", {"skill_id": "community/gis"})
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertFalse(data["lookback_pending"])
            self.assertTrue(data["lookback_summary"]["collecting"])
            self.assertEqual(data["lookback_summary"]["raw_recommendations"], 1)

    def test_status_respects_materialized_setup_audience_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            first = root / ".skills" / "gis-domain"
            second = root / ".skills" / "api-example"
            dev = root / ".skills" / "cuda-writing"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            dev.mkdir(parents=True)
            (first / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (second / "SKILL.md").write_text("# API Example\n\nUse API examples.\n", encoding="utf-8")
            (dev / "SKILL.md").write_text("# CUDA Writing\n\nUse CUDA implementation guidance.\n", encoding="utf-8")
            stdin = TtyStringIO("1\nn\ny\n1\ny\nn\n")
            stdout = TtyStringIO()
            with (
                patch("sys.stdin", stdin),
                patch("sys.stdout", stdout),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["setup", "--audience", "user", "--no-packages"]), 0)
            output = StringIO()
            with (
                redirect_stdout(output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertTrue(data["needs_setup"])
            self.assertEqual(data["selected"], 2)
            self.assertEqual(data["review_needed"], 1)
            self.assertEqual(data["scope"]["audience"], "user")

            all_output = StringIO()
            with (
                redirect_stdout(all_output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["status", "--no-packages", "--all", "--json"]), 0)
            all_data = json.loads(all_output.getvalue())
            self.assertTrue(all_data["needs_setup"])
            self.assertEqual(all_data["selected"], 3)
            self.assertEqual(all_data["review_needed"], 2)

    def test_status_ack_migration_without_pending_is_noop(self) -> None:
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
                    self.assertEqual(main(["status", "--no-packages", "--ack-migration", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertFalse(data["collection_migrations"]["pending"])

    def test_handoff_prioritizes_migration_repair(self) -> None:
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
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "migration-review-needed")
            self.assertEqual(data["next"]["command"], "skillager status --migration-details")

    def test_handoff_routes_clean_migration_to_ack(self) -> None:
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
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "migration-ack-needed")
            self.assertEqual(data["next"]["command"], "skillager status --ack-migration")
            self.assertEqual(data["state"]["migration"]["totals"]["needs_review"], 0)
            self.assertEqual(data["state"]["migration"]["totals"]["tag_needs_repair"], 0)

    def test_handoff_reports_setup_needed_before_artifact_repairs(self) -> None:
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
                    self.assertEqual(main(["handoff", "--agent", "codex"]), 0)
            text = output.getvalue()
            self.assertIn("Setup: needed, 1 unreviewed skill(s)", text)
            self.assertIn("Working skill: missing", text)
            self.assertIn("skillager setup", text)

    def test_handoff_ready_when_working_skill_and_note_are_current(self) -> None:
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
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertTrue(data["readiness"]["ready"])
            self.assertEqual(data["state"]["artifacts"]["working_skill"]["status"], "present")
            self.assertEqual(data["next"]["command"], None)
            self.assertIn("scored slate", data["next"]["message"])
            self.assertIn("tag approved skills", data["next"]["message"])
            self.assertIn("router, stub, native skill, or no new exposure", data["next"]["message"])
            self.assertIn("skillager list --summary-json --agent codex", data["next"]["next_commands"])
            self.assertIn('skillager search "<user-goal>" --trusted-only --agent codex --json', data["next"]["next_commands"])
            self.assertIn("skillager materialize --tag <task-tag> --mode router --agent codex --scope project", data["next"]["next_commands"])

            text = StringIO()
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
                redirect_stdout(text),
            ):
                self.assertEqual(main(["handoff", "--agent", "codex"]), 0)
            self.assertIn("Suggested commands:", text.getvalue())
            self.assertIn("skillager list --summary-json --agent codex", text.getvalue())

    def test_handoff_requires_setup_for_unapproved_skill_in_saved_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            approved = root / ".skills" / "approved"
            unapproved = root / ".skills" / "unapproved"
            approved.mkdir(parents=True)
            unapproved.mkdir(parents=True)
            (approved / "SKILL.md").write_text("# Approved\n\nUse approved guidance.\n", encoding="utf-8")
            (unapproved / "SKILL.md").write_text("# Unapproved\n\nUse unapproved guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                data = build_index(state, include_packages=False)
                by_id = {skill["id"]: skill for skill in data["skills"]}
                set_trust(state, "project/approved", "reviewed", by_id["project/approved"]["content_hash"], by_id["project/approved"]["source"])
                (state / "status_scope.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.status-scope.v1",
                            "selected_count": 2,
                            "agents": ["codex"],
                            "baseline": {skill["id"]: skill["content_hash"] for skill in data["skills"]},
                        }
                    ),
                    encoding="utf-8",
                )
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            handoff = json.loads(output.getvalue())
            self.assertEqual(handoff["status"], "setup-needed")
            self.assertEqual(handoff["state"]["setup"]["unreviewed"], 1)
            self.assertIn("skillager setup", handoff["next"]["next_commands"])

    def test_handoff_reports_stale_working_skill(self) -> None:
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
                sidecar = root / ".agents" / "skills" / "skillager-working" / "skillager.materialized.yaml"
                data = loads(sidecar.read_text(encoding="utf-8"))
                data["source_hash"] = "old-protocol"
                sidecar.write_text("\n".join(f"{key}: {value}" for key, value in data.items()) + "\n", encoding="utf-8")
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            handoff = json.loads(output.getvalue())
            self.assertEqual(handoff["status"], "artifact-attention-needed")
            self.assertEqual(handoff["state"]["artifacts"]["working_skill"]["status"], "stale")
            self.assertFalse(handoff["readiness"]["handoff_ready"])
            self.assertEqual(handoff["readiness"]["handoff"]["reason_code"], "working_stale")
            self.assertEqual(handoff["next"]["command"], "skillager bootstrap --agent codex")

    def test_handoff_reports_missing_project_note(self) -> None:
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
                (root / "AGENTS.md").unlink()
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            handoff = json.loads(output.getvalue())
            self.assertEqual(handoff["status"], "artifact-attention-needed")
            self.assertEqual(handoff["state"]["artifacts"]["project_notes"][0]["status"], "missing")
            self.assertFalse(handoff["readiness"]["handoff_ready"])
            self.assertEqual(handoff["readiness"]["handoff"]["reason_code"], "project_note_missing")
            self.assertEqual(handoff["next"]["command"], "skillager bootstrap --agent codex")

    def test_handoff_reports_unmanaged_working_skill_as_manual_repair(self) -> None:
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
                materialize_working_skill(agents=["codex"], project_dir=root)
                (root / ".agents" / "skills" / "skillager-working" / "skillager.materialized.yaml").unlink()
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            handoff = json.loads(output.getvalue())
            self.assertEqual(handoff["status"], "manual-artifact-repair-needed")
            self.assertEqual(handoff["state"]["artifacts"]["working_skill"]["status"], "unmanaged")
            self.assertEqual(handoff["readiness"]["handoff"]["reason_code"], "working_unmanaged")
            self.assertTrue(handoff["state"]["setup"]["needed"])
            self.assertIsNone(handoff["next"]["command"])
            self.assertIn("Move or remove unmanaged Skillager Working target", handoff["next"]["message"])

    def test_handoff_reports_unmaterialized_attached_tags_without_blocking_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gis"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS\n\nUse GIS guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertTrue(data["readiness"]["ready"])
            self.assertEqual(data["readiness"]["exposure"]["unmaterialized_attached_tags"], ["gis"])
            self.assertEqual(data["state"]["attached_tags"], ["gis"])
            self.assertEqual(data["state"]["unmaterialized_attached_tags"], ["gis"])
            self.assertIn("diagnostic only", data["state"]["unmaterialized_attached_tags_policy"])

    def test_handoff_reports_materialized_router_tags_as_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gis"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS\n\nUse GIS guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                    self.assertEqual(main(["materialize", "--tag", "gis", "--mode", "router", "--agent", "codex", "--scope", "project"]), 0)

                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)

                text_output = StringIO()
                with redirect_stdout(text_output):
                    self.assertEqual(main(["handoff", "--agent", "codex"]), 0)

            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertEqual(data["state"]["attached_tags"], ["gis"])
            self.assertEqual(data["state"]["materialized_router_tags"], ["gis"])
            self.assertEqual(data["state"]["unmaterialized_attached_tags"], [])
            self.assertIn("Existing materialized router tag(s)", data["next"]["message"])
            self.assertIn('skillager search "<user-goal>" --tag gis --approved-only --agent codex --json', data["next"]["next_commands"])
            self.assertIn("Materialized router tags: gis", text_output.getvalue())

    def test_handoff_prioritizes_lookback_over_unmaterialized_attached_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gis"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS\n\nUse GIS guidance.\n", encoding="utf-8")
            lookback_report = {
                "recommendations": [
                    {
                        "action": "materialize",
                        "skill_id": "community/gis",
                        "events": {"skill_activated": 2},
                        "sessions": ["sks_1", "sks_2"],
                        "session_count": 2,
                        "active_session_count": 0,
                    }
                ],
                "observed_overlaps": [],
                "candidate_session_count": 2,
                "active_candidate_sessions": 0,
                "completed_candidate_sessions": 2,
            }
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with patch("skillager.cli.build_lookback", return_value=lookback_report), redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "lookback-pending")
            self.assertEqual(data["next"]["command"], "skillager lookback")
            self.assertEqual(data["state"]["unmaterialized_attached_tags"], ["gis"])

    def test_handoff_does_not_block_on_active_only_lookback_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            lookback_report = {
                "recommendations": [
                    {
                        "action": "route-only",
                        "skill_id": "community/gis",
                        "events": {"skill_activated": 3},
                        "sessions": ["sks_active"],
                        "session_count": 1,
                        "active_session_count": 1,
                    }
                ],
                "observed_overlaps": [],
                "candidate_session_count": 1,
                "active_candidate_sessions": 1,
                "completed_candidate_sessions": 0,
            }
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                materialize_working_skill(agents=["codex"], project_dir=root)
                output = StringIO()
                with patch("skillager.cli.build_lookback", return_value=lookback_report), redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["status"], "ready")
            self.assertFalse(data["state"]["lookback"]["pending"])
            self.assertTrue(data["state"]["lookback"]["collecting"])

    def test_session_records_external_id_and_lookback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            meta = start_session(state, agent="codex", external_session_id="codex-123")
            skill = {
                "id": "demo/skill",
                "content_hash": "abc",
                "source": {"type": "project"},
                "entrypoint": "/tmp/SKILL.md",
            }
            from skillager.session import record_skill_event

            record_skill_event(state, "skill_activated", skill)
            events = read_events(state, meta["session_id"])
            self.assertEqual(events[0]["external_session_id"], "codex-123")
            report = build_lookback(state, agent="codex", external_session_id="codex-123")
            self.assertEqual(report["external_session_id"], "codex-123")
            self.assertIn("demo/skill", report["skills"])

    def test_lookback_recommends_route_only_for_single_session_and_block_for_harmful(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            meta = start_session(state, agent="codex", external_session_id="codex-123")
            session_id = meta["session_id"]

            append_event(state, session_id, "skill_activated", {"skill_id": "community/gis"})
            append_event(state, session_id, "skill_activated", {"skill_id": "community/gis"})
            append_event(state, session_id, "skill_activated", {"skill_id": "community/gis"})
            append_event(state, session_id, "feedback_not_useful", {"skill_id": "community/noisy"})
            append_event(state, session_id, "skill_rejected", {"skill_id": "community/noisy"})
            append_event(state, session_id, "feedback_harmful", {"skill_id": "community/risky"})

            report = build_lookback(state, session_id=session_id)
            actions = {item["skill_id"]: item["action"] for item in report["recommendations"]}
            self.assertEqual(actions["community/gis"], "route-only")
            self.assertEqual(actions["community/noisy"], "route-only")
            self.assertEqual(actions["community/risky"], "block")
            gis = next(item for item in report["recommendations"] if item["skill_id"] == "community/gis")
            self.assertEqual(gis["session_count"], 1)

    def test_lookback_materialize_recommendation_uses_recent_and_active_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            first = start_session(state, agent="codex", external_session_id="codex-1")
            append_event(state, first["session_id"], "skill_activated", {"skill_id": "community/gis"})
            append_event(state, first["session_id"], "skill_activated", {"skill_id": "community/gis"})
            second = start_session(state, agent="codex", external_session_id="codex-2")
            append_event(state, second["session_id"], "skill_activated", {"skill_id": "community/gis"})
            append_event(state, second["session_id"], "feedback_useful", {"skill_id": "community/gis"})

            report = build_lookback(state, session_id=second["session_id"], recent=2)
            rec = next(item for item in report["recommendations"] if item["skill_id"] == "community/gis")
            self.assertEqual(rec["action"], "materialize")
            self.assertEqual(rec["session_count"], 2)
            self.assertEqual(rec["active_session_count"], 2)
            self.assertEqual(set(rec["sessions"]), {first["session_id"], second["session_id"]})
            self.assertEqual(report["candidate_session_count"], 2)

    def test_session_prune_bounds_age_size_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            meta = start_session(state, agent="codex")
            for index in range(6):
                append_event(state, meta["session_id"], "skill_search", {"query_preview": f"query {index}", "top_ids": ["a", "b"]})
            result = prune_sessions(state, days=30, max_mb=5, max_events_per_session=3)
            events = read_events(state, meta["session_id"])
            self.assertEqual(result["trimmed_sessions"], 1)
            self.assertLessEqual(len(events), 3)
            self.assertEqual(events[0]["event"], "session_started")

    def test_session_prune_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".skillager"
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                self.assertEqual(main(["session", "start", "--agent", "codex"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["session", "prune", "--max-events-per-session", "2", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertIn("bytes_after", data)
            self.assertEqual(data["max_events_per_session"], 2)


if __name__ == "__main__":
    unittest.main()
