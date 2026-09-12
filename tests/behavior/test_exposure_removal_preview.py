from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import BODY_SENTINEL, CliResult, SkillagerCli, make_basic_workspace


class ExposureRemovalPreviewBehaviorTests(unittest.TestCase):
    def checked(self, result: CliResult, expected: int = 0) -> dict:
        self.assertEqual(result.code, expected, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json() if result.stdout.startswith(("{", "[")) else {}

    def fixture(self, root: Path) -> tuple[Path, SkillagerCli, Path]:
        project, cli = make_basic_workspace(root)
        library = root / "library"
        self.checked(cli.run("library", "init", "--path", str(library), "--no-git", "--json"))
        self.checked(cli.run("library", "new", "demo", "--json"))
        source = library / "skills" / "demo"
        (source / "SKILL.md").write_text("---\nname: Demo\ndescription: Explain the removal fixture.\n---\n\nRead references/data.bin and scripts/example.sh.\n" + BODY_SENTINEL + "\n", encoding="utf-8")
        (source / "references").mkdir()
        (source / "references/data.bin").write_bytes(b"\x00\x01fixture\xff")
        (source / "scripts").mkdir()
        (source / "scripts/example.sh").write_text("#!/bin/sh\nprintf 'fixture\\n'\n", encoding="utf-8")
        (source / "scripts/example.sh").chmod(0o755)
        accepted = self.checked(cli.run_confirmed("library", "accept", "lib/demo", "--yes", "--json"))
        self.assertEqual(accepted["status"], "accepted")
        return project, cli, library

    def expose(self, cli: SkillagerCli, *, mode: str = "native", agent: str = "codex") -> Path:
        result = self.checked(cli.run("expose", "lib/demo", "--mode", mode, "--agent", agent, "--json"))[0]
        self.assertEqual(result["status"], "exposed")
        return Path(result["target"])

    def preview(self, cli: SkillagerCli, target: Path, *, agent: str = "codex", force: bool = False) -> dict:
        args = ["expose", "--remove", target.name, "--agent", agent, "--scope", "project", "--json"]
        if force:
            args.append("--force")
        result = self.checked(cli.run(*args))
        self.assertEqual(result["schema"], "skillager.exposure-remove.v1")
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual(item["status"], "would_remove")
        self.assertEqual(item["preview"]["schema"], "skillager.exposure-remove-preview.v1")
        return item

    @staticmethod
    def tree(path: Path) -> dict | None:
        if not path.exists():
            return None
        result = {".": {"type": "directory", "mode": stat.S_IMODE(path.stat().st_mode)}}
        for entry in sorted(path.rglob("*")):
            info = entry.lstat()
            item = {"mode": stat.S_IMODE(info.st_mode)}
            if entry.is_symlink():
                item.update(type="symlink", link_target=os.readlink(entry))
            elif entry.is_dir():
                item["type"] = "directory"
            else:
                content = entry.read_bytes()
                item.update(type="file", size=len(content), sha256=hashlib.sha256(content).hexdigest())
            result[entry.relative_to(path).as_posix()] = item
        return result

    def test_complete_effects_remove_only_the_selected_copy(self) -> None:
        for agent in ("codex", "claude"):
            for mode in ("native", "stub"):
                with self.subTest(agent=agent, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    project, cli, library = self.fixture(Path(tmp))
                    target = self.expose(cli, agent=agent, mode=mode)
                    other_agent = self.expose(cli, agent="claude" if agent == "codex" else "codex")
                    other_project = Path(tmp) / "other-project"
                    other_project.mkdir()
                    cli.project = other_project
                    other_copy = self.expose(cli, agent=agent)
                    cli.project = project
                    target_before = self.tree(target)
                    preserved = {path: self.tree(path) for path in (library, other_agent, other_copy)}
                    preview = self.preview(cli, target, agent=agent)
                    effects = preview["preview"]
                    self.assertRegex(effects["target_state_hash"], r"^[0-9a-f]{64}$")
                    self.assertEqual(effects["target_directory"], {"before_mode": target_before["."]["mode"], "after_mode": None})
                    self.assertEqual({item["path"]: item["before"] for item in effects["file_effects"]}, {path: item for path, item in target_before.items() if path != "."})
                    self.assertTrue(all(item["action"] == "remove" and item["after"] is None for item in effects["file_effects"]))
                    self.assertIn("skillager.materialized.yaml", {item["path"] for item in effects["file_effects"]})
                    self.assertEqual(self.tree(target), target_before)
                    result = self.checked(cli.run(*preview["next_command_argv"][1:]))["results"][0]
                    self.assertEqual(result["status"], "removed")
                    self.assertEqual(result["target"], preview["target"])
                    self.assertEqual(result["preview"], effects)
                    self.assertNotIn("next_command_argv", result)
                    self.assertFalse(target.exists())
                    self.assertFalse(any(target.parent.glob(f".{target.name}.skillager-remove-*")))
                    for path, before in preserved.items():
                        self.assertEqual(self.tree(path), before)
                    self.checked(cli.run(*preview["next_command_argv"][1:]), 2)

    def test_root_directory_permission_change_invalidates_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, _ = self.fixture(Path(tmp))
            target = self.expose(cli)
            preview = self.preview(cli, target)
            target.chmod(0o700 if stat.S_IMODE(target.stat().st_mode) != 0o700 else 0o755)
            before = self.tree(target)
            result = cli.run(*preview["next_command_argv"][1:])
            self.checked(result, 2)
            self.assertIn("preview is stale", result.stderr)
            self.assertEqual(self.tree(target), before)
            current = self.preview(cli, target)
            self.assertNotEqual(current["preview"]["target_directory"], preview["preview"]["target_directory"])
            self.assertEqual(current["preview"]["target_state_hash"], preview["preview"]["target_state_hash"])
            self.assertEqual(self.checked(cli.run(*current["next_command_argv"][1:]))["results"][0]["status"], "removed")

    def test_changed_support_and_metadata_are_preserved(self) -> None:
        for mutation in ("bytes", "mode", "sidecar", "extra", "missing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                _, cli, library = self.fixture(Path(tmp))
                target = self.expose(cli)
                preview = self.preview(cli, target)
                if mutation == "bytes":
                    path = target / "references/data.bin"
                    before_stat = path.stat()
                    path.write_bytes(b"\x00\x01changed\xff")
                    os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
                elif mutation == "mode":
                    (target / "scripts/example.sh").chmod(0o644)
                elif mutation == "sidecar":
                    with (target / "skillager.materialized.yaml").open("a") as handle:
                        handle.write("# retain metadata edit\n")
                elif mutation == "extra":
                    (target / "notes.tmp").write_text("Keep local notes.\n", encoding="utf-8")
                else:
                    shutil.rmtree(target)
                before = self.tree(target)
                canonical = self.tree(library)
                self.checked(cli.run(*preview["next_command_argv"][1:]), 2)
                self.assertEqual(self.tree(target), before)
                self.assertEqual(self.tree(library), canonical)
                if mutation != "missing":
                    current = self.preview(cli, target)
                    self.assertTrue(current["requires_force"])
                    self.assertNotIn("next_command_argv", current)

    def test_text_preview_withholds_confirmation_until_complete_json_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, _ = self.fixture(Path(tmp))
            target = self.expose(cli)
            text = cli.run("expose", "--remove", target.name, "--agent", "codex")
            self.checked(text)
            self.assertIn("would_remove", text.stdout)
            self.assertIn("Rerun with --json to review complete file effects", text.stdout)
            self.assertNotIn("--confirmation-token", text.stdout)
            self.assertNotIn("--yes", text.stdout)
            complete = self.preview(cli, target)
            self.assertIn("--confirmation-token", complete["next_command_argv"])
            self.assertTrue(target.exists())

    def test_existing_force_workflow_discloses_extra_entries_and_preserves_link_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, library = self.fixture(Path(tmp))
            target = self.expose(cli)
            (target / "notes.tmp").write_text("Local notes to disclose.\n", encoding="utf-8")
            outside = Path(tmp) / "outside.txt"
            outside.write_text("Preserve this separate file.\n", encoding="utf-8")
            (target / "outside-link").symlink_to(outside)
            canonical = self.tree(library)
            refused = self.preview(cli, target)
            self.assertTrue(refused["requires_force"])
            self.assertNotIn("next_command_argv", refused)
            forced = self.preview(cli, target, force=True)
            effects = {item["path"]: item for item in forced["preview"]["file_effects"]}
            self.assertEqual(effects["outside-link"]["before"]["type"], "symlink")
            self.assertEqual(effects["outside-link"]["before"]["link_target"], str(outside))
            self.assertEqual(effects["notes.tmp"]["action"], "remove")
            self.assertIn("--force", forced["next_command_argv"])
            self.assertEqual(self.checked(cli.run(*forced["next_command_argv"][1:]))["results"][0]["status"], "removed")
            self.assertFalse(target.exists())
            self.assertEqual(outside.read_text(), "Preserve this separate file.\n")
            self.assertEqual(self.tree(library), canonical)
