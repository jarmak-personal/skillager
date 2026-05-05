from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from skillager_linter.api import lint_skill_root
from skillager_linter.cli import main
from skillager_linter.templates import MINIMAL_MANIFEST_YAML


class LinterApiCliTests(unittest.TestCase):
    def test_minimal_manifest_lints_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Demo\n\nUseful testing workflow.", encoding="utf-8")
            (root / "skillager.yaml").write_text(MINIMAL_MANIFEST_YAML, encoding="utf-8")

            result = lint_skill_root(root)

            self.assertEqual(result.lint.status, "ok")
            self.assertEqual(result.lint.findings, ())
            self.assertEqual(result.skill_id, "local/demo")

    def test_json_result_excludes_body_derived_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Secret Heading\n\nSecret summary payload.", encoding="utf-8")
            (root / "skillager.yaml").write_text(MINIMAL_MANIFEST_YAML, encoding="utf-8")

            output = json.dumps(lint_skill_root(root).to_dict())

            self.assertNotIn("Secret Heading", output)
            self.assertNotIn("Secret summary payload", output)

    def test_unknown_manifest_key_blocks_without_raw_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Demo\n\nUseful testing workflow.", encoding="utf-8")
            (root / "skillager.yaml").write_text(
                MINIMAL_MANIFEST_YAML + "name: hostile manifest bait\n",
                encoding="utf-8",
            )

            result = lint_skill_root(root)
            payload = json.dumps(result.to_dict())

            self.assertEqual(result.lint.status, "blocked")
            self.assertEqual(result.lint.findings[0].code, "unknown_key")
            self.assertNotIn("hostile manifest bait", payload)

    def test_cli_prints_minimal_manifest(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--print-minimal-manifest"])

        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), MINIMAL_MANIFEST_YAML)

    def test_cli_json_does_not_emit_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Demo\n\nSecret body payload.", encoding="utf-8")
            (root / "skillager.yaml").write_text(MINIMAL_MANIFEST_YAML, encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                code = main(["--json", str(root)])

            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data[0]["lint"]["status"], "ok")
            self.assertNotIn("Secret body payload", output.getvalue())

    def test_malformed_yaml_exits_one_as_blocking_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Demo\n\nUseful testing workflow.", encoding="utf-8")
            (root / "skillager.yaml").write_text("schema: [\n", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                code = main(["--json", str(root)])

            data = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(data[0]["lint"]["status"], "blocked")

    def test_read_error_exits_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "SKILL.md").write_text("# Demo\n\nUseful testing workflow.", encoding="utf-8")
            (root / "skillager.yaml").write_text(MINIMAL_MANIFEST_YAML, encoding="utf-8")
            stderr = StringIO()

            with patch("skillager_linter.api.load_manifest_mapping", side_effect=OSError("could not read")), redirect_stderr(stderr):
                code = main(["--json", str(root)])

            self.assertEqual(code, 3)
            self.assertIn("could not read", stderr.getvalue())

    def test_broken_pipe_exits_zero(self) -> None:
        with patch("skillager_linter.cli.run", side_effect=BrokenPipeError):
            self.assertEqual(main([]), 0)

    def test_print_minimal_manifest_rejects_paths_and_json(self) -> None:
        with redirect_stderr(StringIO()):
            self.assertEqual(main(["--print-minimal-manifest", "--json"]), 2)
        with redirect_stderr(StringIO()):
            self.assertEqual(main(["--print-minimal-manifest", "unused"]), 2)


if __name__ == "__main__":
    unittest.main()
