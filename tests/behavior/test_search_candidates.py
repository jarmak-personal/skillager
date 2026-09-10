from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from .search_catalog import build_catalog, require_success
from .support import BODY_SENTINEL


class SearchCandidateBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="skillager-search-candidates-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.catalog = build_catalog(self.root, size=16)
        self.cli = self.catalog.cli

    def search(self, query: str, *options: str) -> list[dict]:
        result = self.cli.run("search", query, "--json", *options)
        require_success(result)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def test_stale_high_ranked_match_does_not_consume_limit(self) -> None:
        self.assertEqual(self.search("rankneedle", "--limit", "1")[0]["id"], "synthetic-b/rank-title")
        path = self.root / "synthetic-b/rank-title/SKILL.md"
        stat = path.stat()
        path.write_text(path.read_text().replace("rankneedle", "newkeyword"))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, stat.st_size)
        self.assertEqual(self.search("rankneedle", "--limit", "1")[0]["id"], "synthetic-a/rank-summary")
        self.assertEqual(self.search("newkeyword"), [])
        shutil.rmtree(self.root / "synthetic-a/rank-summary")
        self.assertEqual(self.search("rankneedle", "--limit", "1")[0]["id"], "synthetic-b/rank-body")

    def test_accepted_external_edit_is_searchable_without_catalog_refresh(self) -> None:
        self.search("rankneedle")
        path = self.root / "synthetic-b/rank-body/SKILL.md"
        path.write_text(path.read_text().replace("rankneedle", "externalrevision"))
        self.assertEqual(self.search("externalrevision"), [])
        require_success(self.cli.run("review", "approve", "synthetic-b/rank-body", "--json"))
        rows = self.search("externalrevision")
        self.assertEqual([row["id"] for row in rows], ["synthetic-b/rank-body"])
        self.assertIn("body:externalrevision", rows[0]["reasons"])
        self.assertNotIn("synthetic-b/rank-body", [row["id"] for row in self.search("rankneedle")])

    def test_new_sources_and_preferred_variant_changes_remain_discoverable(self) -> None:
        for slug in ("fresh-codex", "fresh-claude"):
            folder = self.root / "synthetic-a" / slug
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"---\nname: {slug}\ndescription: Use variantneedle examples.\n---\n\nCompare the examples.\n"
            )
        self.assertEqual(self.search("variantneedle"), [])
        require_success(self.cli.run("review", "approve", "synthetic-a/fresh-codex", "synthetic-a/fresh-claude", "--json"))
        self.assertEqual(len(self.search("variantneedle")), 2)
        require_success(self.cli.run("collection", "refresh", "synthetic-a", "--json"))
        # Without a native-agent path hint, the existing preference is lexical.
        self.assertEqual(self.search("variantneedle", "--agent", "codex")[0]["id"], "synthetic-a/fresh-claude")
        path = self.root / "synthetic-a/fresh-claude/SKILL.md"
        path.write_text(path.read_text() + "\nAn unaccepted edit to the preferred variant.\n")
        rows = self.search("variantneedle", "--agent", "codex", "--limit", "1")
        self.assertEqual([row["id"] for row in rows], ["synthetic-a/fresh-codex"])

    def test_whole_tree_changes_invalidate_cached_body_match(self) -> None:
        self.search("deadlockneedle")
        folder = self.root / "library/skills/body-match"
        helper = folder / "helper.txt"
        helper.write_text("New supporting instructions.\n")
        self.assertEqual(self.search("deadlockneedle"), [])
        require_success(self.cli.run_confirmed("library", "accept", "body-match", "--yes", "--json"))
        self.assertEqual(len(self.search("deadlockneedle")), 1)
        helper.chmod(0o755)
        self.assertEqual(self.search("deadlockneedle"), [])
        helper.chmod(0o644)
        self.assertEqual(len(self.search("deadlockneedle")), 1)
        helper.unlink()
        self.assertEqual(self.search("deadlockneedle"), [])
        # Cold indexing must also reject the changed tree.
        shutil.rmtree(self.root / "cache")
        self.assertEqual(self.search("deadlockneedle"), [])

    def test_library_scope_filters_before_limit_and_uses_personal_authority(self) -> None:
        self.assertEqual(self.search("scopeprobe", "--limit", "1")[0]["id"], "synthetic-a/priority-match")
        rows = self.search("scopeprobe", "--scope", "library", "--limit", "1")
        self.assertEqual([row["id"] for row in rows], ["lib/body-match"])
        self.assertEqual(rows[0]["exposure"], "unknown")
        self.assertNotIn("exposed_via", rows[0])
        require_success(self.cli.run("review", "block", "lib/body-match", "--project-only", "--json"))
        self.assertEqual(self.search("deadlockneedle"), [])
        self.assertEqual(len(self.search("deadlockneedle", "--scope", "library")), 1)
        self.assertEqual(self.search("pendingneedle", "--scope", "library"), [])
        for option in (("--tag", "library-probes"), ("--include-global",)):
            result = self.cli.run("search", "scopeprobe", "--scope", "library", *option)
            self.assertEqual(result.code, 2, result.stdout + result.stderr)
        # A damaged workspace authority must not prevent personal-library queries.
        (self.root / "state/project/trust.sqlite3").write_bytes(b"damaged database")
        self.assertEqual(len(self.search("deadlockneedle", "--scope", "library")), 1)
        self.assertEqual(self.cli.run("search", "deadlockneedle", "--json").code, 2)

    def test_router_dependencies_outside_search_tag_are_verified(self) -> None:
        require_success(self.cli.run("expose", "--tag", "library-probes", "--mode", "router", "--agent", "codex"))
        require_success(self.cli.run("tag", "create", "one"))
        require_success(self.cli.run("tag", "add", "one", "lib/body-match"))
        rows = self.search("deadlockneedle", "--tag", "one")
        self.assertEqual(rows[0]["exposure"], "router")
        path = self.root / "library/skills/title-match/SKILL.md"
        path.write_text(path.read_text() + "\nAn unaccepted change to another router member.\n")
        for options in ((), ("--tag", "one")):
            rows = self.search("deadlockneedle", *options)
            self.assertEqual([row["id"] for row in rows], ["lib/body-match"])
            self.assertEqual(rows[0]["exposure"], "hidden")
            self.assertNotIn("exposed_via", rows[0])

    def test_search_is_read_only_for_catalog_and_authority(self) -> None:
        def state_bytes() -> dict[str, bytes]:
            return {str(path): path.read_bytes() for path in (self.root / "state").rglob("*") if path.is_file()}

        before = state_bytes()
        self.search("rankneedle")
        self.search("scopeprobe", "--scope", "library")
        self.assertEqual(state_bytes(), before)
