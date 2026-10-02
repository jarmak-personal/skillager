"""Standalone Full export through the CLI, with no approval or exposure mutation."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path

from .support import BODY_SENTINEL, CliResult, make_basic_workspace


class LibraryExportBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillager-full-export-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.root)
        self.library = self.root / "library"
        self.checked(self.cli.run("library", "init", "--path", str(self.library), "--no-git", "--json"))
        self.checked(self.cli.run("library", "new", "payload", "--json"))
        self.source = self.library / "skills" / "payload"
        (self.source / "SKILL.md").write_text(
            "---\nname: Export example\ndescription: Explain a reviewed complete payload.\n---\n\n"
            f"Inspect the nested reference files.\n{BODY_SENTINEL}\n")
        nested = self.source / "nested" / "bin"
        nested.mkdir(parents=True)
        (nested / "example.sh").write_text("#!/bin/sh\nexit 0\n")
        (nested / "example.sh").chmod(0o755)
        (self.source / "reference.txt").write_text("Reviewed reference bytes.\n")
        (self.source / "reference.txt").chmod(0o640)
        self.version = self.checked(self.cli.run_confirmed("library", "accept", "payload", "--yes", "--json"))["skill"]["working_hash"]

    def checked(self, result: CliResult, code: int = 0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def export(self, destination: Path, *, version: str | None = None, agent: str = "codex", code: int = 0):
        return self.checked(self.cli.run("export", "lib/payload", "--version", version or self.version,
                                         "--agent", agent, "--dest", str(destination), "--json"), code)

    def files(self, root: Path):
        return {path.relative_to(root).as_posix(): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                for path in root.rglob("*") if path.is_file() and not path.is_symlink()}

    def state(self):
        return {str(path): path.read_bytes() for parent in (self.library, self.root / "state", self.project)
                for path in parent.rglob("*") if path.is_file()}

    def test_both_agents_reproduce_native_bytes_modes_and_authenticated_provenance(self) -> None:
        # An accepted regular file gaining a hardlink does not change its hash.
        # Canonical selection also excludes existing source symlinks for both paths.
        outside = self.root / "outside-hardlink.txt"
        os.link(self.source / "reference.txt", outside)
        (self.source / "ignored-alias.txt").symlink_to(outside)
        expected_outside = outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode)
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                before = self.state()
                destination = self.root / f"export-{agent}"
                exported = self.export(destination, agent=agent)
                self.assertEqual(self.state(), before)
                self.assertEqual(exported["scope"], "export")
                self.assertEqual(exported["content_hash"], self.version)
                self.assertEqual(exported["agent"], agent)
                actual = self.files(destination)
                self.assertEqual(set(actual), set(self.files(self.source)) | {"skillager.materialized.yaml"})
                for row in exported["files"]:
                    data, mode = actual[row["path"]]
                    self.assertEqual(row, {"path": row["path"], "mode": mode, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                preview = self.checked(self.cli.run("expose", "lib/payload", "--agent", agent, "--mode", "native", "--dry-run", "--json"))[0]
                installed = self.checked(self.cli.run(*preview["next_command_argv"][1:]))[0]
                native = self.files(Path(installed["target"]))
                sidecar = actual.pop("skillager.materialized.yaml")[0].decode()
                native.pop("skillager.materialized.yaml")
                self.assertEqual(actual, native)
                self.assertEqual((outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode)), expected_outside)
                self.assertNotEqual((destination / "reference.txt").stat().st_ino, outside.stat().st_ino)
                self.assertEqual((destination / "reference.txt").stat().st_nlink, 1)
                self.assertIn("scope: export", sidecar)
                self.assertIn(f"agent: {agent}", sidecar)
                self.assertIn("materialized_sidecar_hash:", sidecar)

    def test_distinct_pending_changed_blocked_and_unaccepted_refusals(self) -> None:
        destination = self.root / "refused"
        self.assertEqual(self.export(destination, version="0" * 64, code=2)["error"]["code"], "version_not_accepted")
        with (self.source / "SKILL.md").open("a") as handle:
            handle.write("\nUnaccepted change.\n")
        self.assertEqual(self.export(destination, code=2)["error"]["code"], "changed_content")
        where = self.checked(self.cli.run("library", "status", "payload", "--json"))["skill"]
        self.assertEqual(self.export(destination, version=where["working_hash"], code=2)["error"]["code"], "version_not_accepted")
        self.checked(self.cli.run("--state-dir", str(self.root / "state/catalog"), "review", "block", "lib/payload", "--json"))
        self.assertEqual(self.export(destination, code=2)["error"]["code"], "blocked_content")
        self.assertFalse(destination.exists())
        self.checked(self.cli.run("library", "new", "draft", "--json"))
        draft = self.checked(self.cli.run("library", "status", "draft", "--json"))["skill"]
        result = self.checked(self.cli.run("export", "lib/draft", "--version", draft["working_hash"],
                                          "--agent", "claude", "--dest", str(destination), "--json"), 2)
        self.assertEqual(result["error"]["code"], "pending_content")

    def test_historical_accepted_hash_is_a_truthful_current_content_mismatch(self) -> None:
        with (self.source / "SKILL.md").open("a") as handle:
            handle.write("\nAccepted revised guidance.\n")
        current = self.checked(self.cli.run_confirmed("library", "accept", "payload", "--yes", "--json"))["skill"]["working_hash"]
        self.assertNotEqual(current, self.version)
        refused = self.export(self.root / "historical", code=2)
        self.assertEqual(refused["error"]["code"], "version_content_mismatch")
        self.export(self.root / "current", version=current)

    def test_nonempty_symlink_overlap_and_missing_parent_destinations_preserve_state(self) -> None:
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("Preserve these bytes.\n")
        before = self.state()
        self.assertEqual(self.export(occupied, code=2)["error"]["code"], "destination_not_empty")
        self.assertEqual((occupied / "keep.txt").read_text(), "Preserve these bytes.\n")
        alias = self.root / "alias"
        alias.symlink_to(occupied, target_is_directory=True)
        for destination in (alias, alias / "new", self.library / "new", self.root / "state/catalog" / "new", self.root / "absent" / "new"):
            with self.subTest(destination=destination):
                self.export(destination, code=2)
        self.assertEqual(self.state(), before)
        self.assertEqual(list(occupied.iterdir()), [occupied / "keep.txt"])

    def test_existing_empty_destination_and_personal_authority_without_project_access(self) -> None:
        destination = self.root / "empty"
        destination.mkdir()
        damaged = self.root / "state/project/trust.sqlite3"
        damaged.parent.mkdir(parents=True)
        damaged.write_bytes(b"damaged project authority")
        self.export(destination)
        self.assertTrue((destination / "SKILL.md").is_file())

    def test_lint_quarantine_is_refused_without_export_effects(self) -> None:
        metadata = self.source / "skillager.yaml"
        metadata.write_text("schema: invalid\n")
        self.assertEqual(self.export(self.root / "lint", code=2)["error"]["code"], "lint_blocked_content")
        self.assertFalse((self.root / "lint").exists())

    def test_agent_version_and_canonical_skill_identity_are_explicit(self) -> None:
        base = ("export", "lib/payload", "--dest", str(self.root / "unused"), "--json")
        self.assertEqual(self.cli.run(*base, "--version", self.version).code, 2)
        self.assertEqual(self.cli.run(*base, "--agent", "codex").code, 2)
        self.assertEqual(self.export(self.root / "short", version=self.version[:12], code=2)["error"]["code"], "invalid_request")
