from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skillager.commands.impl import build_parser
from skillager.commands.search_view import run_search_view
from skillager.library.sync_lineage import identity
from skillager.skills.discovery import discover
from skillager.skills.search_view import installed_keys, lineage_relations


LIBRARY = "12345678-1234-1234-1234-123456789012"


class SearchViewPolicyTests(unittest.TestCase):
    def test_native_observation_cannot_be_broadened_by_other_discovery_inputs(self):
        for options in ({"paths": []}, {"extra_paths": []}, {"include_packages": True}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "project-native observation excludes"):
                discover(project_native_root=Path("/not-read"), **{"include_packages": False, **options})

    def test_internal_errors_are_distinct_and_never_expose_exception_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["--catalog-state-dir", tmp, "search", "--scope", "library",
                                             "--view", "skills", "--include-installed", "--json", "--", "query"])
            for error in (KeyError("PRIVATE BODY"), TypeError("PRIVATE BODY"), OSError("PRIVATE BODY"), ValueError("PRIVATE BODY")):
                with self.subTest(error=type(error).__name__):
                    stdout, stderr = StringIO(), StringIO()
                    with patch("skillager.commands.impl._search_inventory", side_effect=error), redirect_stdout(stdout), redirect_stderr(stderr):
                        code = run_search_view(args)
                    internal = isinstance(error, (KeyError, TypeError))
                    self.assertEqual(code, 1 if internal else 2)
                    self.assertEqual(json.loads(stdout.getvalue())["reason_code"], "internal-error" if internal else "observation-unavailable")
                    expected = f"skillager: internal search view error ({type(error).__name__}).\n" if internal else ""
                    self.assertEqual(stderr.getvalue(), expected)
                    self.assertNotIn("PRIVATE BODY", stdout.getvalue() + stderr.getvalue())

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
