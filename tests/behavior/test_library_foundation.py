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
            self.assertEqual(
                result.json()["next_command_argv"],
                ["skillager", "library", "init"],
            )
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
            self.assertIn("history is disabled", initialized.json()["advisories"][0])
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

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_new_refuses_internal_and_escaping_skill_symlink_aliases_without_writing_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "owned-skills"
            self.assertEqual(
                cli.run("library", "init", "--path", str(library), "--no-git").code,
                0,
            )

            internal_target = library / "skills" / "internal-target"
            internal_alias = library / "skills" / "internal-alias"
            internal_alias.symlink_to(internal_target, target_is_directory=True)
            refused_internal = cli.run("library", "new", "internal-alias")

            self.assertEqual(refused_internal.code, 2)
            self.assertIn("must not be a symlink alias", refused_internal.stderr)
            self.assertFalse(internal_target.exists())

            outside_target = root / "outside-draft"
            outside_alias = library / "skills" / "outside-alias"
            outside_alias.symlink_to(outside_target, target_is_directory=True)
            refused_outside = cli.run("library", "new", "outside-alias")

            self.assertEqual(refused_outside.code, 2)
            self.assertIn("escapes the library", refused_outside.stderr)
            self.assertFalse(outside_target.exists())
            self.assertEqual(
                sorted(path.name for path in (library / "skills").iterdir()),
                [".gitkeep", "internal-alias", "outside-alias"],
            )

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
            self.assertIn("no Git remote", data["advisories"][0])
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

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_first_new_skill_initializes_the_default_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            cli.env["GIT_CONFIG_GLOBAL"] = str(root / "missing-global-gitconfig")
            cli.env["GIT_CONFIG_NOSYSTEM"] = "1"
            library = root / "home" / ".skillager" / "library"

            invalid = cli.run("library", "new", "/", "--json")
            self.assertEqual(invalid.code, 2)
            self.assertFalse(library.exists())

            created = cli.run("library", "new", "first-owned", "--json")

            self.assertEqual(created.code, 0, created.stderr)
            self.assertEqual(created.json()["status"], "pending")
            self.assertEqual(created.json()["skill"]["id"], "lib/first-owned")
            self.assertNotIn("library_initialized", created.json())
            self.assertTrue((library / ".git").is_dir())
            self.assertTrue((library / "skills" / "first-owned" / "SKILL.md").is_file())
            status = cli.run("library", "status", "--json")
            self.assertEqual(status.json()["status"], "ready")
            self.assertEqual(Path(status.json()["library"]["root"]), library.resolve())

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
            self.assertIn("pending exact-hash acceptance", content.stderr)
            self.assertIn("skillager library accept lib/gis-owned", content.stderr)

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

    def test_moved_library_is_diagnosed_and_requires_explicit_identity_checked_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project, cli = make_basic_workspace(root)
            first = root / "first"
            moved = root / "moved"
            self.assertEqual(cli.run("library", "init", "--path", str(first), "--no-git").code, 0)
            self.assertEqual(cli.run("library", "new", "portable").code, 0)
            self.assertEqual(cli.run_confirmed("library", "accept", "portable", "--yes").code, 0)
            shutil.move(first, moved)

            status = cli.run("library", "status", "--json")
            self.assertEqual(status.code, 0, status.stderr)
            self.assertEqual(status.json()["status"], "degraded")
            self.assertIn("path is missing", status.json()["warnings"][0])
            self.assertEqual(status.json()["git"]["mode"], "unavailable")
            self.assertEqual(status.json()["git"]["reason"], "library-path-missing")
            self.assertEqual(status.json()["recovery"]["action"], "relocate")
            self.assertEqual(status.json()["recovery"]["required_arguments"], ["--path"])
            self.assertNotIn("next_command_argv", status.json())
            plain_status = cli.run("library", "status")
            self.assertEqual(plain_status.code, 0, plain_status.stderr)
            self.assertIn("Git: unavailable (library path missing)", plain_status.stdout)
            self.assertNotIn("Git: disabled (--no-git)", plain_status.stdout)
            working = cli.run("working", "--agent", "codex", "--json")
            self.assertEqual(working.code, 0, working.stderr)
            self.assertTrue(working.json()["can_proceed"])
            self.assertEqual(working.json()["library"]["status"], "degraded")
            self.assertEqual(working.json()["library"]["recovery"]["action"], "relocate")
            self.assertEqual(working.json()["library"]["recovery"]["required_arguments"], ["--path"])
            self.assertNotIn("next_command_argv", working.json()["library"])
            self.assertIsNone(working.json()["next"]["command"])
            self.assertEqual(working.json()["next"]["next_commands"], [])
            owned = working.json()["pending_owned_changes"]
            self.assertEqual(owned[0]["status"], "missing")
            self.assertEqual(owned[0]["command"], "skillager library status")
            self.assertNotIn("library accept", working.stdout)
            plain_working = cli.run("working", "--agent", "codex")
            self.assertEqual(plain_working.code, 0, plain_working.stderr)
            self.assertIn("Personal library unavailable (does not block other work).", plain_working.stdout)
            self.assertIn("skillager library status", plain_working.stdout)
            self.assertNotIn("library accept", plain_working.stdout)
            doctor = cli.run("doctor", "--no-packages", "--json")
            self.assertEqual(doctor.code, 0, doctor.stderr)
            self.assertEqual(doctor.json()["status"], "ready")
            self.assertEqual(doctor.json()["library"]["status"], "degraded")
            plain_doctor = cli.run("doctor", "--no-packages")
            self.assertEqual(plain_doctor.code, 0, plain_doctor.stderr)
            self.assertIn("Library: degraded", plain_doctor.stdout)
            self.assertIn("recovery: skillager library status", plain_doctor.stdout)

            collections_path = root / "state" / "catalog" / "collections.json"
            before = collections_path.read_bytes()
            imposter = root / "imposter"
            shutil.copytree(moved, imposter)
            identity_path = imposter / ".skillager" / "library.json"
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["library_id"] = "11111111-1111-1111-1111-111111111111"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            refused = cli.run("library", "relocate", "--path", str(imposter), "--yes")
            self.assertEqual(refused.code, 2)
            self.assertIn("does not have the registered personal-library identity", refused.stderr)
            self.assertEqual(collections_path.read_bytes(), before)

            preview = cli.run("library", "relocate", "--path", str(moved), "--json")
            self.assertEqual(preview.code, 0, preview.stderr)
            self.assertEqual(preview.json()["status"], "preview")
            self.assertEqual(collections_path.read_bytes(), before)

            applied = cli.run("library", "relocate", "--path", str(moved), "--yes", "--json")
            self.assertEqual(applied.code, 0, applied.stderr)
            self.assertEqual(applied.json()["status"], "relocated")
            self.assertEqual(Path(applied.json()["library"]["root"]), moved.resolve())
            self.assertEqual(cli.run("library", "status", "--json").json()["status"], "ready")

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
    def test_ignored_required_paths_fail_before_writes_and_retry_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "ignored-library"
            library.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.name", "Library Test"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.email", "library@example.invalid"], cwd=library, check=True)
            ignore = library / ".gitignore"
            ignore.write_text(".skillager/\nskills/.gitkeep\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=library, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "ignore required paths"], cwd=library, check=True)

            refused = cli.run("library", "init", "--path", str(library))

            self.assertEqual(refused.code, 2)
            self.assertIn("required library paths are ignored by Git", refused.stderr)
            self.assertIn(".skillager/library.json", refused.stderr)
            self.assertIn(".skillager/provenance.json", refused.stderr)
            self.assertIn("skills/.gitkeep", refused.stderr)
            self.assertFalse((library / ".skillager").exists())
            self.assertFalse((library / "skills").exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=library,
                    text=True,
                    stdout=subprocess.PIPE,
                    check=True,
                ).stdout,
                "",
            )

            ignore.write_text(".cache/\n", encoding="utf-8")
            subprocess.run(["git", "commit", "--quiet", "-am", "allow required paths"], cwd=library, check=True)
            retried = cli.run("library", "init", "--path", str(library), "--json")

            self.assertEqual(retried.code, 0, retried.stderr)
            self.assertTrue(retried.json()["created"])
            self.assertTrue(retried.json()["history"]["available"])
            tracked = subprocess.run(
                ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
                cwd=library,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(tracked, [".skillager/library.json", ".skillager/provenance.json", "skills/.gitkeep"])

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_untracked_required_metadata_degrades_history_availability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "untracked-metadata-library"
            self.assertEqual(cli.run("library", "init", "--path", str(library)).code, 0)
            (library / ".gitignore").write_text(".skillager/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=library, env=cli.env, check=True)
            subprocess.run(
                ["git", "rm", "--quiet", "--cached", ".skillager/library.json", ".skillager/provenance.json"],
                cwd=library,
                env=cli.env,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Library Test",
                    "-c",
                    "user.email=library@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "drop required metadata",
                ],
                cwd=library,
                env=cli.env,
                check=True,
            )

            status = cli.run("library", "status", "--json")

            self.assertEqual(status.code, 0, status.stderr)
            self.assertEqual(status.json()["status"], "degraded")
            self.assertEqual(status.json()["history"], {"available": False, "reason": "metadata-untracked"})
            self.assertTrue(any("not recorded at Git HEAD" in warning for warning in status.json()["warnings"]))

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_failed_initial_commit_rolls_back_files_and_staging_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "hooked-library"
            library.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.name", "Library Test"], cwd=library, check=True)
            subprocess.run(["git", "config", "user.email", "library@example.invalid"], cwd=library, check=True)
            (library / "README.md").write_text("existing repository\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=library, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "existing repository"], cwd=library, check=True)
            hook = library / ".git" / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o700)

            refused = cli.run("library", "init", "--path", str(library))

            self.assertEqual(refused.code, 2)
            self.assertIn("could not commit library metadata", refused.stderr)
            self.assertFalse((library / ".skillager").exists())
            self.assertFalse((library / "skills").exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=library,
                    text=True,
                    stdout=subprocess.PIPE,
                    check=True,
                ).stdout,
                "",
            )

            hook.unlink()
            retried = cli.run("library", "init", "--path", str(library), "--json")
            self.assertEqual(retried.code, 0, retried.stderr)
            self.assertTrue(retried.json()["history"]["available"])

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
