from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main
from skillager.materialize import working_source_hash
from skillager.simple_yaml import loads


class SkillagerBootstrapTests(unittest.TestCase):

    def test_bootstrap_writes_codex_project_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            working = root / ".agents" / "skills" / "skillager-working"
            note = root / "AGENTS.md"
            self.assertTrue((working / "SKILL.md").exists())
            self.assertTrue((working / "skillager.materialized.yaml").exists())
            self.assertIn("skillager handoff", note.read_text(encoding="utf-8"))
            self.assertFalse((root / "CLAUDE.md").exists())
            self.assertIn("codex working_skill: materialized", output.getvalue())
            self.assertIn("codex project_note: materialized", output.getvalue())
            self.assertIn("Ready: 2 of 2 artifacts current.", output.getvalue())

    def test_bootstrap_refreshes_legacy_status_project_agent_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = (
                "Run `skillager status` at session start. Use only reviewed/materialized Skillager-managed skills; "
                "ask the user to run `skillager setup` if review is needed."
            )
            (root / "AGENTS.md").write_text(
                f"Existing project notes.\n## Skillager \n\n{legacy}\nOther notes stay.\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            text = (root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(text.count("## Skillager"), 1)
            self.assertIn("skillager handoff", text)
            self.assertNotIn("skillager status", text)
            self.assertIn("Other notes stay.", text)

    def test_bootstrap_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(StringIO()):
                self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            output = StringIO()
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(output):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--json"]), 0)
            data = json.loads(output.getvalue())
            artifacts = {item["kind"]: item for item in data["artifacts"]}
            self.assertEqual(artifacts["working_skill"]["status"], "skipped")
            self.assertEqual(artifacts["working_skill"]["reason"], "already up to date")
            self.assertEqual(artifacts["project_note"]["status"], "skipped")
            self.assertEqual(artifacts["project_note"]["reason"], "already up to date")

    def test_bootstrap_writes_claude_project_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["bootstrap", "--agent", "claude"]), 0)
            working = root / ".claude" / "skills" / "skillager-working"
            self.assertTrue((working / "SKILL.md").exists())
            self.assertTrue((working / "skillager.materialized.yaml").exists())
            self.assertIn("skillager handoff", (root / "CLAUDE.md").read_text(encoding="utf-8"))
            self.assertFalse((root / "AGENTS.md").exists())

    def test_bootstrap_all_agents_writes_both_agent_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(StringIO()):
                self.assertEqual(main(["bootstrap", "--all-agents"]), 0)
            codex_working = root / ".agents" / "skills" / "skillager-working"
            claude_working = root / ".claude" / "skills" / "skillager-working"
            self.assertTrue((codex_working / "SKILL.md").exists())
            self.assertTrue((claude_working / "SKILL.md").exists())
            self.assertIn("skillager handoff", (root / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("skillager handoff", (root / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_bootstrap_dry_run_json_does_not_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = StringIO()
            with (
                redirect_stdout(output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
            ):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--dry-run", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["schema"], "skillager.bootstrap.v1")
            self.assertEqual(data["summary"]["by_status"], {"would_write": 2})
            self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())
            self.assertFalse((root / "AGENTS.md").exists())

    def test_bootstrap_refreshes_stale_managed_working_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".agents" / "skills" / "skillager-working"
            env = {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(StringIO()):
                self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            sidecar = target / "skillager.materialized.yaml"
            data = loads(sidecar.read_text(encoding="utf-8"))
            data["source_hash"] = "old-protocol"
            sidecar.write_text("\n".join(f"{key}: {value}" for key, value in data.items()) + "\n", encoding="utf-8")
            output = StringIO()
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(output):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--json"]), 0)
            result = json.loads(output.getvalue())
            working = next(item for item in result["artifacts"] if item["kind"] == "working_skill")
            self.assertEqual(working["status"], "materialized")
            refreshed = loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(refreshed["source_hash"], working_source_hash("codex"))

    def test_bootstrap_refuses_unmanaged_working_skill_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".agents" / "skills" / "skillager-working"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# Local Working\n", encoding="utf-8")
            output = StringIO()
            with (
                redirect_stdout(output),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
            ):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--json"]), 11)
            data = json.loads(output.getvalue())
            working = next(item for item in data["artifacts"] if item["kind"] == "working_skill")
            note = next(item for item in data["artifacts"] if item["kind"] == "project_note")
            self.assertEqual(working["status"], "skipped")
            self.assertTrue(working["unmanaged_artifact_blocked"])
            self.assertEqual(note["reason"], "working skill not ready")
            self.assertFalse((target / "skillager.materialized.yaml").exists())
            self.assertFalse((root / "AGENTS.md").exists())

    def test_bootstrap_refuses_customized_working_skill_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".agents" / "skills" / "skillager-working"
            env = {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(StringIO()):
                self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            with (target / "SKILL.md").open("a", encoding="utf-8") as handle:
                handle.write("\n# Local note\n")
            output = StringIO()
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(output):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--json"]), 11)
            data = json.loads(output.getvalue())
            working = next(item for item in data["artifacts"] if item["kind"] == "working_skill")
            self.assertEqual(working["status"], "skipped")
            self.assertTrue(working["local_customization_blocked"])
            self.assertFalse(working["unmanaged_artifact_blocked"])

    def test_bootstrap_force_overwrites_unmanaged_working_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".agents" / "skills" / "skillager-working"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# Local Working\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(["bootstrap", "--agent", "codex", "--force"]), 0)
            self.assertTrue((target / "skillager.materialized.yaml").exists())
            self.assertIn("Skillager Working", (target / "SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("skillager handoff", (root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_bootstrap_does_not_trust_project_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            env = {
                "SKILLAGER_STATE_DIR": str(state),
                "SKILLAGER_CATALOG_STATE_DIR": str(state),
                "SKILLAGER_NO_UPDATE_CHECK": "1",
                "NO_COLOR": "1",
            }
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(StringIO()):
                self.assertEqual(main(["bootstrap", "--agent", "codex"]), 0)
            status_output = StringIO()
            with patch.dict(os.environ, env), chdir(root), redirect_stdout(status_output):
                self.assertEqual(main(["status", "--no-packages", "--json"]), 0)
            status = json.loads(status_output.getvalue())
            self.assertEqual(status["available"], 0)
            self.assertEqual(status["pending_owner_review"], 1)

    def test_bootstrap_requires_explicit_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(root / ".skillager"), "SKILLAGER_CATALOG_STATE_DIR": str(root / ".skillager"), "NO_COLOR": "1"}),
                chdir(root),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    main(["bootstrap"])
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
