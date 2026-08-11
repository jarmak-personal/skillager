from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from skillager.trust import content_hash, content_hash_entries, set_trust, trust_state


class ContentHashTests(unittest.TestCase):
    def test_executable_marker_cannot_be_confused_with_file_content(self) -> None:
        payload = b"payload"

        executable = content_hash_entries([("SKILL.md", payload, True)])
        marker_prefixed = content_hash_entries([("SKILL.md", b"executable\0" + payload, False)])

        self.assertNotEqual(executable, marker_prefixed)

    def test_file_boundaries_cannot_be_shifted_between_entries(self) -> None:
        one_file = content_hash_entries([("a", b"payload\0b\0")])
        two_files = content_hash_entries([("a", b"payload"), ("b", b"")])

        self.assertNotEqual(one_file, two_files)

    def test_directory_and_in_memory_hashes_agree_and_preserve_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill"
            skill.mkdir()
            skill_file = skill / "SKILL.md"
            tool = skill / "tool.sh"
            skill_file.write_bytes(b"# Demo\n")
            tool.write_bytes(b"#!/bin/sh\nexit 0\n")
            skill_file.chmod(0o644)
            tool.chmod(0o755)

            tree_hash = content_hash(skill)
            memory_hash = content_hash_entries(
                [
                    ("tool.sh", b"#!/bin/sh\nexit 0\n", "100755"),
                    ("SKILL.md", b"# Demo\n", "100644"),
                ]
            )
            non_executable_hash = content_hash_entries(
                [
                    ("SKILL.md", b"# Demo\n", "100644"),
                    ("tool.sh", b"#!/bin/sh\nexit 0\n", "100644"),
                ]
            )

            self.assertEqual(tree_hash, memory_hash)
            self.assertNotEqual(tree_hash, non_executable_hash)

            tool.chmod(0o644)
            self.assertEqual(content_hash(skill), non_executable_hash)

    def test_standalone_file_hash_preserves_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool.sh"
            path.write_bytes(b"#!/bin/sh\n")
            path.chmod(0o644)
            regular = content_hash(path)

            path.chmod(0o755)

            self.assertNotEqual(content_hash(path), regular)

    def test_legacy_ambiguous_hash_approval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            skill = root / "skill"
            skill.mkdir()
            payload = b"# Demo\n"
            (skill / "SKILL.md").write_bytes(payload)
            legacy = hashlib.sha256(b"SKILL.md\0" + payload + b"\0").hexdigest()
            current = content_hash(skill)
            set_trust(state, "project/demo", "reviewed", legacy, {"type": "project"})

            self.assertNotEqual(current, legacy)
            self.assertEqual(trust_state(state, "project/demo", current), "discovered")


if __name__ == "__main__":
    unittest.main()
