"""Exact project inventory through the installed public command boundary."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from .support import BODY_SENTINEL, CliResult, make_basic_workspace


class ProjectInventoryBehaviorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skillager-project-inventory-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.base)

    def checked(self, result: CliResult, code=0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def page(self, *options, code=0):
        return self.checked(self.cli.run("list", "--scope", "project", "--json", *options), code)

    def skill(self, path: Path, name="Example"):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Declared inventory description.\n---\n\n{BODY_SENTINEL}\n")
        return path

    def test_editable_package_overlap_does_not_hide_unapproved_native_occurrence(self):
        native = self.skill(self.project / ".agents/skills/example")
        packages = self.editable_package()
        self.skill(packages / "example/skills/package-only")
        self.skill(self.base / "home/.agents/skills/global-only")
        self.skill(self.project / "child-repository/.agents/skills/child-only")
        self.skill(self.base / "sibling/.agents/skills/sibling-only")
        broad = self.checked(self.cli.run("review", "--include-global", "--include-blocked", "--include-lint-blocked", "--json"))
        shared = [row for row in broad["selected"] if row.get("entrypoint") == str(native / "SKILL.md")]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["source"]["type"], "python-package")
        before = self.state_bytes()
        result = self.page()
        self.assertEqual(result["project_root"], str(self.project))
        self.assertEqual(result["observation"], {"status": "complete", "errors": [], "error_count": 0})
        row, = result["skills"]
        self.assertEqual(row["skill_file"], str(native / "SKILL.md"))
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["trust"], "discovered")
        self.assertEqual(row["kind"], "native")
        self.assertEqual(row["native"], {"scope": "project", "managed": False, "agent": "codex"})
        self.assertEqual(self.state_bytes(), before)
        self.assertFalse((self.project / ".skillager").exists())

    def editable_package(self):
        packages = self.project / ".venv/lib/python3.13/site-packages"
        distribution = packages / "example-1.0.dist-info"
        distribution.mkdir(parents=True)
        (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: example\nVersion: 1.0\n")
        (distribution / "direct_url.json").write_text(json.dumps({"url": self.project.as_uri(), "dir_info": {"editable": True}}))
        return packages

    def test_editable_overlap_preserves_ordinary_review_approval_block_and_unblock(self):
        native = self.skill(self.project / ".agents/skills/example")
        packages = self.editable_package()
        self.skill(packages / "example/skills/example")
        observed = self.checked(self.cli.run("review", "--json"))
        original, = [row for row in observed["selected"] if row["entrypoint"] == str(native / "SKILL.md")]
        sid = original["id"]
        for scope in (("--project-only",), ()):
            with self.subTest(scope=scope):
                for action, expected in (("approve", "accepted"), ("block", "blocked"), ("unblock", "accepted")):
                    self.checked(self.cli.run("review", action, sid, *scope, "--json"))
                    # The persisted index is a disposable cache, never authority
                    # for connecting this occurrence to its review decision.
                    (self.base / "state/project/index.json").unlink(missing_ok=True)
                    before = self.state_bytes()
                    page = self.page()
                    row, = page["skills"]
                    self.assertEqual(page["observation"]["status"], "complete")
                    self.assertEqual((row["id"], row["skill_file"], row["status"]),
                                     (sid, str(native / "SKILL.md"), expected))
                    self.assertEqual(self.state_bytes(), before)
        instruction = native / "SKILL.md"
        instruction.write_text(instruction.read_text() + "Changed instructions.\n")
        self.assertEqual(self.page()["skills"][0]["status"], "pending")

    def test_editable_overlap_same_named_native_occurrences_do_not_share_decisions(self):
        one = self.skill(self.project / ".agents/skills/example", "Same name")
        two = self.skill(self.project / ".claude/skills/example", "Same name")
        self.editable_package()
        observed = self.checked(self.cli.run("review", "--json"))
        ids = {row["entrypoint"]: row["id"] for row in observed["selected"]}
        self.assertNotEqual(ids[str(one / "SKILL.md")], ids[str(two / "SKILL.md")])
        self.checked(self.cli.run("review", "approve", ids[str(one / "SKILL.md")], "--project-only", "--json"))
        self.checked(self.cli.run("review", "block", ids[str(two / "SKILL.md")], "--project-only", "--json"))
        rows = {row["skill_file"]: row for row in self.page()["skills"]}
        self.assertEqual(rows[str(one / "SKILL.md")]["status"], "accepted")
        self.assertEqual(rows[str(two / "SKILL.md")]["status"], "blocked")
        self.assertEqual(len({row["occurrence_id"] for row in rows.values()}), 2)

    def state_bytes(self):
        return {str(path.relative_to(self.base)): path.read_bytes() for directory in ("state", "cache", "home")
                for path in (self.base / directory).rglob("*") if path.is_file()}

    def test_same_names_and_internal_aliases_have_exact_stable_occurrences(self):
        one = self.skill(self.project / ".agents/skills/one", "Same name")
        two = self.skill(self.project / ".claude/skills/two", "Same name")
        (self.project / ".agents/skills/alias").symlink_to(two, target_is_directory=True)
        rows = self.page()["skills"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["skill_file"] for row in rows}, {str(one / "SKILL.md"), str(two / "SKILL.md")})
        self.assertEqual(len({row["occurrence_id"] for row in rows}), 2)
        for row in rows:
            self.assertEqual(row["occurrence_id"], "project:" + hashlib.sha256(os.fsencode(row["skill_file"])).hexdigest())
        before = {row["skill_file"]: row["occurrence_id"] for row in rows}
        self.skill(self.project / ".skills/zero", "Same name")
        after = {row["skill_file"]: row["occurrence_id"] for row in self.page()["skills"]}
        self.assertEqual({path: after[path] for path in before}, before)

    def test_escape_and_broken_links_report_partial_instead_of_false_empty(self):
        outside = self.skill(self.base / "outside")
        collection = self.project / ".agents/skills"
        collection.mkdir(parents=True)
        (collection / "escape").symlink_to(outside, target_is_directory=True)
        (collection / "broken").symlink_to(self.base / "missing", target_is_directory=True)
        result = self.page()
        self.assertEqual(result["skills"], [])
        self.assertEqual(result["observation"]["status"], "partial")
        self.assertEqual({item["code"] for item in result["observation"]["errors"]}, {"outside-project", "unreadable-path"})
        self.assertNotIn(str(outside / "SKILL.md"), json.dumps(result))

    def test_missing_metadata_does_not_infer_body_and_quarantine_stays_visible(self):
        valid = self.skill(self.project / ".skills/no-header")
        (valid / "SKILL.md").write_text(f"# Body heading\n\n{BODY_SENTINEL}\n")
        broken = self.skill(self.project / ".skills/broken")
        (broken / "skillager.yaml").write_text("schema: invalid\n")
        result = self.page()
        self.assertEqual(len(result["skills"]), 2)
        self.assertEqual(result["observation"]["status"], "partial")
        rows = {row["skill_file"]: row for row in result["skills"]}
        self.assertEqual(rows[str(broken / "SKILL.md")]["status"], "lint_blocked")
        self.assertEqual(rows[str(valid / "SKILL.md")]["description"], "")
        self.assertEqual(rows[str(valid / "SKILL.md")]["name"], "no-header")

    def test_all_trust_states_remain_visible_without_changing_approval_state(self):
        accepted = self.skill(self.project / ".skills/accepted")
        self.skill(self.project / ".skills/blocked")
        self.skill(self.project / ".skills/pending")
        self.checked(self.cli.run("review", "approve", "project/accepted", "--project-only", "--json"))
        self.checked(self.cli.run("review", "block", "project/blocked", "--project-only", "--json"))
        before = self.state_bytes()
        rows = {row["id"]: row for row in self.page()["skills"]}
        self.assertEqual({key: row["status"] for key, row in rows.items()}, {
            "project/accepted": "accepted", "project/blocked": "blocked", "project/pending": "pending"})
        self.assertEqual(self.state_bytes(), before)
        content = accepted / "SKILL.md"
        content.write_text(content.read_text() + "Edited version.\n")
        rows = {row["id"]: row for row in self.page()["skills"]}
        self.assertEqual(rows["project/accepted"]["status"], "pending")

    def test_exact_cwd_git_worktree_and_plain_subfolder_do_not_borrow_parent_inventory(self):
        self.skill(self.project / ".skills/main")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        worktree = self.base / "worktree"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-q", "-b", "other", str(worktree)], check=True)
        self.skill(worktree / ".skills/other")
        self.cli.project = worktree
        result = self.page()
        self.assertEqual(result["project_root"], str(worktree))
        self.assertEqual([row["id"] for row in result["skills"]], ["project/other"])
        nested = self.project / "plain"
        nested.mkdir()
        self.cli.project = nested
        result = self.page()
        self.assertEqual(result["project_root"], str(nested))
        self.assertEqual(result["skills"], [])
        self.assertEqual(result["observation"]["status"], "complete")

    def managed(self, slug, source_type):
        target = self.skill(self.project / ".agents/skills" / slug)
        (target / "skillager.materialized.yaml").write_text(
            f"schema: skillager.materialized.v1\nid: lib/example\nsource_id: lib/example\n"
            f"source_type: {source_type}\nagent: codex\nscope: project\n")
        return target

    def test_combined_pagination_has_managed_full_stub_router_once_and_invalidates_on_change(self):
        self.skill(self.project / ".skills/original")
        targets = [self.managed("full", "library"), self.managed("stub", "skillager-stub"),
                   self.managed("router", "skillager-router")]
        rows = []
        cursor = None
        first = None
        while True:
            page = self.page("--limit", "1", *(["--cursor", cursor] if cursor else []))
            first = first or page
            self.assertEqual(page["observation"]["status"], "complete")
            rows.extend(page["skills"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["kind"] for row in rows}, {"native", "full", "stub", "router"})
        for row in rows:
            if row["kind"] != "native":
                self.assertEqual(row["exposure"]["skill_id"], "lib/example")
                self.assertEqual(row["status"], "present")
                self.assertIsNone(row["trust"])
        path = targets[0] / "SKILL.md"
        path.write_text(path.read_text() + "Current copy edit.\n")
        stale = self.page("--limit", "1", "--cursor", first["next_cursor"], code=15)
        self.assertEqual(stale["error"]["code"], "stale_cursor")

    def test_original_content_and_approval_changes_invalidate_pagination(self):
        one = self.skill(self.project / ".skills/one")
        self.skill(self.project / ".skills/two")
        first = self.page("--limit", "1")
        path = one / "SKILL.md"
        old = path.stat()
        text = path.read_text()
        path.write_text(text.replace("Declared", "Modified"))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.page("--limit", "1", "--cursor", first["next_cursor"], code=15)["error"]["code"], "stale_cursor")
        first = self.page("--limit", "1")
        self.checked(self.cli.run("review", "block", "project/two", "--project-only", "--json"))
        self.page("--limit", "1", "--cursor", first["next_cursor"], code=15)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "requires POSIX permission enforcement")
    def test_unreadable_collection_never_claims_complete_empty_and_errors_are_bounded(self):
        collection = self.project / ".agents/skills"
        self.skill(collection / "unreadable")
        mode = stat.S_IMODE(collection.stat().st_mode)
        collection.chmod(0)
        try:
            result = self.page()
            self.assertEqual(result["observation"]["status"], "partial")
            self.assertEqual(result["skills"], [])
        finally:
            collection.chmod(mode)
        for number in range(25):
            (collection / f"broken-{number}").symlink_to(self.base / f"missing-{number}")
        result = self.page()
        self.assertEqual(result["observation"]["error_count"], 25)
        self.assertEqual(len(result["observation"]["errors"]), 20)
        self.assertEqual(len(result["skills"]), 1)

    def test_invalid_managed_sidecar_is_partial_not_empty_complete(self):
        target = self.managed("broken", "library")
        (target / "skillager.materialized.yaml").write_text("schema: unsupported\n")
        result = self.page()
        self.assertEqual(result["observation"]["status"], "partial")
        self.assertEqual(result["skills"], [])
        self.assertEqual(result["observation"]["error_count"], 1)
        self.assertEqual(result["observation"]["errors"], [{"code": "unknown-target", "path": str(target)}])

    def test_unrecognized_managed_location_and_working_helper_have_explicit_coverage(self):
        working = self.managed("working", "skillager-working")
        unsupported = self.skill(self.project / ".skills/unsupported")
        (unsupported / "skillager.materialized.yaml").write_text(
            "schema: skillager.materialized.v1\nid: lib/example\nsource_type: library\n")
        result = self.page()
        self.assertEqual(result["skills"], [])
        self.assertEqual(result["observation"]["status"], "partial")
        self.assertEqual(result["observation"]["errors"], [
            {"code": "unrecognized-managed-copy", "path": str(unsupported)}])
        self.assertTrue(working.is_dir())

    def test_public_exposure_generated_copy_keeps_original_lineage_and_is_not_approval(self):
        self.skill(self.project / ".skills/example")
        approved = self.checked(self.cli.run("review", "approve", "project/example", "--json"))
        canonical = approved["action"]["library_sync"]["items"][0]["canonical_skill_id"]
        preview = self.checked(self.cli.run("expose", canonical, "--mode", "stub", "--agent", "codex", "--dry-run", "--json"))[0]
        self.checked(self.cli.run(*preview["next_command_argv"][1:]))
        result = self.page()
        self.assertEqual(result["observation"]["status"], "complete")
        original, = [row for row in result["skills"] if row["kind"] == "native"]
        managed, = [row for row in result["skills"] if row["kind"] == "stub"]
        self.assertEqual(original["status"], "accepted")
        self.assertEqual(managed["status"], "present")
        self.assertIsNone(managed["trust"])
        self.assertEqual(managed["exposure"]["skill_id"], canonical)
        self.assertIsNotNone(managed["exposure"]["source_library_id"])
        self.assertEqual(managed["exposure"]["target"] + "/SKILL.md", managed["skill_file"])

    def test_readable_resource_symlinks_are_not_directory_errors_and_instruction_lint_is_preserved(self):
        source = self.skill(self.project / ".agents/skills/example")
        resource = source / "instructions.md"
        resource.write_text("Linked resource.\n")
        (source / "reference.md").symlink_to(resource)
        result = self.page()
        self.assertEqual(result["observation"]["status"], "complete")
        resource.write_text((source / "SKILL.md").read_text())
        (source / "SKILL.md").unlink()
        (source / "SKILL.md").symlink_to(resource)
        result = self.page()
        self.assertEqual(result["skills"][0]["skill_file"], str(resource))
        self.assertEqual(result["skills"][0]["status"], "lint_blocked")
        self.assertEqual({error["code"] for error in result["observation"]["errors"]}, {"invalid-skill"})

    def test_managed_instruction_symlinks_and_invalid_sidecar_context_are_partial(self):
        variants = ("instruction-link", "agent", "scope", "working-schema", "malformed-members")
        for number, variant in enumerate(variants, start=1):
            with self.subTest(variant=variant):
                target = self.managed(variant, "library")
                sidecar = target / "skillager.materialized.yaml"
                if variant == "instruction-link":
                    other = target / "instructions.md"
                    (target / "SKILL.md").rename(other)
                    (target / "SKILL.md").symlink_to(other)
                elif variant == "working-schema":
                    sidecar.write_text("schema: unsupported\nsource_type: skillager-working\n")
                elif variant == "malformed-members":
                    sidecar.write_text(sidecar.read_text() + "skill_ids: 123\n")
                else:
                    previous = "codex" if variant == "agent" else "project"
                    sidecar.write_text(sidecar.read_text().replace(f"{variant}: {previous}", f"{variant}: other"))
                result = self.page()
                self.assertEqual(result["observation"]["status"], "partial")
                self.assertEqual(result["skills"], [])
                self.assertEqual(result["observation"]["error_count"], number)
                self.assertEqual(len(result["observation"]["errors"]), number)
                self.assertEqual({error["path"] for error in result["observation"]["errors"]},
                                 {str(target.parent / name) for name in variants[:number]})

    def test_unsupported_options_and_limits_are_rejected(self):
        for options in (("--limit", "0"), ("--limit", "101"), ("--source", "project"), ("--agent", "codex")):
            result = self.cli.run("list", "--scope", "project", "--json", *options)
            self.assertEqual(result.code, 2, result.stdout + result.stderr)
        self.assertEqual(self.cli.run("list", "--scope", "project").code, 2)
