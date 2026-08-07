from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import make_basic_workspace


class PersonalLibraryPhase1BehaviorTests(unittest.TestCase):

    def test_collection_cli_reserves_lib_namespace_for_library_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            collection = root / "collection"
            collection.mkdir()

            result = cli.run("collection", "add", str(collection), "--name", "lib")

            self.assertEqual(result.code, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("collection name 'lib' is reserved for the personal skill library", result.stderr)
            self.assertIn("library", cli.run("--help").stdout.split("commands:")[-1])
