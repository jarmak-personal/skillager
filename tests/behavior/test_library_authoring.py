from __future__ import annotations

import json
import shutil
import subprocess
import sys
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

    def test_pending_library_skill_cannot_be_overwritten_or_force_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)

            created = cli.run("library", "new", "orbital-review", "--json")
            self.assert_code(created, 0)
            self.assertEqual(created.json()["status"], "pending")
            skill_file = library / "skills" / "orbital-review" / "SKILL.md"
            skill_file.write_text(
                "# Orbital Review\n\nCanonical orbital review workflow.\n\n" + BODY_SENTINEL + "\n",
                encoding="utf-8",
            )

            collision = cli.run("library", "new", "Orbital Review")
            self.assert_code(collision, 2)
            self.assertIn("already exists", collision.stderr)
            self.assertIn(BODY_SENTINEL, skill_file.read_text(encoding="utf-8"))

            where = cli.run("where", "lib/orbital-review", "--json")
            self.assert_code(where, 0)
            self.assertEqual(where.json()["skill"]["acceptance"], "pending")
            self.assert_body_not_exposed(where)

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
            self.assert_code(preview, 1)
            self.assertEqual(preview.json()["status"], "confirmation-required")
            self.assertFalse((catalog / "trust.json").exists())
            self.assert_body_not_exposed(preview)

            accepted = cli.run("library", "accept", "lib/orbital-review", "--yes", "--json")
            self.assert_code(accepted, 0)
            accepted_data = accepted.json()
            self.assertEqual(accepted_data["status"], "accepted")
            self.assertEqual(accepted_data["skill"]["acceptance"], "accepted")
            approval_key = accepted_data["approval"]["approval_key"]
            library_id = json.loads(
                (library / ".skillager" / "library.json").read_text(encoding="utf-8")
            )["library_id"]
            self.assertEqual(approval_key, f"library:{library_id}#orbital-review")

            trust = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))
            self.assertEqual(trust["global_approvals"][approval_key]["content_hash"], accepted_data["skill"]["working_hash"])
            self.assertNotIn("lib/orbital-review", trust.get("skills", {}))

            search = cli.run("search", "orbital", "--no-session-record", "--json")
            self.assert_code(search, 0)
            self.assertEqual(search.json()[0]["id"], "lib/orbital-review")
            self.assert_body_not_exposed(search)

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
            self.assertEqual(sidecar["ownership"], "library")
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

            where = cli.run("where", "lib/orbital-review", "--json")
            self.assert_code(where, 0)
            self.assertEqual(where.json()["skill"]["exposures"][0]["kind"], "stub")

    def test_out_of_band_or_editor_change_immediately_revokes_exact_hash_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            self.assert_code(cli.run("library", "init", "--path", str(library), "--no-git"), 0)
            self.assert_code(cli.run("library", "new", "mutable-skill"), 0)
            self.assert_code(cli.run("library", "accept", "mutable-skill", "--yes"), 0)

            plain_edit = cli.run("edit", "lib/mutable-skill")
            self.assert_code(plain_edit, 0)
            skill_file = library / "skills" / "mutable-skill" / "SKILL.md"
            self.assertEqual(plain_edit.stdout.strip(), str(skill_file.resolve()))

            editor = root / "editor.py"
            editor.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "path = Path(sys.argv[1])\n"
                f"path.write_text(path.read_text(encoding='utf-8') + '\\n{BODY_SENTINEL}\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            cli.env["EDITOR"] = f"{sys.executable} {editor}"
            edited = cli.run("edit", "lib/mutable-skill", "--open")
            self.assert_code(edited, 0)
            self.assertIn("Acceptance: pending", edited.stdout)

            where = cli.run("where", "lib/mutable-skill", "--json")
            self.assert_code(where, 0)
            skill_status = where.json()["skill"]
            self.assertEqual(skill_status["acceptance"], "pending")
            self.assertNotEqual(skill_status["working_hash"], skill_status["accepted_hash"])
            self.assert_body_not_exposed(where)

            for blocked in (
                cli.run("show", "lib/mutable-skill", "--content"),
                cli.run("activate", "lib/mutable-skill", "--force", "--no-session-record"),
            ):
                self.assert_code(blocked, 2)
                self.assert_body_not_exposed(blocked)

            self.assert_code(cli.run("library", "accept", "mutable-skill", "--yes"), 0)
            shown = cli.run("show", "lib/mutable-skill", "--content")
            self.assert_code(shown, 0)
            self.assertIn(BODY_SENTINEL, shown.stdout)

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

            refused = cli.run("library", "accept", "linted-skill", "--yes")
            self.assert_code(refused, 2)
            self.assertIn("--override-lint --reason", refused.stderr)
            self.assertFalse((catalog / "trust.json").exists())

            missing_reason = cli.run("library", "accept", "linted-skill", "--yes", "--override-lint")
            self.assert_code(missing_reason, 2)
            self.assertIn("--reason is required", missing_reason.stderr)

            accepted = cli.run(
                "library",
                "accept",
                "linted-skill",
                "--yes",
                "--override-lint",
                "--reason",
                "Reviewed local authoring metadata",
                "--json",
            )
            self.assert_code(accepted, 0)
            override = accepted.json()["approval"]["lint_override"]
            self.assertEqual(override["reason"], "Reviewed local authoring metadata")
            approval_key = accepted.json()["approval"]["approval_key"]
            trust = json.loads((catalog / "trust.json").read_text(encoding="utf-8"))
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
            skill_file = library / "skills" / "git-skill" / "SKILL.md"
            skill_file.write_text("# Git Skill\n\nChanged before acceptance.\n", encoding="utf-8")

            unrelated = library / "unrelated.txt"
            unrelated.write_text("do not commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "unrelated.txt"], cwd=library, env=cli.env, check=True)
            refused = cli.run("library", "accept", "git-skill", "--yes")
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
            accepted = cli.run("library", "accept", "git-skill", "--yes", "--json")
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

            approval_key = skill["approval_key"]
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.invalid/personal-skills.git"],
                cwd=library,
                env=cli.env,
                check=True,
            )
            after_remote = cli.run("where", "git-skill", "--json")
            self.assert_code(after_remote, 0)
            self.assertEqual(after_remote.json()["skill"]["approval_key"], approval_key)
            self.assertEqual(after_remote.json()["skill"]["acceptance"], "accepted")


if __name__ == "__main__":
    unittest.main()
