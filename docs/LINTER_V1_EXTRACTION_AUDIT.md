# Linter V1 Extraction Audit

This audit records the intended ownership split before moving validation code
out of core Skillager. It is deliberately limited to existing V1 behavior: no
new lint policy, no scanner extraction, and no trust or exposure behavior in
the standalone linter package.

## Move To `skillager_linter`

### Strict Manifest Loading

Current source: `src/skillager/skills/simple_yaml.py`

- `MAX_MANIFEST_BYTES`
- `YamlError`
- `StrictYamlError`
- `load_manifest_mapping`
- Shared non-strict YAML helpers currently used by core sidecar handling:
  `load_mapping`, `loads`, `dumps`

The standalone linter owns strict `skillager.yaml` parsing, including duplicate
key, alias, anchor, merge-key, custom-tag, multi-document, UTF-8, mapping-shape,
and byte-size checks.

For V1, the linter package also owns the small YAML helper module because core
sidecar reads and manifest linting share the same parsing primitives. Core
continues exposing those helpers through compatibility shims.

### Findings

Current source: `src/skillager/skills/lint.py`

- `RULE_KEYS`
- `finding`
- `lint_status`
- `lint_report`
- `lint_skill`
- `safe_finding_identity`
- `blocking_findings`

`valid_lint_override` stays in core because it depends on audited trust record
shape.

### Compatibility

Current source: `src/skillager/skills/compatibility.py`

- `KNOWN_AGENTS`
- `WARNING_CODES`
- `WARNING_MESSAGES`
- `normalize_compatibility`
- `infer_compatibility`
- `compatibility_problem`
- `compatibility_warnings`
- `is_explicitly_incompatible`

These helpers are pure runtime and lint helpers. Core compatibility modules
should become facades so compatibility constants and inferred warnings cannot
drift.

### Manifest Validation

Current source: `src/skillager/skills/schema.py`

- Manifest constants: `SCHEMA`, `AUDIENCES`, `ACTIVATION_MODES`, `ENV_RE`,
  `PACKAGE_RE`, `MAX_TARGET_PACKAGES`, `MAX_ENV_NAMES`,
  `MAX_SPECIFIER_LENGTH`
- Validation error type: move as `ManifestValidationError`; keep core
  `SchemaError` as a compatibility alias or wrapper
- Pure validators: `_required_mapping`, `_optional_mapping`,
  `_check_allowed_keys`, `_check_no_control_chars`, `_enum_list`,
  `_compatibility`, `_agent`, `_assumptions`, `_env_list`, `_warnings`,
  `_targets`
- Entrypoint and manifest checks: `_canonical_entrypoint`, `_find_manifest`
- Safe identity helpers: `_identity_from_skill_md`, `_first_heading`,
  `_first_sentence`, `_frontmatter`, `_body_without_frontmatter`
- Pure diagnostic ID helpers: `_infer_id`, `_id_part`
- Safe exception conversion: `_schema_findings`, `_safe_error`
- Minimal manifest template helper used by `--print-minimal-manifest` and docs

The linter may read `SKILL.md` for validation, compatibility inference, and
description-quality warnings, but linter result serialization must not include
body text, body excerpts, body-derived names, or body-derived summaries.

## Stay In Core `skillager`

Current source: `src/skillager/skills/schema.py`

- `TRUST_STATES`
- `Skill`
- `QuarantinedSkill`
- `load_skill_from_dir`
- `quarantine_skill_from_dir`
- `_fallback_quarantine_id`
- `parse_skill` as a compatibility wrapper, if existing imports require it
- `infer_skill` as a core `Skill` constructor
- `manifest_for_skill` as a wrapper around the linter-owned minimal manifest
  template

Core remains responsible for discovery-derived source, package, version, root,
entrypoint, content hash, scan context, trust state, approval overrides,
activation, exposure, search, collections, and materialization.

## Required Core Shims

After extraction, these imports must continue working:

- `skillager.skills.simple_yaml`: `MAX_MANIFEST_BYTES`, `YamlError`,
  `StrictYamlError`, `load_manifest_mapping`, `load_mapping`, `loads`, `dumps`
- `skillager.skills.lint`: `RULE_KEYS`, `finding`, `lint_status`,
  `lint_report`, `lint_skill`, `safe_finding_identity`, `blocking_findings`,
  `valid_lint_override`
- `skillager.skills.compatibility` and `skillager.compatibility`:
  `KNOWN_AGENTS`, `WARNING_CODES`, `WARNING_MESSAGES`,
  `normalize_compatibility`, `infer_compatibility`,
  `compatibility_problem`, `compatibility_warnings`,
  `is_explicitly_incompatible`
- `skillager.skills.schema`: `Skill`, `QuarantinedSkill`, `SchemaError`,
  `load_skill_from_dir`, `quarantine_skill_from_dir`, `parse_skill`,
  `infer_skill`, `manifest_for_skill`, `TRUST_STATES`

## Import Switch Gate

Do not replace core imports with `skillager_linter` imports until:

- The `skillager-linter` package exists in the uv workspace.
- Direct linter tests cover strict YAML, manifest validation, compatibility
  inference, finding shape, CLI output safety, and minimal manifest output.
- Equivalence tests compare `skillager-lint --json` and `skillager lint --json`
  findings for current reachable rule keys.
