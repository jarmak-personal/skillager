from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skillager.exposure.plan_targets import tree_state
from tests.behavior.support import BODY_SENTINEL, SkillagerCli, make_basic_workspace


class ExposurePlanBehaviorTests(unittest.TestCase):
    def checked(self, result, code=0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def fixture(self, root: Path, agent="codex"):
        project, cli = make_basic_workspace(root)
        self.checked(cli.run("library", "init", "--path", str(root / "library"), "--no-git", "--json"))
        source = project / (".agents" if agent == "codex" else ".claude") / "skills" / "demo"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: Demo\ndescription: Use precise fixture guidance.\n---\n\nUse the supporting guide.\n" + BODY_SENTINEL)
        (source / "support").mkdir()
        (source / "support" / "helper.sh").write_text("#!/bin/sh\ntrue\n")
        (source / "support" / "helper.sh").chmod(0o755)
        self.checked(cli.run("review", "approve", "project/demo", "--json"))
        status = self.checked(cli.run("library", "sync", "--status", "--json"))
        lineage = next(item for item in status["lineages"] if any(origin["path"] == str(source) for origin in item["origins"]))
        origin = next(item for item in lineage["origins"] if item["path"] == str(source))
        relation = {"library_id": lineage["canonical"]["library_id"], "skill_id": lineage["canonical"]["skill_id"]}
        return project, cli, source, origin["origin_id"], relation

    def preview(self, cli: SkillagerCli, action: str, agent="codex", **fields):
        request = {"schema": "skillager.exposure-request.v1", "action": action, **fields}
        return self.checked(cli.run("expose", "--request-json", json.dumps(request), "--agent", agent, "--scope", "project", "--dry-run", "--json"))

    def apply(self, cli: SkillagerCli, preview, code=0):
        return self.checked(cli.run(*preview["next_command_argv"][1:]), code)

    def test_native_adoption_group_departure_ungroup_and_remove_both_agents(self):
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                project, cli, source, origin, relation = self.fixture(Path(tmp).resolve(), agent)
                original = tree_state(source)
                adoption = self.preview(cli, "adopt-native", agent, origin_id=origin, source=relation, mode="stub")
                self.assertEqual(tree_state(source), original)
                self.assertEqual(adoption, self.preview(cli, "adopt-native", agent, origin_id=origin, source=relation, mode="stub"))
                self.assertEqual(self.apply(cli, adoption)["status"], "applied")
                self.assertFalse((source / "support").exists())
                grouped = self.preview(cli, "group", agent, name="Review tools", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[{"exposure_id": "demo"}])
                self.assertFalse((project / ".skillager" / "tags.json").exists())
                self.assertEqual(self.apply(cli, grouped)["status"], "applied")
                self.assertFalse(source.exists())
                router = source.parent / "skillager-review-tools"
                self.assertTrue(router.exists())
                ungroup = self.preview(cli, "ungroup", agent, router_id=router.name, mode="native")
                self.assertEqual(self.apply(cli, ungroup)["status"], "applied")
                self.assertFalse(router.exists())
                tag = self.checked(cli.run("tag", "show", "review-tools", "--json"))
                self.assertIn(relation["skill_id"], json.dumps(tag))
                regroup = self.preview(cli, "group", agent, name="Second", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[])
                self.assertEqual(self.apply(cli, regroup)["status"], "applied")
                standalone = next(path for path in source.parent.iterdir() if path.name.startswith("lib-") and path.is_dir())
                standalone_state = tree_state(standalone)
                # Removing membership never consumes the body or an unselected copy.
                self.checked(cli.run("review", "block", relation["skill_id"], "--json"))
                remove = self.preview(cli, "set-members", agent, router_id="skillager-second", members=[], library_id=relation["library_id"], replace=[], departures=[{"skill_id": relation["skill_id"], "mode": "remove"}])
                self.assertEqual(self.apply(cli, remove)["status"], "applied")
                self.assertEqual(tree_state(standalone), standalone_state)

    def test_remove_native_preserves_library_and_refuses_stale_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, source, origin, relation = self.fixture(Path(tmp).resolve())
            preview = self.preview(cli, "remove-native", origin_id=origin, source=relation)
            (source / "extra.txt").write_text("not preserved")
            refused = self.apply(cli, preview, 2)
            self.assertEqual(refused["status"], "refused")
            self.assertTrue((source / "extra.txt").exists())
            (source / "extra.txt").unlink()
            self.assertEqual(self.apply(cli, preview)["status"], "applied")
            self.assertFalse(source.exists())
            self.assertTrue((Path(tmp) / "library" / "skills" / relation["skill_id"][4:] / "SKILL.md").exists())

    def test_invalid_shapes_and_request_limits_are_structured_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cli = make_basic_workspace(Path(tmp).resolve())
            for request in ({"schema": "skillager.exposure-request.v1", "action": []}, {"schema": "other"}, "x" * 65537):
                result = self.checked(cli.run("expose", "--request-json", json.dumps(request), "--agent", "codex", "--scope", "project", "--dry-run", "--json"), 2)
                self.assertEqual(result["status"], "refused")

    def assert_complete_effects(self, preview):
        from skillager.simple_yaml import load_mapping
        import hashlib
        for target in preview["targets"]:
            root = Path(target["path"])
            if target["after"] is None:
                self.assertFalse(root.exists(), target)
                continue
            self.assertEqual(root.stat().st_mode & 0o7777, target["after"]["mode"])
            expected = set()
            for effect in target["file_effects"]:
                path = root if effect["path"] == "." else root / effect["path"]
                after = effect["after"]
                if after is None:
                    self.assertFalse(path.exists(), effect)
                    continue
                expected.add(effect["path"])
                self.assertEqual(path.stat().st_mode & 0o7777, after["mode"])
                if after["type"] == "file":
                    if "metadata" in after:
                        actual = json.loads(path.read_text()) if target["kind"] == "tags" else load_mapping(path)
                        for key in after["generated_fields"]:
                            if key.startswith("tags."):
                                self.assertIsInstance(actual["tags"][target["tag"]].pop("updated_at"), str)
                            else:
                                self.assertIn(key, actual)
                                actual.pop(key)
                        self.assertEqual(actual, after["metadata"])
                    else:
                        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), after["sha256"])
                        self.assertEqual(path.stat().st_size, after["size"])
            if target["kind"] not in {"tags", "parent"}:
                self.assertEqual({path.relative_to(root).as_posix() for path in root.rglob("*")}, expected)

    def test_complete_supporting_file_metadata_and_directory_effects(self):
        for mode in ("native", "stub"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                _, cli, source, origin, relation = self.fixture(Path(tmp).resolve())
                source.chmod(0o751)
                preview = self.preview(cli, "adopt-native", origin_id=origin, source=relation, mode=mode)
                self.apply(cli, preview)
                self.assert_complete_effects(preview)
                group = self.preview(cli, "group", name="Precise group", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[{"exposure_id": source.name}])
                applied = self.apply(cli, group)
                self.assertEqual({item["target_id"] for item in applied["results"]}, {item["target_id"] for item in group["targets"]})
                self.assert_complete_effects(group)

    def test_exact_copy_selector_keeps_default_and_adopted_copies_independent(self):
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                _, cli, source, origin, relation = self.fixture(Path(tmp).resolve(), agent)
                skill_id = relation["skill_id"]
                direct = self.checked(cli.run("expose", skill_id, "--agent", agent, "--mode", "native", "--dry-run", "--json"))[0]
                self.checked(cli.run(*direct["next_command_argv"][1:]))
                default = Path(direct["target"])
                adoption = self.preview(cli, "adopt-native", agent, origin_id=origin, source=relation, mode="native")
                self.apply(cli, adoption)
                ambiguous = self.checked(cli.run("expose", skill_id, "--agent", agent, "--mode", "stub", "--dry-run", "--json"))[0]
                self.assertEqual(ambiguous["status"], "skipped")
                self.assertIn("multiple managed project copies", ambiguous["reason"])
                self.assertIsNone(ambiguous["target"])
                for selected, other in ((source, default), (default, source)):
                    for mode in ("stub", "native"):
                        untouched = tree_state(other)
                        preview = self.checked(cli.run("expose", skill_id, "--exposure-id", selected.name, "--agent", agent, "--mode", mode, "--dry-run", "--json"))[0]
                        self.assertEqual(preview["target"], str(selected))
                        self.assertEqual(preview["preview"]["selected_exposure_id"], selected.name)
                        self.assertEqual(self.checked(cli.run(*preview["next_command_argv"][1:]))[0]["status"], "exposed")
                        self.assertEqual(tree_state(other), untouched)
                for selector in ("missing", "../demo"):
                    bad = cli.run("expose", skill_id, "--exposure-id", selector, "--agent", agent, "--mode", "stub", "--dry-run", "--json")
                    self.assertEqual(bad.code, 2)
                listed = self.checked(cli.run("expose", "--list", "--agent", agent, "--json"))["exposures"]
                self.assertEqual({item["target"] for item in listed}, {str(source), str(default)})
                self.assertTrue(all(item["status"] == "current" for item in listed))

    def test_ambiguous_copy_skips_only_that_member_and_agent_in_an_existing_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, cli, source, origin, relation = self.fixture(root)
            skill_id = relation["skill_id"]
            default = Path(self.checked(cli.run("expose", skill_id, "--agent", "codex", "--mode", "native", "--json"))[0]["target"])
            self.apply(cli, self.preview(cli, "adopt-native", origin_id=origin, source=relation, mode="native"))
            self.checked(cli.run("library", "new", "second", "--json"))
            (root / "library/skills/second/SKILL.md").write_text("---\nname: Second\ndescription: Use secondary test guidance.\n---\n\nUse secondary test guidance.\n")
            self.checked(cli.run_confirmed("library", "accept", "lib/second", "--yes", "--json"))
            before = {path: tree_state(path) for path in (source, default)}
            results = self.checked(cli.run("expose", skill_id, "lib/second", "--all-agents", "--mode", "stub", "--json"))
            skipped = [item for item in results if item["status"] == "skipped"]
            self.assertEqual(len(skipped), 1, results)
            self.assertEqual((skipped[0]["skill_id"], skipped[0]["agent"]), (skill_id, "codex"))
            self.assertIn("multiple managed project copies", skipped[0]["reason"])
            self.assertEqual({(item["skill_id"], item["agent"]) for item in results if item["status"] == "exposed"},
                             {(skill_id, "claude"), ("lib/second", "codex"), ("lib/second", "claude")})
            self.assertEqual({path: tree_state(path) for path in before}, before)
            self.assertFalse((project / ".skillager-locks").exists())

    def test_native_target_and_approval_changes_refuse_without_target_writes(self):
        for change in ("root-mode", "file-mode", "disappear", "origin-block", "canonical-block", "extra", "symlink"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                _, cli, source, origin, relation = self.fixture(Path(tmp).resolve())
                preview = self.preview(cli, "adopt-native", origin_id=origin, source=relation, mode="stub")
                if change == "root-mode":
                    source.chmod(0o700)
                elif change == "file-mode":
                    (source / "support/helper.sh").chmod(0o600)
                elif change == "disappear":
                    source.rename(source.with_name("moved"))
                elif change == "origin-block":
                    self.checked(cli.run("review", "block", "project/demo", "--json"))
                elif change == "canonical-block":
                    self.checked(cli.run("review", "block", relation["skill_id"], "--json"))
                elif change == "extra":
                    (source / ".DS_Store").write_bytes(b"preserve me")
                else:
                    (source / "link").symlink_to("SKILL.md")
                before = self.snapshot(source.parent)
                self.assertEqual(self.apply(cli, preview, 2)["status"], "refused")
                self.assertEqual(self.snapshot(source.parent), before)

    def snapshot(self, root):
        import stat
        return {str(path.relative_to(root)): (stat.S_IMODE(path.lstat().st_mode), path.readlink().as_posix() if path.is_symlink() else path.read_bytes() if path.is_file() else None)
                for path in root.rglob("*")}

    def test_router_tag_conflicts_and_preview_identity_changes_preserve_files(self):
        for change in ("tag-appearance", "target-appearance", "agent", "source-accepted", "modified-replacement"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                project, cli, source, origin, relation = self.fixture(Path(tmp).resolve())
                self.apply(cli, self.preview(cli, "adopt-native", origin_id=origin, source=relation, mode="native"))
                preview = self.preview(cli, "group", name="Group", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[{"exposure_id": source.name}])
                if change == "tag-appearance":
                    self.assertEqual(cli.run("tag", "create", "group").code, 0)
                elif change == "target-appearance":
                    target = source.parent / "skillager-group"
                    target.mkdir()
                    (target / "SKILL.md").write_text("Unmanaged local material")
                elif change == "agent":
                    argv = preview["next_command_argv"]
                    argv[argv.index("--agent") + 1] = "claude"
                elif change == "source-accepted":
                    canonical = Path(tmp).resolve() / "library/skills" / relation["skill_id"][4:]
                    with (canonical / "SKILL.md").open("a") as handle:
                        handle.write("\nNew approved canonical guidance.\n")
                    self.checked(cli.run_confirmed("library", "accept", relation["skill_id"], "--yes", "--json"))
                else:
                    (source / "local.txt").write_text("local edit")
                before = self.snapshot(project)
                self.assertEqual(self.apply(cli, preview, 2)["status"], "refused")
                self.assertEqual(self.snapshot(project), before)

    def test_shared_tag_refuses_membership_changes_but_managed_remove_remains_source_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cli, source, _, relation = self.fixture(Path(tmp).resolve())
            preview = self.preview(cli, "group", name="Shared", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[])
            self.apply(cli, preview)
            self.checked(cli.run("expose", "--tag", "shared", "--mode", "router", "--agent", "claude", "--json"))
            request = {"schema": "skillager.exposure-request.v1", "action": "set-members", "router_id": "skillager-shared", "members": [], "library_id": relation["library_id"], "replace": [], "departures": [{"skill_id": relation["skill_id"], "mode": "remove"}]}
            refused = self.checked(cli.run("expose", "--request-json", json.dumps(request), "--agent", "codex", "--dry-run", "--json"), 2)
            self.assertEqual(refused["reason_code"], "shared-tag-conflict")
            self.checked(cli.run("review", "block", relation["skill_id"], "--json"))
            remove = self.checked(cli.run("expose", "--remove", "skillager-shared", "--agent", "codex", "--json"))["results"][0]
            self.checked(cli.run(*remove["next_command_argv"][1:]))
            self.assertFalse((source.parent / "skillager-shared").exists())
            self.assertEqual(self.checked(cli.run("expose", "--list", "--agent", "claude", "--json"))["exposures"][0]["exposure_id"], "skillager-shared")
            self.assertIn(relation["skill_id"], json.dumps(self.checked(cli.run("tag", "show", "shared", "--json"))))

    def test_change_members_discloses_advancement_and_explicit_stub_departure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, cli, source, _, relation = self.fixture(root)
            self.checked(cli.run("library", "new", "second", "--json"))
            second = root / "library/skills/second"
            (second / "SKILL.md").write_text("---\nname: Second\ndescription: Use secondary test guidance.\n---\n\nUse secondary test guidance.\n")
            self.checked(cli.run_confirmed("library", "accept", "lib/second", "--yes", "--json"))
            first = self.preview(cli, "group", name="Mutable", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[])
            self.apply(cli, first)
            original = tree_state(source)
            add = self.preview(cli, "set-members", router_id="skillager-mutable", members=[relation["skill_id"], "lib/second"], library_id=relation["library_id"], replace=[], departures=[])
            self.apply(cli, add)
            self.assert_complete_effects(add)
            canonical = root / "library/skills" / relation["skill_id"][4:]
            with (canonical / "SKILL.md").open("a") as handle:
                handle.write("\nNew approved guidance to disclose during restoration.\n")
            self.checked(cli.run_confirmed("library", "accept", relation["skill_id"], "--yes", "--json"))
            change = self.preview(cli, "set-members", router_id="skillager-mutable", members=["lib/second"], library_id=relation["library_id"], replace=[], departures=[{"skill_id": relation["skill_id"], "mode": "stub"}])
            old_hash = next(item["content_hash"] for item in add["sources"] if item["id"] == relation["skill_id"])
            self.assertNotEqual(next(item["content_hash"] for item in change["sources"] if item["id"] == relation["skill_id"]), old_hash)
            self.apply(cli, change)
            self.assert_complete_effects(change)
            self.assertEqual(tree_state(source), original)
            ungroup = self.preview(cli, "ungroup", router_id="skillager-mutable", mode="stub")
            self.apply(cli, ungroup)
            self.assert_complete_effects(ungroup)
            self.assertEqual(ungroup["group"]["after_tag_members"], ["lib/second"])

    def test_64_member_public_plan_and_65_member_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, cli = make_basic_workspace(root)
            self.checked(cli.run("library", "init", "--path", str(root / "library"), "--no-git", "--json"))
            ids = []
            for index in range(64):
                path = project / ".skills" / f"member-{index:02}"
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text(f"---\nname: Member {index}\ndescription: Use member guidance.\n---\n\nUse member guidance.\n")
                ids.append(f"project/member-{index:02}")
            reviewed = self.checked(cli.run("review", "approve", *ids, "--json"))["action"]["library_sync"]
            self.assertEqual(reviewed["counts"]["created"], 64)
            status = self.checked(cli.run("library", "sync", "--status", "--json"))
            members = sorted(item["canonical"]["skill_id"] for item in status["lineages"])
            library_id = status["lineages"][0]["canonical"]["library_id"]
            preview = self.preview(cli, "group", name="Capacity", members=members, library_id=library_id, replace=[])
            self.assertEqual(len(preview["sources"]), 64)
            self.assertLess(len(json.dumps(preview).encode()), 4 * 1024 * 1024)
            self.apply(cli, preview)
            self.assert_complete_effects(preview)
            restoration = self.preview(cli, "ungroup", router_id="skillager-capacity", mode="native")
            self.assertEqual(sum(item["kind"] == "direct" for item in restoration["targets"]), 64)
            self.apply(cli, restoration)
            self.assert_complete_effects(restoration)
            request = {**preview["request"], "members": [*members, "lib/over-limit"]}
            refused = self.checked(cli.run("expose", "--request-json", json.dumps(request), "--agent", "codex", "--dry-run", "--json"), 2)
            self.assertEqual(refused["reason_code"], "request-limit")

    @unittest.skipUnless(Path("/dev/shm").is_dir(), "requires a real second local filesystem")
    def test_cross_filesystem_staging_is_budgeted_and_preserves_exact_effects(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as tmp:
            root = Path(tmp).resolve()
            if root.stat().st_dev == Path(tempfile.gettempdir()).stat().st_dev:
                self.skipTest("fixture scratch and destination are on the same filesystem")
            _, cli, _, _, relation = self.fixture(root)
            preview = self.preview(cli, "group", name="Other filesystem", members=[relation["skill_id"]], library_id=relation["library_id"], replace=[])
            self.assertGreater(preview["staging"]["transfer_reserve_bytes"], 0)
            self.assertEqual(preview["staging"]["peak_bytes"], sum(preview["staging"][key] for key in ("candidate_bytes", "retained_original_bytes", "transfer_reserve_bytes")))
            self.apply(cli, preview)
            self.assert_complete_effects(preview)
