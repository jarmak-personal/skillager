from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main as skillager_main
from skillager.index import build_index
from skillager_linter.cli import main as linter_main


class LinterEquivalenceTests(unittest.TestCase):
    def test_unknown_key_findings_match_core_lint(self) -> None:
        manifest = (
            "schema: skillager.skill.v1\n"
            "summary: hostile manifest bait\n"
            "audience:\n"
            "  - user\n"
            "activation:\n"
            "  default: manual\n"
        )
        self.assertEqual(
            self._standalone_findings(manifest),
            self._core_findings(manifest),
        )

    def test_strict_yaml_findings_match_core_lint(self) -> None:
        manifest = '"reset; rm -rf /": one\n"reset; rm -rf /": two\n'
        self.assertEqual(
            self._standalone_findings(manifest),
            self._core_findings(manifest),
        )

    def test_warning_findings_match_core_lint(self) -> None:
        manifest = (
            "schema: skillager.skill.v1\n"
            "audience:\n"
            "  - user\n"
            "  - dev\n"
            "activation:\n"
            "  default: manual\n"
        )
        self.assertEqual(
            self._standalone_findings(manifest),
            self._core_findings(manifest),
        )

    def _standalone_findings(self, manifest: str) -> list[dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = _write_skill(root, manifest)
            output = StringIO()
            with redirect_stdout(output):
                code = linter_main(["--json", str(skill_dir)])
            self.assertIn(code, {0, 1})
            return json.loads(output.getvalue())[0]["lint"]["findings"]

    def _core_findings(self, manifest: str) -> list[dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            _write_skill(root, manifest)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                build_index(state, include_packages=False)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(skillager_main(["lint", "project/demo", "--json"]), 0)
            return json.loads(output.getvalue())[0]["lint"]["findings"]


def _write_skill(root: Path, manifest: str) -> Path:
    skill_dir = root / ".skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
    (skill_dir / "skillager.yaml").write_text(manifest, encoding="utf-8")
    return skill_dir


if __name__ == "__main__":
    unittest.main()
