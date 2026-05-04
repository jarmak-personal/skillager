# Structural Refactor Plan

This plan keeps Skillager usage unchanged while reducing the largest source and
test files and moving the flat source package toward clearer subsystem
boundaries.

## Acceptance Criteria

- `skillager` remains exposed as `skillager.cli:main`.
- Existing command names, flags, exit codes, JSON fields, and metadata-only
  output boundaries remain unchanged.
- Compatibility imports keep working for existing callers and tests.
- No behavior changes are introduced intentionally.
- The full test suite passes.

## Work Plan

1. Add repository hygiene rules for generated local state and cache directories.
2. Split the monolithic unit test file into domain-focused files with shared
   helpers.
3. Keep `skillager.cli` as the public facade and move command implementation
   into `skillager.commands`.
4. Keep `skillager.materialize` as the compatibility facade and move
   materialization internals into `skillager.exposure`.
5. Keep `skillager.collections` as the compatibility facade and move collection
   and tag internals into `skillager.catalog`.
6. Add source grouping packages for stable skill-domain and state-domain APIs
   while preserving existing flat-module imports.
7. Run formatting/lint/test checks and fix any regressions without changing CLI
   behavior.

## Refactor Boundaries

- `skillager.commands` owns parser construction, command handlers, handoff and
  status assembly, interactive flows, and terminal output helpers.
- `skillager.exposure` owns materialized skill rendering, target path policy,
  sidecars, file writes, locks, and agent-note management.
- `skillager.catalog` owns reusable collection registration, collection
  indexing, tag state, project tag attachment, and migration records.
- Existing modules remain as facades where external imports may already exist.

## Verification

Run these before handoff:

```bash
uv run ruff check
uv run python -m unittest discover -s tests
```

For release-level confidence, run the project check script when available:

```bash
uv run --python 3.13 python scripts/check.py
```
