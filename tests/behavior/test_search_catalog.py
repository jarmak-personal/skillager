from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .search_catalog import CASES, build_catalog


class SyntheticSearchCatalogBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="skillager-search-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.catalog = build_catalog(Path(cls.tmp.name), size=24, timeout=30)

    def test_search_matches_fields_orders_results_and_limits_within_tag(self) -> None:
        self.catalog.verify_inventory()
        for case in CASES:
            with self.subTest(case=case.name):
                result = self.catalog.cli.run(*case.argv)
                self.catalog.check(result, case)

    def test_same_size_edit_with_restored_mtime_requires_exact_hash_acceptance(self) -> None:
        self.catalog.verify_edit_invalidation()
