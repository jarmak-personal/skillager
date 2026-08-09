# Personal Skill Library Implementation Plan

Status: accepted execution plan.

Product contract: [`PERSONAL_SKILL_LIBRARY_PLAN.md`](PERSONAL_SKILL_LIBRARY_PLAN.md).

This document turns the accepted personal-library direction into independently
shippable implementation slices. It is intentionally more concrete than the product
plan: command behavior, state transitions, module seams, migration rules, tests, and
release gates belong here.

## Outcome

Skillager gains one user-level library for skills the user owns. The finished loop is:

1. Initialize a plain-file, optionally Git-backed library.
2. Create a new skill or import an external reviewed skill.
3. Edit either the canonical library copy or a project exposure.
4. Accept or promote an exact hash after lint/static checks.
5. Inspect history, compare versions, and restore without rewriting history.
6. Detect exposed-copy drift without mutating during `working`.
7. Reconcile local changes explicitly.
8. Update clean exposures in the current project with preview-first `sync`.

External collections, packages, environment skills, project skills, and native agent
directories keep their existing discovery and review behavior. The library adds an
ownership path; it does not replace discovery or become a remote registry.

## Definition Of Complete

The personal-library initiative is complete when all of the following are true:

- `lib/<name>` skills are discoverable, searchable, reviewable, and exposable through
  the public CLI without special paths supplied by the user.
- Ownership never automatically approves changed bytes. Every emitted library body
  matches an accepted exact content hash.
- A user can create, edit, accept, import, inspect history, diff, and restore library
  skills.
- A changed native exposure can be promoted safely when its library base has not
  moved; divergence is shown and refused rather than silently merged.
- `working` detects current-project exposure drift cheaply, read-only, and without
  leaking bodies or changing readiness semantics.
- `sync` updates only clean, unpinned, library-sourced exposures in the current
  project; customized targets are never overwritten by default.
- Git-backed libraries recover historical versions by Skillager content hash. A
  no-Git library remains usable and reports history-dependent operations as
  unavailable.
- Existing commands and old materialization sidecars continue to work.
- The normal unit/behavior suite, Ruff, the one-interpreter full check, and the
  Skillager black-box setup simulation pass at the phases that affect their scope.

## Locked Product Decisions

| Decision | Initial contract |
| --- | --- |
| Library count | One per selected user catalog. |
| Default location | `~/.skillager/library`. |
| Namespace | Fixed and reserved as `lib`. |
| Layout | Plain directories below `<library>/skills/`. |
| Ownership | Library registration identifies ownership; it never grants acceptance. |
| Acceptance | Exact content hash, recorded through the existing trust boundary after lint/static checks. |
| History | System Git when available; no automatic network operations or history rewrites. |
| External updates | Explicit `import --refresh`; never automatically applied. |
| Propagation | Current-project, preview-first `sync`; no global exposure ledger. |
| New-skill compatibility | Add `skillager library new`; preserve current `skillager new`. |
| Import size | One skill per command initially. |
| Git-less mode | Supported; history, restore, and historical exposure rollback report `unavailable`. |

## Core State Model

### Two Independent Axes

Every discovered body is classified independently by ownership and acceptance.

Ownership:

- `library`: canonical source is the reserved personal library.
- `external`: canonical source remains a project, collection, package, environment,
  editable repository, or native agent directory.

Acceptance for the current hash:

- `accepted`: the exact current hash passed the applicable review/authored flow.
- `pending`: owned library content changed since its last accepted hash.
- `lint_blocked`: the current hash has blocking lint/static findings without a valid
  audited override.
- `blocked`: the exact hash or source has an explicit block decision.

Do not add `authored` as a state that bypasses `trust_info`. It is provenance and UX
metadata. The existing exact-hash trust machinery remains the delivery gate.

### Library Skill State

For each `lib/<name>`, commands distinguish:

- `working_hash`: hash of the current library worktree skill directory.
- `accepted_hash`: exact hash currently accepted in local catalog state, if any.
- `head_hash`: hash reconstructed from the Git `HEAD` version of that skill, if Git
  history exists.
- `exposed_hashes`: hashes recorded by current-project sidecars, only when a command
  explicitly asks for exposure information.

Derived status:

- `clean`: working hash equals accepted hash and the relevant Git path is committed.
- `pending`: working hash differs from accepted hash or has never been accepted.
- `accepted_uncommitted`: exact hash is accepted but not recoverable from Git; this is
  a repair state, not a normal successful mutation outcome.
- `conflicted`: Git reports conflicts, merge/rebase state, or an unreadable repository.
- `no_git`: usable library with history features unavailable.

Pending library skills remain metadata-visible. Body output, activation, exposure,
router membership, and sync must continue to fail closed.

### Exposure State

Existing sidecars remain authoritative for each exposure. Additive fields are:

- `ownership`: `library` or `external`.
- `materialized_fingerprint`.
- `customized_hash`, `customized_fingerprint`, `customization_decision`,
  `customized_at`.
- `exposure_blocked_hashes`, `quarantine_path`, `quarantined_at`.
- `pin_hash`.

Old sidecars infer `ownership=library` only when their source resolves to the reserved
library identity; otherwise they infer `external`. Never infer ownership from a
directory merely named `lib`.

## On-Disk Schemas

### User Catalog Registration

Extend the reserved entry in the existing catalog `collections.json` additively:

```json
{
  "collections": {
    "lib": {
      "name": "lib",
      "path": "/resolved/library/path/skills",
      "kind": "library",
      "library_id": "uuid",
      "library_root": "/resolved/library/path"
    }
  }
}
```

Rules:

- `library init` is the only command that may create or replace a `kind=library`
  registration.
- `collection add --name lib` always refuses. `collection remove lib` refuses for a
  reserved `kind=library` entry but may remove a legacy ordinary collection using that
  name, which gives existing users a migration path.
- Re-running `library init` for the same `library_id` and resolved path is idempotent.
- A different registered path or ID is a conflict, not an implicit relocation.
- Custom `--catalog-state-dir` selects an isolated catalog and therefore an isolated
  library registration, which keeps behavior tests hermetic.

### `<library>/.skillager/library.json`

```json
{
  "schema": "skillager.library.v1",
  "library_id": "uuid",
  "namespace": "lib",
  "created_at": "ISO-8601",
  "git": {"mode": "system"}
}
```

For `--no-git`, `git.mode` is `disabled`. Store the resolved path only in the catalog
registration; the internal identity file must survive moving or cloning the library.

### `<library>/.skillager/provenance.json`

```json
{
  "schema": "skillager.library-provenance.v1",
  "skills": {
    "brainstorm": {
      "artifact_kind": "skill",
      "imported_from": {
        "source_key": "collection:superpowers",
        "skill_id": "superpowers/brainstorm",
        "content_hash": "sha256"
      },
      "imported_at": "ISO-8601"
    }
  }
}
```

Forks use `forked_from` with library skill ID and content hash. Provenance is lineage,
not acceptance authority. A malicious or stale provenance edit must not approve a
body.

### Stable Library Approval Keys

The existing approval-key logic changes identity when a Git remote is added or a
local repository moves. Library skills need a dedicated stable key:

```text
library:<library_id>#<relative-skill-name>
```

Collection indexing must carry `library_id` into the source metadata, and
`approval_key_for` must prefer the stable library key for that source type. Adding a
private remote or moving a registered library must not invalidate accepted hashes.

Acceptance records remain local catalog state. Cloning the Git library onto another
machine preserves bodies and provenance but requires local acceptance before Skillager
will emit those bodies on that machine.

## CLI Contracts

All JSON listed below is metadata-only unless a command is explicitly documented as
content-bearing. New JSON payloads use versioned `schema` fields from their first
release.

### Foundation And Authoring

```bash
skillager library init [--path <dir>] [--no-git] [--json]
skillager library status [<skill>] [--json]
skillager library new <name> [--json]
skillager library accept <skill> [--yes] [--override-lint --reason <text>] [--json]
skillager where <skill> [--json]
skillager edit <skill> [--open]
```

- `init` and `new` have explicit, collision-free targets and may write without `--yes`.
  Neither overwrites an existing path.
- `init` may adopt an existing directory or Git repository, but it moves no files and
  treats any skill bodies already below `skills/` as pending until separately accepted.
  Existing conflicts or staged changes refuse initialization.
- Default initialization requires the system `git`; if it is unavailable, the command
  fails with the explicit `--no-git` alternative. Skillager never changes global Git
  configuration. Commits use the repository/user identity when configured and a
  command-scoped `Skillager <skillager@localhost>` fallback otherwise, reported by
  `library status`.
- `new` writes a generated draft and records it in Git when enabled, but does not
  accept the placeholder body. Output points to `edit` and `library accept`.
- `status`, `where`, and plain `edit` are read-only. `edit` prints the canonical path;
  `edit --open` launches `$EDITOR` and reports the resulting skill as pending.
- `accept` previews the exact current hash and lint/static result. Interactive TTYs
  may confirm; non-interactive mutation requires `--yes`.
- `accept` can accept an already committed clean `HEAD` version or commit the selected
  dirty skill path. It refuses conflicts and unrelated staged changes.
- `where` reports canonical path, ownership, working/accepted/head hashes, Git state,
  and current-project exposures. It does not print the skill body.

### Import

```bash
skillager import <external-skill-id> [--as <name>] [--yes] [--json]
skillager import --refresh <library-skill-id> [--json]
```

- Import resolves one discovered source and one destination.
- Preview is read-only and identifies source, source hash, destination, provenance,
  risk/lint state, and whether owner review is required.
- The origin is rehashed after confirmation and under the mutation lock. If it changed
  since preview, import refuses and produces a new preview.
- Unreviewed external content is never staged under the library path. Interactive
  import performs the existing owner-review gate at the doorway; agent-mediated or
  non-interactive import requires the user's explicit choice before `--yes` is used.
- Blocking findings require `--override-lint --reason` using the existing audited
  semantics.
- Successful import copies the filtered agent-visible tree, records provenance,
  commits when Git is enabled, records the new library approval key/hash, and refreshes
  the `lib` collection index.
- `--refresh` is preview-only initially. It resolves provenance, compares the current
  upstream hash with the imported base and library head, and never applies changes.

### Versioning

```bash
skillager library history <skill> [--json]
skillager library diff <skill> [--from <hash>] [--to <hash>] [--stat]
skillager library restore <skill> --to <hash> [--yes] [--json]
```

- `history` lists deduplicated Skillager content hashes with short hash, commit ID,
  commit time, operation when known, and head/current markers. It emits no bodies.
- `diff --stat` is metadata-only. Plain `diff` is intentionally content-bearing and
  must be documented as a human/admin command, like other body-revealing operations.
- Hash arguments accept a unique content-hash prefix; ambiguous prefixes refuse.
- Historical lookup walks path-specific Git history, reconstructs eligible regular
  files outside the library, and verifies the full Skillager content hash. Git commit
  IDs never masquerade as Skillager versions.
- `restore` previews the selected historical tree, re-runs lint/static checks, and
  writes it as a new head commit. It never invokes reset, checkout over the worktree,
  rebase, or another history-rewriting operation.

### Drift And Reconciliation

```bash
skillager working --agent <agent> --json
skillager reconcile [<skill>] [--json]
skillager reconcile keep-local <skill> --yes
skillager reconcile quarantine <skill> --yes
skillager reconcile repair <skill> --yes
skillager reconcile promote <skill> --yes
skillager reconcile rollback <skill> --yes
skillager reconcile import <skill> --as <name> --yes
```

- `working` remains read-only, non-interactive, metadata-only, and exit-code stable.
- `reconcile` without an action is read-only.
- `promote` exists only for native library exposures. It compares exposure base,
  current exposure, and accepted library head.
- Fast-forward promote requires `library accepted hash == exposure source_hash`.
  Divergence displays both change sets and refuses; the first implementation has no
  automatic merge.
- External native exposure edits offer `reconcile import`, not promote.
- Stub/router changes can be kept local, repaired, or quarantined but never promoted
  or imported as source content.
- Exposure rollback restores the sidecar's recorded `source_hash` from library Git
  history. Dirty target content is quarantined first. External and no-Git rollback
  report `unavailable` without writing.

### Fork, Sync, And Pin

```bash
skillager fork <library-skill> --as <name> --description <text> [--from <hash>] [--yes]
skillager sync [--agent <agent>] [--apply] [--json]
skillager pin <library-skill> [--to <hash>] [--agent <agent>]
skillager unpin <library-skill> [--agent <agent>]
```

- Fork is a new library identity with lineage, not an exposure pin.
- The first non-interactive contract requires a new description through
  `--description`; an optional editor flow may come later. An unchanged description
  refuses because indistinguishable variants are an activation defect.
- Bare `sync` is preview-only. `--apply` updates only current-project, clean,
  unpinned, library-native or library-stub exposures whose new source hash is accepted.
- Customized, unresolved-drift, blocked, malformed-sidecar, missing, and pinned
  targets are skipped with stable reason codes.
- No command walks or writes other projects. There is no exposure ledger.

## Target Module Boundaries

Do not block library work on a wholesale rewrite of `commands/impl.py`. Establish a
small seam and extract touched behavior incrementally.

New modules:

```text
src/skillager/library/
  __init__.py
  model.py          # library/skill state objects and validation
  paths.py          # registration lookup, containment, reserved namespace
  metadata.py       # library.json and provenance.json
  git.py            # constrained system-Git adapter
  history.py        # content-hash history reconstruction and diff inputs
  operations.py     # init/new/accept/import/fork/restore/promote transactions

src/skillager/commands/
  context.py        # state/catalog/project path resolution shared with impl.py
  library.py        # library namespace, where, edit handlers/parsers
  importing.py      # top-level import handler/parser
  reconcile.py      # reconciliation handlers/parsers
  sync.py           # sync/pin handlers/parsers

src/skillager/skills/
  tree.py           # hash exclusions plus reusable fingerprint helper

src/skillager/exposure/
  drift.py          # current-project exposure classification
  reconcile.py      # keep-local/quarantine/rollback mechanics

src/skillager/state/
  locking.py        # bounded cross-process resource locks
```

Existing modules changed deliberately:

- `commands/impl.py`: register new parsers/handlers and progressively import shared
  context; do not add large new command bodies.
- `catalog/impl.py`: reserved collection registration, library source metadata, atomic
  catalog writes.
- `skills/index.py`: fingerprint-gated cache and library ownership/acceptance
  annotations.
- `state/trust.py`: stable library approval keys and lock-safe read/modify/write.
- `exposure/impl.py`: additive sidecar fields and reuse of shared locks/tree rules.
- top-level facade modules: left intact unless a touched import can be migrated safely;
  broad deletion is separate cleanup after feature stability.

## Mutation And Concurrency Protocol

### Locks

Add a small standard-library lock adapter using `fcntl` on Unix and `msvcrt` on
Windows, with a bounded timeout and clear error. Lock keys are based on canonical
resource identities, not user-provided unresolved strings.

Resources include:

- selected catalog state;
- library ID;
- individual exposure target when necessary.

Multi-resource operations acquire locks in sorted canonical-key order. No command
holds a lock while waiting for interactive confirmation. After confirmation it
acquires locks and recomputes hashes/preconditions to prevent time-of-check/time-of-use
writes.

Replace open-coded read/modify/write sequences needed by the new flow with a
lock-aware atomic mutation helper. Migrate existing trust mutation first because
accept/import/promote depend on it. Migrate the existing unbounded materialization
lock to the shared helper when exposure reconciliation lands.

### Library Mutation Sequence

Every accepting library mutation follows this order:

1. Resolve and preview without writes.
2. Obtain user confirmation.
3. Acquire catalog/library locks in canonical order.
4. Re-resolve paths, recompute full hashes, and re-run lint/static checks.
5. Verify destination containment, Git state, collision rules, and expected base hash.
6. Prepare candidate content in a private temporary directory outside the library.
7. Move/copy the validated tree and update provenance atomically enough to restore the
   previous tree on a pre-commit failure.
8. Create a path-scoped Git commit when Git is enabled.
9. Record the exact library approval key/hash in local catalog trust state.
10. Refresh the reserved collection index and return the final hashes.

Commit-before-acceptance is fail-safe. If Git succeeds and the later trust write fails,
the library body is committed but pending and cannot be emitted; `library status` and
`library accept` repair it. Never accept first and then leave unrecoverable content.

Refuse Git conflicts, merge/rebase state, or unrelated staged changes. Unstaged edits
to other skill directories may remain pending while a path-scoped operation commits
only its target and the metadata it owns. Never use `git reset --hard`, broad checkout,
clean, stash, push, or pull.

## Delivery Phases

Each phase is independently mergeable. “Done” means its behavioral tests and normal
suite pass, its new JSON schema is documented, and no later-phase behavior is partially
advertised.

### Phase 0 — Contract And Baseline

Scope:

- Land the accepted product and implementation documents.
- Delete the superseded v1 plan.
- Record baseline timings for `working` with small and large discovered inventories.
- Add reusable behavior-test fixtures for an isolated home, catalog, project, library,
  and Git identity.
- Exclude internal planning documents from wheel force-includes.

Tests/gate:

- Current normal suite remains green with no production behavior changes.
- Docs links resolve and examples remain opt-in under the documented fixture marker.

### Phase 1 — Safety And Command Seams

Scope:

- Add `commands/context.py` and move only state/catalog/project path resolution needed
  by new commands.
- Add bounded cross-process resource locking.
- Add lock-safe atomic JSON mutation and migrate trust plus collection registration
  writes needed by the library.
- Create the `library/` package with models and path validation.
- Reserve the `lib` collection identity without yet exposing public library commands.

Tests/gate:

- Two concurrent trust/catalog mutators serialize without lost updates.
- Held locks time out with a stable, actionable error.
- Lock acquisition order cannot deadlock in the tested multi-resource cases.
- Collection add/remove cannot claim a reserved library registration.
- All existing CLI behavior and monkeypatch-based tests remain green.

Deferred deliberately:

- Full `commands/impl.py` split.
- Facade deletion.
- Incremental index work not yet needed on a mutation path.

### Phase 2 — Library Foundation And Direct Authoring

Scope:

- Implement `library init`, `status`, `new`, and `accept`.
- Implement `where` and `edit` for library skills.
- Add stable library approval keys.
- Register/index the library through the existing collection inventory.
- Add minimal Git adapter operations: availability, init, status, conflict detection,
  path-scoped add/commit, HEAD metadata.
- Keep Git identity changes command-scoped; never write global Git configuration and
  never overwrite repository-local identity.
- Add `doctor` library checks for missing path, registration mismatch, Git conflicts,
  uncommitted/pending skills, no remote, and no-Git degradation. Diagnostics are
  read-only unless an existing explicit fix path is appropriate.

Tests/gate:

- Init is idempotent and rejects conflicting path/identity.
- Default/custom paths and `--no-git` work under isolated homes.
- `library new` never overwrites and remains pending until accept.
- Out-of-band edits invalidate exact-hash availability.
- Accept records only the recomputed hash and enforces lint overrides/reasons.
- Adding a Git remote does not change the library approval key.
- An accepted library skill works through list/search/show/expose using the public CLI.
- Pending content never appears in `show --content`, activation, exposure, stub, or
  router output.

User checkpoint:

The user can create a canonical personal skill, edit it, accept it once, and expose it
to a project. This is the first usable centralized-library release.

### Phase 3 — Import Boundary

Scope:

- Implement top-level single-skill `import` from project, collection, environment,
  Python/npm/Cargo package, editable source, and native directories.
- Reuse current discovery and review selection; do not import packages or execute
  package code.
- Add collision-safe `--as` naming and provenance.
- Implement preview-only `import --refresh` with unresolvable-provenance degradation.
- Refresh library index after successful mutations.

Tests/gate:

- Import from every supported discovered source type.
- Unreviewed or lint-blocked content never appears anywhere below the library root
  before review/override succeeds.
- Source changes between preview and lock cause refusal.
- Sidecars, evidence, caches, symlinks, and transient files follow the canonical
  content/copy exclusion rules.
- Origin is byte-for-byte unchanged.
- Failed Git or trust steps leave either the prior state or a safe pending state with a
  documented repair command.
- Deleted/renamed upstream degrades refresh only; owned copy stays usable.

User checkpoint:

The user can adopt an external skill into one canonical owned home without weakening
the existing review gate.

### Phase 4 — First-Class Version History

Scope:

- Implement `library history`, metadata-only history JSON, `diff`, and append-only
  `restore`.
- Add path-specific Git history reconstruction with full content-hash verification.
- Deduplicate commits that produce the same agent-visible skill hash.
- Support unique short-hash selection.
- Extend `where` and `library status` with head/history availability.

Tests/gate:

- History resolves the correct content hash across commits that touch multiple skills.
- Metadata-only history/status output contains no body fragments.
- Diff reports correct changes and is clearly classified as content-bearing.
- Restore recreates exact historical content as a new head and accepted hash.
- Ambiguous/missing hashes, deleted history, symlinks, conflicts, and no-Git mode fail
  closed without changing the worktree.
- No Git operation rewrites history or touches remotes.

User checkpoint:

The library now provides understandable, content-addressed version management rather
than merely storing files in a central folder.

### Phase 5 — Incremental Index And Read-Only Drift

Scope:

- Add one shared tree-fingerprint helper using the same file eligibility rules as
  `content_hash`.
- Persist per-skill fingerprints with index entries and rehash/rescan/relint only on
  fingerprint miss.
- Keep full hashes authoritative on every approval and mutation path.
- Add current-project managed exposure scanning and drift-state classification.
- Bump `working` once to `skillager.working.v2`, remove its dead hardcoded fields, and
  add advisory `exposure_changes` with `ownership`.
- Do not resolve library freshness or write fingerprints/sidecars during `working` in
  a way that violates read purity. Reuse persisted index data only when already valid;
  a read-only invocation may compute in memory but must not mutate state.

Tests/gate:

- Fingerprint hit skips full hash/scan/lint; miss recomputes; cold and warm outputs are
  identical.
- Mtime-only changes invalidate the advisory fingerprint.
- Clean, local-edit, kept-local, partial-missing, blocked, malformed-sidecar, and
  unmanaged states classify correctly.
- Fully deleted exposure directories remain explicitly undetectable without a ledger.
- `working` readiness, exit code, concise normal output, and metadata body boundaries are
  unchanged apart from the documented v2 JSON schema.
- Read-purity tests prove `working` writes no catalog, project state, sidecar, or target.

Performance gate:

- Warm `working` time scales primarily with metadata/fingerprint inspection rather
  than total skill bytes. Record before/after measurements; avoid a brittle fixed
  millisecond assertion in CI.

Phase 5 implementation measurement (2026-08-07, local macOS development machine):
a 3.8 MiB eligible tree with 41 files took 1.54–1.58 seconds for a cold `working`
across three runs and 0.15–0.16 seconds with a persisted fingerprint hit. The test
suite asserts the skipped hash/scan/lint calls rather than these machine-specific
times.

### Phase 6 — Reconciliation And The Edit-Anywhere Loop

Deliver in two internal slices while keeping one coherent command namespace.

Phase 6A:

- Read-only reconcile inventory.
- `keep-local` with exact customized hash/fingerprint.
- Recoverable project-local quarantine and exposure-scoped blocks.
- Repair semantics for generated stubs/routers.

Phase 6B:

- Fast-forward-only `promote` for library native exposures.
- `reconcile import` for edited external native exposures.
- Historical exposure rollback for Git-backed library sources.
- Dirty-target quarantine before any restore.

Tests/gate:

- Every mutation requires interactive confirmation or `--yes` after user choice.
- Recomputed post-lock hashes prevent stale-preview writes.
- Keep-local suppresses only the exact kept hash; further edits reappear as drift.
- Quarantine preserves all target files outside agent-visible roots and never deletes
  by default.
- Promote excludes sidecars/evidence and succeeds only when accepted library head
  equals the exposure base hash.
- Diverged library/exposure edits show both diffs and leave both sides untouched.
- External, stub, router, no-Git, malformed-sidecar, and missing-history cases refuse
  with correct next actions.
- Concurrent reconcile/promote operations serialize per resource.

User checkpoint:

The core promise is complete: edit where the skill is being used, promote once, and
recover any managed library version without losing project-local work.

Phase 6 implementation note (2026-08-07): delivered the metadata-only
`skillager.reconcile.v1` inventory, exact-hash keep-local decisions, recoverable
project quarantine and exposure-scoped blocks, generated stub/router repair,
fast-forward-only library promotion, edited-external import, and verified historical
exposure rollback. All mutation paths rehash after confirmation under shared bounded
resource locks; dirty repair/rollback targets are preserved before replacement.

### Phase 7 — Variants, Sync, And Pins

Scope:

- Implement `fork` with lineage and mandatory distinct description.
- Implement current-project `sync`, preview-first and clean-only.
- Implement per-exposure `pin` and `unpin` using `pin_hash`.
- Make source freshness resolution explicit in `sync`/`reconcile`; keep it out of
  `working` unless later profiling justifies a cheap count.

Tests/gate:

- Fork from head or historical hash produces a distinct library identity and exact
  lineage.
- Identical descriptions refuse; agent selection metadata differs after a valid fork.
- Sync updates only clean accepted library exposures and preserves sidecar provenance.
- Customized, pinned, blocked, dirty, malformed, missing, external, and unaccepted
  sources are skipped.
- No filesystem outside the current project and selected library changes.
- Bare sync and JSON preview are read-only; `--apply` is required for writes.

Phase 7 implementation note (2026-08-08): delivered preview-first accepted-head and
verified-history forks with exact `forked_from` provenance and mandatory distinct
agent-facing descriptions; current-project clean-only native/stub sync with stable
skip reasons and atomic verified replacement; and sidecar-only exact-source pins.
Source freshness is resolved in `sync`/`reconcile` and remains outside `working`.

### Phase 8 — Product Recenter And Cleanup

Scope:

- Recenter README, user guide, repository guide, CLI help, and release notes on the
  personal library while preserving external discovery documentation.
- Add opt-in runnable docs examples with isolated fixtures.
- Run the repository's `Simulate Skillager Setup` workflow because collection,
  discovery, setup, exposure, and handoff behavior have changed.
- Remove obsolete wheel inclusion for internal plan documents.
- Finish incremental per-command extraction for touched commands.
- Re-evaluate facade deletion as a separate behavior-preserving cleanup; do not couple
  it to the feature release if it enlarges review risk.
- Address small packaging/developer dependency repairs only in isolated commits.

Release gate:

```bash
uv run python -m unittest discover -s tests
uv run python -m unittest tests.behavior.test_cli_contracts -v
uv run ruff check
uv run --python 3.13 python scripts/check.py
```

Also run the black-box setup simulation in a fresh temporary directory and record any
discovery, approval, exposure, or onboarding regression before release.

Phase 8 implementation note (2026-08-08): recentered the README, user and agent
guides, repository guidance, package metadata, top-level CLI help, and planned 0.9.0
release notes on the personal library while keeping every external discovery path
documented. Added an isolated no-Git library docs fixture alongside the existing
project fixture. The built wheel now includes public docs explicitly, excludes both
internal personal-library plans, and verifies that boundary during wheelhouse smoke.
All personal-library command surfaces introduced in Phases 2–7 remain extracted under
`commands/library.py`, `commands/importing.py`, `commands/reconcile.py`, and
`commands/lifecycle.py`; facade deletion remains a separate cleanup because it would
enlarge feature-release review risk.

The required fresh-worker setup audit completed without environmental failure or a
Skillager discovery, approval, onboarding, curation, or exposure regression. It
indexed 60 entries, selected 49 source skills from both manifest-free child
repositories, collapsed them to 39 Codex choices, skipped 11 installed global skills,
and guided a focused six-skill GIS router. Follow-up product opportunities, outside
this release-hardening phase, are improved semantic search ranking, safe reviewed-risk
metadata during curation, a more compact inventory payload, structured optional
curation commands, and simpler count reconciliation. The retained audit directory is
`/tmp/skillager-setup-audit.uAvo00` (`/private/tmp/skillager-setup-audit.uAvo00` after
macOS canonicalization).

Phase 8 UX hardening follow-up (2026-08-08): repeated Git, no-Git,
external-source, two-project, native, stub, pin, sync, import, fork, history,
and reconcile journeys through the public CLI. The command-surface lookback found
that `library`, `where`, `edit`, `import`, `reconcile`, `fork`, `sync`, `pin`, and
`unpin` each retain a distinct user-facing job; no namespace deletion justified a
compatibility break. The fix cycles instead preserve agent scope in every generated
reconcile/sync command, explain reconcile choices and diffs in plain language, show
sync version movement and readable skip reasons, make tag edits report membership
changes, distinguish idempotent pin state, provide safe non-interactive
accept/restore previews, separate source-entry and agent-choice counts, remove
placeholder plural forms, and reduce collection-name noise in semantic search.
An unused legacy status renderer and its private duplicate-content formatter were
removed rather than preserved as unreachable parallel UX.
Reviewed-risk diagnostics remain owner-facing rather than becoming an agent curation
signal, preserving the approval/availability authority boundary.

A post-fix fresh-worker audit repeated setup against both child repositories, again
indexing 60 entries, selecting 49 source skills, collapsing them to 39 Codex choices,
and skipping 11 installed global skills. It then created an eight-skill GIS router
without an environmental, discovery, approval, onboarding, or exposure failure. The
audit validated the revised setup counts and tag-mutation feedback while catching
three remaining presentation issues: an irregular hash plural, an ambiguous routed
exposure count, and source/tag-only semantic-search noise. Focused replays after those
fixes confirmed explicit source-entry, agent-choice, and routed-choice units and a
smaller meaningfully matched result set. The retained audit directory is
`/tmp/skillager-setup-dNqvXi` (`/private/tmp/skillager-setup-dNqvXi` after macOS
canonicalization).

## Behavioral Test Matrix

All new end-to-end tests invoke the installed/public CLI through subprocesses with
isolated `HOME`, project state, catalog state, cache, library, and Git config.

| Boundary | Required scenarios |
| --- | --- |
| Discovery | Default/custom library, missing library, moved path, reserved collection, accepted and pending hashes. |
| Review | Imported reviewed/unreviewed/blocked/lint-blocked content; authored accept with audited override. |
| Body leaks | Every metadata command against unreviewed import candidates and pending library edits. |
| Git | Missing executable, no-Git mode, clean repo, unrelated unstaged edit, staged edit, conflict, failed commit, history lookup. |
| Concurrency | Two accepts, accept plus import, promote plus direct accept, two reconciles. |
| Paths | Absolute/relative custom root, `..`, slug collisions, symlinks, nested repo, destination containment. |
| Versioning | Multiple skills per commit, repeated hash, short-hash ambiguity, restore deleted file, old exposure hash. |
| Exposure | Native/stub/router, clean/customized/pinned/missing/malformed, Codex/Claude project roots. |
| Migration | Old collection data, old sidecars, existing collection named `lib`, no library configured. |
| Read purity | `working`, status, where, history, import preview, reconcile preview, sync preview. |

Tests should assert stable outcomes: exit codes, schema fields, state transitions, exact
hashes, file creation/preservation, Git commit ancestry, and body-leak boundaries.
Avoid assertions on incidental prose, absolute paths in normal JSON, or wall-clock
timing.

## Migration And Compatibility

- No automatic bulk migration of existing personal collections.
- If a normal collection already owns the name `lib`, `library init` refuses and
  explains how to rename/remove it; Skillager never silently takes the namespace.
- Existing collections remain external even if their path resembles the library.
- Existing exposures remain valid. New ownership fields are additive and inferred
  conservatively.
- The first drift release intentionally performs the documented single JSON break from
  `working.v1` to `working.v2`; do not mix another working-schema break into later
  phases.
- No command changes the default behavior of `skillager new`, setup, review, expose,
  collection refresh, or metadata body output without an explicit documented phase.
- A library can be abandoned without export: its `skills/` tree is already the exit
  format.

## Security Checklist For Every Mutating PR

- Is the user-selected source rehashed after confirmation and under lock?
- Is the destination resolved and proven to remain below the intended library or
  project root?
- Are symlinks and excluded/evidence/transient files handled by the canonical shared
  tree rules?
- Can any unaccepted body reach `show --content`, activate, expose, router, stub, sync,
  or normal JSON?
- Can a failure overwrite or strand user work without a recoverable copy?
- Does the command refuse conflict/rebase/staged-surprise states rather than guessing?
- Does a changed Git remote or filesystem path accidentally grant or revoke trust?
- Are locks bounded, acquired in canonical order, and released on every exception?
- Does the command avoid network operations and history rewriting?
- Are normal JSON paths/provenance appropriately compact and non-sensitive?

## Recommended Pull Request Sequence

Keep review units narrow even when phases share a release:

1. Accepted docs, fixtures, and plan packaging exclusion.
2. Locking plus atomic state mutation.
3. Command context seam plus library models/registration.
4. `library init/status` and Git status adapter.
5. `library new/edit/where/accept` and stable approval keys.
6. Import plus provenance and refresh preview.
7. History/diff/restore.
8. Shared fingerprint plus incremental index.
9. `working.v2` exposure drift.
10. Reconcile inventory/keep-local/quarantine.
11. Promote/import/rollback reconciliation.
12. Fork.
13. Sync/pin.
14. Docs recenter, black-box simulation, and release hardening.

Each PR should run the focused tests it owns plus the normal suite. Avoid combining the
facade deletion, dependency-floor changes, or broad formatting churn with a public CLI
feature PR.

## Explicitly Parked

- Cross-project exposure inventory and rollout.
- Automatic Git pull/push/fetch or remote setup.
- Automatic application of upstream imported-skill changes.
- Semantic-version requirements for prose skills; content hashes are versions.
- Multi-library and shared/team-library ownership.
- Bulk import.
- Automatic merges during promote or refresh.
- Hosted registry, publishing, sharing, accounts, or remote discovery.
- Agent definitions and multi-artifact packs.

Revisit a parked item only with a demonstrated workflow that cannot be handled safely
by the personal library, explicit import, current-project sync, and existing external
collections.
