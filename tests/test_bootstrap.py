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


class SkillagerBootstrapRemovedCommandTests(unittest.TestCase):

    def test_bootstrap_is_invalid_choice_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                chdir(root),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                with self.assertRaises(SystemExit) as cm:
                    main(["bootstrap", "--agent", "codex"])

            self.assertEqual(cm.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("invalid choice: 'bootstrap'", stderr.getvalue())
            self.assertFalse((root / ".agents").exists())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
