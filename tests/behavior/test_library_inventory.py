from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import BODY_SENTINEL, CliResult, SkillagerCli, make_basic_workspace


class LibraryInventoryBehaviorTests(unittest.TestCase):
    def checked(self, result: CliResult, code: int = 0):
        self.assertEqual(result.code, code, f"{result.stdout}\n{result.stderr}")
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def library(self, root: Path) -> tuple[Path, SkillagerCli]:
        _, cli = make_basic_workspace(root)
        library = root / "library"
        self.checked(cli.run("library", "init", "--path", str(library), "--no-git", "--json"))
        for name in ("zulu", "alpha", "middle"):
            self.checked(cli.run("library", "new", name, "--json"))
            (library / "skills" / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Metadata about {name}.\n---\n\n# {name}\n\n{BODY_SENTINEL}\n",
                encoding="utf-8",
            )
        return library, cli

    def page(self, cli: SkillagerCli, cursor: str | None = None, *, limit: str = "1") -> CliResult:
        args = ["list", "--scope", "library", "--json", "--limit", limit]
        if cursor is not None:
            args.extend(["--cursor", cursor])
        return cli.run(*args)

    def test_every_owned_skill_appears_once_with_acceptance_and_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library, cli = self.library(Path(tmp))
            accepted = self.checked(cli.run_confirmed("library", "accept", "alpha", "--yes", "--json"))
            (library / "skills" / "middle" / "skillager.yaml").write_text(
                "schema: unsupported-schema\n", encoding="utf-8",
            )
            rows = []
            cursor = None
            while True:
                data = self.checked(self.page(cli, cursor))
                self.assertEqual(data["schema"], "skillager.list.v1")
                self.assertEqual(data["scope"], "library")
                self.assertEqual(len(data["skills"]), 1)
                rows.extend(data["skills"])
                cursor = data["next_cursor"]
                if cursor is None:
                    break
            self.assertEqual([row["id"] for row in rows], ["lib/alpha", "lib/middle", "lib/zulu"])
            self.assertEqual([row["status"] for row in rows], ["accepted", "blocked", "pending"])
            self.assertEqual(rows[0]["accepted_hash"], accepted["skill"]["working_hash"])
            self.assertIsNone(rows[2]["accepted_hash"])
            for row in rows:
                self.assertEqual(set(row), {"id", "name", "description", "status", "accepted_hash", "skill_file"})
                self.assertEqual(Path(row["skill_file"]), (library / "skills" / row["id"].split("/")[1] / "SKILL.md").resolve())
                self.assertNotIn("exposure", row)

    def test_missing_description_never_uses_body_as_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library, cli = self.library(Path(tmp))
            for content in (
                f"---\nname: alpha\n---\n\n{BODY_SENTINEL}\n",
                f"# Alpha\n\n{BODY_SENTINEL}\n",
                f"# {BODY_SENTINEL}\n\nGuidance follows.\n",
            ):
                (library / "skills" / "alpha" / "SKILL.md").write_text(content, encoding="utf-8")
                row = self.checked(self.page(cli))["skills"][0]
                self.assertEqual(row["name"], "alpha")
                self.assertIsNone(row["description"])

    def test_content_addition_removal_and_approval_changes_invalidate_cursor(self) -> None:
        for change in ("body", "add", "remove", "accept", "asset", "mode"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                library, cli = self.library(Path(tmp))
                first = self.checked(self.page(cli))
                skill = library / "skills" / "zulu"
                if change == "body":
                    with (skill / "SKILL.md").open("a", encoding="utf-8") as stream:
                        stream.write("\nChanged instructions.\n")
                elif change == "add":
                    self.checked(cli.run("library", "new", "beta", "--json"))
                elif change == "remove":
                    shutil.rmtree(skill)
                elif change == "accept":
                    self.checked(cli.run_confirmed("library", "accept", "zulu", "--yes", "--json"))
                elif change == "asset":
                    (skill / "notes.txt").write_text("Changed asset.\n", encoding="utf-8")
                else:
                    (skill / "SKILL.md").chmod(0o755)
                stale = self.checked(self.page(cli, first["next_cursor"]), 15)
                self.assertEqual(stale["schema"], "skillager.error.v1")
                self.assertEqual(stale["error"]["code"], "stale_cursor")
                self.assertNotIn("skills", stale)
                self.checked(self.page(cli))

    def test_invalid_and_wrong_request_cursors_are_distinct_from_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli = self.library(Path(tmp))
            first = self.checked(self.page(cli))
            for token in ("not a cursor!", "e30", "a" * 5000):
                invalid = self.checked(self.page(cli, token), 2)
                self.assertEqual(invalid["error"]["code"], "invalid_cursor")
                self.assertNotIn(token, str(invalid))
            changed_request = self.checked(self.page(cli, first["next_cursor"], limit="2"), 2)
            self.assertEqual(changed_request["error"]["code"], "invalid_cursor")
            self.assertEqual(self.checked(self.page(cli, "")), first)

    def test_personal_catalog_ignores_project_binding_without_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            cli.env.pop("SKILLAGER_CATALOG_STATE_DIR", None)
            cli.env.pop("XDG_CONFIG_HOME", None)
            self.checked(cli.run("library", "new", "personal", "--json"))
            tags = cli.project / ".skillager" / "tags.json"
            tags.parent.mkdir(parents=True, exist_ok=True)
            tags.write_text("{invalid project metadata", encoding="utf-8")
            result = self.checked(self.page(cli))
            self.assertEqual(result["skills"][0]["id"], "lib/personal")
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            cli.project = elsewhere
            self.assertEqual(self.checked(self.page(cli)), result)
            self.assertFalse((elsewhere / ".skillager").exists())

    def test_uninitialized_library_and_last_page_have_null_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli = make_basic_workspace(Path(tmp))
            empty = self.checked(self.page(cli))
            self.assertEqual(empty["skills"], [])
            self.assertIsNone(empty["next_cursor"])

    def test_default_list_output_is_unchanged_and_scope_options_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cli = self.library(Path(tmp))
            self.assertEqual(cli.run("list", "--json"), cli.run("list", "--scope", "workspace", "--json"))
            for args in (
                ("list", "--limit", "1", "--json"),
                ("list", "--cursor", "", "--json"),
                ("list", "--scope", "library"),
                ("list", "--scope", "library", "--include-global", "--json"),
                ("list", "--scope", "library", "--summary-json"),
                ("list", "--scope", "library", "--json", "--limit", "0"),
                ("list", "--scope", "library", "--json", "--limit", "-1"),
            ):
                result = cli.run(*args)
                self.assertEqual(result.code, 2, result.stdout + result.stderr)
                self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
