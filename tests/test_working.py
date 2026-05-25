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
from skillager.index import build_index


def write_skill(root: Path, body: str = "# Demo\n\nUse demo guidance.\n") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")


class SkillagerWorkingTests(unittest.TestCase):

    def run_cli(self, args: list[str], *, root: Path, state: Path) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        env = {
            "SKILLAGER_STATE_DIR": str(state),
            "SKILLAGER_CATALOG_STATE_DIR": str(state),
            "NO_COLOR": "1",
        }
        with (
            patch.dict(os.environ, env),
            patch("pathlib.Path.home", return_value=root),
            chdir(root),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def setup_project(self, root: Path, state: Path) -> None:
        write_skill(root / ".skills" / "base", "# Base\n\nUse base guidance.\n")
        code, _, stderr = self.run_cli(
            ["setup", "--source", "project", "--accept-low", "--no-packages", "--summary-json"],
            root=root,
            state=state,
        )
        self.assertEqual(code, 0, stderr)

    def listed_skill(self, root: Path, state: Path, skill_id: str) -> dict[str, object]:
        code, stdout, stderr = self.run_cli(["list", "--no-packages", "--json", "--full-json"], root=root, state=state)
        self.assertEqual(code, 0, stderr)
        by_id = {skill["id"]: skill for skill in json.loads(stdout)}
        return by_id[skill_id]

    def indexed_skill(self, root: Path, state: Path, skill_id: str) -> dict[str, object]:
        env = {
            "SKILLAGER_STATE_DIR": str(state),
            "SKILLAGER_CATALOG_STATE_DIR": str(state),
            "NO_COLOR": "1",
        }
        with (
            patch.dict(os.environ, env),
            patch("pathlib.Path.home", return_value=root),
            chdir(root),
        ):
            by_id = {skill["id"]: skill for skill in build_index(state, include_packages=False)["skills"]}
        return by_id[skill_id]

    def test_working_clean_empty_project_can_proceed_without_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"

            code, stdout, stderr = self.run_cli(["working", "--json"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)
            self.assertEqual(data["status"], "ready")
            self.assertTrue(data["can_proceed"])
            self.assertTrue(data["readiness"]["ready"])
            self.assertTrue(data["readiness"]["can_proceed"])
            self.assertTrue(data["readiness"]["artifacts_ready"])
            self.assertIsNone(data["readiness"]["reason_code"])
            self.assertEqual(data["readiness"]["exposure"]["approved"], 0)

    def test_working_does_not_auto_approve_project_skill_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            write_skill(root / ".agents" / "skills" / "local-tool")

            code, stdout, stderr = self.run_cli(["working", "--json"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)
            self.assertEqual(data["status"], "review-needed")
            self.assertFalse(data["can_proceed"])
            self.assertFalse(data["setup_complete"])
            self.assertEqual(data["auto_approved_project_count"], 0)
            self.assertEqual(data["pending_owner_review_count"], 1)
            self.assertFalse(data["readiness"]["review_ready"])
            self.assertTrue(data["readiness"]["artifacts_ready"])
            self.assertEqual(data["readiness"]["reason_code"], "review_needed")
            self.assertNotIn("handoff", json.dumps(data["readiness"]))
            self.assertEqual(self.indexed_skill(root, state, "project/local-tool")["trust"], "discovered")

    def test_working_does_not_auto_approve_project_native_skill_after_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            self.setup_project(root, state)
            write_skill(root / ".agents" / "skills" / "local-tool")

            code, stdout, stderr = self.run_cli(["working"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            self.assertEqual(stdout, "")
            skill = self.indexed_skill(root, state, "project/local-tool")
            self.assertEqual(skill["trust"], "discovered")

    def test_working_json_reports_no_project_skill_auto_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            self.setup_project(root, state)
            write_skill(root / ".claude" / "skills" / "claude-tool")

            code, stdout, stderr = self.run_cli(["working", "--agent", "claude", "--json"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)
            self.assertEqual(data["agent"], "claude")
            self.assertEqual(data["readiness"]["artifacts"]["command"], "skillager doctor --agent claude --fix")
            self.assertEqual(data["next"]["command"], "skillager setup --agent claude")
            self.assertEqual(data["auto_approved_project_count"], 0)
            self.assertEqual(data["auto_approved_project_skills"], [])
            self.assertEqual(self.indexed_skill(root, state, "project/claude-tool")["trust"], "discovered")

    def test_working_does_not_approve_lint_warned_project_local_skill_after_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            self.setup_project(root, state)
            skill_dir = root / ".agents" / "skills" / "bad-manifest"
            write_skill(skill_dir, "# Bad Manifest\n\nUse this project-local skill.\n")
            (skill_dir / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\nunknown: true\n",
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["working", "--json"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)
            self.assertEqual(data["auto_approved_project_skills"], [])
            skill = self.indexed_skill(root, state, "project/bad-manifest")
            self.assertEqual(skill["trust"], "lint_blocked")
            code, body, stderr = self.run_cli(["show", "project/bad-manifest", "--content"], root=root, state=state)
            self.assertEqual(code, 2)
            self.assertEqual(body, "")

    def test_working_reports_pending_external_review_only_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            self.setup_project(root, state)
            code, stdout, stderr = self.run_cli(["working"], root=root, state=state)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stdout, "")

            write_skill(root / "community" / ".agents" / "skills" / "external-tool")
            code, stdout, stderr = self.run_cli(["working"], root=root, state=state)

            self.assertEqual(code, 0, stderr)
            self.assertEqual(stdout, "")
            self.assertEqual(self.indexed_skill(root, state, "community/external-tool")["trust"], "discovered")

            code, stdout, stderr = self.run_cli(["working", "--json"], root=root, state=state)
            self.assertEqual(code, 0, stderr)
            data = json.loads(stdout)
            self.assertEqual(data["new_external_review_count"], 0)
            self.assertEqual(data["pending_external_review_count"], 1)
            self.assertEqual(data["pending_external_review"][0]["id"], "community/external-tool")
            self.assertEqual(data["pending_owner_review_count"], 1)


if __name__ == "__main__":
    unittest.main()
