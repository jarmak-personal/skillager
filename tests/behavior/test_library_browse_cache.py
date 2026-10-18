"""Live browse invalidation and bounded work, through the CLI and its owning seams."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from skillager.catalog import impl as catalog_impl, storage
from skillager.library import browse
from skillager.commands import impl as commands

from .support import BODY_SENTINEL, CliResult, make_basic_workspace


class LibraryBrowseCacheBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillager-browse-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        _, self.cli = make_basic_workspace(self.root)
        self.library = self.root / "library"
        self.checked(self.cli.run("library", "init", "--path", str(self.library), "--no-git", "--json"))
        for index in range(8):
            path = self.library / "skills" / f"item-{index}"
            path.mkdir()
            (path / "SKILL.md").write_text(
                f"---\nname: item-{index}\ndescription: Describe browse examples.\n---\n\n"
                f"Inspect cacheneedle examples.\n{BODY_SENTINEL}\n", encoding="utf-8")
        for index in range(8):
            self.checked(self.cli.run_confirmed("library", "accept", f"item-{index}", "--yes", "--json"))

    def checked(self, result: CliResult, code: int = 0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def invoke(self, *argv: str) -> CliResult:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.cli.env, clear=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = commands.main(list(argv))
        return CliResult(code, stdout.getvalue(), stderr.getvalue())

    def page(self, cursor: str = "", *, code: int = 0):
        return self.checked(self.invoke("list", "--scope", "library", "--json", "--limit", "2", "--cursor", cursor), code)

    def search(self, cursor: str = "", *, view: bool = True, code: int = 0):
        extra = ("--view", "skills", "--include-installed") if view else ()
        return self.checked(self.invoke("search", "cacheneedle", "--scope", "library", "--json",
                                        "--limit", "2", "--cursor", cursor, "--no-session-record", *extra), code)

    def test_unchanged_pages_read_no_skill_bytes_and_only_changed_tree_is_reindexed(self) -> None:
        first = self.page()
        with mock.patch.object(catalog_impl, "content_hash", side_effect=AssertionError("unexpected exact inventory hash")), \
                mock.patch.object(browse, "content_hash", side_effect=AssertionError("unexpected seed hash")), \
                mock.patch.object(catalog_impl, "_index_collection_skills", side_effect=AssertionError("unexpected reindex")), \
                mock.patch.object(Path, "read_text", autospec=True, side_effect=self._metadata_read_only):
            self.assertEqual(self.page(), first)
            self.assertEqual([row["id"] for row in self.page(first["next_cursor"])["skills"]], ["lib/item-2", "lib/item-3"])
        path = self.library / "skills/item-7/SKILL.md"
        before = path.stat()
        path.write_text(path.read_text().replace("browse examples", "edited examples"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        with mock.patch.object(catalog_impl, "_index_collection_skills", wraps=catalog_impl._index_collection_skills) as scan:
            self.page(first["next_cursor"], code=15)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(scan.call_args.kwargs["skill_dirs"], [path.parent])
        rows = self.checked(self.invoke("list", "--scope", "library", "--json", "--limit", "20"))["skills"]
        self.assertEqual(rows[-1]["description"], "Describe edited examples.")
        self.assertEqual(rows[-1]["status"], "pending")

    _read_text = staticmethod(Path.read_text)

    def _metadata_read_only(self, path: Path, *args, **kwargs):
        self.assertFalse(path.is_relative_to(self.library / "skills"), f"warm browse reread {path}")
        return self._read_text(path, *args, **kwargs)

    def test_search_exact_checks_stop_at_requested_prefix_and_continuation(self) -> None:
        # Populate the prose index before counting publication checks.
        self.search()
        for view in (False, True):
            with self.subTest(view=view), mock.patch.object(commands, "_search_result_is_current", wraps=commands._search_result_is_current) as verify, \
                    mock.patch.object(catalog_impl, "_skill_dirs", wraps=catalog_impl._skill_dirs) as discover, \
                    mock.patch.object(browse, "metadata_tree_receipt", wraps=browse.metadata_tree_receipt) as receipts:
                first = self.search(view=view)
                self.assertEqual(verify.call_count, 3)
                self.assertEqual(discover.call_count, 1)
                self.assertEqual(receipts.call_count, 8)
                verify.reset_mock()
                discover.reset_mock()
                receipts.reset_mock()
                second = self.search(first["next_cursor"], view=view)
                self.assertEqual(verify.call_count, 5)
                self.assertEqual(discover.call_count, 1)
                self.assertEqual(receipts.call_count, 8)
                self.assertEqual([row["id"] for row in first["results"] + second["results"]],
                                 [f"lib/item-{index}" for index in range(4)])

    def test_metadata_receipt_never_authorizes_search_or_body_or_acceptance(self) -> None:
        self.search()
        target = self.library / "skills/item-0"
        receipt = browse.metadata_tree_receipt(target)
        preview = self.checked(self.cli.run("library", "accept", "item-0", "--json"))
        path = target / "SKILL.md"
        path.write_text(path.read_text().replace("cacheneedle", "editneedle"))
        real_receipt = browse.metadata_tree_receipt
        with mock.patch.object(browse, "metadata_tree_receipt", side_effect=lambda root: receipt if root == target else real_receipt(root)):
            result = self.search()
            self.assertNotIn("lib/item-0", [row["id"] for row in result["results"]])
            content = self.invoke("show", "lib/item-0", "--content", "--json")
            self.assertNotEqual(content.code, 0)
            self.assertNotIn(BODY_SENTINEL, content.stdout + content.stderr)
            self.assertTrue(preview["next_command_argv"])
            refused = self.invoke(*preview["next_command_argv"][1:])
            self.assertNotEqual(refused.code, 0)
            self.assertNotIn(BODY_SENTINEL, refused.stdout + refused.stderr)

    def test_supporting_changes_and_approval_and_provenance_invalidate_hidden_rows(self) -> None:
        target = self.library / "skills/item-7"
        asset = target / "notes.txt"
        for change in ("add", "mode", "remove", "block", "provenance"):
            with self.subTest(change=change):
                first, search = self.page(), self.search()
                if change == "add":
                    asset.write_text("Supporting instructions.\n")
                elif change == "mode":
                    asset.chmod(0o755)
                elif change == "remove":
                    asset.unlink()
                elif change == "block":
                    self.checked(self.cli.run("--state-dir", self.cli.env["SKILLAGER_CATALOG_STATE_DIR"],
                                              "review", "block", "lib/item-7", "--json"))
                else:
                    path = self.library / ".skillager/provenance.json"
                    provenance = json.loads(path.read_text())
                    provenance["skills"]["item-7"] = {"imported_from": {"skill_id": "other/example", "content_hash": "a" * 64}}
                    path.write_text(json.dumps(provenance))
                self.assertEqual(self.page(first["next_cursor"], code=15)["error"]["code"], "stale_cursor")
                self.assertEqual(self.search(search["next_cursor"], code=15)["error"]["code"], "stale_cursor")

    def test_corrupt_or_unwritable_cache_falls_back_without_authority_changes(self) -> None:
        first = self.page()
        authority = {str(path): path.read_bytes() for path in (self.root / "state").rglob("*") if path.is_file()}
        database = next((self.root / "cache/library-browse-v1").glob("*/catalog.sqlite3"))
        database.write_bytes(b"not a SQLite database")
        self.assertEqual(self.page(), first)
        self.assertTrue(self.page(first["next_cursor"])["skills"])
        cached = storage.read_collection(database.parent, "lib")
        assert cached is not None
        cached["skills"][0]["_browse_declared"] = "invalid cached metadata"
        storage.write_collection(database.parent, "lib", cached)
        self.assertEqual(self.page(), first)
        with mock.patch.object(storage, "read_collection", return_value=None), \
                mock.patch.object(storage, "write_collection", side_effect=PermissionError("cache is read only")):
            self.assertEqual(self.page(), first)
        self.assertEqual({str(path): path.read_bytes() for path in (self.root / "state").rglob("*") if path.is_file()}, authority)

    def test_new_and_deleted_skill_roots_invalidate_warm_traversals(self) -> None:
        for change in ("add", "remove"):
            with self.subTest(change=change):
                first, search = self.page(), self.search()
                if change == "add":
                    target = self.library / "skills/item-new"
                    target.mkdir()
                    (target / "SKILL.md").write_text("---\nname: item-new\ndescription: Added example.\n---\nInspect cacheneedle.\n")
                else:
                    shutil.rmtree(self.library / "skills/item-3")
                self.page(first["next_cursor"], code=15)
                self.search(search["next_cursor"], code=15)
                rows = self.checked(self.invoke("list", "--scope", "library", "--json", "--limit", "20"))["skills"]
                expected = {f"lib/item-{index}" for index in range(8)} | {"lib/item-new"}
                if change == "remove":
                    expected.remove("lib/item-3")
                self.assertEqual({row["id"] for row in rows}, expected)
                self.assertEqual(next(row for row in rows if row["id"] == "lib/item-new")["status"], "pending")
                found = self.checked(self.invoke("search", "cacheneedle", "--scope", "library", "--json",
                                                 "--limit", "20", "--cursor", "", "--no-session-record"))["results"]
                self.assertEqual({row["id"] for row in found}, expected - {"lib/item-new"})

    def test_partial_search_omits_unobservable_roots_but_pagination_requires_complete_inventory(self) -> None:
        self.search()
        target = self.library / "skills/item-7"
        receipt = browse.metadata_tree_receipt
        for failure in (PermissionError("unreadable skill"), FileNotFoundError("file disappeared during traversal")):
            def unavailable(path):
                if path == target:
                    raise failure
                return receipt(path)

            with self.subTest(failure=type(failure).__name__), mock.patch.object(browse, "metadata_tree_receipt", side_effect=unavailable):
                for view in (False, True):
                    extra = ("--view", "skills", "--include-installed") if view else ()
                    result = self.checked(self.invoke("search", "cacheneedle", "--scope", "library", "--json",
                                                      "--limit", "20", "--no-session-record", *extra))
                    rows = result["results"] if view else result
                    self.assertEqual({row["id"] for row in rows}, {f"lib/item-{index}" for index in range(7)})
                    refused = self.invoke("search", "cacheneedle", "--scope", "library", "--json",
                                          "--limit", "2", "--cursor", "", "--no-session-record", *extra)
                    self.assertEqual(refused.code, 2)
                    self.assertNotIn(BODY_SENTINEL, refused.stdout + refused.stderr)
                    if view:
                        self.assertEqual(refused.json()["status"], "unavailable")
                        self.assertEqual(refused.json()["results"], [])
                    else:
                        self.assertEqual(refused.stdout, "")
                self.assertEqual(self.page(code=2)["error"]["code"], "inventory_unavailable")
        rows = self.checked(self.invoke("list", "--scope", "library", "--json", "--limit", "20"))["skills"]
        self.assertEqual(len(rows), 8)

    def test_racing_metadata_refresh_is_unavailable_and_does_not_poison_cache(self) -> None:
        first = self.page()
        path = self.library / "skills/item-7/SKILL.md"
        path.write_text(path.read_text() + "\nFirst change.\n")
        real_index = catalog_impl._index_collection_skills

        def race(*args, **kwargs):
            result = real_index(*args, **kwargs)
            path.write_text(path.read_text() + "\nSecond change.\n")
            return result

        with mock.patch.object(catalog_impl, "_index_collection_skills", side_effect=race):
            self.assertEqual(self.page(code=2)["error"]["code"], "inventory_unavailable")
        self.page(first["next_cursor"], code=15)
        self.assertEqual(self.page()["skills"], first["skills"])

    def test_replaced_library_identity_refuses_list_and_both_search_contracts(self) -> None:
        self.page()
        self.search()
        path = self.library / ".skillager/library.json"
        identity = json.loads(path.read_text())
        identity["library_id"] = str(uuid4())
        path.write_text(json.dumps(identity))
        with mock.patch.object(storage, "read_collection", side_effect=AssertionError("identity gate must precede cache")):
            for argv in (("list", "--scope", "library", "--json"),
                         ("search", "cacheneedle", "--scope", "library", "--json"),
                         ("search", "cacheneedle", "--scope", "library", "--json", "--view", "skills", "--include-installed")):
                with self.subTest(argv=argv):
                    refused = self.invoke(*argv)
                    self.assertEqual(refused.code, 2)
                    self.assertNotIn(BODY_SENTINEL, refused.stdout + refused.stderr)

    def test_signature_and_card_evidence_refresh_metadata_without_changing_acceptance(self) -> None:
        target = self.library / "skills/item-7"
        signature, card = target / "skill.oms.sig", target / "skill-card.md"
        accepted_hash = self.checked(self.invoke("list", "--scope", "library", "--json", "--limit", "20"))["skills"][-1]["accepted_hash"]
        for change in ("signature-add", "signature-edit", "card-add", "card-remove", "signature-remove"):
            with self.subTest(change=change):
                first = self.search()
                if change == "signature-add":
                    signature.write_text("{}\n")
                    # Cold seeding must refresh evidence even though the exact
                    # accepted content hash still matches the collection index.
                    next((self.root / "cache/library-browse-v1").glob("*/catalog.sqlite3")).unlink()
                elif change == "signature-edit":
                    signature.write_text('{"version": 2}\n')
                elif change == "card-add":
                    card.write_text("Updated evidence card.\n")
                elif change == "card-remove":
                    card.unlink()
                else:
                    signature.unlink()
                self.assertEqual(self.search(first["next_cursor"], code=15)["error"]["code"], "stale_cursor")
                results = self.checked(self.invoke("search", "cacheneedle", "--scope", "library", "--view", "skills",
                                                   "--include-installed", "--json", "--limit", "50", "--cursor", "", "--no-session-record"))["results"]
                row = next(row for row in results if row["id"] == "lib/item-7")
                self.assertEqual(row["content_hash"], accepted_hash)
                self.assertEqual(row["review_gates"]["availability"], "available")
                if signature.exists():
                    self.assertEqual(row["signature"]["signature_hash"], hashlib.sha256(signature.read_bytes()).hexdigest())
                    self.assertEqual(row["signature"]["card"]["present"], card.exists())
                    if card.exists():
                        self.assertEqual(row["signature"]["card"]["hash"], hashlib.sha256(card.read_bytes()).hexdigest())
                else:
                    self.assertNotIn("signature", row)

    def test_evidence_symlink_targets_refresh_without_changing_link_metadata(self) -> None:
        target = self.library / "skills/item-7"
        signature, card = self.root / "signature.json", self.root / "card.md"
        signature.write_text("{}\n")
        card.write_text("Initial card.\n")
        (target / "skill.oms.sig").symlink_to(signature)
        (target / "skill-card.md").symlink_to(card)
        self.search()
        for path, link in ((signature, target / "skill.oms.sig"), (card, target / "skill-card.md")):
            with self.subTest(path=path):
                first = self.search()
                before, link_before = path.stat(), link.lstat()
                path.write_text("[]\n" if path == signature else "Changed card.\n")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(path.stat().st_size, before.st_size)
                self.assertEqual(link.lstat(), link_before)
                self.search(first["next_cursor"], code=15)
                rows = self.checked(self.invoke("search", "cacheneedle", "--scope", "library", "--view", "skills",
                                                "--include-installed", "--json", "--limit", "50", "--cursor", "", "--no-session-record"))["results"]
                row = next(row for row in rows if row["id"] == "lib/item-7")
                self.assertEqual(row["review_gates"]["availability"], "available")
                self.assertEqual(row["signature"]["signature_hash"], hashlib.sha256(signature.read_bytes()).hexdigest())
                self.assertEqual(row["signature"]["card"]["hash"], hashlib.sha256(card.read_bytes()).hexdigest())
