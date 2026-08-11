from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main
from skillager.project_tags import (
    add_tag_skills,
    clear_tags,
    create_tag,
    delete_tag,
    load_tags,
    remove_tag_skills,
)


class SkillagerProjectTagTests(unittest.TestCase):
    def test_project_tag_mutations_are_atomic_under_concurrent_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            skill_ids = [f"community/skill-{index}" for index in range(16)]

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda skill_id: add_tag_skills(project, "shared", [skill_id]), skill_ids))

            self.assertEqual(len(results), len(skill_ids))
            self.assertEqual(load_tags(project)["tags"]["shared"]["skills"], sorted(skill_ids))
            json.loads((project / ".skillager" / "tags.json").read_text(encoding="utf-8"))

    def test_ordinary_project_tag_lifecycle_remains_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()

            self.assertEqual(create_tag(project, "Needs Review")["skills"], [])
            self.assertEqual(
                add_tag_skills(project, "Needs Review", ["community/two", "community/one"])["skills"],
                ["community/one", "community/two"],
            )
            self.assertEqual(remove_tag_skills(project, "Needs Review", ["community/one"])["skills"], ["community/two"])
            self.assertTrue(delete_tag(project, "Needs Review")["removed"])
            self.assertEqual(load_tags(project)["tags"], {})

            create_tag(project, "one")
            create_tag(project, "two")
            self.assertEqual(clear_tags(project), 2)
            self.assertFalse((project / ".skillager" / "tags.json").exists())
            self.assertEqual(load_tags(project)["tags"], {})

    def test_failed_atomic_replacement_preserves_existing_project_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            create_tag(project, "safe")
            path = project / ".skillager" / "tags.json"
            original = path.read_bytes()

            with patch("skillager.state.statefiles.os.replace", side_effect=OSError("injected replacement failure")):
                with self.assertRaisesRegex(OSError, "injected replacement failure"):
                    add_tag_skills(project, "safe", ["community/new"])

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".tags.json.*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "symlink creation requires elevated privileges on Windows")
    def test_symlinked_project_tags_file_is_rejected_without_touching_victim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            state = project / ".skillager"
            state.mkdir()
            victim = root / "victim.json"
            victim.write_bytes(b'{"schema":"victim","tags":{"sentinel":{"skills":[]}}}\n')
            original = victim.read_bytes()
            (state / "tags.json").symlink_to(victim)

            self._assert_unsafe_tag_operations_are_rejected(project, victim, original)
            self.assertFalse((root / ".skillager-locks").exists())

    @unittest.skipIf(os.name == "nt", "symlink creation requires elevated privileges on Windows")
    def test_symlinked_project_tag_state_directory_is_rejected_without_touching_victim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            victim_dir = root / "victim-state"
            victim_dir.mkdir()
            victim = victim_dir / "tags.json"
            victim.write_bytes(b'{"schema":"victim","tags":{"sentinel":{"skills":[]}}}\n')
            original = victim.read_bytes()
            (project / ".skillager").symlink_to(victim_dir, target_is_directory=True)

            self._assert_unsafe_tag_operations_are_rejected(project, victim, original)
            self.assertFalse((victim_dir / ".skillager-locks").exists())

    @unittest.skipIf(os.name == "nt", "symlink creation requires elevated privileges on Windows")
    def test_symlinked_project_tag_lock_directory_cannot_redirect_lock_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            state = project / ".skillager"
            state.mkdir()
            victim_dir = root / "victim-locks"
            victim_dir.mkdir()
            sentinel = victim_dir / "sentinel"
            sentinel.write_bytes(b"leave this alone\n")
            (state / ".skillager-locks").symlink_to(victim_dir, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "unsafe Skillager lock directory"):
                create_tag(project, "new")

            self.assertEqual([path.name for path in victim_dir.iterdir()], ["sentinel"])
            self.assertEqual(sentinel.read_bytes(), b"leave this alone\n")

    def test_non_directory_project_tag_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            state = project / ".skillager"
            state.write_text("not a directory\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                load_tags(project)
            with self.assertRaisesRegex(ValueError, "not a directory"):
                create_tag(project, "new")

            state.unlink()
            state.mkdir()
            (state / "tags.json").mkdir()
            with self.assertRaisesRegex(ValueError, "non-file"):
                load_tags(project)
            with self.assertRaisesRegex(ValueError, "non-file"):
                create_tag(project, "new")

    def _assert_unsafe_tag_operations_are_rejected(self, project: Path, victim: Path, original: bytes) -> None:
        operations = {
            "read": lambda: load_tags(project),
            "create": lambda: create_tag(project, "new"),
            "add": lambda: add_tag_skills(project, "sentinel", ["community/new"]),
            "remove": lambda: remove_tag_skills(project, "sentinel", ["community/old"]),
            "delete": lambda: delete_tag(project, "sentinel"),
            "clear": lambda: clear_tags(project),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                with self.assertRaisesRegex(ValueError, "symlinked"):
                    operation()
                self.assertEqual(victim.read_bytes(), original)

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

    def test_tag_sync_uses_caller_catalog_as_authority(self) -> None:
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
            self.assertEqual(tags["catalog_state_dir"], str(caller_catalog.resolve()))

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
                        with self.assertRaises(SystemExit) as cm:
                            main(["state", "migrate-tags", "--to", "projects", "--json"])
                    self.assertEqual(cm.exception.code, 2)
            self.assertIn("invalid choice: 'state'", stderr.getvalue())
            self.assertFalse((project / ".skillager" / "tags.json").exists())


if __name__ == "__main__":
    unittest.main()
