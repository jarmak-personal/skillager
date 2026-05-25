from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main


class SkillagerRemovedReadinessCommandTests(unittest.TestCase):

    def run_removed(self, command: str, *args: str) -> tuple[int, str, str, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        state = root / ".skillager"
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
            patch("pathlib.Path.home", return_value=root),
            chdir(root),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main([command, *args])
        return code, stdout.getvalue(), stderr.getvalue(), root

    def test_status_is_removed_with_agent_and_human_replacements(self) -> None:
        code, stdout, stderr, root = self.run_removed("status", "--json")

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("skillager working --json", stderr)
        self.assertIn("skillager doctor --agent <agent>", stderr)
        self.assertIn("skillager doctor --json", stderr)
        self.assertFalse((root / ".skillager").exists())

    def test_handoff_is_removed_with_working_and_doctor_replacements(self) -> None:
        code, stdout, stderr, root = self.run_removed("handoff", "--agent", "codex")

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("skillager working", stderr)
        self.assertIn("skillager doctor --agent <agent>", stderr)
        self.assertFalse((root / ".skillager").exists())

    def test_removed_commands_do_not_appear_in_top_level_help(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            main(["--help"])

        self.assertEqual(caught.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertNotIn(" status", help_text)
        self.assertNotIn(" handoff", help_text)
        self.assertNotIn(" bootstrap", help_text)

    def test_setup_help_does_not_advertise_removed_readiness_workflow(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            main(["setup", "--help"])

        self.assertEqual(caught.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertNotIn("bootstrap", help_text)
        self.assertNotIn("handoff", help_text)
        self.assertNotIn("status", help_text)


if __name__ == "__main__":
    unittest.main()
