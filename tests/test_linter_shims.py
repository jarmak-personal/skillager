from __future__ import annotations

import unittest

import skillager.compatibility as core_compatibility
import skillager.lint as core_lint
import skillager.schema as core_schema
import skillager.simple_yaml as core_yaml
import skillager.skills.compatibility as skills_compatibility
import skillager.skills.lint as skills_lint
import skillager.skills.schema as skills_schema
import skillager.skills.simple_yaml as skills_yaml
import skillager_linter.compatibility as linter_compatibility
import skillager_linter.findings as linter_findings
import skillager_linter.simple_yaml as linter_yaml
import skillager_linter.validators as linter_validators


class LinterShimTests(unittest.TestCase):
    def test_simple_yaml_reexports_linter_symbols(self) -> None:
        expected = {
            "MAX_MANIFEST_BYTES",
            "StrictYamlError",
            "YamlError",
            "dumps",
            "load_manifest_mapping",
            "load_mapping",
            "loads",
        }
        self.assertLessEqual(expected, set(skills_yaml.__all__))
        self.assertIs(skills_yaml.StrictYamlError, linter_yaml.StrictYamlError)
        self.assertIs(skills_yaml.YamlError, linter_yaml.YamlError)
        self.assertIs(skills_yaml.load_manifest_mapping, linter_yaml.load_manifest_mapping)
        self.assertIs(core_yaml.load_manifest_mapping, linter_yaml.load_manifest_mapping)
        self.assertEqual(core_yaml.MAX_MANIFEST_BYTES, linter_yaml.MAX_MANIFEST_BYTES)

    def test_lint_reexports_linter_symbols_but_keeps_override_in_core(self) -> None:
        expected = {
            "RULE_KEYS",
            "blocking_findings",
            "finding",
            "lint_report",
            "lint_skill",
            "lint_status",
            "safe_finding_identity",
            "valid_lint_override",
        }
        self.assertLessEqual(expected, set(skills_lint.__all__))
        self.assertIs(skills_lint.RULE_KEYS, linter_findings.RULE_KEYS)
        self.assertIs(skills_lint.finding, linter_findings.finding)
        self.assertIs(skills_lint.lint_skill, linter_findings.lint_skill)
        self.assertIs(core_lint.blocking_findings, linter_findings.blocking_findings)

        lint = {
            "findings": [
                skills_lint.finding("schema_violation", "block", "schema", "expected skillager.skill.v1"),
            ]
        }
        record = {"lint_override": {"findings": lint["findings"]}}
        self.assertTrue(skills_lint.valid_lint_override(record, lint))
        self.assertFalse(skills_lint.valid_lint_override(None, lint))

    def test_compatibility_reexports_linter_symbols(self) -> None:
        expected = {
            "KNOWN_AGENTS",
            "WARNING_CODES",
            "WARNING_MESSAGES",
            "compatibility_problem",
            "compatibility_warnings",
            "infer_compatibility",
            "is_explicitly_incompatible",
            "normalize_compatibility",
        }
        self.assertLessEqual(expected, set(skills_compatibility.__all__))
        self.assertIs(skills_compatibility.KNOWN_AGENTS, linter_compatibility.KNOWN_AGENTS)
        self.assertIs(skills_compatibility.WARNING_CODES, linter_compatibility.WARNING_CODES)
        self.assertIs(skills_compatibility.normalize_compatibility, linter_compatibility.normalize_compatibility)
        self.assertIs(core_compatibility.compatibility_problem, linter_compatibility.compatibility_problem)
        self.assertIs(core_compatibility.compatibility_warnings, linter_compatibility.compatibility_warnings)
        self.assertIs(core_compatibility.is_explicitly_incompatible, linter_compatibility.is_explicitly_incompatible)

    def test_schema_reexports_core_contract_and_linter_constants(self) -> None:
        expected = {
            "Skill",
            "QuarantinedSkill",
            "SchemaError",
            "TRUST_STATES",
            "load_skill_from_dir",
            "quarantine_skill_from_dir",
            "parse_skill",
            "infer_skill",
            "manifest_for_skill",
        }
        self.assertLessEqual(expected, set(skills_schema.__all__))
        self.assertIs(skills_schema.SchemaError, linter_validators.ManifestValidationError)
        self.assertIs(core_schema.SchemaError, linter_validators.ManifestValidationError)
        self.assertEqual(skills_schema.SCHEMA, linter_validators.SCHEMA)
        self.assertEqual(skills_schema.AUDIENCES, linter_validators.AUDIENCES)
        self.assertEqual(skills_schema.ACTIVATION_MODES, linter_validators.ACTIVATION_MODES)


if __name__ == "__main__":
    unittest.main()
