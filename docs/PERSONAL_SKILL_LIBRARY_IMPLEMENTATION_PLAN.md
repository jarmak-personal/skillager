# Personal Skill Library Implementation Plan

## Scope

Implement the narrow personal-library product defined in
[PERSONAL_SKILL_LIBRARY_PLAN.md](PERSONAL_SKILL_LIBRARY_PLAN.md): canonical ownership,
exact-hash acceptance, explicit single-skill import, verified history/diff/restore, and
safe use through existing discovery and exposure.

## Completed Components

### Foundation

- Reserved `lib` collection registration with stable library UUID.
- Plain-file library layout with optional system Git.
- Explicit same-UUID relocation recovery.
- Library health in status and doctor without blocking unrelated external-only work.
- Stable approval identity independent of path or Git remote.

### Authoring And Acceptance

- Collision-free pending draft creation.
- Canonical path returned directly by `library new`.
- Placeholder left uncommitted; the first accepted content is the first skill version.
- Metadata-only preview followed by explicit exact-hash confirmation.
- Canonical-tree, lint, scanner, Git, and TOCTOU checks before trust changes.
- No-Git degradation without disabling ordinary ownership.

### Import

- One discovered external skill per explicit import.
- Source identity and exact-hash revalidation under locks.
- Filtered canonical candidate built outside the library.
- Attribution-only provenance; no upstream synchronization promise.
- External origin left unchanged.
- Ambiguous display IDs claimed by distinct roots fail closed before preview.
- Pending repair path if Git or trust recording fails after the copy boundary.

### Version Recovery

- Path-specific, content-addressed, deduplicated history.
- Metadata-only history and diff-stat output.
- Explicit content-bearing diff.
- Append-only restore through verified historical candidates.
- Scanner/lint and exact-tree revalidation before commit and trust.

### Exposure And Working

- Existing native, stub, and router paths consume accepted library skills.
- Source/library display-ID collisions fail closed.
- Native materialization uses verified candidates and atomic replacement.
- Working keeps the v1 schema and reports additive, metadata-only live drift.
- Locally edited managed targets are never overwritten implicitly.
- Full managed-target hashes protect canonically excluded entries during refresh and
  make removal confirmations stale after any target change.
- Sidecars retain exact hashes, agent/scope, materialized fingerprints, and stable
  `source_library_id` where applicable; redundant ownership/policy labels are not
  written.

### UX Reduction

Removed before release:

- `fork`, `sync`, exposure `pin`/`unpin`;
- public `reconcile` and its action vocabulary;
- preview-only `import --refresh`;
- redundant top-level `where` and `edit`;
- future-only `artifact_kind`;
- generated placeholder commits;
- internal transaction keys and duplicate booleans from public JSON;
- unrelated manifest-free heading inference changes.

## Validation

Focused behavioral tests protect:

- pending-body gates;
- collision-safe identity;
- canonical-tree acceptance;
- import isolation and source races;
- append-only history/diff/restore;
- Git conflict and failure recovery;
- native exposure source/candidate races;
- drift metadata body boundaries;
- scanner-finding body boundaries on every personal-library preview/status surface;
- user-owned catalog authority despite repository-controlled portable tag hints;
- ambiguous external-ID import refusal;
- excluded-file exposure refresh/removal and stale-preview refusal;
- non-hidden prompting and moved-library Working recovery;
- optional degraded-library doctor behavior;
- absence of deferred commands from top-level help.

Release validation:

```bash
uv run python -m unittest discover -s tests
uv run python -m unittest tests.behavior.test_cli_contracts -v
uv run ruff check
uv run --python 3.13 python scripts/check.py
```

Changes to setup, working, discovery, or exposure also require the packaged
`simulate-skillager-setup` workflow in a fresh no-context worker.
