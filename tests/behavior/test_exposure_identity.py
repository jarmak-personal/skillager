from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skillager.commands.impl import _router_tag_is_current
from skillager.exposure.plan_targets import tree_state
from skillager.exposure.target_state import write_materialized_sidecar
from skillager.simple_yaml import load_mapping
from tests.behavior.support import BODY_SENTINEL, SkillagerCli, make_basic_workspace


class ExposureIdentityBehaviorTests(unittest.TestCase):
    def checked(self, result, code=0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def library(self, root, cli, label):
        path = root / label
        initialized = self.checked(cli.run("library", "init", "--path", str(path), "--no-git", "--json"))
        self.checked(cli.run("library", "new", "x", "--json"))
        (path / "skills/x/SKILL.md").write_text(
            "---\nname: Identity fixture\ndescription: Use precise identity fixture guidance.\n---\n\n" + BODY_SENTINEL + "\n"
        )
        self.checked(cli.run_confirmed("library", "accept", "lib/x", "--yes", "--json"))
        return path, initialized["library"]["library_id"]

    def plan(self, cli, agent, action, code=0, **fields):
        request = {"schema": "skillager.exposure-request.v1", "action": action, **fields}
        return self.checked(cli.run("expose", "--request-json", json.dumps(request), "--agent", agent,
                                    "--scope", "project", "--dry-run", "--json"), code)

    def apply(self, cli, preview, code=0):
        return self.checked(cli.run(*preview["next_command_argv"][1:]), code)

    def exposures(self, cli, agent):
        return self.checked(cli.run("expose", "--list", "--agent", agent, "--json"))["exposures"]

    def expose(self, cli, agent, mode):
        preview = self.checked(cli.run("expose", "lib/x", "--agent", agent, "--mode", mode,
                                       "--scope", "project", "--dry-run", "--json"))[0]
        self.apply(cli, preview)
        return Path(preview["target"])

    def group(self, cli, agent, library_id):
        name = f"Identity {agent}"
        plan = self.plan(cli, agent, "group", name=name, library_id=library_id, members=["lib/x"], replace=[])
        metadata = next(effect["after"]["metadata"] for target in plan["targets"] if target["kind"] == "router"
                        for effect in target["file_effects"] if effect["path"] == "skillager.materialized.yaml")
        self.assertEqual(metadata["member_sources"], [{"skill_id": "lib/x", "source_library_id": library_id}])
        self.apply(cli, plan)
        return f"skillager-identity-{agent}"

    def foreign_fixture(self, root):
        project, first = make_basic_workspace(root)
        library_a, id_a = self.library(root, first, "library-a")
        for agent in ("codex", "claude"):
            # Both direct modes use the same recorded canonical identity.
            self.expose(first, agent, "native")
            self.expose(first, agent, "stub")
            self.group(first, agent, id_a)
        second = SkillagerCli(project, state=root / "state-b/project", catalog_state=root / "state-b/catalog",
                              home=root / "home-b", cache=root / "cache-b")
        library_b, id_b = self.library(root, second, "library-b")
        self.assertNotEqual(id_a, id_b)
        return project, first, second, library_a, library_b, id_a, id_b

    def test_foreign_library_same_and_different_bytes_never_owns_or_restores_old_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _, cli, _, library_b, id_a, id_b = self.foreign_fixture(Path(tmp).resolve())
            for changed in (False, True):
                with self.subTest(changed=changed):
                    if changed:
                        with (library_b / "skills/x/SKILL.md").open("a") as file:
                            file.write("New approved guidance from library B.\n")
                        self.checked(cli.run_confirmed("library", "accept", "lib/x", "--yes", "--json"))
                    before = tree_state(project)
                    status = self.checked(cli.run("library", "status", "lib/x", "--json"))
                    self.assertEqual(status["skill"]["exposures"], [])
                    current = {"id": "lib/x", "content_hash": status["skill"]["accepted_hash"], "source": {"library_id": id_b}}
                    for skill in (self.checked(cli.run("list", "--json"))[0], self.checked(cli.run("show", "lib/x", "--json"))["skill"]):
                        self.assertEqual(skill["source"]["library_id"], id_b)
                        self.assertEqual(skill.get("exposed_via", []), [])
                        self.assertEqual(skill["exposure"], "hidden")
                    for agent in ("codex", "claude"):
                        records = self.exposures(cli, agent)
                        self.assertEqual(len(records), 2)
                        for record in records:
                            self.assertEqual(record["status"], "source_unavailable")
                            self.assertNotIn("next_command_argv", record)
                            if record["mode"] == "router":
                                self.assertEqual(record["member_sources"], [{"skill_id": "lib/x", "source_library_id": id_a}])
                            else:
                                self.assertEqual(record["source_library_id"], id_a)
                        selected = cli.run("expose", "lib/x", "--exposure-id", "lib-x", "--mode", "native", "--agent", agent,
                                           "--scope", "project", "--dry-run", "--json")
                        self.assertEqual(selected.code, 2, selected.stdout + selected.stderr)
                        self.assertFalse(_router_tag_is_current(project, agent=agent, tag=f"identity-{agent}", skills=[current]))
                        router_id = f"skillager-identity-{agent}"
                        refusal = self.plan(cli, agent, "ungroup", code=2, router_id=router_id, mode="native")
                        self.assertEqual(refusal["reason_code"], "source-identity")
                        for members, departures in ((["lib/x"], []), ([], [{"skill_id": "lib/x", "mode": "stub"}])):
                            refusal = self.plan(cli, agent, "set-members", code=2, router_id=router_id, library_id=id_b,
                                                members=members, departures=departures, replace=[])
                            self.assertEqual(refusal["reason_code"], "source-identity")
                        activation = cli.run("activate", "lib/x", "--from-router", router_id, "--agent", agent)
                        self.assertEqual(activation.code, 2, activation.stdout + activation.stderr)
                        self.assertNotIn(BODY_SENTINEL, activation.stdout + activation.stderr)
                    self.assertEqual(tree_state(project), before)
            self.checked(cli.run("review", "block", "lib/x", "--json"))
            library_state = tree_state(library_b)
            for agent in ("codex", "claude"):
                # Removing membership consumes no canonical source, even for a foreign blocked ID.
                removal = self.plan(cli, agent, "set-members", router_id=f"skillager-identity-{agent}", library_id=id_b,
                                    members=[], departures=[{"skill_id": "lib/x", "mode": "remove"}], replace=[])
                self.assertEqual(removal["sources"], [])
                self.assertEqual(self.apply(cli, removal)["status"], "applied")
                direct = next(item for item in self.exposures(cli, agent) if item["mode"] != "router")
                preview = self.checked(cli.run("expose", "--remove", direct["exposure_id"], "--agent", agent, "--dry-run", "--json"))["results"][0]
                self.assertEqual(preview["source_library_id"], id_a)
                self.apply(cli, preview)
            self.assertEqual(tree_state(library_b), library_state)

    def test_same_uuid_relocation_keeps_copies_members_and_activation_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, cli = make_basic_workspace(root)
            library, identity = self.library(root, cli, "library")
            for agent in ("codex", "claude"):
                self.expose(cli, agent, "native")
                self.group(cli, agent, identity)
            moved = root / "moved"
            library.rename(moved)
            self.checked(cli.run("library", "relocate", "--path", str(moved), "--yes", "--json"))
            before = tree_state(project)
            status = self.checked(cli.run("library", "status", "lib/x", "--json"))
            self.assertEqual(status["library"]["library_id"], identity)
            self.assertEqual(len(status["skill"]["exposures"]), 4)
            for agent in ("codex", "claude"):
                self.assertTrue(_router_tag_is_current(project, agent=agent, tag=f"identity-{agent}",
                                skills=[{"id": "lib/x", "content_hash": status["skill"]["accepted_hash"], "source": {"library_id": identity}}]))
                for item in self.exposures(cli, agent):
                    self.assertEqual(item["status"], "current")
                activation = cli.run("activate", "lib/x", "--from-router", f"skillager-identity-{agent}", "--agent", agent)
                self.assertEqual(activation.code, 0, activation.stderr)
                self.assertIn(BODY_SENTINEL, activation.stdout)
                preview = self.plan(cli, agent, "ungroup", router_id=f"skillager-identity-{agent}", mode="native")
                self.assertEqual(preview["sources"][0]["source"]["library_id"], identity)
            self.assertEqual(tree_state(project), before)

    def test_legacy_unknown_member_is_visible_but_not_associated_or_restored_and_remove_stays_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, cli = make_basic_workspace(root)
            library, identity = self.library(root, cli, "library")
            for agent, folder in (("codex", ".agents"), ("claude", ".claude")):
                router = self.group(cli, agent, identity)
                sidecar = project / folder / "skills" / router / "skillager.materialized.yaml"
                data = load_mapping(sidecar)
                data.pop("member_sources")
                # Model a valid old managed sidecar, not a locally corrupted target.
                write_materialized_sidecar(sidecar, data)
                before = tree_state(project)
                record = self.exposures(cli, agent)[0]
                self.assertEqual(record["member_sources"], [{"skill_id": "lib/x", "source_library_id": None}])
                self.assertEqual(record["status"], "source_unavailable")
                self.assertEqual(self.checked(cli.run("library", "status", "lib/x", "--json"))["skill"]["exposures"], [])
                self.plan(cli, agent, "ungroup", code=2, router_id=router, mode="stub")
                activation = cli.run("activate", "lib/x", "--from-router", router, "--agent", agent)
                self.assertEqual(activation.code, 2)
                self.assertNotIn(BODY_SENTINEL, activation.stdout + activation.stderr)
                self.assertEqual(tree_state(project), before)
                library_state = tree_state(library)
                preview = self.checked(cli.run("expose", "--remove", router, "--agent", agent, "--dry-run", "--json"))["results"][0]
                self.assertEqual(preview["member_sources"], record["member_sources"])
                self.apply(cli, preview)
                self.assertFalse(sidecar.parent.exists())
                self.assertEqual(tree_state(library), library_state)

    def test_unknown_direct_identity_and_changed_router_qualification_refuse_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, cli = make_basic_workspace(root)
            _, identity = self.library(root, cli, "library")
            for agent, mode in (("codex", "native"), ("claude", "stub")):
                direct = self.expose(cli, agent, mode)
                sidecar = direct / "skillager.materialized.yaml"
                data = load_mapping(sidecar)
                data.pop("source_library_id")
                write_materialized_sidecar(sidecar, data)
                record = self.exposures(cli, agent)[0]
                self.assertIsNone(record["source_library_id"])
                self.assertEqual(record["status"], "source_unavailable")
                self.assertEqual(self.checked(cli.run("library", "status", "lib/x", "--json"))["skill"]["exposures"], [])
                preview = self.checked(cli.run("expose", "--remove", "lib-x", "--agent", agent, "--dry-run", "--json"))["results"][0]
                self.apply(cli, preview)
                router = self.group(cli, agent, identity)
                ungroup = self.plan(cli, agent, "ungroup", router_id=router, mode=mode)
                router_path = direct.parent / router
                router_sidecar = router_path / "skillager.materialized.yaml"
                data = load_mapping(router_sidecar)
                data["member_sources"][0]["source_library_id"] = "22222222-2222-4222-8222-222222222222"
                write_materialized_sidecar(router_sidecar, data)
                before = tree_state(project)
                refusal = self.apply(cli, ungroup, 2)
                self.assertEqual(refusal["reason_code"], "source-identity")
                self.assertEqual(tree_state(project), before)
                removal = self.checked(cli.run("expose", "--remove", router, "--agent", agent, "--dry-run", "--json"))["results"][0]
                # Qualifiers also join ordinary Remove's exact metadata/token binding.
                data["member_sources"][0]["source_library_id"] = identity
                write_materialized_sidecar(router_sidecar, data)
                before = tree_state(project)
                stale = cli.run(*removal["next_command_argv"][1:])
                self.assertEqual(stale.code, 2, stale.stdout + stale.stderr)
                self.assertNotIn(BODY_SENTINEL, stale.stdout + stale.stderr)
                self.assertEqual(tree_state(project), before)
                self.apply(cli, self.checked(cli.run("expose", "--remove", router, "--agent", agent, "--dry-run", "--json"))["results"][0])
