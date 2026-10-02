"""Public search paging preserves ordering, presentation policy, and metadata safety."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from .search_catalog import require_success
from .support import BODY_SENTINEL, CliResult, make_basic_workspace


class SearchPaginationBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillager-search-pages-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.root)
        self.library = self.root / "library"
        self.checked(self.cli.run("library", "init", "--path", str(self.library), "--no-git", "--json"))
        for name, title, description, body in (
            ("title", "pageneedle", "Explain ranking examples.", "Inspect the inputs."),
            ("summary", "Summary example", "Explain pageneedle ranking.", "Inspect the inputs."),
            ("body", "Body example", "Explain ranking examples.", "Inspect pageneedle inputs."),
            ("unmatched", "Separate example", "Explain unrelated examples.", "Inspect other inputs."),
        ):
            self.checked(self.cli.run("library", "new", name, "--json"))
            (self.library / "skills" / name / "SKILL.md").write_text(
                f"---\nname: {title}\ndescription: {description}\n---\n\n{body}\n{BODY_SENTINEL}\n",
                encoding="utf-8",
            )
            self.checked(self.cli.run_confirmed("library", "accept", name, "--yes", "--json"))

    def checked(self, result: CliResult, code: int = 0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def page(self, cursor: str = "", *options: str, query: str = "pageneedle", code: int = 0):
        return self.checked(self.cli.run("search", query, "--json", "--limit", "1",
                                         "--cursor", cursor, *options), code)

    def traverse(self, *options: str):
        rows = []
        cursor = ""
        for _ in range(20):
            payload = self.page(cursor, *options)
            expected_schema = "skillager.search.v1" if "--view" in options else "skillager.search-page.v1"
            self.assertEqual(payload["schema"], expected_schema)
            self.assertLessEqual(len(payload["results"]), 1)
            rows.extend(payload["results"])
            cursor = payload["next_cursor"]
            if cursor is None:
                return rows
        self.fail("paging did not terminate")

    def test_every_match_once_in_unchanged_rank_order_for_both_scopes(self) -> None:
        for scope in ("workspace", "library"):
            with self.subTest(scope=scope):
                options = ("--scope", scope)
                expected = self.checked(self.cli.run("search", "pageneedle", "--json", "--limit", "0", *options))
                rows = self.traverse(*options)
                self.assertEqual(rows, expected)
                self.assertEqual([row["id"] for row in rows], ["lib/title", "lib/summary", "lib/body"])
                self.assertEqual(len({row["id"] for row in rows}), len(rows))
                full = self.traverse(*options, "--full-json")
                full_expected = self.checked(self.cli.run("search", "pageneedle", "--json", "--full-json", "--limit", "0", *options))
                self.assertEqual(full, full_expected)

    def test_nonmatching_exact_tree_and_authority_changes_are_stale(self) -> None:
        path = self.library / "skills" / "unmatched" / "SKILL.md"
        for scope in ("workspace", "library"):
            for change in ("same-size-restored-mtime", "asset", "mode", "block", "pending-add", "remove"):
                with self.subTest(scope=scope, change=change):
                    first = self.page("", "--scope", scope)
                    self.assertIsNotNone(first["next_cursor"])
                    if change == "same-size-restored-mtime":
                        stat = path.stat()
                        path.write_text(path.read_text().replace("other inputs", "fresh inputs"))
                        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                        self.assertEqual(path.stat().st_size, stat.st_size)
                    elif change == "asset":
                        (path.parent / "notes.txt").write_text(f"{scope} asset change\n")
                    elif change == "mode":
                        path.chmod(0o755 if scope == "workspace" else 0o644)
                    elif change == "block":
                        authority = self.root / "state" / ("project" if scope == "workspace" else "catalog")
                        blocked = self.checked(self.cli.run("--state-dir", str(authority), "review", "block", "lib/title", "--json"))
                        self.assertTrue(blocked["action"]["changed"])
                    elif change == "pending-add":
                        self.checked(self.cli.run("library", "new", f"pending-{scope}", "--json"))
                    else:
                        (self.library / "skills" / f"pending-{scope}" / "SKILL.md").unlink()
                    stale = self.page(first["next_cursor"], "--scope", scope, code=15)
                    self.assertEqual(stale["error"]["code"], "stale_cursor")
                    self.assertNotIn("results", stale)
                    # Restore an accepted candidate for the next traversal.
                    if change == "block":
                        self.checked(self.cli.run("--state-dir", str(authority), "review", "unblock", "lib/title", "--json"))
                    self.checked(self.cli.run_confirmed("library", "accept", "title", "--yes", "--json"))
                    self.checked(self.cli.run_confirmed("library", "accept", "unmatched", "--yes", "--json"))
                    if change == "same-size-restored-mtime":
                        path.write_text(path.read_text().replace("fresh inputs", "other inputs"))
                        self.checked(self.cli.run_confirmed("library", "accept", "unmatched", "--yes", "--json"))

    def test_request_mismatch_and_malformed_cursors_are_invalid(self) -> None:
        token = self.page()["next_cursor"]
        for options in (("--scope", "library"), ("--limit", "2"), ("--agent", "codex"), ("--full-json",)):
            with self.subTest(options=options):
                self.assertEqual(self.page(token, *options, code=2)["error"]["code"], "invalid_cursor")
        self.assertEqual(self.page(token, query="ranking", code=2)["error"]["code"], "invalid_cursor")
        nested = base64.urlsafe_b64encode(("[" * 1200 + "]" * 1200).encode()).decode()
        for value in ("not a cursor!", "e30", "a" * 5000, nested):
            self.assertEqual(self.page(value, code=2)["error"]["code"], "invalid_cursor")
        listed = self.checked(self.cli.run("list", "--scope", "library", "--limit", "1", "--json"))
        self.assertEqual(self.page(listed["next_cursor"], "--scope", "library", code=2)["error"]["code"], "invalid_cursor")

    def test_grouping_and_copy_projection_precede_each_page(self) -> None:
        source = self.project / ".skills" / "original"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: pageneedle original\ndescription: Explain pageneedle grouping.\n---\n\nInspect the source.\n")
        self.checked(self.cli.run("review", "approve", "project/original", "--json"))
        for scope in ("workspace", "library"):
            for view in ("skills", "copies"):
                with self.subTest(scope=scope, view=view):
                    options = ("--scope", scope, "--view", view, "--include-installed")
                    if scope == "library":
                        options += ("--installed-project", str(self.project))
                    expected = self.checked(self.cli.run("search", "pageneedle", "--json", "--limit", "50", *options))["results"]
                    rows = self.traverse(*options)
                    self.assertEqual(rows, expected)
                    identities = [row["search"]["occurrence"]["id"] if view == "copies"
                                  else row["search"]["group_id"] for row in rows]
                    self.assertEqual(len(set(identities)), len(identities))
                    # Installed groups disappear before paging and cannot consume
                    # slots that belong to the remaining uninstalled identities.
                    filtered_options = tuple(value for value in options if value != "--include-installed")
                    filtered = self.traverse(*filtered_options)
                    self.assertEqual([row["id"] for row in filtered], ["lib/title", "lib/summary", "lib/body"])

    def test_presence_input_changes_invalidate_view_cursor(self) -> None:
        identifier = self.checked(self.cli.run("library", "status", "--json"))["library"]["library_id"]
        installed = self.root / "installed.json"
        installed.write_text(json.dumps({"schema": "skillager.search-installed.v1", "identities": []}))
        options = ("--scope", "library", "--view", "skills", "--installed-identities", str(installed))
        token = self.page("", *options)["next_cursor"]
        installed.write_text(json.dumps({"schema": "skillager.search-installed.v1", "identities": [
            {"library_id": identifier, "skill_id": "lib/summary"}]}))
        self.assertEqual(self.page(token, *options, code=15)["error"]["code"], "stale_cursor")

    def test_quarantined_nonmatching_inventory_remains_snapshot_only(self) -> None:
        blocked = self.library / "skills" / "unmatched" / "skillager.yaml"
        blocked.write_text("schema: invalid\n")
        for scope in ("workspace", "library"):
            options = ("--scope", scope, "--view", "skills", "--include-installed")
            first = self.page("", *options)
            self.assertEqual(first["results"][0]["id"], "lib/title")
            blocked.write_text(blocked.read_text() + f"# {scope} changed metadata\n")
            self.assertEqual(self.page(first["next_cursor"], *options, code=15)["error"]["code"], "stale_cursor")

    def test_tag_and_compatibility_filters_precede_paging(self) -> None:
        require_success(self.cli.run("tag", "create", "selected"))
        require_success(self.cli.run("tag", "add", "selected", "lib/summary", "lib/body"))
        self.assertEqual([row["id"] for row in self.traverse("--tag", "selected")], ["lib/summary", "lib/body"])
        options = ("--agent", "codex", "--compatible-only")
        expected = self.checked(self.cli.run("search", "pageneedle", "--json", "--limit", "0", *options))
        self.assertEqual(self.traverse(*options), expected)
        token = self.page("", "--tag", "selected")["next_cursor"]
        require_success(self.cli.run("tag", "remove", "selected", "lib/body"))
        self.assertEqual(self.page(token, "--tag", "selected", code=15)["error"]["code"], "stale_cursor")

    def test_cursors_bind_workspace_context_and_search_leaves_authority_unchanged(self) -> None:
        before = {str(path): path.read_bytes() for path in (self.root / "state").rglob("*") if path.is_file()}
        self.traverse()
        self.traverse("--scope", "library", "--view", "skills", "--include-installed")
        self.assertEqual({str(path): path.read_bytes() for path in (self.root / "state").rglob("*") if path.is_file()}, before)
        token = self.page()["next_cursor"]
        other = self.root / "other-project"
        other.mkdir()
        (other / "pyproject.toml").write_text('[project]\nname = "other"\n')
        self.cli.project = other
        self.assertEqual(self.page(token, code=2)["error"]["code"], "invalid_cursor")

    def test_legacy_contracts_and_empty_last_pages_remain_explicit(self) -> None:
        legacy = self.checked(self.cli.run("search", "pageneedle", "--json", "--limit", "1"))
        self.assertIsInstance(legacy, list)
        view = self.checked(self.cli.run("search", "pageneedle", "--json", "--view", "skills"))
        self.assertNotIn("next_cursor", view)
        empty = self.page(query="absentneedle")
        self.assertEqual(empty["results"], [])
        self.assertIsNone(empty["next_cursor"])
        for options in (("--limit", "0"), ("--limit", "-1")):
            self.assertEqual(self.cli.run("search", "pageneedle", "--cursor", "", "--json", *options).code, 2)
        self.assertEqual(self.cli.run("search", "pageneedle", "--cursor", "").code, 2)
