from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import BODY_SENTINEL, make_basic_workspace


class PersonalLibraryFoundationBehaviorTests(unittest.TestCase):
    def test_status_before_init_is_read_only_and_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            catalog = root / "state" / "catalog"

            result = cli.run("library", "status", "--json")

            self.assertEqual(result.code, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.json()["schema"], "skillager.library-status.v1")
            self.assertEqual(result.json()["status"], "not-initialized")
            self.assertEqual(result.json()["next_command"], "skillager library init")
            self.assertFalse(catalog.exists())

    def test_no_git_custom_init_is_idempotent_and_registered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "owned-skills"

            initialized = cli.run("library", "init", "--path", str(library), "--no-git", "--json")
            repeated = cli.run("library", "init", "--json")
            status = cli.run("library", "status", "--json")

            self.assertEqual(initialized.code, 0, initialized.stderr)
            self.assertEqual(initialized.json()["schema"], "skillager.library-init.v1")
            self.assertTrue(initialized.json()["created"])
            self.assertEqual(initialized.json()["git"]["mode"], "disabled")
            self.assertFalse((library / ".git").exists())
            self.assertTrue((library / "skills" / ".gitkeep").is_file())
            identity = json.loads((library / ".skillager" / "library.json").read_text(encoding="utf-8"))
            provenance = json.loads((library / ".skillager" / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(identity["schema"], "skillager.library.v1")
            self.assertEqual(identity["git"], {"mode": "disabled"})
            self.assertEqual(provenance, {"schema": "skillager.library-provenance.v1", "skills": {}})
            registration = json.loads((root / "state" / "catalog" / "collections.json").read_text(encoding="utf-8"))["collections"]["lib"]
            self.assertEqual(registration["kind"], "library")
            self.assertEqual(Path(registration["library_root"]), library.resolve())
            self.assertEqual(registration["library_id"], identity["library_id"])
            self.assertEqual(repeated.code, 0, repeated.stderr)
            self.assertFalse(repeated.json()["created"])
            self.assertEqual(repeated.json()["library"]["library_id"], identity["library_id"])
            self.assertEqual(status.json()["status"], "ready")

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_default_init_creates_clean_commit_with_command_scoped_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            cli.env["GIT_CONFIG_GLOBAL"] = str(root / "missing-global-gitconfig")
            cli.env["GIT_CONFIG_NOSYSTEM"] = "1"

            result = cli.run("library", "init", "--json")

            self.assertEqual(result.code, 0, result.stderr)
            data = result.json()
            library = root / "home" / ".skillager" / "library"
            self.assertEqual(data["git"]["mode"], "system")
            self.assertTrue(data["git"]["clean"])
            self.assertEqual(data["commit"]["identity"]["source"], "skillager-fallback")
            author = subprocess.run(
                ["git", "log", "-1", "--format=%an <%ae>"],
                cwd=library,
                env=cli.env,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
            self.assertEqual(author, "Skillager <skillager@localhost>")
            local_name = subprocess.run(
                ["git", "config", "--local", "--get", "user.name"],
                cwd=library,
                env=cli.env,
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(local_name.returncode, 0)

    def test_existing_skill_is_indexed_pending_and_status_stays_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "existing-library"
            skill = library / "skills" / "gis-owned"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "# Owned GIS\n\nCanonical spatial workflow.\n\n" + BODY_SENTINEL + "\n",
                encoding="utf-8",
            )

            initialized = cli.run("library", "init", "--path", str(library), "--no-git", "--json")
            status = cli.run("library", "status", "lib/gis-owned", "--json")
            search = cli.run("search", "canonical spatial", "--json", "--no-session-record")
            content = cli.run("show", "lib/gis-owned", "--content")

            self.assertEqual(initialized.code, 0, initialized.stderr)
            self.assertEqual(initialized.json()["indexed"], 1)
            self.assertNotIn(BODY_SENTINEL, initialized.stdout)
            self.assertEqual(status.code, 0, status.stderr)
            self.assertEqual(status.json()["skill"]["id"], "lib/gis-owned")
            self.assertIn("working_hash", status.json()["skill"])
            self.assertNotIn(BODY_SENTINEL, status.stdout)
            self.assertEqual(search.json(), [])
            self.assertEqual(content.code, 2)
            self.assertNotIn(BODY_SENTINEL, content.stdout)
            self.assertNotIn(BODY_SENTINEL, content.stderr)
            self.assertIn("not available", content.stderr)

    def test_conflicting_registration_and_relocation_fail_without_creating_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            catalog = root / "state" / "catalog"
            catalog.mkdir(parents=True)
            ordinary = root / "ordinary"
            ordinary.mkdir()
            (catalog / "collections.json").write_text(
                json.dumps({"collections": {"lib": {"name": "lib", "path": str(ordinary)}}}) + "\n",
                encoding="utf-8",
            )
            target = root / "must-not-exist"

            conflict = cli.run("library", "init", "--path", str(target), "--no-git")

            self.assertEqual(conflict.code, 2)
            self.assertIn("collection name 'lib' is already in use", conflict.stderr)
            self.assertFalse(target.exists())

    def test_registered_library_refuses_implicit_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            first = root / "first"
            second = root / "second"
            self.assertEqual(cli.run("library", "init", "--path", str(first), "--no-git").code, 0)

            relocation = cli.run("library", "init", "--path", str(second), "--no-git")

            self.assertEqual(relocation.code, 2)
            self.assertIn("relocation is not implicit", relocation.stderr)
            self.assertFalse(second.exists())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_existing_repository_with_staged_changes_is_refused_before_metadata_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "staged-library"
            library.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=library, check=True)
            staged = library / "existing.txt"
            staged.write_text("existing\n", encoding="utf-8")
            subprocess.run(["git", "add", "existing.txt"], cwd=library, check=True)

            result = cli.run("library", "init", "--path", str(library))

            self.assertEqual(result.code, 2)
            self.assertIn("staged changes", result.stderr)
            self.assertFalse((library / ".skillager").exists())
            self.assertFalse((library / "skills").exists())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_existing_repository_with_conflicts_is_refused_before_metadata_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "conflicted-library"
            library.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.name", "Library Test"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.email", "library@example.invalid"], cwd=library, check=True)
            conflict_file = library / "conflict.txt"
            conflict_file.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "conflict.txt"], cwd=library, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=library, check=True)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=library,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
            subprocess.run(["git", "checkout", "--quiet", "-b", "other"], cwd=library, check=True)
            conflict_file.write_text("other\n", encoding="utf-8")
            subprocess.run(["git", "commit", "--quiet", "-am", "other"], cwd=library, check=True)
            subprocess.run(["git", "checkout", "--quiet", branch], cwd=library, check=True)
            conflict_file.write_text("current\n", encoding="utf-8")
            subprocess.run(["git", "commit", "--quiet", "-am", "current"], cwd=library, check=True)
            merge = subprocess.run(
                ["git", "merge", "other"],
                cwd=library,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(merge.returncode, 0)

            result = cli.run("library", "init", "--path", str(library))

            self.assertEqual(result.code, 2)
            self.assertIn("unresolved conflicts", result.stderr)
            self.assertFalse((library / ".skillager").exists())
            self.assertFalse((library / "skills").exists())

    def test_missing_git_has_explicit_no_git_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            cli.env["PATH"] = str(root / "empty-path")
            failed = root / "failed"
            usable = root / "usable"

            result = cli.run("library", "init", "--path", str(failed))
            fallback = cli.run("library", "init", "--path", str(usable), "--no-git", "--json")

            self.assertEqual(result.code, 2)
            self.assertIn("git executable is unavailable", result.stderr)
            self.assertIn("--no-git", result.stderr)
            self.assertFalse(failed.exists())
            self.assertEqual(fallback.code, 0, fallback.stderr)
            self.assertEqual(fallback.json()["git"]["mode"], "disabled")


if __name__ == "__main__":
    unittest.main()
