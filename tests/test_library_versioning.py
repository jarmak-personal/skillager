from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from skillager.library.service import accept_library_skill, initialize_library, library_where, new_library_skill
from skillager.library.versioning import (
    library_restore_preview,
    resolve_history_version,
    restore_library_skill,
)
from tests.support import chdir


class LibraryVersionSelectionTests(unittest.TestCase):
    def test_content_hash_prefix_must_be_unique_and_never_resolves_git_ids(self) -> None:
        versions = [
            {"content_hash": "aa" + "0" * 62},
            {"content_hash": "ab" + "1" * 62},
            {"content_hash": "ff" + "2" * 62},
        ]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_history_version(versions, "a")
        self.assertEqual(resolve_history_version(versions, "aa")["content_hash"], "aa" + "0" * 62)
        with self.assertRaisesRegex(ValueError, "not found"):
            resolve_history_version(versions, "1234567890abcdef1234567890abcdef12345678")
        for invalid in ("", "not-a-hash", "0" * 65):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "content-hash prefix"):
                resolve_history_version(versions, invalid)

    def test_trust_failure_after_restore_commit_is_pending_and_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
            catalog = root / "catalog"
            library = root / "library"
            with patch("pathlib.Path.home", return_value=root / "home"), chdir(project):
                initialize_library(catalog, path=library)
                new_library_skill(catalog, "trust-restore")
                skill_file = library / "skills" / "trust-restore" / "SKILL.md"
                skill_file.write_text("# Trust Restore\n\nFirst version.\n", encoding="utf-8")
                first_preview = library_where(catalog, "trust-restore")["skill"]
                accept_library_skill(
                    catalog,
                    "trust-restore",
                    expected_hash=first_preview["working_hash"],
                )
                first_hash = first_preview["working_hash"]
                skill_file.write_text("# Trust Restore\n\nSecond version.\n", encoding="utf-8")
                second_preview = library_where(catalog, "trust-restore")["skill"]
                accept_library_skill(
                    catalog,
                    "trust-restore",
                    expected_hash=second_preview["working_hash"],
                )
                excluded = skill_file.parent / "draft.tmp"
                excluded.write_text("untracked local artifact\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "canonical content tree"):
                    library_restore_preview(catalog, "trust-restore", first_hash)
                excluded.unlink()
                restore_preview = library_restore_preview(catalog, "trust-restore", first_hash)
                selected = restore_preview["selected_version"]
                skill_file.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "tree changed since restore preview"):
                    restore_library_skill(
                        catalog,
                        "trust-restore",
                        expected_hash=selected["content_hash"],
                        expected_commit=selected["commit"],
                        expected_current_hash=restore_preview["current_hash"],
                        expected_current_fingerprint=restore_preview["current_tree_fingerprint"],
                    )
                skill_file.chmod(0o644)
                restore_preview = library_restore_preview(catalog, "trust-restore", first_hash)
                selected = restore_preview["selected_version"]
                with patch("skillager.library.versioning.set_trust", side_effect=RuntimeError("trust unavailable")):
                    with self.assertRaisesRegex(ValueError, "committed but pending acceptance"):
                        restore_library_skill(
                            catalog,
                            "trust-restore",
                            expected_hash=selected["content_hash"],
                            expected_commit=selected["commit"],
                            expected_current_hash=restore_preview["current_hash"],
                            expected_current_fingerprint=restore_preview["current_tree_fingerprint"],
                        )
                pending = library_where(catalog, "trust-restore")["skill"]
                self.assertEqual(pending["working_hash"], first_hash)
                self.assertEqual(pending["head_hash"], first_hash)
                self.assertEqual(pending["acceptance"], "pending")
                repaired = accept_library_skill(catalog, "trust-restore", expected_hash=first_hash)
                self.assertEqual(repaired["skill"]["status"], "clean")


if __name__ == "__main__":
    unittest.main()
