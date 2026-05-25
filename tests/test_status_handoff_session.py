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
from skillager.materialize import materialize_working_skill
from skillager.simple_yaml import loads
from skillager.trust import content_hash, set_trust


def write_manifest(skill_dir: Path, audience: str) -> None:
    skill_dir.joinpath("skillager.yaml").write_text(
        "schema: skillager.skill.v1\n"
        "audience:\n"
        f"  - {audience}\n"
        "activation:\n"
        "  default: manual\n",
        encoding="utf-8",
    )


OLD_HANDOFF_NOTE = (
    "Run `skillager handoff` at session start. Follow its Next item, use only available/materialized "
    "Skillager-managed skills, ask before setup or approval changes, ask the user to run `skillager doctor --agent <agent>` if state seems off, "
    "and report curation/exposure changes."
)


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
            self.assertIn("pending owner review: 1", text)
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
            self.assertEqual(data["pending_owner_review"], 0)
            self.assertTrue(data["readiness"]["review_ready"])
            self.assertFalse(data["readiness"]["handoff_ready"])
            self.assertFalse(data["readiness"]["ready"])
            self.assertEqual(data["readiness"]["handoff"]["kind"], "agent-required")
            self.assertEqual(data["readiness"]["handoff"]["reason_code"], "agent_required")
            self.assertIsNone(data["readiness"]["handoff"]["command"])
            self.assertIn("status --agent codex", data["message"])

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

    def test_handoff_reports_legacy_project_note_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            note = root / "AGENTS.md"
            note.write_text(
                f"Existing notes.\n## Skillager\n{OLD_HANDOFF_NOTE}\nOther notes stay.\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                json_output = StringIO()
                with redirect_stdout(json_output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
                text_output = StringIO()
                with redirect_stdout(text_output):
                    self.assertEqual(main(["handoff", "--agent", "codex"]), 0)
            updated = note.read_text(encoding="utf-8")
            self.assertEqual(updated.count("## Skillager"), 1)
            self.assertIn(OLD_HANDOFF_NOTE, updated)
            self.assertIn("Other notes stay.", updated)
            data = json.loads(json_output.getvalue())
            self.assertEqual(data["note_updates"], [])
            self.assertEqual(data["state"]["artifacts"]["project_notes"][0]["status"], "stale")
            self.assertNotIn("Updated project note:", text_output.getvalue())

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
            write_manifest(first, "user")
            write_manifest(second, "user")
            write_manifest(dev, "dev")
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
            self.assertEqual(data["pending_owner_review"], 1)
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
            self.assertEqual(all_data["pending_owner_review"], 2)

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
            self.assertIn("Setup: needed, 1 skill(s) pending owner review", text)
            self.assertIn("Working skill: missing", text)
            self.assertIn("skillager setup --agent codex", text)

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
            self.assertIn("tag available skills", data["next"]["message"])
            self.assertIn("router, stub, native skill, or no new exposure", data["next"]["message"])
            self.assertIn("skillager list --summary-json --agent codex", data["next"]["next_commands"])
            self.assertIn('skillager search "<user-goal>" --agent codex --json', data["next"]["next_commands"])
            self.assertIn("skillager expose --tag <task-tag> --mode router --agent codex --scope project", data["next"]["next_commands"])

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
            self.assertEqual(handoff["state"]["setup"]["pending_owner_review"], 1)
            self.assertIn("skillager setup --agent codex", handoff["next"]["next_commands"])

    def test_handoff_explains_same_content_duplicate_review(self) -> None:
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
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
            handoff = json.loads(output.getvalue())
            self.assertEqual(handoff["status"], "setup-needed")
            self.assertNotIn("source-key approval", handoff["next"]["message"])
            self.assertNotIn("duplicate_content", handoff["state"])

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
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)
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
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)
                    self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
                    self.assertEqual(main(["expose", "--tag", "gis", "--mode", "router", "--agent", "codex", "--scope", "project"]), 0)

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
            self.assertIn("Existing exposed router tag(s)", data["next"]["message"])
            self.assertIn('skillager search "<user-goal>" --tag gis --agent codex --json', data["next"]["next_commands"])
            self.assertIn("Exposed router tags: gis", text_output.getvalue())

    def test_handoff_exposure_breakdown_reports_routed_and_stubbed_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            routed = root / ".skills" / "gis-domain"
            stubbed = root / ".skills" / "api-example"
            routed.mkdir(parents=True)
            stubbed.mkdir(parents=True)
            (routed / "SKILL.md").write_text("# GIS Domain\n\nUse GIS guidance.\n", encoding="utf-8")
            (stubbed / "SKILL.md").write_text("# API Example\n\nUse API guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
                    self.assertEqual(main(["tag", "create", "mapping"]), 0)
                    self.assertEqual(main(["tag", "add", "mapping", "project/gis-domain"]), 0)
                    self.assertEqual(main(["project", "attach-tag", "mapping"]), 0)
                    self.assertEqual(main(["expose", "--tag", "mapping", "--mode", "router", "--agent", "codex", "--scope", "project"]), 0)
                    self.assertEqual(main(["expose", "project/api-example", "--mode", "stub", "--agent", "codex", "--scope", "project"]), 0)

                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["handoff", "--agent", "codex", "--json"]), 0)
                text_output = StringIO()
                with redirect_stdout(text_output):
                    self.assertEqual(main(["handoff", "--agent", "codex"]), 0)

            data = json.loads(output.getvalue())
            exposure = data["readiness"]["exposure"]
            self.assertEqual(exposure["exposed"], 2)
            self.assertEqual(exposure["router_tags"], 1)
            self.assertEqual(exposure["routed"], 1)
            self.assertEqual(exposure["stubbed"], 1)
            self.assertEqual(exposure["native"], 0)
            self.assertEqual(exposure["available_on_demand"], 0)
            self.assertEqual(exposure["count_basis"], "available source entries")
            self.assertEqual(exposure["available_source_entries_on_demand"], 0)
            self.assertIn("2 exposed entries (1 router tag(s), 1 routed, 1 stubbed), 0 available entries on demand", text_output.getvalue())

if __name__ == "__main__":
    unittest.main()
