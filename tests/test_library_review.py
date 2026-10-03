from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from skillager.library import review, service
from skillager.catalog.impl import load_collections, save_collections
from tests.behavior.support import make_basic_workspace


class LibraryReviewObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, cli = make_basic_workspace(self.root)
        self.library = self.root / "library"
        self.catalog = self.root / "state/catalog"
        for argv in (("library", "init", "--path", str(self.library), "--no-git", "--json"),
                     ("library", "new", "observed", "--json")):
            result = cli.run(*argv)
            self.assertEqual(result.code, 0, result.stderr)
        self.skill = self.library.resolve() / "skills/observed"
        self.path = self.skill / "SKILL.md"
        self.path.write_text("# Observed\n\nStable review guidance.\n")

    def test_access_time_changes_are_harmless_to_bounded_capture(self):
        expected, _ = review.capture_review_tree(self.skill)
        original = os.fstat
        calls = 0

        def accessed(descriptor):
            nonlocal calls
            calls += 1
            value = original(descriptor)
            fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            return SimpleNamespace(**{name: getattr(value, name) for name in fields},
                                   st_atime_ns=value.st_atime_ns + calls)

        with patch.object(review.os, "fstat", accessed):
            actual, _ = review.capture_review_tree(self.skill)
        self.assertEqual(actual, expected)
        self.assertGreaterEqual(calls, 2)

    def test_actual_content_or_mode_changes_during_read_refuse_capture(self):
        original = os.fstat
        for change in ("bytes", "mode"):
            with self.subTest(change=change):
                self.path.chmod(0o644)
                calls = 0

                def changed(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if change == "bytes":
                            self.path.write_bytes(b"x" * self.path.stat().st_size)
                        else:
                            self.path.chmod(0o744)
                    return original(descriptor)

                with patch.object(review.os, "fstat", changed), self.assertRaises(review.ReviewRefusal) as raised:
                    review.capture_review_tree(self.skill)
                self.assertEqual(raised.exception.code, "review_changed")

    def test_live_byte_growth_after_private_scan_refuses_before_unbounded_source_reads(self):
        original = review.index_library_candidate

        def growth(*args, **kwargs):
            result = original(*args, **kwargs)
            with self.path.open("wb") as writer:
                writer.truncate(review.REVIEW_LIMITS["file_bytes"] + 1)
            return result

        with patch.object(review, "index_library_candidate", growth), \
                patch.object(service, "_library_skill_entry", side_effect=AssertionError("legacy mutable-source scan must not run")), \
                self.assertRaises(review.ReviewRefusal) as raised:
            service.library_acceptance_preview(self.catalog, "observed", review_manifest=True)
        self.assertEqual(raised.exception.code, "review_limit_exceeded")

    def test_path_changes_after_private_scan_cannot_emit_a_usable_preview(self):
        original = review.index_library_candidate
        for change in ("missing", "additional", "executable"):
            with self.subTest(change=change):
                self.path.write_text("# Observed\n\nStable review guidance.\n")
                self.path.chmod(0o644)
                additional = self.skill / "additional.txt"
                additional.unlink(missing_ok=True)

                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if change == "missing":
                        self.path.unlink()
                    elif change == "additional":
                        additional.write_text("New eligible bytes.\n")
                    else:
                        self.path.chmod(0o744)
                    return result

                with patch.object(review, "index_library_candidate", changed), self.assertRaises(review.ReviewRefusal) as raised:
                    service.library_acceptance_preview(self.catalog, "observed", review_manifest=True)
                self.assertEqual(raised.exception.code, "review_changed")

    def test_bound_library_identity_is_revalidated_inside_acceptance_lock(self):
        preview = service.library_acceptance_preview(self.catalog, "observed", review_manifest=True)
        identity_path = self.library / ".skillager/library.json"
        identity = json.loads(identity_path.read_text())
        identity["library_id"] = str(uuid4())
        identity_path.write_text(json.dumps(identity))
        collections = load_collections(self.catalog)
        collections["collections"]["lib"]["library_id"] = identity["library_id"]
        save_collections(self.catalog, collections)
        with patch.object(service, "set_trust", side_effect=AssertionError("stale identity must not write trust")), \
                self.assertRaisesRegex(ValueError, "identity or provenance changed"):
            service.accept_library_skill(self.catalog, "observed", expected_hash=preview["skill"]["working_hash"],
                                         expected_library=preview["_library_binding"],
                                         expected_provenance=preview["_provenance_fingerprint"], review_manifest=True)
