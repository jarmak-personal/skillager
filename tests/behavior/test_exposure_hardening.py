from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from skillager.simple_yaml import load_mapping
from tests.behavior.support import CliResult, make_basic_workspace


class ExposureHardeningBehaviorTests(unittest.TestCase):
    def assert_code(self, result: CliResult, expected: int) -> None:
        self.assertEqual(
            result.code,
            expected,
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def test_native_projection_rejects_manifest_free_external_skill_but_stub_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            source = project / ".skills" / "plain-external"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(
                "# Plain External\n\nUse this external workflow.\n",
                encoding="utf-8",
            )
            self.assert_code(
                cli.run("setup", "--source", "project", "--accept-low", "--non-interactive", "--json"),
                0,
            )

            native = cli.run("expose", "project/plain-external", "--mode", "native", "--agent", "codex", "--json")
            self.assert_code(native, 0)
            self.assertEqual(native.json()[0]["status"], "skipped")
            self.assertIn("frontmatter", native.json()[0]["reason"])
            self.assertFalse((project / ".agents" / "skills" / "project-plain-external").exists())

            stub = cli.run("expose", "project/plain-external", "--mode", "stub", "--agent", "codex", "--json")
            self.assert_code(stub, 0)
            self.assertEqual(stub.json()[0]["status"], "exposed")
            self.assertTrue((project / ".agents" / "skills" / "project-plain-external" / "SKILL.md").is_file())

    def test_native_and_stub_slug_collisions_allocate_distinct_targets(self) -> None:
        for mode in ("native", "stub"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, cli = make_basic_workspace(root)
                first_root = root / "first-collection"
                second_root = root / "second-collection"
                self._write_skill(first_root / "a" / "b", "First collision body")
                self._write_skill(second_root / "b", "Second collision body")
                self.assert_code(cli.run("collection", "add", str(first_root), "--name", "team"), 0)
                self.assert_code(cli.run("collection", "add", str(second_root), "--name", "team-a"), 0)
                self.assert_code(cli.run("review", "approve", "team/a/b"), 0)
                self.assert_code(cli.run("review", "approve", "team-a/b"), 0)

                first = cli.run("expose", "team/a/b", "--mode", mode, "--agent", "codex", "--json")
                second = cli.run("expose", "team-a/b", "--mode", mode, "--agent", "codex", "--json")
                self.assert_code(first, 0)
                self.assert_code(second, 0)
                self.assertEqual(first.json()[0]["status"], "exposed")
                self.assertEqual(second.json()[0]["status"], "exposed")

                base = project / ".agents" / "skills" / "team-a-b"
                suffix = hashlib.sha256(b"team-a/b").hexdigest()[:8]
                alternate = base.with_name(f"team-a-b-{suffix}")
                self.assertTrue((base / "SKILL.md").is_file())
                self.assertTrue((alternate / "SKILL.md").is_file())
                self.assertEqual(load_mapping(base / "skillager.materialized.yaml")["source_id"], "team/a/b")
                self.assertEqual(load_mapping(alternate / "skillager.materialized.yaml")["source_id"], "team-a/b")
                if mode == "native":
                    self.assertIn("First collision body", (base / "SKILL.md").read_text(encoding="utf-8"))
                    self.assertIn("Second collision body", (alternate / "SKILL.md").read_text(encoding="utf-8"))
                else:
                    first_activated = cli.run(
                        "activate",
                        "team/a/b",
                        "--from-stub",
                        base.name,
                        "--no-session-record",
                    )
                    self.assert_code(first_activated, 0)
                    self.assertIn("First collision body", first_activated.stdout)
                    activated = cli.run(
                        "activate",
                        "team-a/b",
                        "--from-stub",
                        alternate.name,
                        "--no-session-record",
                    )
                    self.assert_code(activated, 0)
                    self.assertIn("Second collision body", activated.stdout)

    def test_occupied_collision_fallback_fails_closed_without_force_or_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            first_root = root / "first-collection"
            second_root = root / "second-collection"
            self._write_skill(first_root / "a" / "b", "First collision body")
            self._write_skill(second_root / "b", "Second collision body")
            self.assert_code(cli.run("collection", "add", str(first_root), "--name", "team"), 0)
            self.assert_code(cli.run("collection", "add", str(second_root), "--name", "team-a"), 0)
            self.assert_code(cli.run("review", "approve", "team/a/b"), 0)
            self.assert_code(cli.run("review", "approve", "team-a/b"), 0)
            self.assert_code(cli.run("expose", "team/a/b", "--mode", "stub", "--agent", "codex", "--json"), 0)

            base = project / ".agents" / "skills" / "team-a-b"
            suffix = hashlib.sha256(b"team-a/b").hexdigest()[:8]
            alternate = base.with_name(f"team-a-b-{suffix}")
            alternate.mkdir(parents=True)
            sentinel = alternate / "KEEP.txt"
            sentinel.write_text("do not overwrite\n", encoding="utf-8")

            for force in (False, True):
                args = ["expose", "team-a/b", "--mode", "stub", "--agent", "codex", "--json"]
                if force:
                    args.append("--force")
                refused = cli.run(*args)
                self.assert_code(refused, 0)
                self.assertEqual(refused.json()[0]["status"], "skipped")
                self.assertIn("collision fallback target is occupied", refused.json()[0]["reason"])
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not overwrite\n")
                self.assertFalse((alternate / "SKILL.md").exists())
            self.assertEqual(load_mapping(base / "skillager.materialized.yaml")["source_id"], "team/a/b")

    @staticmethod
    def _write_skill(path: Path, body: str) -> None:
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            "---\n"
            f"name: {path.name}\n"
            f"description: Use {body.lower()}.\n"
            "---\n\n"
            f"# {path.name}\n\n{body}\n",
            encoding="utf-8",
        )
