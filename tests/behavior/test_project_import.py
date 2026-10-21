"""Batch save through the public CLI, including stale and partial outcomes."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skillager.state import approvals
from .support import BODY_SENTINEL, make_basic_workspace


class ProjectImportBehaviorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skillager-project-import-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.base)
        self.library = self.base / "library"

    def skill(self, slug="example", *, agent=None, body=None):
        root = self.project / (f".{agent}/skills" if agent else ".skills") / slug
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: Declared metadata.\n---\n\n{body or BODY_SENTINEL}\n")
        return root

    def checked(self, result, code=0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def inventory(self, *options):
        return self.checked(self.cli.run("import-project", "--json", *options))

    def preview(self, selection=None, code=0):
        selected = selection if selection is not None else self.inventory()["default_selection"]
        return self.checked(self.cli.run("import-project", "--json", "--select", json.dumps(selected), "--review-manifest"), code)

    def apply(self, preview, code=0):
        return self.checked(self.cli.run(*preview["next_command_argv"][1:]), code)

    def init(self, *, git=False):
        result = self.cli.run("library", "init", "--path", str(self.library), *([] if git else ["--no-git"]), "--json")
        self.checked(result)

    def state_bytes(self):
        return {str(path.relative_to(self.base)): path.read_bytes() for directory in ("state", "cache", "home", "library")
                for path in (self.base / directory).rglob("*") if path.is_file()}

    def status(self, preview):
        return self.checked(self.cli.run("import-project", "--status", "--json", "--select", json.dumps(preview["selection"]),
                    "--destinations", json.dumps([item["destination"]["id"] for item in preview["items"]])))

    def test_project_summary_crosses_pages_and_explicit_subset_leaves_other_originals_pending(self):
        roots = [self.skill(f"item-{index:02}") for index in range(4)]
        self.skill("outside", agent="agents").rename(self.base / "outside")
        first = self.inventory("--limit", "1")
        self.assertEqual(first["summary"]["eligible"], 4)
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(len(first["default_selection"]), 4)
        second = self.inventory("--limit", "1", "--cursor", first["next_cursor"])
        self.assertNotEqual(first["items"][0]["occurrence_id"], second["items"][0]["occurrence_id"])
        selected = first["default_selection"][-2:]
        before = self.state_bytes()
        preview = self.preview(selected)
        self.assertEqual(self.state_bytes(), before)
        self.assertFalse((self.base / "home/.skillager/library").exists())
        self.assertEqual(preview["selection"], sorted(selected))
        self.assertEqual(len(preview["items"]), 2)
        for item in preview["items"]:
            self.assertEqual(item["review_manifest"]["file_count"], 1)
            self.assertEqual(item["review_manifest"]["files"][0]["path"], "SKILL.md")
        result = self.apply(preview)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"]["accepted"], 2)
        self.assertEqual(result["library"]["status"], "ready")
        self.assertIsNotNone(result["library"]["library_id"])
        self.assertEqual(self.inventory()["summary"]["already_saved"], 2)
        source_rows = self.checked(self.cli.run("list", "--scope", "project", "--json"))["skills"]
        self.assertEqual({row["status"] for row in source_rows}, {"pending"})
        self.assertTrue(all(root.is_dir() for root in roots))
        self.assertEqual({item["canonical"]["acceptance"] for item in self.status(preview)["items"]}, {"accepted"})

    def test_same_slug_exact_copies_deduplicate_but_conflicting_versions_do_not(self):
        self.skill("same", agent="agents")
        self.skill("same", agent="claude")
        self.skill("conflicting", agent="agents", body="Version one")
        self.skill("conflicting", agent="claude", body="Version two")
        inventory = self.inventory()
        self.assertEqual(inventory["summary"], {"total": 4, "eligible": 1, "duplicate": 1,
                         "conflict": 2, "blocked": 0, "managed": 0, "already_saved": 0})
        same = [item for item in inventory["items"] if item["name"] == "same"]
        duplicate = next(item for item in same if item["eligibility"] == "duplicate")
        self.assertIn(duplicate["duplicate_of"], inventory["default_selection"])
        self.apply(self.preview())
        self.assertEqual(self.inventory()["summary"]["already_saved"], 1)
        self.assertEqual(self.inventory()["summary"]["duplicate"], 1)

    def test_filtered_import_records_saved_version_without_replacement_authority(self):
        source = self.skill(agent="agents")
        (source / "notes.tmp").write_text("Keep original excluded data")
        (source / "link").symlink_to("SKILL.md")
        self.init()
        preview = self.preview()
        self.assertEqual([item["path"] for item in preview["items"][0]["review_manifest"]["files"]], ["SKILL.md"])
        self.apply(preview)
        self.assertTrue((source / "link").is_symlink())
        self.assertEqual((source / "notes.tmp").read_text(), "Keep original excluded data")
        self.assertFalse((self.library / "skills/example/notes.tmp").exists())
        row, = self.inventory()["items"]
        self.assertEqual(row["eligibility"], "already_saved")
        unavailable = self.checked(self.cli.run("expose", "--observe-native", str(source / "SKILL.md"),
                                 "--agent", "codex", "--scope", "project", "--json"), 2)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["candidates"], [])
        # Equal bytes/name in another project are not this accepted import.
        other = self.base / "other-project"
        (other / ".agents/skills").mkdir(parents=True)
        shutil.copytree(source, other / ".agents/skills/example", symlinks=True)
        self.cli.project = other
        row, = self.inventory()["items"]
        self.assertEqual(row["eligibility"], "conflict")

    def test_occupied_name_and_customized_canonical_are_conflicts(self):
        original = self.skill()
        self.init()
        preview = self.preview()
        self.apply(preview)
        target = self.library / "skills/example/SKILL.md"
        target.write_text(target.read_text() + "Canonical customization.\n")
        row, = self.inventory()["items"]
        self.assertEqual(row["eligibility"], "conflict")
        self.assertEqual(row["canonical"]["acceptance"], "pending")
        self.assertNotEqual(row["canonical"]["accepted_hash"], row["canonical"]["working_hash"])
        self.assertNotIn("Canonical customization", (original / "SKILL.md").read_text())
        refusal = self.preview(preview["selection"], code=2)
        self.assertNotIn("confirmation_token", refusal)

    def test_managed_projection_is_not_an_original(self):
        self.skill(agent="agents")
        self.init()
        self.apply(self.preview())
        self.checked(self.cli.run("expose", "lib/example", "--agent", "claude", "--mode", "stub", "--scope", "project", "--json"))
        self.assertEqual(self.inventory()["summary"]["managed"], 1)
        self.assertEqual(self.inventory()["summary"]["eligible"], 0)

    def test_blocked_and_invalid_sources_stay_unselected(self):
        self.skill("blocked")
        invalid = self.skill("invalid")
        (invalid / "skillager.yaml").write_text("schema: invalid\n")
        self.checked(self.cli.run("review", "block", "project/blocked", "--project-only", "--json"))
        inventory = self.inventory()
        self.assertEqual(inventory["summary"]["blocked"], 2)
        self.assertEqual(inventory["default_selection"], [])
        self.assertEqual(inventory["observation"]["status"], "partial")

    def test_partial_discovery_exposes_error_and_known_subset_can_be_reviewed(self):
        self.skill()
        (self.project / ".skills/broken").symlink_to(self.base / "missing")
        inventory = self.inventory()
        self.assertEqual(inventory["observation"]["status"], "partial")
        preview = self.preview()
        self.assertEqual(preview["observation"]["error_count"], 1)
        self.apply(preview)

    def test_inventory_more_than_batch_limit_never_defaults_to_first_page(self):
        for index in range(33):
            self.skill(f"item-{index}")
        inventory = self.inventory("--limit", "1")
        self.assertEqual(inventory["summary"]["eligible"], 33)
        self.assertIsNone(inventory["default_selection"])
        self.assertEqual(len(self.preview([inventory["items"][0]["occurrence_id"]])["items"]), 1)
        self.preview([], code=2)
        self.preview([inventory["items"][0]["occurrence_id"]] * 33, code=2)

    def test_selected_source_tree_mode_path_destination_and_library_staleness_refuse_before_effects(self):
        for change in ("body", "new-file", "mode", "path", "destination", "library", "selection"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                project, cli = make_basic_workspace(base)
                for slug in ("first", "second"):
                    path = project / ".skills" / slug
                    path.mkdir(parents=True)
                    (path / "SKILL.md").write_text(f"# {slug}\n\nSafe instructions.\n")
                inventory = self.checked(cli.run("import-project", "--json"))
                preview = self.checked(cli.run("import-project", "--json", "--select", json.dumps(inventory["default_selection"]), "--review-manifest"))
                source = Path(preview["items"][-1]["source"]["file"])
                argv = preview["next_command_argv"][1:]
                if change == "body":
                    source.write_text(source.read_text() + "Changed.\n")
                elif change == "new-file":
                    (source.parent / "references.txt").write_text("Added")
                elif change == "mode":
                    source.chmod(0o755)
                elif change == "path":
                    moved = source.parent.with_name("replacement")
                    source.parent.rename(moved)
                    source.parent.symlink_to(moved, target_is_directory=True)
                elif change == "destination":
                    Path(preview["items"][-1]["destination"]["path"]).mkdir(parents=True)
                elif change == "library":
                    self.checked(cli.run("library", "init", "--path", str(base / "other-library"), "--no-git", "--json"))
                else:
                    argv[argv.index("--select") + 1] = json.dumps(preview["selection"][:1])
                refused = self.checked(cli.run(*argv), 2)
                self.assertEqual(refused["status"], "refused")
                self.assertNotIn("confirmation_token", refused)
                self.assertFalse((base / "state/catalog/trust.sqlite3").exists())

    def test_review_response_and_file_limits_never_return_usable_token(self):
        root = self.skill()
        # Long relative file identities fill a complete review before file count.
        for index in range(512):
            (root / f"{index:03}-{'a' * 180}.txt").write_text("x")
        refusal = self.preview(code=2)
        self.assertNotIn("confirmation_token", refusal)
        self.assertNotIn("next_command_argv", refusal)
        self.assertFalse((self.base / "home/.skillager/library").exists())

    def test_status_retains_destination_after_original_disappears(self):
        root = self.skill()
        self.init()
        preview = self.preview()
        before = self.status(preview)
        self.assertEqual(before["items"][0]["canonical"]["acceptance"], "missing")
        self.apply(preview)
        shutil.rmtree(root)
        status = self.status(preview)
        self.assertIsNone(status["items"][0]["source"])
        self.assertEqual(status["items"][0]["canonical"]["acceptance"], "accepted")
        self.assertNotIn("completed", json.dumps(status))

    def test_missing_canonical_approval_hash_never_reports_accepted(self):
        self.skill()
        self.init()
        preview = self.preview()
        self.apply(preview)
        def remove_hash(data):
            next(iter(data["global_approvals"].values())).pop("content_hash")
        approvals.mutate(self.base / "state/catalog", remove_hash)
        facts = self.status(preview)["items"][0]["canonical"]
        self.assertIsNone(facts["accepted_hash"])
        self.assertEqual(facts["acceptance"], "pending")
        self.assertEqual(self.inventory()["items"][0]["eligibility"], "conflict")

    def test_status_refuses_symlinked_or_noncanonical_saved_destination(self):
        self.skill()
        self.init()
        preview = self.preview()
        self.apply(preview)
        target = self.library / "skills/example"
        extra = target / "alias"
        extra.symlink_to(target / "SKILL.md")
        argv = ["import-project", "--status", "--json", "--select", json.dumps(preview["selection"]),
                "--destinations", json.dumps(["lib/example"])]
        refused = self.checked(self.cli.run(*argv), 2)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(self.inventory()["items"][0]["eligibility"], "conflict")
        extra.unlink()
        moved = self.library / "elsewhere"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        refused = self.checked(self.cli.run(*argv), 2)
        self.assertEqual(refused["status"], "refused")

    @unittest.skipUnless(shutil.which("git"), "Git is needed for fault injection")
    def test_later_commit_failure_keeps_accepted_success_and_reports_uncertain_pending(self):
        self.skill("one")
        self.skill("two")
        self.skill("three")
        self.init(git=True)
        preview = self.preview()
        failed_name = preview["items"][1]["destination"]["name"]
        hook = self.library / ".git/hooks/pre-commit"
        hook.write_text(f'#!/bin/sh\nif test -d "skills/{failed_name}"; then exit 1; fi\n')
        hook.chmod(0o755)
        # Installing an untracked Git hook changes no reviewed library file.
        result = self.apply(preview, code=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([item["status"] for item in result["items"]], ["accepted", "uncertain", "skipped"])
        hook.unlink()
        # A failed commit leaves a staged pending destination; status must still
        # report truthful current facts without requiring clean Git for a read.
        status = self.status(preview)
        self.assertEqual([item["canonical"]["acceptance"] for item in status["items"]], ["accepted", "pending", "missing"])

    def test_two_same_token_processes_never_duplicate_acceptance(self):
        self.skill()
        self.init()
        preview = self.preview()
        argv = [sys.executable, "-m", "skillager", *preview["next_command_argv"][1:]]
        processes = [subprocess.Popen(argv, cwd=self.project, env=self.cli.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        replies = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(stderr, "")
            replies.append((process.returncode, json.loads(stdout)))
        self.assertEqual(sorted(code for code, _ in replies), [0, 2])
        self.assertEqual(sorted(reply["status"] for _, reply in replies), ["completed", "refused"])
        self.assertEqual(self.inventory()["summary"]["already_saved"], 1)


if __name__ == "__main__":
    unittest.main()
