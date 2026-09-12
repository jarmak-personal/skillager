from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from skillager.simple_yaml import load_mapping
from tests.behavior.support import BODY_SENTINEL, CliResult, SkillagerCli, make_basic_workspace


class ExposureConfirmationBehaviorTests(unittest.TestCase):
    def assert_code(self, result: CliResult, expected: int = 0) -> None:
        self.assertEqual(result.code, expected, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)

    def fixture(self, root: Path) -> tuple[Path, SkillagerCli, Path]:
        project, cli = make_basic_workspace(root)
        library = root / "library"
        self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git", "--json"))
        self.assert_code(cli.run("library", "new", "demo", "--json"))
        source = library / "skills" / "demo"
        (source / "SKILL.md").write_text(
            "---\nname: Demo\ndescription: Explain the bundled fixture reference.\n---\n\n"
            "Read references/data.bin and scripts/example.sh.\n" + BODY_SENTINEL + "\n",
            encoding="utf-8",
        )
        (source / "references").mkdir()
        (source / "references" / "data.bin").write_bytes(b"\x00\x01fixture\xff")
        (source / "scripts").mkdir()
        helper = source / "scripts" / "example.sh"
        helper.write_text("#!/bin/sh\nprintf 'fixture\\n'\n", encoding="utf-8")
        helper.chmod(0o755)
        self.accept(cli)
        return project, cli, source

    def accept(self, cli: SkillagerCli) -> None:
        result = cli.run_confirmed("library", "accept", "lib/demo", "--yes", "--json")
        self.assert_code(result)
        self.assertEqual(result.json()["status"], "accepted")

    def preview(self, cli: SkillagerCli, mode: str = "native", agent: str = "codex", skill_id: str = "lib/demo") -> dict:
        result = cli.run("expose", skill_id, "--mode", mode, "--agent", agent, "--scope", "project", "--dry-run", "--json")
        self.assert_code(result)
        item = result.json()[0]
        self.assertEqual(item["status"], "would_expose", item)
        self.assertEqual(item["preview"]["schema"], "skillager.exposure-preview.v1")
        return item

    def apply(self, cli: SkillagerCli, preview: dict) -> CliResult:
        return cli.run(*preview["next_command_argv"][1:])

    def assert_effects_match(self, preview: dict) -> None:
        target = Path(preview["target"])
        expected_paths = set()
        for effect in preview["preview"]["file_effects"]:
            path = target / effect["path"]
            after = effect["after"]
            if after is None:
                self.assertFalse(path.exists(), effect)
                continue
            expected_paths.add(effect["path"])
            self.assertEqual(path.stat().st_mode & 0o7777, after["mode"])
            if after["type"] == "directory":
                self.assertTrue(path.is_dir())
            elif "metadata" in after:
                actual = load_mapping(path)
                self.assertEqual({key: value for key, value in actual.items() if key not in after["generated_fields"]}, after["metadata"])
                self.assertEqual(set(actual), set(after["metadata"]) | set(after["generated_fields"]))
            else:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), after["sha256"])
                self.assertEqual(path.stat().st_size, after["size"])
        self.assertEqual({path.relative_to(target).as_posix() for path in target.rglob("*")}, expected_paths)

    def test_complete_native_stub_effects_and_bound_mode_changes_for_both_agents(self) -> None:
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                project, cli, source = self.fixture(Path(tmp))
                preview = self.preview(cli, agent=agent)
                self.assertFalse(Path(preview["target"]).exists())
                self.assertFalse((project / (".agents" if agent == "codex" else ".claude")).exists())
                self.assertEqual(self.preview(cli, agent=agent)["preview"], preview["preview"])
                applied = self.apply(cli, preview)
                self.assert_code(applied)
                self.assertEqual(applied.json()[0]["status"], "exposed")
                self.assert_effects_match(preview)
                self.assertEqual((Path(preview["target"]) / "references/data.bin").read_bytes(), (source / "references/data.bin").read_bytes())
                stub = self.preview(cli, mode="stub", agent=agent)
                removed = {item["path"] for item in stub["preview"]["file_effects"] if item["action"] == "remove"}
                self.assertEqual(removed, {"references", "references/data.bin", "scripts", "scripts/example.sh"})
                self.assert_code(self.apply(cli, stub))
                self.assert_effects_match(stub)
                native = self.preview(cli, agent=agent)
                self.assertEqual(native["target"], preview["target"])
                self.assert_code(self.apply(cli, native))
                self.assert_effects_match(native)
                listed = cli.run("expose", "--list", "--agent", agent, "--json")
                self.assert_code(listed)
                self.assertEqual(len(listed.json()["exposures"]), 1)
                self.assertEqual(listed.json()["exposures"][0]["status"], "current")

    def test_accepting_new_source_requires_new_preview_and_reports_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, source = self.fixture(Path(tmp))
            first = self.preview(cli)
            self.assert_code(self.apply(cli, first))
            stale = self.preview(cli)
            (source / "references/data.bin").unlink()
            (source / "scripts/example.sh").write_text("#!/bin/sh\nprintf 'new fixture\\n'\n", encoding="utf-8")
            self.accept(cli)
            refused = self.apply(cli, stale)
            self.assert_code(refused)
            self.assertEqual(refused.json()[0]["status"], "skipped")
            self.assertIn("preview is stale", refused.json()[0]["reason"])
            self.assert_effects_match(first)
            current = self.preview(cli)
            self.assertNotEqual(current["preview"]["source"]["content_hash"], stale["preview"]["source"]["content_hash"])
            self.assert_code(self.apply(cli, current))
            self.assert_effects_match(current)

    def test_text_preview_requires_complete_json_preview_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, _ = self.fixture(Path(tmp))
            for mode in ("native", "stub"):
                text_preview = cli.run("expose", "lib/demo", "--mode", mode, "--agent", "codex", "--dry-run")
                self.assert_code(text_preview)
                self.assertIn("would_expose", text_preview.stdout)
                self.assertIn("Rerun with --json to review complete file effects", text_preview.stdout)
                self.assertNotIn("--confirmation-token", text_preview.stdout)
                self.assertNotIn("--yes", text_preview.stdout)
                complete = self.preview(cli, mode=mode)
                self.assertNotIn(complete["preview"]["confirmation_token"], text_preview.stdout)
                if mode == "native":
                    ordinary = cli.run("expose", "lib/demo", "--agent", "codex")
                    self.assert_code(ordinary)
                    self.assertIn("lib/demo: exposed", ordinary.stdout)
                    self.assertNotIn("Rerun with --json", ordinary.stdout)
                else:
                    self.assertTrue(any(item["action"] == "remove" for item in complete["preview"]["file_effects"]))
                    self.assertTrue((Path(complete["target"]) / "scripts/example.sh").exists())

    def test_managed_collision_invalidates_preview_and_preserves_other_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli, source = self.fixture(root)
            first_collection = root / "first-collection"
            second_collection = root / "second-collection"
            shutil.copytree(source, first_collection / "a" / "b")
            shutil.copytree(source, second_collection / "b")
            with (second_collection / "b" / "SKILL.md").open("a") as handle:
                handle.write("Different guidance for the second fixture.\n")
            for collection, name, skill_id in ((first_collection, "team", "team/a/b"), (second_collection, "team-a", "team-a/b")):
                self.assert_code(cli.run("collection", "add", str(collection), "--name", name))
                reviewed = cli.run("review", "approve", skill_id, "--json")
                self.assert_code(reviewed)
                self.assertTrue(reviewed.json()["action"]["changed"])
            stale = self.preview(cli, skill_id="team/a/b")
            other = cli.run("expose", "team-a/b", "--mode", "native", "--agent", "codex", "--json")
            self.assert_code(other)
            self.assertEqual(other.json()[0]["status"], "exposed")
            self.assertEqual(other.json()[0]["target"], stale["target"])
            target = Path(stale["target"])
            before = self.tree_state(target)
            before_mode = target.stat().st_mode
            self.assertEqual(load_mapping(target / "skillager.materialized.yaml")["source_id"], "team-a/b")
            refused = self.apply(cli, stale)
            self.assert_code(refused)
            self.assertEqual(refused.json()[0]["status"], "skipped")
            self.assertIn("preview is stale", refused.json()[0]["reason"])
            self.assertEqual(self.tree_state(target), before)
            self.assertEqual(target.stat().st_mode, before_mode)
            current = self.preview(cli, skill_id="team/a/b")
            self.assertNotEqual(current["target"], stale["target"])
            self.assertFalse(Path(current["target"]).exists())
            listed = cli.run("expose", "--list", "--agent", "codex", "--json")
            self.assert_code(listed)
            self.assertEqual([item["skill_id"] for item in listed.json()["exposures"]], ["team-a/b"])

    def test_target_changes_refuse_without_repeating_or_overwriting(self) -> None:
        for mutation in ("body", "mode", "directory-mode", "excluded", "sidecar", "disappear", "appear"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                _, cli, _ = self.fixture(Path(tmp))
                first = self.preview(cli)
                target = Path(first["target"])
                if mutation != "appear":
                    self.assert_code(self.apply(cli, first))
                preview = self.preview(cli)
                if mutation == "body":
                    (target / "SKILL.md").write_text("local edits\n", encoding="utf-8")
                elif mutation == "mode":
                    (target / "scripts/example.sh").chmod(0o644)
                elif mutation == "directory-mode":
                    target.chmod(0o700)
                elif mutation == "excluded":
                    (target / "local.tmp").write_text("preserve\n", encoding="utf-8")
                elif mutation == "sidecar":
                    with (target / "skillager.materialized.yaml").open("a") as handle:
                        handle.write("# local metadata edit\n")
                elif mutation == "disappear":
                    shutil.rmtree(target)
                else:
                    target.mkdir(parents=True)
                before = self.tree_state(target)
                for _ in range(2):
                    refused = self.apply(cli, preview)
                    self.assert_code(refused)
                    self.assertEqual(refused.json()[0]["status"], "skipped")
                    self.assertEqual(self.tree_state(target), before)

    def test_command_identity_and_confirmation_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli, _ = self.fixture(root)
            preview = self.preview(cli)
            for option, value in (("--mode", "stub"), ("--agent", "claude")):
                args = preview["next_command_argv"][1:]
                args[args.index(option) + 1] = value
                result = cli.run(*args)
                self.assert_code(result)
                self.assertEqual(result.json()[0]["status"], "skipped")
                self.assertIn("preview is stale", result.json()[0]["reason"])
            other = root / "other-project"
            other.mkdir()
            cli.project = other
            refused = self.apply(cli, preview)
            self.assert_code(refused)
            self.assertEqual(refused.json()[0]["status"], "skipped")
            self.assertIn("preview is stale", refused.json()[0]["reason"])
            for flags in (("--yes",), ("--confirmation-token", "invented"), ("--yes", "--confirmation-token", "invented", "--force")):
                self.assert_code(cli.run("expose", "lib/demo", "--agent", "codex", *flags, "--json"), 2)

    def test_pending_blocked_incompatible_and_unmanaged_are_not_confirmable(self) -> None:
        for protection in ("pending", "blocked", "incompatible", "unmanaged"):
            with self.subTest(protection=protection), tempfile.TemporaryDirectory() as tmp:
                _, cli, source = self.fixture(Path(tmp))
                preview = self.preview(cli)
                if protection == "pending":
                    with (source / "SKILL.md").open("a") as handle:
                        handle.write("New pending instructions.\n")
                elif protection == "blocked":
                    self.assert_code(cli.run("review", "block", "lib/demo", "--json"))
                elif protection == "incompatible":
                    (source / "skillager.yaml").write_text("schema: skillager.skill.v1\naudience: [user]\nactivation:\n  default: manual\ncompatibility:\n  exclusive_to: claude\n", encoding="utf-8")
                    self.accept(cli)
                    current = cli.run("expose", "lib/demo", "--agent", "codex", "--dry-run", "--json")
                    self.assert_code(current)
                    self.assertEqual(current.json()[0]["status"], "skipped")
                    self.assertEqual(current.json()[0]["reason"], "exclusive to claude")
                else:
                    target = Path(preview["target"])
                    target.mkdir(parents=True)
                    (target / "SKILL.md").write_text("unmanaged\n", encoding="utf-8")
                before = self.tree_state(Path(preview["target"]))
                refused = self.apply(cli, preview)
                if refused.code == 0:
                    self.assert_code(refused)
                    self.assertEqual(refused.json()[0]["status"], "skipped")
                    self.assertNotIn("next_command_argv", refused.json()[0])
                else:
                    self.assert_code(refused, 2)
                self.assertEqual(self.tree_state(Path(preview["target"])), before)

    def test_pinned_source_requires_its_current_approval_and_exact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, cli = make_basic_workspace(Path(tmp))
            source = project / ".skills" / "demo"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: Demo\ndescription: Explain the local fixture.\n---\n\nRead the fixture.\n", encoding="utf-8")
            self.assert_code(cli.run("setup", "--source", "project", "--accept-low", "--no-packages", "--non-interactive", "--json"))
            original = self.preview(cli, skill_id="project/demo")
            self.assert_code(cli.run("review", "pin", "project/demo", "--json"))
            stale_approval = self.apply(cli, original)
            self.assert_code(stale_approval)
            self.assertEqual(stale_approval.json()[0]["status"], "skipped")
            self.assertIn("preview is stale", stale_approval.json()[0]["reason"])
            pinned = self.preview(cli, skill_id="project/demo")
            self.assertEqual(pinned["preview"]["source"]["trust"], "pinned")
            self.assert_code(self.apply(cli, pinned))
            stale_source = self.preview(cli, skill_id="project/demo")
            with (source / "SKILL.md").open("a") as handle:
                handle.write("Unaccepted update after pin.\n")
            result = self.apply(cli, stale_source)
            self.assert_code(result)
            self.assertEqual(result.json()[0]["status"], "skipped")
            self.assert_effects_match(pinned)

    def test_existing_exposure_block_policy_is_not_bypassed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, _ = self.fixture(Path(tmp))
            first = self.preview(cli)
            self.assert_code(self.apply(cli, first))
            stale = self.preview(cli)
            sidecar = Path(first["target"]) / "skillager.materialized.yaml"
            with sidecar.open("a") as handle:
                handle.write("exposure_blocked_hashes:\n  - " + first["preview"]["source"]["content_hash"] + "\n")
            before = self.tree_state(Path(first["target"]))
            for result in (self.apply(cli, stale), cli.run("expose", "lib/demo", "--agent", "codex", "--dry-run", "--json")):
                self.assert_code(result)
                self.assertEqual(result.json()[0]["status"], "skipped")
                self.assertIn("blocked by prior project policy", result.json()[0]["reason"])
                self.assertNotIn("preview", result.json()[0])
            self.assertEqual(self.tree_state(Path(first["target"])), before)

    @staticmethod
    def tree_state(target: Path) -> dict | None:
        if not target.exists():
            return None
        return {
            path.relative_to(target).as_posix(): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in target.rglob("*")
        }
