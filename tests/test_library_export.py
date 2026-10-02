"""Race proofs at the real export preparation/publication seam."""
from __future__ import annotations

import os
import shutil
import unittest
from unittest.mock import patch

from skillager.exposure import impl as exposure
from skillager.exposure import native_payload
from skillager.library import exporting
from skillager.state import approvals
from tests.behavior import test_library_export as fixtures


class LibraryExportRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.LibraryExportBehaviorTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.catalog = self.root / "state/catalog"
        self.destination = self.root / "export"

    def run_export(self):
        return exporting.export_skill(self.catalog, "lib/payload", version=self.fixture.version,
                                      agent="codex", destination=self.destination)

    def outside(self):
        root = self.root / "outside"
        root.mkdir()
        (root / "keep.txt").write_text("Keep these unrelated bytes unchanged.\n")
        return root

    def test_destination_replaced_by_symlink_during_copy_has_no_outside_effects(self):
        outside = self.outside()
        expected = self.fixture.files(outside)
        original = exporting.prepare_direct_candidate

        def replacement(*args, **kwargs):
            self.destination.rename(self.root / "detached")
            self.destination.symlink_to(outside, target_is_directory=True)
            return original(*args, **kwargs)

        with patch.object(exporting, "prepare_direct_candidate", replacement), self.assertRaises(exporting.ExportRefusal):
            self.run_export()
        self.assertEqual(self.fixture.files(outside), expected)
        self.assertEqual(list((self.root / "detached").iterdir()), [])
        self.assertTrue(self.destination.is_symlink())

    def test_parent_replacement_cannot_redirect_preparation_writes(self):
        outside = self.outside()
        expected = self.fixture.files(outside)
        parent = self.root / "parent"
        parent.mkdir()
        self.destination = parent / "export"
        original = exporting.prepare_direct_candidate

        def replacement(*args, **kwargs):
            parent.rename(self.root / "detached-parent")
            parent.symlink_to(outside, target_is_directory=True)
            return original(*args, **kwargs)

        with patch.object(exporting, "prepare_direct_candidate", replacement), self.assertRaises(exporting.ExportRefusal):
            self.run_export()
        self.assertEqual(self.fixture.files(outside), expected)
        self.assertEqual(list((self.root / "detached-parent").iterdir()), [])

    def test_sidecar_write_cannot_follow_a_replaced_destination(self):
        outside = self.outside()
        expected = self.fixture.files(outside)
        original = exposure.write_materialized_sidecar

        def replacement(*args, **kwargs):
            self.destination.rename(self.root / "detached")
            self.destination.symlink_to(outside, target_is_directory=True)
            return original(*args, **kwargs)

        with patch.object(exposure, "write_materialized_sidecar", replacement), self.assertRaises(exporting.ExportRefusal):
            self.run_export()
        self.assertEqual(self.fixture.files(outside), expected)
        self.assertEqual(list((self.root / "detached").iterdir()), [])

    def test_source_file_symlink_swap_between_inventory_and_copy_is_refused(self):
        outside = self.outside()
        expected = self.fixture.files(outside)
        original = native_payload.iter_content_files

        def replacement(source):
            files = original(source)
            if source == self.fixture.source:
                reference = source / "reference.txt"
                reference.unlink()
                reference.symlink_to(outside / "keep.txt")
            return files

        with patch.object(native_payload, "iter_content_files", replacement), self.assertRaises(exporting.ExportRefusal):
            self.run_export()
        self.assertEqual(self.fixture.files(outside), expected)
        self.assertFalse(self.destination.exists())

    def test_added_destination_file_is_preserved_and_only_created_objects_are_removed(self):
        original = exporting.prepare_direct_candidate

        def addition(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.destination / "keep.txt").write_text("New concurrent user bytes.\n")
            return result

        with patch.object(exporting, "prepare_direct_candidate", addition), self.assertRaises(exporting.ExportRefusal) as raised:
            self.run_export()
        self.assertEqual(raised.exception.code, "destination_changed")
        self.assertEqual(self.fixture.files(self.destination), {"keep.txt": (b"New concurrent user bytes.\n", 0o644)})

    def test_noncanonical_foreign_entry_before_sidecar_is_refused_and_preserved(self):
        original = exposure.write_materialized_sidecar

        def addition(*args, **kwargs):
            excluded = self.destination / ".git"
            excluded.mkdir()
            (excluded / "keep.txt").write_text("Foreign bytes outside the canonical content hash.\n")
            return original(*args, **kwargs)

        with patch.object(exposure, "write_materialized_sidecar", addition), self.assertRaises(exporting.ExportRefusal) as raised:
            self.run_export()
        self.assertEqual(raised.exception.code, "destination_changed")
        self.assertEqual(self.fixture.files(self.destination), {".git/keep.txt": (b"Foreign bytes outside the canonical content hash.\n", 0o644)})

    def test_replaced_created_file_with_identical_bytes_modes_and_times_is_preserved(self):
        original = exporting.prepare_direct_candidate

        def replacement(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.destination / "reference.txt"
            other = self.root / "replacement.txt"
            shutil.copy2(path, other)
            os.replace(other, path)
            return result

        with patch.object(exporting, "prepare_direct_candidate", replacement), self.assertRaises(exporting.ExportRefusal) as raised:
            self.run_export()
        self.assertEqual(raised.exception.code, "destination_changed")
        self.assertEqual(self.fixture.files(self.destination), {"reference.txt": (b"Reviewed reference bytes.\n", 0o640)})

    def test_source_changed_through_hardlink_after_preparation_cannot_report_success(self):
        original = exporting.prepare_direct_candidate
        alias = self.root / "source-hardlink.txt"
        os.link(self.fixture.source / "reference.txt", alias)

        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            alias.write_text("An unaccepted source change through another hardlink.\n")
            return result

        with patch.object(exporting, "prepare_direct_candidate", change), self.assertRaises(exporting.ExportRefusal) as raised:
            self.run_export()
        self.assertEqual(raised.exception.code, "changed_content")
        self.assertFalse(self.destination.exists())
        self.assertEqual(alias.read_bytes(), (self.fixture.source / "reference.txt").read_bytes())

    def test_approval_revocation_after_preparation_cannot_report_success(self):
        original = exporting.prepare_direct_candidate

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            identifier = self.fixture.checked(self.fixture.cli.run("library", "status", "--json"))["library"]["library_id"]
            key = f"library:{identifier}#payload"
            approvals.mutate_record(self.catalog, "global_approvals", key,
                                    lambda record: {**(record or {}), "state": "blocked"})
            return result

        with patch.object(exporting, "prepare_direct_candidate", revoke), self.assertRaises(exporting.ExportRefusal) as raised:
            self.run_export()
        self.assertEqual(raised.exception.code, "blocked_content")
        self.assertFalse(self.destination.exists())

    def test_failure_after_publication_preserves_unrelated_concurrent_files(self):
        original = exporting._verify_materialized_projection

        def modify(target, **kwargs):
            if target == self.destination:
                (target / "keep.txt").write_text("User bytes added during verification.\n")
            return original(target, **kwargs)

        with patch.object(exporting, "_verify_materialized_projection", modify), self.assertRaises(exporting.ExportRefusal):
            self.run_export()
        self.assertEqual(self.fixture.files(self.destination), {"keep.txt": (b"User bytes added during verification.\n", 0o644)})
