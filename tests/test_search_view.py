from __future__ import annotations

from copy import deepcopy
import unittest

from skillager.library.sync_lineage import identity
from skillager.skills.search_view import installed_keys, lineage_relations


LIBRARY = "12345678-1234-1234-1234-123456789012"


class SearchViewPolicyTests(unittest.TestCase):
    def test_installed_input_does_not_recover_ambiguous_or_unqualified_identities(self):
        canonical = {"library_id": LIBRARY, "skill_id": "lib/example"}
        valid = {"schema": "skillager.search-installed.v1", "identities": [canonical]}
        self.assertEqual(len(installed_keys(valid)), 1)
        for identities in ([canonical, canonical], [{**canonical, "library_id": None}],
                           [{**canonical, "library_id": "ABCDEF12-1234-1234-1234-123456789012"}],
                           [{**canonical, "skill_id": "lib/../example"}],
                           [{**canonical, "skill_id": "project/example"}],
                           [{**canonical, "skill_id": "lib/Example"}],
                           [{**canonical, "path": "/private/path"}], [canonical] * 10_001):
            with self.subTest(identities=identities[:2]), self.assertRaises(ValueError):
                installed_keys({**valid, "identities": identities})

    def test_structural_lineage_without_actual_approval_binding_cannot_join(self):
        key = "path:/source#example"
        source = identity("source", key)
        stored = {"schema": "skillager.library-sync-lineage.v1", "source_key": key,
                  "source_identity": source, "lineage_id": identity("lineage", source, LIBRARY, "example"),
                  "source_approval": {"scope": "project", "content_hash": "a" * 64}}
        provenance = {"schema": "skillager.library-provenance.v1", "skills": {"example": {"sync": stored}}}
        self.assertEqual(lineage_relations(provenance, LIBRARY, {}), ({}, False))
        copied_provenance_only = {"library:" + LIBRARY + "#example": {"derived_from": {"lineage": deepcopy(stored)}}}
        self.assertEqual(lineage_relations(provenance, LIBRARY, copied_provenance_only), ({}, False))

    def test_legacy_import_description_never_establishes_logical_identity(self):
        value = {"schema": "skillager.library-provenance.v1", "skills": {
            "example": {"imported_from": {"skill_id": "project/example", "content_hash": "a" * 64}}}}
        self.assertEqual(lineage_relations(value, LIBRARY, {}), ({}, True))
