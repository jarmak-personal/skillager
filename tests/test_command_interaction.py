from __future__ import annotations

import argparse
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from skillager.commands.importing import cmd_import
from skillager.commands.library import cmd_library_accept, cmd_library_restore
from tests.support import TtyStringIO


class MutationPromptTests(unittest.TestCase):
    def test_accept_import_and_restore_do_not_prompt_when_output_is_captured(self) -> None:
        cases = (
            (
                "accept",
                cmd_library_accept,
                argparse.Namespace(
                    skill="lib/demo",
                    yes=False,
                    override_lint=False,
                    reason=None,
                    confirmation_token=None,
                    json=False,
                ),
                "skillager.commands.library.library_acceptance_preview",
                self.accept_preview(),
                "skillager.commands.library.accept_library_skill",
            ),
            (
                "import",
                cmd_import,
                argparse.Namespace(
                    skill="project/demo",
                    destination_name=None,
                    yes=False,
                    override_lint=False,
                    reason=None,
                    confirmation_token=None,
                    json=False,
                ),
                "skillager.commands.importing.import_preview",
                self.import_preview(),
                "skillager.commands.importing.import_library_skill",
            ),
            (
                "restore",
                cmd_library_restore,
                argparse.Namespace(
                    skill="lib/demo",
                    to_hash="0123456789ab",
                    yes=False,
                    override_lint=False,
                    reason=None,
                    confirmation_token=None,
                    json=False,
                ),
                "skillager.commands.library.library_restore_preview",
                self.restore_preview(),
                "skillager.commands.library.restore_library_skill",
            ),
        )
        for name, command, args, preview_target, preview, mutation_target in cases:
            with self.subTest(name=name):
                output = StringIO()
                terminal_input = TtyStringIO("yes\n")
                with (
                    patch("sys.stdin", terminal_input),
                    redirect_stdout(output),
                    patch("builtins.input", side_effect=AssertionError("hidden prompt")),
                    patch(preview_target, return_value=preview),
                    patch(mutation_target) as mutation,
                    patch("skillager.commands.library.catalog_root", return_value=Path("/catalog")),
                    patch("skillager.commands.library.current_project_dir", return_value=Path("/project")),
                    patch("skillager.commands.importing.root", return_value=Path("/state")),
                    patch("skillager.commands.importing.catalog_root", return_value=Path("/catalog")),
                    patch("skillager.commands.importing.current_project_dir", return_value=Path("/project")),
                ):
                    self.assertEqual(command(args), 0)
                mutation.assert_not_called()
                self.assertEqual(terminal_input.tell(), 0)
                self.assertIn("Next: skillager", output.getvalue())

    @staticmethod
    def accept_preview() -> dict[str, object]:
        return {
            "schema": "skillager.library-accept.v1",
            "status": "preview",
            "skill": {"id": "lib/demo", "path": "/library/skills/demo", "working_hash": "a" * 64},
            "lint": {"status": "ok", "blocking_count": 0, "findings": []},
            "scan": {"risk": "low", "finding_count": 0, "findings": []},
            "requires_override": False,
            "git": {"mode": "disabled"},
        }

    @staticmethod
    def import_preview() -> dict[str, object]:
        return {
            "schema": "skillager.import.v1",
            "status": "preview",
            "source": {"id": "project/demo", "type": "project", "path": "/project/.skills/demo"},
            "destination": {"id": "lib/demo", "name": "demo", "path": "/library/skills/demo"},
            "source_hash": "b" * 64,
            "_source_key": "source-key",
            "_library_binding": {
                "status": "ready",
                "root": "/library",
                "library_id": "00000000-0000-0000-0000-000000000000",
            },
            "library": {"status": "ready", "root": "/library", "git_mode": "disabled"},
            "owner_review_required": True,
            "blocked": False,
            "lint": {"status": "ok", "blocking_count": 0, "findings": []},
            "scan": {"risk": "low", "finding_count": 0, "findings": []},
            "requires_override": False,
        }

    @staticmethod
    def restore_preview() -> dict[str, object]:
        return {
            "schema": "skillager.library-restore.v1",
            "status": "preview",
            "skill": {"id": "lib/demo"},
            "selected_version": {
                "content_hash": "c" * 64,
                "commit": "d" * 40,
                "short_hash": "c" * 12,
            },
            "current_hash": "e" * 64,
            "_current_tree_fingerprint": "f" * 64,
            "lint": {"status": "ok", "blocking_count": 0, "findings": []},
            "scan": {"risk": "low", "finding_count": 0, "findings": []},
            "requires_override": False,
            "stat": {"files_changed": 0, "additions": 0, "deletions": 0, "files": []},
        }


if __name__ == "__main__":
    unittest.main()
