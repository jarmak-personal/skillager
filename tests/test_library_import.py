from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.library.importing import import_library_skill, import_preview
from skillager.library.metadata import load_library_provenance
from skillager.library.model import LibraryLayout
from skillager.library.service import accept_library_skill, initialize_library
from skillager.state.trust import set_trust
from tests.support import chdir


class PersonalLibraryImportTransactionTests(unittest.TestCase):
    def test_source_change_after_preview_refuses_before_library_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, state, catalog, library, source = self.workspace(root, "changing")
            with patch("pathlib.Path.home", return_value=root / "home"), chdir(project):
                initialize_library(catalog, path=library, no_git=True)
                preview = import_preview(state, catalog, "project/changing", project_dir=project)
                (source / "SKILL.md").write_text(
                    "# Changing\n\nUse content changed after the preview.\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "source changed since preview"):
                    import_library_skill(
                        state,
                        catalog,
                        "project/changing",
                        destination_name="changing",
                        expected_hash=preview["source_hash"],
                        expected_source_key=preview["source"]["source_key"],
                        project_dir=project,
                    )
            self.assertFalse((library / "skills" / "changing").exists())
            provenance = load_library_provenance(LibraryLayout.from_root(library))
            self.assertIsNotNone(provenance)
            self.assertEqual(provenance["skills"], {})  # type: ignore[index]

    def test_blocked_source_refuses_even_with_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, state, catalog, library, _source = self.workspace(root, "blocked")
            with patch("pathlib.Path.home", return_value=root / "home"), chdir(project):
                initialize_library(catalog, path=library, no_git=True)
                preview = import_preview(state, catalog, "project/blocked", project_dir=project)
                set_trust(
                    state,
                    "project/blocked",
                    "blocked",
                    preview["source_hash"],
                    {"type": "project"},
                )
                blocked_preview = import_preview(state, catalog, "project/blocked", project_dir=project)
                self.assertTrue(blocked_preview["blocked"])
                with self.assertRaisesRegex(ValueError, "source skill is blocked"):
                    import_library_skill(
                        state,
                        catalog,
                        "project/blocked",
                        destination_name="blocked",
                        expected_hash=blocked_preview["source_hash"],
                        expected_source_key=blocked_preview["source"]["source_key"],
                        project_dir=project,
                    )
            self.assertFalse((library / "skills" / "blocked").exists())

    def test_trust_write_failure_leaves_safe_pending_copy_repairable_by_accept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, state, catalog, library, _source = self.workspace(root, "trust-failure")
            with patch("pathlib.Path.home", return_value=root / "home"), chdir(project):
                initialize_library(catalog, path=library, no_git=True)
                preview = import_preview(state, catalog, "project/trust-failure", project_dir=project)
                with patch("skillager.library.importing.set_trust", side_effect=RuntimeError("trust unavailable")):
                    with self.assertRaisesRegex(ValueError, "copied but pending acceptance"):
                        import_library_skill(
                            state,
                            catalog,
                            "project/trust-failure",
                            destination_name="trust-failure",
                            expected_hash=preview["source_hash"],
                            expected_source_key=preview["source"]["source_key"],
                            project_dir=project,
                        )
                target = library / "skills" / "trust-failure"
                self.assertTrue(target.is_dir())
                accepted = accept_library_skill(
                    catalog,
                    "trust-failure",
                    expected_hash=preview["source_hash"],
                    project_dir=project,
                )
                self.assertEqual(accepted["skill"]["acceptance"], "accepted")

    @staticmethod
    def workspace(root: Path, name: str) -> tuple[Path, Path, Path, Path, Path]:
        project = root / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
        source = project / ".skills" / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            f"# {name.replace('-', ' ').title()}\n\nUse initial import guidance.\n",
            encoding="utf-8",
        )
        return project, root / "state", root / "catalog", root / "library", source

if __name__ == "__main__":
    unittest.main()
