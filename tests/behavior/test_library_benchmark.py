from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import REPO_ROOT
from tests.behavior.library_catalog import source_tree


class OwnedLibraryBenchmarkBehaviorTests(unittest.TestCase):
    def test_product_source_identity_refuses_dirty_or_untracked_product_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "src" / "skillager" / "__init__.py"
            linter = repo / "packages" / "skillager-linter" / "src" / "skillager_linter" / "__init__.py"
            for path in (source, linter):
                path.parent.mkdir(parents=True)
                path.write_text("# Original product source\n")
            env = {"HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(repo / "no-config")}
            for argv in (["init"], ["add", "src", "packages"], ["-c", "user.name=Benchmark", "-c", "user.email=benchmark@localhost", "commit", "-m", "Product source"]):
                subprocess.run(["git", *argv], cwd=repo, env=env, capture_output=True, check=True)
            clean = source_tree(repo)
            (repo / "benchmark-doc.md").write_text("Unrelated benchmark edits are allowed.\n")
            self.assertEqual(source_tree(repo), clean)
            for path in (source, linter):
                original = path.read_text()
                path.write_text(original + "# Modified product\n")
                with self.assertRaisesRegex(ValueError, "clean tracked and untracked product source"):
                    source_tree(repo)
                path.write_text(original)
            module = source.parent / "untracked_module.py"
            module.write_text("# Importable untracked product\n")
            with self.assertRaisesRegex(ValueError, "clean tracked and untracked product source"):
                source_tree(repo)
            module.unlink()
            self.assertEqual(source_tree(repo), clean)

    def test_small_complete_report_measures_real_mutations_and_exact_paged_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "benchmark_library.py"),
                                     "--size", "4", "--page-size", "2", "--repeats", "1", "--output", str(report)],
                                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["traversal_proof"]["owned_count"], 4)
            self.assertEqual(payload["traversal_proof"]["search_count"], 4)
            self.assertFalse(Path(payload["fixture"]).exists())
            commands = {item["name"]: item for item in payload["commands"]}
            self.assertEqual(len(commands), 14)
            for item in commands.values():
                self.assertEqual([sample["phase"] for sample in item["samples"]], ["cold", "warm"])
                self.assertGreater(item["max_stdout_bytes"], 0)
                self.assertTrue(all(sample["exit_code"] == 0 for sample in item["samples"]))
            self.assertTrue(all(sample["commit"] and sample["accepted_hash"] != sample["before_hash"] for sample in commands["accept_changed"]["samples"]))
            self.assertTrue(all(sample["commit"] is None and sample["accepted_hash"] == sample["before_hash"] for sample in commands["accept_unchanged"]["samples"]))

    def test_public_cli_fixture_has_owned_accepted_git_history_and_refuses_changed_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            argv = [sys.executable, str(REPO_ROOT / "scripts" / "benchmark_library.py"),
                    "--size", "4", "--page-size", "2", "--repeats", "1", "--prepare-only", "--output", str(report)]
            result = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            fixture = Path(payload["fixture"])
            try:
                proof = payload["fixture_proof"]
                self.assertEqual(payload["status"], "prepared")
                self.assertEqual(proof["owned_files"], 4)
                self.assertEqual(proof["accepted_count"], 4)
                self.assertEqual(proof["git_tracked_skills"], 4)
                self.assertGreaterEqual(proof["git_commit_count"], 4)
                self.assertEqual(proof["history_versions"], 2)
                self.assertEqual(proof["synced_history_versions"], 1)
                self.assertTrue(proof["git"]["clean"])
                resumed = subprocess.run([*argv, "--resume", str(fixture)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
                source = next((fixture / "sources").glob("*/SKILL.md"))
                source.write_text(source.read_text() + "\nChanged after preparation.\n")
                refused = subprocess.run([*argv, "--resume", str(fixture)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(refused.returncode, 1)
                self.assertIn("generated source changed", json.loads(report.read_text())["error"])
                self.assertTrue((fixture / "library" / ".git").is_dir())
            finally:
                shutil.rmtree(fixture)

    def test_unowned_resume_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinel = root / "user-data"
            sentinel.write_text("preserve")
            result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "benchmark_library.py"),
                                     "--size", "4", "--page-size", "2", "--output", str(root / "report.json"), "--resume", str(root)],
                                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertFalse((root / "library").exists())
