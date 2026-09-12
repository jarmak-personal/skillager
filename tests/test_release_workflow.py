from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_skillager_notes_are_generated_before_version_mutation_and_push(self) -> None:
        steps = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]["release"]["steps"]
        names = [step.get("name") for step in steps]
        notes_index = names.index("Build release notes")
        self.assertLess(names.index("Reuse existing current release tag"), notes_index)
        self.assertLess(notes_index, names.index("Update version files"))
        self.assertLess(notes_index, names.index("Commit version bump and tag"))
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
