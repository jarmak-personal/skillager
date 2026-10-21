from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.library import project_import
from skillager.state.trust import content_hash
from tests.behavior.support import make_basic_workspace, write_basic_skill


class ProjectImportPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skillager-project-import-policy-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.base)
        self.state, self.catalog = self.base / "state/project", self.base / "state/catalog"
        self.library = self.base / "library"
        initialized = self.cli.run("library", "init", "--path", str(self.library), "--no-git", "--json")
        self.assertEqual(initialized.code, 0, initialized.stderr)
        write_basic_skill(self.project)
        previous = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)
        env = patch.dict(os.environ, self.cli.env)
        env.start()
        self.addCleanup(env.stop)

    def test_five_thousand_unrelated_owned_skills_never_enter_hash_or_catalog_owners(self):
        for index in range(5000):
            (self.library / "skills" / f"unrelated-{index}").mkdir()

        def project_hash(path):
            self.assertFalse(Path(path).is_relative_to(self.library), f"unexpected canonical hash: {path}")
            return content_hash(path)

        with patch("skillager.skills.index.content_hash", side_effect=project_hash), patch(
            "skillager.library.project_import.content_hash", side_effect=project_hash
        ), patch("skillager.library.importing.import_inventory", side_effect=AssertionError("broad import inventory")), patch(
            "skillager.library.importing.refresh_collection", side_effect=AssertionError("unrelated library refresh")
        ):
            observed = project_import.project_import_inventory(self.state, self.catalog, self.project)
            self.assertEqual(observed["summary"]["eligible"], 1)
            preview = project_import.prepare_project_import(self.state, self.catalog, self.project, observed["default_selection"])
            result, code = project_import.apply_project_import(self.state, self.catalog, self.project, preview["selection"], preview["confirmation_token"])
            self.assertEqual(code, 0)
            self.assertEqual(result["counts"]["accepted"], 1)

    def test_response_budget_refuses_before_usable_confirmation_or_library_effect(self):
        observed = project_import.project_import_inventory(self.state, self.catalog, self.project)
        with patch.dict(project_import.LIMITS, response_bytes=2048):
            with self.assertRaisesRegex(project_import.ReviewRefusal, "Complete response exceeds"):
                project_import.prepare_project_import(self.state, self.catalog, self.project, observed["default_selection"])
        self.assertFalse((self.library / "skills/gis-domain").exists())

    def test_changed_review_under_mutation_lock_refuses_before_any_import(self):
        observed = project_import.project_import_inventory(self.state, self.catalog, self.project)
        preview = project_import.prepare_project_import(self.state, self.catalog, self.project, observed["default_selection"])
        changed = {**preview, "confirmation_token": "0" * 64}
        with patch.object(project_import, "prepare_project_import", side_effect=[preview, changed]), patch.object(
            project_import, "import_library_skill_locked", side_effect=AssertionError("unexpected effect")
        ):
            with self.assertRaisesRegex(ValueError, "stale"):
                project_import.apply_project_import(self.state, self.catalog, self.project, preview["selection"], preview["confirmation_token"])
        self.assertFalse((self.library / "skills/gis-domain").exists())
