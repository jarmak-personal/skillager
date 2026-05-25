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


class SkillagerProjectTagTests(unittest.TestCase):
    def test_setup_records_project_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            project = root / "project"
            skill = project / ".skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(catalog), "NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(project):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--no-packages", "--json"]), 0)
            registry = json.loads((catalog / "projects.json").read_text(encoding="utf-8"))
            self.assertIn(str(project.resolve()), registry["projects"])

    def test_tag_sync_copies_project_local_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            source = root / "source"
            dest = root / "dest"
            source.mkdir()
            dest.mkdir()
            (source / ".skillager").mkdir()
            (source / ".skillager" / "tags.json").write_text(
                json.dumps({"schema": "skillager.project-tags.v1", "tags": {"gis": {"skills": ["community/gis"]}}}, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(catalog), "NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(dest):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["tag", "sync", "--from", str(source), "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["results"][0]["tag"], "gis")
            tags = json.loads((dest / ".skillager" / "tags.json").read_text(encoding="utf-8"))
            self.assertEqual(tags["tags"]["gis"]["skills"], ["community/gis"])

    def test_tag_sync_preserves_source_catalog_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_catalog = root / "source-catalog"
            caller_catalog = root / "caller-catalog"
            source = root / "source"
            dest = root / "dest"
            source.mkdir()
            dest.mkdir()
            (source / ".skillager").mkdir()
            (source / ".skillager" / "tags.json").write_text(
                json.dumps(
                    {
                        "schema": "skillager.project-tags.v1",
                        "catalog_state_dir": str(source_catalog.resolve()),
                        "tags": {"gis": {"skills": ["community/gis"]}},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(caller_catalog), "NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(dest):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["tag", "sync", "--from", str(source)]), 0)
            tags = json.loads((dest / ".skillager" / "tags.json").read_text(encoding="utf-8"))
            self.assertEqual(tags["catalog_state_dir"], str(source_catalog.resolve()))

    def test_tag_sync_rejects_missing_destination_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            source = root / "source"
            dest = root / "missing-dest"
            source.mkdir()
            (source / ".skillager").mkdir()
            (source / ".skillager" / "tags.json").write_text(
                json.dumps({"schema": "skillager.project-tags.v1", "tags": {"gis": {"skills": ["community/gis"]}}}, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(catalog), "NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(root):
                self.assertEqual(main(["tag", "sync", "--from", str(source), "--to", str(dest)]), 2)
            self.assertFalse(dest.exists())

    def test_state_migrate_tags_is_removed_without_writing_project_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            project = root / "project"
            project.mkdir()
            catalog.mkdir()
            (catalog / "tags.json").write_text(json.dumps({"tags": {"gis": ["community/gis"]}}, indent=2) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(catalog), "NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root):
                with chdir(project):
                    stderr = StringIO()
                    with redirect_stderr(stderr):
                        self.assertEqual(main(["state", "migrate-tags", "--to", "projects", "--json"]), 2)
            self.assertIn("`state` was removed", stderr.getvalue())
            self.assertFalse((project / ".skillager" / "tags.json").exists())


if __name__ == "__main__":
    unittest.main()
