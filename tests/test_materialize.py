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
from skillager.cli import _print_expose_results, build_parser, main
from skillager.index import build_index, load_index
from skillager.materialize import materialize_skills
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


class SkillagerMaterializeTests(unittest.TestCase):

    def test_materialize_index_mode_is_removed(self) -> None:
        parser = build_parser()
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as cm:
            parser.parse_args(["expose", "--tag", "gis", "--mode", "index", "--agent", "codex"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        self.assertIn("'index'", stderr.getvalue())

    def test_materialize_command_is_removed(self) -> None:
        parser = build_parser()
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as cm:
            parser.parse_args(["materialize", "project/demo"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("invalid choice: 'materialize'", stderr.getvalue())

    def test_expose_list_and_remove_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stderr = StringIO()
            with (
                redirect_stderr(stderr),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["expose", "--list", "--remove", "project-demo", "--agent", "codex"]), 2)
            self.assertIn("--list cannot be combined with --remove", stderr.getvalue())

    def test_expose_management_rejects_agent_and_all_agents(self) -> None:
        cases = [
            ["expose", "--list", "--agent", "codex", "--all-agents"],
            ["expose", "--remove", "project-demo", "--agent", "codex", "--all-agents"],
        ]
        for argv in cases:
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root / ".skillager"
                stderr = StringIO()
                with (
                    redirect_stderr(stderr),
                    patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                    patch("pathlib.Path.home", return_value=root),
                    chdir(root),
                ):
                    self.assertEqual(main(argv), 2)
                self.assertIn("--agent cannot be combined with --all-agents", stderr.getvalue())

    def test_expose_management_rejects_ignored_filters_and_irrelevant_flags(self) -> None:
        cases = [
            (["expose", "--list", "--source", "project"], "--source"),
            (["expose", "--list", "--dry-run"], "--dry-run"),
            (["expose", "--list", "--mode", "stub"], "--mode"),
            (["expose", "--remove", "project-demo", "--audience", "user"], "--audience"),
            (["expose", "--remove", "project-demo", "--force"], "--force"),
            (["expose", "--remove", "project-demo", "--include-blocked"], "--include-blocked"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root / ".skillager"
                stderr = StringIO()
                with (
                    redirect_stderr(stderr),
                    patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                    patch("pathlib.Path.home", return_value=root),
                    chdir(root),
                ):
                    self.assertEqual(main(argv), 2)
                self.assertIn(expected, stderr.getvalue())

    def test_materialize_requires_explicit_selection_or_all_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stderr = StringIO()
            with (
                redirect_stderr(stderr),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["expose", "--agent", "codex"]), 2)
            self.assertIn("expose requires explicit skill IDs, --tag, or --all-reviewed", stderr.getvalue())
            self.assertIn("skillager doctor --agent <agent> --fix", stderr.getvalue())

    def test_materialize_all_reviewed_requires_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stderr = StringIO()
            with (
                redirect_stderr(stderr),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["expose", "--all-reviewed", "--agent", "codex"]), 2)
            self.assertIn("--all-reviewed requires explicit --mode", stderr.getvalue())

    def test_materialize_rejects_all_reviewed_include_unreviewed_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stderr = StringIO()
            with (
                redirect_stderr(stderr),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["expose", "--all-reviewed", "--mode", "native", "--include-unreviewed", "--agent", "codex"]), 2)
            self.assertIn("--all-reviewed cannot be combined with --include-unreviewed", stderr.getvalue())

    def test_materialize_does_not_write_first_party_handoff_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
                    repeat = StringIO()
                    with redirect_stdout(repeat):
                        self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())
            self.assertTrue((root / ".agents" / "skills" / "project-demo" / "SKILL.md").exists())
            self.assertIn("project/demo: exposed", repeat.getvalue())
            self.assertNotIn("Next step", repeat.getvalue())

    def test_materialize_does_not_repair_existing_project_agent_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            legacy = (
                "Run `skillager working` at session start. Use only reviewed/exposed Skillager-managed skills; "
                "ask the user to run `skillager doctor --agent codex` if review or repair is needed."
            )
            (root / "AGENTS.md").write_text(
                f"Existing project notes.\n## Skillager \n\n{legacy}\nOther notes stay.\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            text = (root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("skillager working", text)
            self.assertIn("skillager doctor --agent codex", text)
            self.assertNotIn("handoff", text)
            self.assertNotIn("status", text)
            self.assertNotIn("bootstrap", text)
            self.assertIn("Other notes stay.", text)

    def test_materialize_all_reviewed_materializes_selected_filter_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            user_skill = root / ".skills" / "gis-domain"
            dev_skill = root / ".skills" / "commit"
            user_skill.mkdir(parents=True)
            dev_skill.mkdir(parents=True)
            (user_skill / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (dev_skill / "SKILL.md").write_text("# Commit\n\nUse commit workflow guidance.\n", encoding="utf-8")
            write_manifest(user_skill, "user")
            write_manifest(dev_skill, "dev")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["expose", "--all-reviewed", "--mode", "native", "--audience", "user", "--agent", "codex"]), 0)
            self.assertIn("project/gis-domain: exposed", output.getvalue())
            self.assertNotIn("project/commit: exposed", output.getvalue())
            self.assertTrue((root / ".agents" / "skills" / "project-gis-domain" / "SKILL.md").exists())
            self.assertFalse((root / ".agents" / "skills" / "project-commit" / "SKILL.md").exists())

    def test_materialize_prints_next_steps_for_new_skill_in_existing_skillager_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            first = root / ".skills" / "first"
            second = root / ".skills" / "second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "SKILL.md").write_text("# First\n\nUse first guidance.\n", encoding="utf-8")
            (second / "SKILL.md").write_text("# Second\n\nUse second guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/first", "--agent", "codex"]), 0)
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["expose", "project/second", "--agent", "codex"]), 0)
            self.assertIn("project/second: exposed", output.getvalue())
            self.assertIn("Next step", output.getvalue())

    def test_materialize_output_only_hides_routine_working_skill_results(self) -> None:
        results = [
            {"skill_id": "skillager/working", "status": "materialized", "target": "/tmp/working", "reason": None},
            {"skill_id": "skillager/working", "status": "skipped", "target": "/tmp/working", "reason": "already up to date"},
            {"skill_id": "skillager/working", "status": "skipped", "target": "/tmp/working", "reason": "permission denied"},
            {"skill_id": "project/demo", "status": "materialized", "target": "/tmp/demo", "reason": None},
        ]
        output = StringIO()
        with redirect_stdout(output):
            _print_expose_results(results)
        text = output.getvalue()
        self.assertIn("skillager/working: skipped /tmp/working (permission denied)", text)
        self.assertIn("project/demo: exposed /tmp/demo", text)
        self.assertNotIn("already up to date", text)

    def test_materialize_leaves_existing_agent_instruction_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")
            self.assertNotIn("## Skillager", (root / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_materialize_all_agents_leaves_agent_instruction_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex", "--agent", "claude"]), 0)
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")
            self.assertEqual((root / "CLAUDE.md").read_text(encoding="utf-8"), "# Claude\n")

    def test_materialize_claude_only_does_not_create_handoff_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["expose", "project/demo", "--agent", "claude"]), 0)
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "CLAUDE.md").exists())
            self.assertIn("project/demo: exposed", output.getvalue())
            self.assertNotIn(str(root / "AGENTS.md"), output.getvalue())

    def test_materialize_missing_skill_id_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stderr = StringIO()
            with (
                redirect_stderr(stderr),
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                self.assertEqual(main(["expose", "path/missing", "--mode", "stub", "--agent", "codex"]), 2)
            self.assertIn("skill not found: path/missing", stderr.getvalue())

    def test_activate_refuses_unreviewed_skill_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse ordinary guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root):
                    build_index(state, include_packages=False)
                self.assertEqual(main(["activate", "project/demo"]), 2)
                self.assertEqual(main(["activate", "project/demo", "--force", "--no-session-record"]), 0)

    def test_materialize_copies_reviewed_skill_to_project_agent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            (skill_dir / "skill.oms.sig").write_text("{}\n", encoding="utf-8")
            (skill_dir / "skill-card.md").write_text("# Skill Card\n\nRelease evidence.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            target = root / ".agents" / "skills" / "project-demo"
            self.assertTrue((target / "SKILL.md").exists())
            self.assertFalse((target / "skillager.yaml").exists())
            self.assertFalse((target / "skill.oms.sig").exists())
            self.assertFalse((target / "skill-card.md").exists())
            self.assertTrue((target / "skillager.materialized.yaml").exists())

    def test_expose_json_result_uses_exposure_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    exposed_output = StringIO()
                    with redirect_stdout(exposed_output):
                        self.assertEqual(main(["expose", "project/demo", "--agent", "codex", "--json"]), 0)
                    dry_run_output = StringIO()
                    with redirect_stdout(dry_run_output):
                        self.assertEqual(main(["expose", "project/demo", "--agent", "codex", "--dry-run", "--json"]), 0)

            exposed = json.loads(exposed_output.getvalue())
            self.assertEqual(exposed[0]["schema"], "skillager.exposure-result.v1")
            self.assertEqual(exposed[0]["status"], "exposed")
            self.assertEqual(exposed[0]["exposure_id"], "project-demo")
            self.assertEqual(exposed[0]["mode"], "native")
            self.assertTrue(exposed[0]["restart_required"])
            self.assertNotIn("materialized", exposed_output.getvalue())

            would_expose = json.loads(dry_run_output.getvalue())
            self.assertEqual(would_expose[0]["status"], "would_expose")
            self.assertEqual(would_expose[0]["exposure_id"], "project-demo")
            self.assertFalse(would_expose[0]["restart_required"])
            self.assertNotIn("would_write", dry_run_output.getvalue())

    def test_expose_list_and_remove_manage_sidecar_backed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
                    unmanaged = root / ".agents" / "skills" / "unmanaged"
                    unmanaged.mkdir(parents=True)
                    (unmanaged / "SKILL.md").write_text("# Unmanaged\n\nUse unmanaged guidance.\n", encoding="utf-8")
                    malformed = root / ".agents" / "skills" / "malformed"
                    malformed.mkdir(parents=True)
                    (malformed / "SKILL.md").write_text("# Malformed\n\nUse malformed guidance.\n", encoding="utf-8")
                    (malformed / "skillager.materialized.yaml").write_text(
                        "source_id: project/malformed\nsource_type: project\n",
                        encoding="utf-8",
                    )
                    listed = StringIO()
                    with redirect_stdout(listed):
                        self.assertEqual(main(["expose", "--list", "--agent", "codex", "--json"]), 0)
                    payload = json.loads(listed.getvalue())
                    self.assertEqual(payload["schema"], "skillager.exposures.v1")
                    self.assertEqual(len(payload["exposures"]), 1)
                    self.assertEqual(payload["exposures"][0]["exposure_id"], "project-demo")
                    self.assertEqual(payload["exposures"][0]["skill_id"], "project/demo")
                    self.assertEqual(payload["exposures"][0]["mode"], "native")
                    stderr = StringIO()
                    with redirect_stderr(stderr):
                        self.assertEqual(main(["expose", "--remove", "unmanaged", "--agent", "codex"]), 2)
                    dry_removed = StringIO()
                    with redirect_stdout(dry_removed):
                        self.assertEqual(main(["expose", "--remove", "project-demo", "--agent", "codex", "--dry-run"]), 0)
                    self.assertIn("project-demo: would_remove", dry_removed.getvalue())
                    self.assertTrue((root / ".agents" / "skills" / "project-demo").exists())
                    removed = StringIO()
                    with redirect_stdout(removed):
                        self.assertEqual(main(["expose", "--remove", "project-demo", "--agent", "codex"]), 0)
            self.assertIn("project-demo: removed", removed.getvalue())
            self.assertFalse((root / ".agents" / "skills" / "project-demo").exists())
            self.assertTrue((root / ".agents" / "skills" / "unmanaged" / "SKILL.md").exists())
            self.assertTrue((root / ".agents" / "skills" / "malformed" / "SKILL.md").exists())

    def test_expose_remove_rejects_ambiguous_id_and_removes_one_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex", "--agent", "claude"]), 0)
                    codex_target = root / ".agents" / "skills" / "project-demo"
                    claude_target = root / ".claude" / "skills" / "project-demo"
                    self.assertTrue(codex_target.exists())
                    self.assertTrue(claude_target.exists())

                    ambiguous = StringIO()
                    with redirect_stderr(ambiguous):
                        self.assertEqual(main(["expose", "--remove", "project-demo", "--all-agents"]), 2)
                    self.assertIn("ambiguous exposure id: project-demo", ambiguous.getvalue())
                    self.assertTrue(codex_target.exists())
                    self.assertTrue(claude_target.exists())

                    removed = StringIO()
                    with redirect_stdout(removed):
                        self.assertEqual(main(["expose", "--remove", "project-demo", "--agent", "codex"]), 0)
                    self.assertIn("project-demo: removed", removed.getvalue())
                    self.assertFalse(codex_target.exists())
                    self.assertTrue(claude_target.exists())

    def test_materialize_stub_writes_tiny_activation_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--mode", "stub", "--agent", "codex"]), 0)
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["show", "project/demo", "--json"]), 0)
                    activated = StringIO()
                    with redirect_stdout(activated):
                        self.assertEqual(main(["activate", "project/demo", "--from-stub", "project-demo", "--no-session-record"]), 0)
                    self.assertEqual(main(["activate", "project/demo", "--from-stub", "wrong-stub", "--no-session-record"]), 2)
            target = root / ".agents" / "skills" / "project-demo"
            stub = (target / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("This is a Skillager stub", stub)
            self.assertIn("skillager activate project/demo --from-stub project-demo", stub)
            self.assertNotIn("Use project guidance.", stub.split("Before following", 1)[-1])
            sidecar = (target / "skillager.materialized.yaml").read_text(encoding="utf-8")
            self.assertIn("source_type: skillager-stub", sidecar)
            data = json.loads(output.getvalue())
            self.assertEqual(data["skill"]["exposure"], "stub")
            self.assertEqual(data["skill"]["exposed_via"][0]["kind"], "stub")
            self.assertIn("# Demo Skill", activated.getvalue())

    def test_materialize_stub_uses_skill_id_for_generic_source_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("Use demo guidance.\n\n## Arguments\n\nNone.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--mode", "stub", "--agent", "codex"]), 0)
            stub = (root / ".agents" / "skills" / "project-demo" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("# project/demo\n", stub)

    def test_materialize_existing_native_skill_does_not_create_prefixed_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            native = root / ".agents" / "skills" / "gis-domain"
            native.mkdir(parents=True)
            (native / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(["expose", "project/gis-domain", "--agent", "codex"]), 0)
            self.assertIn("project/gis-domain: already_native", output.getvalue())
            self.assertTrue((native / "SKILL.md").exists())
            self.assertFalse((root / ".agents" / "skills" / "project-gis-domain" / "SKILL.md").exists())
            indexed = load_index(state)["skills"][0]
            self.assertEqual(indexed["native"]["agent"], "codex")
            listing = StringIO()
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}), patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root), redirect_stdout(listing):
                self.assertEqual(main(["list", "--no-packages", "--json", "--full-json"]), 0)
            skill = json.loads(listing.getvalue())[0]
            self.assertEqual(skill["exposure_targets"][0]["exposure_status"], "existing")
            self.assertFalse((state / "native_inventory.json").exists())

    def test_materialize_copies_supporting_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            scripts = skill_dir / "scripts"
            scripts.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            (scripts / "helper.py").write_text("print('helper')\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            target = root / ".agents" / "skills" / "project-demo"
            self.assertEqual((target / "scripts" / "helper.py").read_text(encoding="utf-8"), "print('helper')\n")

    def test_materialize_skips_symlinked_supporting_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            secret = root / "secret.txt"
            secret.write_text("SECRET\n", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            os.symlink(secret, skill_dir / "secret-link.txt")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    data = build_index(state, include_packages=False)
                    skill = data["skills"][0]
                    set_trust(state, skill["id"], "reviewed", skill["content_hash"], skill["source"])
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            target = root / ".agents" / "skills" / "project-demo"
            self.assertFalse((target / "secret-link.txt").exists())

    def test_materialize_slug_collision_uses_hashed_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / ".skills" / "nested"
            second = root / ".skills" / "flat"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "SKILL.md").write_text("# First\n\nUse first guidance.\n", encoding="utf-8")
            (second / "SKILL.md").write_text("# Second\n\nUse second guidance.\n", encoding="utf-8")
            skills = [
                {
                    "id": "project/a/b",
                    "root": str(first),
                    "entrypoint": str(first / "SKILL.md"),
                    "source": {"type": "project"},
                    "content_hash": content_hash(first),
                    "trust": "reviewed",
                    "scan": {"risk": "low"},
                },
                {
                    "id": "project/a-b",
                    "root": str(second),
                    "entrypoint": str(second / "SKILL.md"),
                    "source": {"type": "project"},
                    "content_hash": content_hash(second),
                    "trust": "reviewed",
                    "scan": {"risk": "low"},
                },
            ]
            with patch("pathlib.Path.home", return_value=root), chdir(root):
                results = materialize_skills(skills, agents=["codex"], scope="project", project_dir=root)
            self.assertEqual([item["status"] for item in results], ["materialized", "materialized"])
            base = root / ".agents" / "skills"
            self.assertTrue((base / "project-a-b" / "SKILL.md").exists())
            fallback = [path for path in base.iterdir() if path.name.startswith("project-a-b-")]
            self.assertEqual(len(fallback), 1)
            self.assertIn("Second", (fallback[0] / "SKILL.md").read_text(encoding="utf-8"))

    def test_materialize_skips_unreviewed_skill_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    build_index(state, include_packages=False)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
            target = root / ".agents" / "skills" / "project-demo"
            self.assertFalse((target / "SKILL.md").exists())

    def test_materialize_does_not_overwrite_customized_copy_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse project guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state)}):
                with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root), chdir(root):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages"]), 0)
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
                    target = root / ".agents" / "skills" / "project-demo"
                    (target / "SKILL.md").write_text("# Customized\n\nLocal change.\n", encoding="utf-8")
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex"]), 0)
                    self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "# Customized\n\nLocal change.\n")
                    self.assertEqual(main(["expose", "project/demo", "--agent", "codex", "--force"]), 0)
                    self.assertIn("Demo Skill", (target / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
