from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.behavior.library_catalog import source_tree


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_skillager_notes_are_generated_before_version_mutation_and_push(self) -> None:
        steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["release"]["steps"]
        names = [step.get("name") for step in steps]
        notes_index = names.index("Build release notes")
        self.assertLess(names.index("Reuse existing current release tag"), notes_index)
        self.assertLess(notes_index, names.index("Update version files"))
        self.assertLess(notes_index, names.index("Prepare local version commit"))
        self.assertLess(names.index("Update version files"), names.index("Prepare local version commit"))
        self.assertLess(names.index("Prepare local version commit"), names.index("Validate release build"))
        self.assertLess(names.index("Validate release build"), names.index("Tag and push validated release"))
        script = steps[notes_index]["run"]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            env = {**os.environ, "HOME": str(work / "home"), "XDG_CONFIG_HOME": str(work / "config"),
                   "GIT_CONFIG_NOSYSTEM": "1", "PACKAGE": "skillager", "NEXT": "0.9.1",
                   "TAG_PATTERN": "v[0-9]*", "BUMP": "patch"}

            def git(*args):
                subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True)

            git("init")
            git("config", "user.name", "Release check")
            git("config", "user.email", "release-check@example.invalid")
            git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "Older published fix")
            git("tag", "v0.9.0")
            git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "New Skillager fix")
            result = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            notes = (work / "RELEASE_NOTES.md").read_text()
            self.assertIn("- skillager 0.9.1", notes)
            self.assertIn("- New Skillager fix", notes)
            self.assertNotIn("Older published fix", notes)

    def test_linter_notes_keep_the_same_release_range_before_version_commit(self) -> None:
        steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["release"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == "Build release notes")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            env = {**os.environ, "HOME": str(work / "home"), "XDG_CONFIG_HOME": str(work / "config"),
                   "GIT_CONFIG_NOSYSTEM": "1", "PACKAGE": "skillager-linter", "NEXT": "0.1.3",
                   "TAG_PATTERN": "skillager-linter-v[0-9]*", "BUMP": "patch"}

            def git(*args):
                subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True)

            git("init")
            git("config", "user.name", "Release check")
            git("config", "user.email", "release-check@example.invalid")
            git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "Older published fix")
            git("tag", "skillager-linter-v0.1.2")
            empty = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env,
                                   capture_output=True, text=True)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertNotIn("Older published fix", (work / "RELEASE_NOTES.md").read_text())
            git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "New linter fix")
            for bump in ("patch", "current"):
                if bump == "current":
                    git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "Bump skillager-linter to 0.1.3")
                    git("tag", "skillager-linter-v0.1.3")
                with self.subTest(bump=bump):
                    result = subprocess.run(["bash", "-e", "-c", script], cwd=work,
                                            env={**env, "BUMP": bump}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    notes = (work / "RELEASE_NOTES.md").read_text()
                    self.assertIn("- New linter fix", notes)
                    self.assertNotIn("Older published fix", notes)
                    self.assertNotIn("Bump skillager-linter", notes)

    def test_version_commit_cleans_product_source_without_publishing_before_validation(self) -> None:
        steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["release"]["steps"]
        prepare = next(step["run"] for step in steps if step.get("name") == "Prepare local version commit")
        publish = next(step["run"] for step in steps if step.get("name") == "Tag and push validated release")
        for package, pyproject, init, tag in (
            ("skillager", "pyproject.toml", "src/skillager/__init__.py", "v0.9.4"),
            ("skillager-linter", "packages/skillager-linter/pyproject.toml",
             "packages/skillager-linter/src/skillager_linter/__init__.py", "skillager-linter-v0.9.4"),
        ):
            with self.subTest(package=package), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                work, remote = root / "work", root / "remote.git"
                work.mkdir()
                env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(root / "no-global-config"),
                       "PACKAGE": package, "NEXT": "0.9.4", "PYPROJECT": pyproject, "INIT": init,
                       "TAG": tag, "COMMITTED": "true", "GITHUB_OUTPUT": str(root / "outputs")}

                def git(*args: str) -> str:
                    return subprocess.check_output(["git", *args], cwd=work, env=env, text=True,
                                                   stderr=subprocess.STDOUT).strip()

                git("init", "-b", "main")
                git("config", "user.name", "Release check")
                git("config", "user.email", "release-check@example.invalid")
                for name in ("pyproject.toml", "src/skillager/__init__.py", "uv.lock",
                             "packages/skillager-linter/pyproject.toml",
                             "packages/skillager-linter/src/skillager_linter/__init__.py"):
                    path = work / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('version = "0.9.3"\n')
                git("add", ".")
                git("-c", "core.hooksPath=/dev/null", "commit", "-m", "Original source")
                original = git("rev-parse", "HEAD")
                git("init", "--bare", str(remote))
                git("remote", "add", "origin", str(remote))
                git("push", "origin", "main")
                for name in (pyproject, init, "uv.lock"):
                    (work / name).write_text('version = "0.9.4"\n')
                with self.assertRaisesRegex(ValueError, "clean tracked and untracked product source"):
                    source_tree(work)
                result = subprocess.run(["bash", "-e", "-c", prepare], cwd=work, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / "outputs").read_text().strip(), "committed=true")
                release = git("rev-parse", "HEAD")
                self.assertNotEqual(release, original)
                self.assertEqual(source_tree(work), git("rev-parse", "HEAD:src/skillager", "HEAD:packages/skillager-linter"))
                self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], original)
                # GitHub stops later steps on validation failure; exercise that boundary.
                refused = subprocess.run(["bash", "-e", "-c", "false\n" + publish], cwd=work, env=env,
                                         capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], original)
                self.assertEqual(git("ls-remote", "origin", f"refs/tags/{tag}"), "")
                published = subprocess.run(["bash", "-e", "-c", publish], cwd=work, env=env, capture_output=True, text=True)
                self.assertEqual(published.returncode, 0, published.stderr)
                self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], release)
                self.assertEqual(git("ls-remote", "origin", f"refs/tags/{tag}").split()[0], release)
                # A current-version retry has no new local version commit to push.
                (root / "outputs").write_text("")
                unchanged = subprocess.run(["bash", "-e", "-c", prepare], cwd=work, env=env, capture_output=True, text=True)
                self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
                self.assertEqual((root / "outputs").read_text().strip(), "committed=false")
                self.assertEqual(git("rev-parse", "HEAD"), release)
