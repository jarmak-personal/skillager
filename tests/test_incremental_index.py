from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.exposure.impl import materialize_skills
from skillager.skills import index as index_impl
from skillager.skills.index import build_index
from skillager.skills.review import setup_environment
from skillager.skills.tree import content_tree_fingerprint


def write_skill(root: Path, body: str = "# Demo\n\nUse demo guidance A.\n") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")


class IncrementalIndexTests(unittest.TestCase):

    def test_unchanged_tree_rehashes_exact_identity_but_reuses_scan_and_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            state = root / "state"
            write_skill(skills / "demo")

            cold = build_index(state, [skills], include_packages=False)
            original_hash = index_impl.content_hash
            with (
                patch.object(index_impl, "content_hash", wraps=original_hash) as hash_mock,
                patch.object(index_impl, "scan_path", side_effect=AssertionError("scan should be skipped")),
                patch.object(index_impl, "lint_skill", side_effect=AssertionError("lint should be skipped")),
            ):
                warm = build_index(state, [skills], include_packages=False, persist=False)

            self.assertEqual(warm, cold)
            self.assertEqual(hash_mock.call_count, 1)
            self.assertEqual(warm["version"], 2)
            self.assertRegex(warm["skills"][0]["tree_fingerprint"], r"^[0-9a-f]{64}$")

    def test_mtime_only_change_rehashes_but_reuses_expensive_fields_when_content_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            state = root / "state"
            skill_root = skills / "demo"
            write_skill(skill_root)
            cold = build_index(state, [skills], include_packages=False)
            old_fingerprint = cold["skills"][0]["tree_fingerprint"]
            skill_file = skill_root / "SKILL.md"
            stat = skill_file.stat()
            os.utime(skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

            original_hash = index_impl.content_hash
            original_scan = index_impl.scan_path
            original_lint = index_impl.lint_skill
            with (
                patch.object(index_impl, "content_hash", wraps=original_hash) as hash_mock,
                patch.object(index_impl, "scan_path", wraps=original_scan) as scan_mock,
                patch.object(index_impl, "lint_skill", wraps=original_lint) as lint_mock,
            ):
                warm = build_index(state, [skills], include_packages=False, persist=False)

            self.assertNotEqual(warm["skills"][0]["tree_fingerprint"], old_fingerprint)
            self.assertEqual(warm["skills"][0]["content_hash"], cold["skills"][0]["content_hash"])
            self.assertEqual(hash_mock.call_count, 1)
            self.assertEqual(scan_mock.call_count, 0)
            self.assertEqual(lint_mock.call_count, 0)

    def test_same_size_edit_with_restored_mtime_cannot_reuse_approved_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            state = root / "state"
            skill_root = skills / "demo"
            write_skill(skill_root, "# Demo\n\nUse trusted guidance A.\n")
            cold = build_index(state, [skills], include_packages=False)
            skill_file = skill_root / "SKILL.md"
            stat = skill_file.stat()
            skill_file.write_text("# Demo\n\nUse changed guidance B.\n", encoding="utf-8")
            os.utime(skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))

            warm = build_index(state, [skills], include_packages=False, persist=False)

            self.assertEqual(skill_file.stat().st_size, stat.st_size)
            self.assertEqual(skill_file.stat().st_mtime_ns, stat.st_mtime_ns)
            self.assertNotEqual(warm["skills"][0]["content_hash"], cold["skills"][0]["content_hash"])

    def test_executable_mode_change_invalidates_fingerprint_and_recomputes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            state = root / "state"
            skill_root = skills / "demo"
            write_skill(skill_root)
            tool = skill_root / "tool.sh"
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o644)
            cold = build_index(state, [skills], include_packages=False)
            old_fingerprint = cold["skills"][0]["tree_fingerprint"]
            old_hash = cold["skills"][0]["content_hash"]

            tool.chmod(0o755)
            warm = build_index(state, [skills], include_packages=False, persist=False)

            self.assertNotEqual(warm["skills"][0]["tree_fingerprint"], old_fingerprint)
            self.assertNotEqual(warm["skills"][0]["content_hash"], old_hash)

    def test_fingerprint_uses_content_hash_file_eligibility_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            write_skill(root)
            before = content_tree_fingerprint(root)
            (root / "skillager.materialized.yaml").write_text("ignored: true\n", encoding="utf-8")
            (root / "scratch.tmp").write_text("ignored\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "demo.pyc").write_bytes(b"ignored")
            self.assertEqual(content_tree_fingerprint(root), before)

    def test_review_path_bypasses_advisory_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            state = root / "state"
            write_skill(skills / "demo")
            build_index(state, [skills], include_packages=False)

            original_hash = index_impl.content_hash
            with patch.object(index_impl, "content_hash", wraps=original_hash) as hash_mock:
                result = setup_environment(
                    state,
                    paths=[skills],
                    include_packages=False,
                    accept_low=True,
                )

            self.assertEqual(hash_mock.call_count, 1)
            self.assertEqual(len(result["action"]["changed"]), 1)

    def test_exposure_rehashes_source_even_when_cached_metadata_is_spoofed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            project = root / "project"
            write_skill(source)
            entry = build_index(root / "state", [source], include_packages=False)["skills"][0]
            entry["trust"] = "reviewed"
            skill_file = source / "SKILL.md"
            stat = skill_file.stat()
            skill_file.write_text("# Demo\n\nUse demo guidance B.\n", encoding="utf-8")
            os.utime(skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))

            results = materialize_skills(
                [entry],
                agents=["codex"],
                scope="project",
                project_dir=project,
            )

            self.assertEqual(results[0]["status"], "skipped")
            self.assertIn("source changed since review", results[0]["reason"])
            self.assertFalse((project / ".agents" / "skills").exists())


if __name__ == "__main__":
    unittest.main()
