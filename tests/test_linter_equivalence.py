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
from skillager_linter.findings import RULE_KEYS


DEFAULT_SKILL_TEXT = "# Demo\n\nUse demo guidance.\n"
VALID_MANIFEST = (
    "schema: skillager.skill.v1\n"
    "audience:\n"
    "  - user\n"
    "activation:\n"
    "  default: manual\n"
)

EQUIVALENCE_FIXTURES = {
    "assumptions_env_invalid": {
        "manifest": VALID_MANIFEST
        + "compatibility:\n"
        + "  assumptions:\n"
        + "    env:\n"
        + "      - bad-name\n",
    },
    "audience_both": {
        "manifest": (
            "schema: skillager.skill.v1\n"
            "audience:\n"
            "  - user\n"
            "  - dev\n"
            "activation:\n"
            "  default: manual\n"
        ),
    },
    "control_chars": {
        "manifest": VALID_MANIFEST + "compatibility:\n  exclusive_to: codex\u200b\n",
    },
    "derived_id_invalid": {
        "manifest": VALID_MANIFEST,
        "skill_dir_name": "x" * 65,
    },
    "domain_violation": {
        "manifest": (
            "schema: skillager.skill.v1\n"
            "audience:\n"
            "  - user\n"
            "  - user\n"
            "activation:\n"
            "  default: manual\n"
        ),
    },
    "entrypoint_invalid": {
        "manifest": VALID_MANIFEST,
        "symlink_entrypoint": True,
    },
    "generic_description": {
        "manifest": VALID_MANIFEST,
        "skill_text": "# Demo\n\nUse this skill\n",
    },
    "parallel_subagents_invalid": {
        "manifest": VALID_MANIFEST
        + "compatibility:\n"
        + "  assumptions:\n"
        + "    parallel_subagents:\n"
        + "      preferred: 17\n",
    },
    "schema_violation": {
        "manifest": (
            "schema: skillager.skill.v0\n"
            "audience:\n"
            "  - user\n"
            "activation:\n"
            "  default: manual\n"
        ),
    },
    "target_package_invalid": {
        "manifest": VALID_MANIFEST + "targets:\n  python_packages:\n    - name: Bad Name\n",
    },
    "unknown_key": {
        "manifest": VALID_MANIFEST + "summary: hostile manifest bait\n",
    },
}

UNREACHABLE_RULE_CODES = {
    # Reserved in RULE_KEYS, but no V1 validator or lint path currently emits it.
    "warning_for_undeclared",
}


class LinterEquivalenceTests(unittest.TestCase):
    def test_reachable_rule_findings_match_core_lint(self) -> None:
        for expected_code, fixture in EQUIVALENCE_FIXTURES.items():
            with self.subTest(expected_code=expected_code):
                standalone = self._standalone_findings(**fixture)
                core = self._core_findings(**fixture)
                self.assertEqual(standalone, core)
                self.assertTrue(
                    any(finding["code"] == expected_code for finding in standalone),
                    f"expected {expected_code} in {standalone}",
                )

    def test_rule_key_reachability_is_accounted_for(self) -> None:
        self.assertEqual(set(RULE_KEYS), set(EQUIVALENCE_FIXTURES) | UNREACHABLE_RULE_CODES)

    def test_strict_yaml_findings_match_core_lint(self) -> None:
        manifest = '"reset; rm -rf /": one\n"reset; rm -rf /": two\n'
        standalone = self._standalone_findings(manifest)
        core = self._core_findings(manifest)

        self.assertEqual(standalone, core)
        self.assertNotIn("rm -rf /", json.dumps(standalone))
        self.assertNotIn("rm -rf /", json.dumps(core))

    def _standalone_findings(
        self,
        manifest: str,
        *,
        skill_dir_name: str = "demo",
        skill_text: str = DEFAULT_SKILL_TEXT,
        symlink_entrypoint: bool = False,
    ) -> list[dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = _write_skill(root, manifest, skill_dir_name=skill_dir_name, skill_text=skill_text, symlink_entrypoint=symlink_entrypoint)
            output = StringIO()
            with redirect_stdout(output):
                code = linter_main(["--json", str(skill_dir)])
            # Standalone lint is a CI command: blocking findings produce exit 1.
            self.assertIn(code, {0, 1})
            return json.loads(output.getvalue())[0]["lint"]["findings"]

    def _core_findings(
        self,
        manifest: str,
        *,
        skill_dir_name: str = "demo",
        skill_text: str = DEFAULT_SKILL_TEXT,
        symlink_entrypoint: bool = False,
    ) -> list[dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            _write_skill(root, manifest, skill_dir_name=skill_dir_name, skill_text=skill_text, symlink_entrypoint=symlink_entrypoint)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                data = build_index(state, include_packages=False)
                self.assertEqual(len(data["skills"]), 1)
                skill_id = data["skills"][0]["id"]
                output = StringIO()
                with redirect_stdout(output):
                    # Core lint is a metadata-listing command: findings are data, so JSON mode exits 0.
                    self.assertEqual(skillager_main(["lint", skill_id, "--json"]), 0)
            return json.loads(output.getvalue())[0]["lint"]["findings"]


def _write_skill(
    root: Path,
    manifest: str,
    *,
    skill_dir_name: str,
    skill_text: str,
    symlink_entrypoint: bool,
) -> Path:
    skill_dir = root / ".skills" / skill_dir_name
    skill_dir.mkdir(parents=True)
    if symlink_entrypoint:
        outside = root / "outside.md"
        outside.write_text(skill_text, encoding="utf-8")
        os.symlink(outside, skill_dir / "SKILL.md")
    else:
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (skill_dir / "skillager.yaml").write_text(manifest, encoding="utf-8")
    return skill_dir


if __name__ == "__main__":
    unittest.main()
