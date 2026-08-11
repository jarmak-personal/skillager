from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from skillager.simple_yaml import load_mapping
from tests.behavior.support import BODY_SENTINEL, CliResult, make_basic_workspace


class PersonalLibraryAuthoringBehaviorTests(unittest.TestCase):
    def assert_code(self, result: CliResult, expected: int) -> None:
        self.assertEqual(
            result.code,
            expected,
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def assert_body_not_exposed(self, result: CliResult) -> None:
        self.assertNotIn(BODY_SENTINEL, result.stdout)
        self.assertNotIn(BODY_SENTINEL, result.stderr)

    def test_owned_draft_is_advisory_and_points_to_library_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "pending-draft"), 0)

            working = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(working, 0)
            data = working.json()
            self.assertEqual(data["pending_external_review_count"], 0)
            self.assertEqual(data["pending_owner_review_count"], 0)
            self.assertEqual(data["pending_owned_change_count"], 1)
            self.assertEqual(data["pending_owned_changes"][0]["id"], "lib/pending-draft")
            self.assertIn("library accept", data["pending_owned_changes"][0]["command"])
            self.assertNotEqual((data.get("next") or {}).get("command"), "skillager setup --agent codex")

            shown = cli.run("show", "lib/pending-draft", "--content")
            self.assert_code(shown, 2)
            self.assertIn("skillager library accept lib/pending-draft", shown.stderr)
            self.assertNotIn("skillager setup", shown.stderr)

    def test_pending_library_skill_cannot_be_overwritten_or_force_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)

            created = cli.run("library", "new", "orbital-review", "--json")
            self.assert_code(created, 0)
            created_data = created.json()
            self.assertEqual(created_data["status"], "pending")
            self.assertNotIn("commit", created_data)
            self.assertEqual(
                created_data["next_command_argv"],
                ["skillager", "library", "accept", "lib/orbital-review"],
            )
            template = (library / "skills" / "orbital-review" / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(template.startswith("---\nname: orbital-review\ndescription:"))
            skill_file = library / "skills" / "orbital-review" / "SKILL.md"
            self.assertEqual(Path(created_data["skill"]["skill_file"]), skill_file.resolve())
            skill_file.write_text(
                "# Orbital Review\n\nCanonical orbital review workflow.\n\n" + BODY_SENTINEL + "\n",
                encoding="utf-8",
            )

            collision = cli.run("library", "new", "Orbital Review")
            self.assert_code(collision, 2)
            self.assertIn("already exists", collision.stderr)
            self.assertIn(BODY_SENTINEL, skill_file.read_text(encoding="utf-8"))

            status = cli.run("library", "status", "lib/orbital-review", "--json")
            self.assert_code(status, 0)
            self.assertEqual(status.json()["skill"]["acceptance"], "pending")
            self.assert_body_not_exposed(status)

            search = cli.run("search", "orbital", "--no-session-record", "--json")
            self.assert_code(search, 0)
            self.assertEqual(search.json(), [])
            self.assert_body_not_exposed(search)

            for blocked in (
                cli.run("show", "lib/orbital-review", "--content"),
                cli.run("activate", "lib/orbital-review", "--force", "--no-session-record"),
            ):
                self.assert_code(blocked, 2)
                self.assert_body_not_exposed(blocked)

            expose = cli.run(
                "expose",
                "lib/orbital-review",
                "--mode",
                "native",
                "--agent",
                "codex",
                "--include-unreviewed",
                "--json",
            )
            self.assert_code(expose, 0)
            self.assert_body_not_exposed(expose)
            self.assertEqual(expose.json()[0]["status"], "skipped")
            self.assertFalse((project / ".agents" / "skills" / "lib-orbital-review").exists())

    def test_external_lib_id_collision_fails_closed_without_transferring_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "demo"), 0)
            (library / "skills" / "demo" / "SKILL.md").write_text(
                "# Safe Demo\n\nAccepted personal guidance.\n",
                encoding="utf-8",
            )
            self.assert_code(cli.run_confirmed("library", "accept", "lib/demo", "--yes"), 0)

            external_body = "UNREVIEWED_EXTERNAL_COLLISION_BODY"
            external = project / ".venv" / "lib" / "python3.13" / "site-packages" / "lib" / ".skills" / "demo"
            external.mkdir(parents=True)
            (external / "SKILL.md").write_text(
                f"# External Demo\n\n{external_body}\n",
                encoding="utf-8",
            )

            metadata = cli.run("show", "lib/demo", "--json")
            self.assert_code(metadata, 0)
            self.assertFalse(metadata.json()["skill"]["available"])
            self.assertEqual(metadata.json()["skill"]["identity_collision"]["source_count"], 2)
            self.assertNotIn(external_body, metadata.stdout)

            for blocked in (
                cli.run("show", "lib/demo", "--content"),
                cli.run("activate", "lib/demo", "--no-session-record"),
                cli.run("expose", "lib/demo", "--agent", "codex"),
            ):
                self.assert_code(blocked, 2)
                self.assertIn("ambiguous skill ID lib/demo", blocked.stderr)
                self.assertNotIn(external_body, blocked.stdout)
                self.assertNotIn(external_body, blocked.stderr)

    def test_accept_requires_confirmation_then_enables_guarded_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            library = root / "library"
            catalog = root / "state" / "catalog"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "orbital-review"), 0)
            skill_file = library / "skills" / "orbital-review" / "SKILL.md"
            skill_file.write_text(
                "# Orbital Review\n\nCanonical orbital review workflow.\n\n" + BODY_SENTINEL + "\n",
                encoding="utf-8",
            )

            preview = cli.run("library", "accept", "lib/orbital-review", "--json")
            self.assert_code(preview, 0)
            self.assertEqual(preview.json()["status"], "preview")
            self.assertNotIn("will_accept", preview.json())
            self.assertNotIn("approval_key", preview.stdout)
            self.assertNotIn('"next_command"', preview.stdout)
            next_command = preview.json()["next_command_argv"]
            self.assertEqual(next_command[:5], ["skillager", "library", "accept", "lib/orbital-review", "--yes"])
            self.assertEqual(next_command[5], "--confirmation-token")
            self.assertRegex(next_command[6], r"^[0-9a-f]{64}$")
            self.assertFalse((catalog / "trust.json").exists())
            self.assert_body_not_exposed(preview)

            unbound = cli.run("library", "accept", "lib/orbital-review", "--yes")
            self.assert_code(unbound, 2)
            self.assertIn("confirmation token", unbound.stderr)
            self.assertFalse((catalog / "trust.json").exists())

            previewed_body = skill_file.read_text(encoding="utf-8")
            skill_file.write_text(previewed_body + "\nChanged after preview.\n", encoding="utf-8")
            stale = cli.run(*next_command[1:], "--json")
            self.assert_code(stale, 2)
            self.assertIn("preview is stale", stale.stderr)
            self.assertFalse((catalog / "trust.json").exists())
            skill_file.write_text(previewed_body, encoding="utf-8")

            readable_preview = cli.run("library", "accept", "lib/orbital-review")
            self.assert_code(readable_preview, 0)
            self.assertIn("Preview only; no changes were made.", readable_preview.stdout)
            self.assertIn("Next: skillager library accept lib/orbital-review --yes --confirmation-token", readable_preview.stdout)
            self.assertEqual(readable_preview.stderr, "")
            self.assert_body_not_exposed(readable_preview)

            accepted = cli.run_confirmed("library", "accept", "lib/orbital-review", "--yes", "--json")
            self.assert_code(accepted, 0)
            accepted_data = accepted.json()
            self.assertEqual(accepted_data["status"], "accepted")
            self.assertEqual(accepted_data["skill"]["acceptance"], "accepted")
            self.assertNotIn("approval_key", accepted.stdout)
            library_id = json.loads(
                (library / ".skillager" / "library.json").read_text(encoding="utf-8")
            )["library_id"]
            approval_key = f"library:{library_id}#orbital-review"
            self.assertEqual(approval_key, f"library:{library_id}#orbital-review")

            trust = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))
            self.assertEqual(trust["global_approvals"][approval_key]["content_hash"], accepted_data["skill"]["working_hash"])
            self.assertNotIn("lib/orbital-review", trust.get("skills", {}))

            search = cli.run("search", "orbital", "--no-session-record", "--json")
            self.assert_code(search, 0)
            self.assertEqual(search.json()[0]["id"], "lib/orbital-review")
            self.assert_body_not_exposed(search)

            invalid_native = cli.run(
                "expose",
                "lib/orbital-review",
                "--mode",
                "native",
                "--agent",
                "codex",
                "--json",
            )
            self.assert_code(invalid_native, 0)
            self.assertEqual(invalid_native.json()[0]["status"], "skipped")
            self.assertIn("frontmatter", invalid_native.json()[0]["reason"])

            expose = cli.run(
                "expose",
                "lib/orbital-review",
                "--mode",
                "stub",
                "--agent",
                "codex",
                "--json",
            )
            self.assert_code(expose, 0)
            self.assertEqual(expose.json()[0]["status"], "exposed")
            self.assert_body_not_exposed(expose)
            stub = project / ".agents" / "skills" / "lib-orbital-review" / "SKILL.md"
            self.assertTrue(stub.is_file())
            self.assertNotIn(BODY_SENTINEL, stub.read_text(encoding="utf-8"))
            sidecar = load_mapping(stub.parent / "skillager.materialized.yaml")
            self.assertNotIn("ownership", sidecar)
            self.assertEqual(sidecar["source_library_id"], library_id)
            self.assertRegex(sidecar["materialized_fingerprint"], r"^[0-9a-f]{64}$")

            shown = cli.run("show", "lib/orbital-review", "--content")
            self.assert_code(shown, 0)
            self.assertIn(BODY_SENTINEL, shown.stdout)
            activated = cli.run(
                "activate",
                "lib/orbital-review",
                "--from-stub",
                "lib-orbital-review",
                "--no-session-record",
            )
            self.assert_code(activated, 0)
            self.assertIn(BODY_SENTINEL, activated.stdout)

            status = cli.run("library", "status", "lib/orbital-review", "--json")
            self.assert_code(status, 0)
            self.assertEqual(status.json()["skill"]["exposures"][0]["kind"], "stub")

    def test_out_of_band_change_immediately_revokes_exact_hash_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "mutable-skill"), 0)
            self.assert_code(cli.run_confirmed("library", "accept", "mutable-skill", "--yes"), 0)

            skill_file = library / "skills" / "mutable-skill" / "SKILL.md"
            skill_file.write_text(skill_file.read_text(encoding="utf-8") + f"\n{BODY_SENTINEL}\n", encoding="utf-8")

            status = cli.run("library", "status", "lib/mutable-skill", "--json")
            self.assert_code(status, 0)
            skill_status = status.json()["skill"]
            self.assertEqual(skill_status["acceptance"], "pending")
            self.assertNotEqual(skill_status["working_hash"], skill_status["accepted_hash"])
            self.assert_body_not_exposed(status)

            for blocked in (
                cli.run("show", "lib/mutable-skill", "--content"),
                cli.run("activate", "lib/mutable-skill", "--force", "--no-session-record"),
            ):
                self.assert_code(blocked, 2)
                self.assert_body_not_exposed(blocked)

    def test_executable_mode_change_revokes_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "mode-aware"), 0)
            tool = library / "skills" / "mode-aware" / "tool.sh"
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o644)
            accepted = cli.run_confirmed("library", "accept", "mode-aware", "--yes", "--json")
            self.assert_code(accepted, 0)
            accepted_hash = accepted.json()["skill"]["working_hash"]
            self.assert_code(cli.run("setup", "--agent", "codex", "--json"), 0)

            tool.chmod(0o755)
            status = cli.run("library", "status", "mode-aware", "--json")
            self.assert_code(status, 0)
            self.assertEqual(status.json()["skill"]["acceptance"], "pending")
            self.assertNotEqual(status.json()["skill"]["working_hash"], accepted_hash)
            blocked = cli.run("show", "lib/mode-aware", "--content")
            self.assert_code(blocked, 2)
            doctor = cli.run("doctor", "--agent", "codex", "--json")
            self.assert_code(doctor, 0)
            doctor_data = doctor.json()
            self.assertEqual(doctor_data["status"], "ready")
            self.assertIn("pending owner acceptance", doctor_data["message"])
            self.assertEqual(
                doctor_data["state"]["owned_changes"]["items"][0]["command"],
                "skillager library accept lib/mode-aware",
            )
            self.assertIn(
                "skillager library accept lib/mode-aware",
                doctor_data["next"]["next_commands"],
            )

    def test_accepted_source_updates_make_direct_and_router_exposures_refreshable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            for name in ("native-fresh", "stub-fresh", "router-fresh"):
                self.assert_code(cli.run("library", "new", name), 0)
                self.assert_code(cli.run_confirmed("library", "accept", name, "--yes"), 0)

            self.assert_code(
                cli.run("expose", "lib/native-fresh", "--mode", "native", "--agent", "codex", "--json"),
                0,
            )
            self.assert_code(
                cli.run("expose", "lib/stub-fresh", "--mode", "stub", "--agent", "codex", "--json"),
                0,
            )
            self.assert_code(cli.run("tag", "create", "freshness"), 0)
            self.assert_code(cli.run("tag", "add", "freshness", "lib/router-fresh"), 0)
            self.assert_code(
                cli.run("expose", "--tag", "freshness", "--mode", "router", "--agent", "codex", "--json"),
                0,
            )

            for name in ("native-fresh", "stub-fresh", "router-fresh"):
                skill_file = library / "skills" / name / "SKILL.md"
                skill_file.write_text(
                    skill_file.read_text(encoding="utf-8") + "\nNew accepted guidance.\n",
                    encoding="utf-8",
                )
                if name == "native-fresh":
                    pending = cli.run("working", "--agent", "codex", "--json")
                    self.assert_code(pending, 0)
                    pending_changes = pending.json()["exposure_changes"]
                    self.assertEqual(pending_changes["source_unavailable"], 1)
                    self.assertEqual(pending_changes["source_updates"], 0)
                    unavailable = next(
                        item for item in pending_changes["items"]
                        if item["status"] == "source_unavailable"
                    )
                    self.assertNotIn("command", unavailable)
                    self.assertNotIn("next_command_argv", unavailable)
                    pending_plain = cli.run("working", "--agent", "codex")
                    self.assert_code(pending_plain, 0)
                    self.assertIn("cannot be refreshed until its source is approved", pending_plain.stdout)
                accepted = cli.run_confirmed("library", "accept", name, "--yes", "--json")
                self.assert_code(accepted, 0)

            status = cli.run("library", "status", "lib/native-fresh", "--json")
            self.assert_code(status, 0)
            direct = status.json()["skill"]["exposures"][0]
            self.assertEqual(direct["status"], "update_available")
            self.assertEqual(
                direct["next_command_argv"],
                [
                    "skillager",
                    "expose",
                    "lib/native-fresh",
                    "--mode",
                    "native",
                    "--agent",
                    "codex",
                    "--scope",
                    "project",
                ],
            )

            working = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(working, 0)
            data = working.json()
            self.assertEqual(data["exposure_changes"]["source_updates"], 3)
            self.assertEqual(data["exposure_changes"]["current"], 0)
            self.assertEqual(data["inventory"]["exposed_now"], 0)
            self.assertEqual(data["inventory"]["available_on_demand"], 3)
            updates = data["exposure_changes"]["items"]
            self.assertEqual({item["mode"] for item in updates}, {"native", "stub", "router"})
            self.assertTrue(all(item["status"] == "source_update" for item in updates))
            self.assertTrue(all(item.get("next_command_argv") for item in updates))
            native_target = project / ".agents" / "skills" / "lib-native-fresh"
            self.assertNotIn("New accepted guidance.", (native_target / "SKILL.md").read_text(encoding="utf-8"))

            managed = cli.run("expose", "--list", "--agent", "codex", "--scope", "project", "--json")
            self.assert_code(managed, 0)
            native_exposure = next(
                item for item in managed.json()["exposures"]
                if item["skill_id"] == "lib/native-fresh"
            )
            removal = cli.run(
                "expose",
                "--remove",
                native_exposure["exposure_id"],
                "--agent",
                "codex",
                "--scope",
                "project",
                "--json",
            )
            self.assert_code(removal, 0)
            self.assertEqual(removal.json()["results"][0]["current_status"], "current")
            self.assertFalse(removal.json()["results"][0]["requires_force"])

            listing = cli.run("list", "--full-json")
            self.assert_code(listing, 0)
            listed = {item["id"]: item for item in listing.json()}
            for skill_id in ("lib/native-fresh", "lib/stub-fresh", "lib/router-fresh"):
                self.assertEqual(listed[skill_id]["exposure"], "hidden")
                self.assertEqual(listed[skill_id]["exposure_targets"], [])

            plain = cli.run("working", "--agent", "codex")
            self.assert_code(plain, 0)
            self.assertIn("can be refreshed from newly approved content", plain.stdout)
            self.assertIn("skillager expose lib/native-fresh --mode native", plain.stdout)

            self.assert_code(
                cli.run("expose", "lib/native-fresh", "--mode", "native", "--agent", "codex", "--json"),
                0,
            )
            self.assert_code(
                cli.run("expose", "lib/stub-fresh", "--mode", "stub", "--agent", "codex", "--json"),
                0,
            )
            self.assert_code(
                cli.run("expose", "--tag", "freshness", "--mode", "router", "--agent", "codex", "--json"),
                0,
            )
            refreshed = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(refreshed, 0)
            self.assertEqual(refreshed.json()["exposure_changes"]["source_updates"], 0)
            self.assertEqual(refreshed.json()["exposure_changes"]["current"], 3)
            self.assertEqual(refreshed.json()["inventory"]["exposed_now"], 3)
            self.assertTrue((native_target / "SKILL.md").is_file())

    def test_working_revokes_stale_library_inventory_without_writing_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            catalog = root / "state" / "catalog"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "stale-working"), 0)
            self.assert_code(cli.run_confirmed("library", "accept", "stale-working", "--yes"), 0)
            trust_path = catalog / "trust.json"
            trust_before = trust_path.read_bytes()

            skill_file = library / "skills" / "stale-working" / "SKILL.md"
            skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nChanged after acceptance.\n", encoding="utf-8")
            working = cli.run("working", "--agent", "codex", "--json")
            self.assert_code(working, 0)
            data = working.json()
            self.assertEqual(data["inventory"]["available_source_entries"], 0)
            self.assertEqual(data["pending_owned_change_count"], 1)
            self.assertEqual(data["pending_owned_changes"][0]["status"], "changed")
            self.assertEqual(trust_path.read_bytes(), trust_before)

    def test_lint_blocked_library_acceptance_requires_audited_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            catalog = root / "state" / "catalog"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "linted-skill"), 0)
            (library / "skills" / "linted-skill" / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\n"
                "summary: unknown manifest field\n"
                "audience:\n"
                "  - user\n"
                "activation:\n"
                "  default: manual\n",
                encoding="utf-8",
            )

            preview = cli.run("library", "accept", "linted-skill", "--json")
            self.assert_code(preview, 0)
            self.assertEqual(preview.json()["required_arguments"], ["--override-lint", "--reason"])
            self.assertNotIn("next_command_argv", preview.json())

            refused = cli.run("library", "accept", "linted-skill", "--yes")
            self.assert_code(refused, 2)
            self.assertIn("--override-lint --reason", refused.stderr)
            self.assertFalse((catalog / "trust.json").exists())

            missing_reason = cli.run("library", "accept", "linted-skill", "--yes", "--override-lint")
            self.assert_code(missing_reason, 2)
            self.assertIn("--reason is required", missing_reason.stderr)

            reason_preview = cli.run(
                "library",
                "accept",
                "linted-skill",
                "--override-lint",
                "--reason",
                "Reviewed local authoring metadata",
                "--json",
            )
            self.assert_code(reason_preview, 0)
            self.assertIn("Reviewed local authoring metadata", reason_preview.json()["next_command_argv"])
            accepted = cli.run(*reason_preview.json()["next_command_argv"][1:], "--json")
            self.assert_code(accepted, 0)
            override = accepted.json()["approval"]["lint_override"]
            self.assertEqual(override["reason"], "Reviewed local authoring metadata")
            trust = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))
            approval_key = next(iter(trust["global_approvals"]))
            self.assertEqual(
                trust["global_approvals"][approval_key]["lint_override"]["reason"],
                "Reviewed local authoring metadata",
            )

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_git_acceptance_commits_exact_skill_and_refuses_unrelated_staged_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            catalog = root / "state" / "catalog"
            cli.env["GIT_CONFIG_GLOBAL"] = str(root / "missing-global-gitconfig")
            cli.env["GIT_CONFIG_NOSYSTEM"] = "1"
            self.assert_code(cli.run("library", "init", "--path", str(library)), 0)
            self.assert_code(cli.run("library", "new", "git-skill"), 0)
            draft_history = cli.run("library", "history", "git-skill", "--json")
            self.assert_code(draft_history, 0)
            self.assertEqual(draft_history.json()["versions"], [])
            skill_file = library / "skills" / "git-skill" / "SKILL.md"
            skill_file.write_text("# Git Skill\n\nChanged before acceptance.\n", encoding="utf-8")

            unrelated = library / "unrelated.txt"
            unrelated.write_text("do not commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "unrelated.txt"], cwd=library, env=cli.env, check=True)
            refused = cli.run_confirmed("library", "accept", "git-skill", "--yes")
            self.assert_code(refused, 2)
            self.assertIn("unrelated staged changes", refused.stderr)
            self.assertFalse((catalog / "trust.json").exists())
            staged = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=library,
                env=cli.env,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(staged, ["unrelated.txt"])

            subprocess.run(["git", "restore", "--staged", "unrelated.txt"], cwd=library, env=cli.env, check=True)
            accepted = cli.run_confirmed("library", "accept", "git-skill", "--yes", "--json")
            self.assert_code(accepted, 0)
            skill = accepted.json()["skill"]
            self.assertEqual(skill["status"], "clean")
            self.assertEqual(skill["working_hash"], skill["head_hash"])
            self.assertEqual(skill["working_hash"], skill["accepted_hash"])
            changed_files = subprocess.run(
                ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
                cwd=library,
                env=cli.env,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(changed_files, ["skills/git-skill/SKILL.md"])
            self.assertTrue(unrelated.exists())

            trust_before = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))["global_approvals"]
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.invalid/personal-skills.git"],
                cwd=library,
                env=cli.env,
                check=True,
            )
            after_remote = cli.run("library", "status", "git-skill", "--json")
            self.assert_code(after_remote, 0)
            self.assertEqual(after_remote.json()["skill"]["acceptance"], "accepted")
            trust_after = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))["global_approvals"]
            self.assertEqual(trust_after, trust_before)


if __name__ == "__main__":
    unittest.main()
