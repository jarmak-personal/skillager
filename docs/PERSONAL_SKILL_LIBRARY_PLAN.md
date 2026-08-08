# Personal Skill Library Plan (v2)

Status: accepted product direction. Supersedes the deleted
`CENTRAL_SKILL_LIBRARY_PLAN.md` (v1).

This document defines the product model, safety boundaries, and user workflows. The
execution sequence, code seams, command contracts, and phase gates live in
[`PERSONAL_SKILL_LIBRARY_IMPLEMENTATION_PLAN.md`](PERSONAL_SKILL_LIBRARY_IMPLEMENTATION_PLAN.md).

## Abstract

V1 solved reconciliation: detect edits to exposed skills, preserve them, and route them
back to a canonical source through explicit user intent. It was correct about conduct
(read-only `working`, metadata-only output, explicit mutation) but hedged on ownership:
it treated five kinds of "central source" as peers and left the personal library as a
config option in Phase 4 and an open question at the bottom.

V2 resolves the hedge. Skillager commits to being a **personal skill manager**:

- One user-level **library** is the canonical home for skills the user owns.
- External sources — project repos, collections, packages, native agent dirs — remain
  exactly what they are today: discovered, reviewed, exposed in place. Nothing is taken
  away from the overlay model.
- **Import** is the single doorway between the two. Review happens at that doorway.
- **Lifecycle follows ownership**: accept, promote, restore, sync, fork, and variants
  apply to library skills. External skills get drift detection and quarantine, nothing
  more.

This deletes most of v1's heavy machinery. The snapshot cache is replaced by the
library's git history. The cross-project exposure ledger is replaced by per-project
sync. Source update policy is replaced by "imports are always explicit." The product
becomes a sequence of smaller, independently useful milestones because the library
reuses the existing collection inventory without inheriting collection trust.

The commitment being made is narrow: **your own skills get one home**. It is not a
registry, not a hub, and not infrastructure.

## What Changed From V1

| V1 component | V2 disposition |
| --- | --- |
| Drift detection in `working` (Phase 1) | Kept nearly verbatim. |
| `reconcile` keep-local / quarantine (Phase 2) | Kept nearly verbatim. |
| Snapshot cache (Phase 3) | **Deleted.** Library git history provides rollback for library skills. Non-library exposures report rollback `unavailable`, same as v1's pre-snapshot behavior. |
| Rollback | Kept, but only for library-sourced exposures: restore any prior library version from history. |
| Promote / fork / personal library (Phase 4) | Promoted from config option to the product's center. Fork becomes symmetric (fork your own skill, not just third-party edits) and is the answer to variants. |
| Exposure ledger (Phase 5) | **Parked.** No cross-project index. Updates propagate by running `sync` in a project when you next work there. |
| Cross-project rollout (Phase 6) | **Parked.** Per-project `sync` replaces it. Revisit only if lazy propagation proves insufficient in practice. |
| Source update policy, `auto-accept-source` (Phase 7) | **Deleted.** Library content is user-owned but each changed hash still needs authored acceptance; external sources remain manual-review; imports are always explicit. |
| Trust classes (5) | Replaced by two independent axes: ownership (`library` or `external`) and exact-hash acceptance (`accepted`, `pending`, `lint_blocked`, or `blocked`). |
| Pin/hold (unassigned in v1) | `pin` ships with `sync` in Phase 5. `hold` is dropped as redundant (pin covers it). |
| Hash rules, hot-path fingerprints, agent-safe confirmation, safety boundary | Carried over unchanged. |

Why the deletions are safe: v1's ledger, snapshots, and source policy all existed to
answer "how do I manage versions of things whose canonical home is elsewhere and might
move?" Once owned skills have one home with real history, those questions collapse into
ordinary library operations, and the unowned case is deliberately kept small.

## Design Principles

These are the properties that make Skillager worth keeping. Every phase below must
preserve all five. If a proposed feature conflicts with one, the feature loses.

1. **Small.** Skillager is a local CLI over plain files. New state should be measured
   in one directory and a few JSON/YAML files, not services.
2. **Plain files, zero exit cost.** The library is ordinary skill directories in an
   ordinary git repo. If Skillager is uninstalled tomorrow, every skill remains a
   readable, usable directory. Skillager adds an index and a workflow, never a format.
3. **Low stakes.** No operation is load-bearing for the user's projects. Exposed skills
   are plain native skills; agents never depend on Skillager being installed.
4. **Quiet on the agent hot path.** `working` stays read-only, metadata-only,
   non-interactive, and cheap. Agents relay choices; humans decide.
5. **Explicit mutation.** Anything that writes — import, promote, sync, fork, rollback,
   quarantine — is a user-run command with preview and confirmation.

## Not A Skill Hub

The failure mode to fear is not that the library is a bad idea; it is that a
"centralized store" grows gravity: publishing, sharing, discovery of other people's
skills, a server, accounts. There are plenty of those, and Skillager should not become
one. These are permanent non-goals, not deferrals:

- No server, no accounts, no hosted anything.
- No `publish` or `share` commands. No concept of a public library.
- No remote discovery: no registry, index, or search of skills other people have
  published. Local auto-discovery (project folders, environment packages, native
  agent directories, registered collections) is core Skillager and unaffected —
  everything it finds is already on the user's machine by the user's own actions.
- No network operations except git commands the user runs (or explicitly asks
  Skillager to run) against remotes the user configured.
- No skill format of Skillager's own. `SKILL.md` directories in, `SKILL.md`
  directories out.
- The library is singular and personal. Team distribution remains what it is today:
  a git repo registered as a collection, reviewed at the boundary.

The litmus test: **a skill hub is a place to get skills from other people; a library is
a place to keep your own.** Every feature in this plan should pass that test. If a
future proposal fails it, this section is the citation for rejecting it.

## Product Model

### Ownership Classes

Every skill Skillager knows about belongs to exactly one class:

- **Library** (`lib/<name>`): lives in the user's library. User-owned, with full
  lifecycle: edit, accept, promote, history, restore, fork, variants, and sync.
- **External**: lives where it was discovered — project repo, registered collection,
  Python/npm/Cargo package, native agent directory. Exactly today's model: discovered,
  reviewed per content hash, exposed. Drift on its exposures can be kept local or
  quarantined, but there is no promote, rollback, or sync, because Skillager does not
  own the canonical copy.
- **Exposure**: not a skill at all — a generated delivery artifact for a project,
  agent, scope, and mode, with a sidecar recording provenance.

**Lifecycle follows ownership.** This one rule replaces v1's source-policy machinery.
If the user wants lifecycle features for a skill, the answer is always the same:
import it. Repo-owned skills that belong to a team stay repo-canonical on purpose —
their lifecycle is the repo's PR flow, not Skillager's.

### Ownership Is Not Acceptance

Ownership answers who controls the canonical source. Acceptance answers whether an
exact content hash may be activated, exposed, or synchronized. They are deliberately
independent:

- A library skill is always user-owned.
- A library skill whose working-tree hash matches its last accepted hash is `accepted`.
- A direct edit creates a new `pending` hash. It remains searchable as metadata but is
  not body-readable, activatable, exposable, or syncable until the user runs the
  authored acceptance flow.
- `library accept`, `import`, `promote`, `fork`, and `library restore` record exact
  accepted hashes only after lint/static checks and explicit user confirmation.
- Lint-blocked or high-risk content still requires the existing audited override and
  reason. Being in the library never bypasses those gates.

This preserves the current content-hash trust boundary without pretending Skillager
can identify who edited a local file. `authored` is ownership/provenance metadata, not
a trust state that automatically approves future bytes.

### The Library

- Default location `~/.skillager/library`, configurable once at `library init`,
  printed by `where`. One library per user.
- Layout is plain skill directories:

```text
<library>/
  skills/
    brainstorm/
      SKILL.md
      ...
    pandas-2/
    pandas-3/
  .skillager/
    library.json        # schema, created_at
    provenance.json     # per-skill: imported_from, lineage
```

- The library is a git repository by default. Git provides history (rollback),
  diffs (promote preview), and off-machine backup/sync via a private remote the user
  manages. Skillager never runs `git push`/`pull` on its own.
- Every successful Skillager-managed library mutation creates a path-scoped commit
  when Git is enabled. Skillager content hashes remain the public version identity;
  Git commit IDs are provenance and recovery mechanics, not substitutes for those
  hashes. Historical lookup verifies reconstructed trees against the requested
  Skillager content hash.
- Git is strongly recommended but not required. Without it, the library still works;
  rollback and version history report `unavailable`.
- Internally, the library is registered as a reserved collection named `lib` so
  discovery, review records, tagging, search, and exposure work through existing
  machinery. The reservation protects its identity and path; it does not grant trust.
  New hashes flow through the streamlined authored acceptance path: still hashed
  exactly, still lint-checked, still refusing lint-blocked or high-risk content without
  the existing audited override.
- Skillager metadata lives under `<library>/.skillager/` (inside the repo, so it
  syncs across machines) and never inside skill directories.

Library content invariant (hard rule, same standing as Not A Skill Hub):

- **Unreviewed external content never enters the library.** Candidate imports and
  `import --refresh` content stay at their origin until review passes. Previews read
  from origin; no staging queue, temporary copy, or refresh cache lives below the
  library root.
- **Owned edits may be pending.** Directly editing an existing library skill changes
  its working-tree hash. That pending body can exist in the ordinary Git worktree, but
  Skillager treats it as metadata-only and unavailable until authored acceptance
  records the exact hash.
- **Only accepted hashes cross a Skillager delivery boundary.** `show --content`,
  activation, exposure, sync, router membership, and promotion outputs must continue
  to require an accepted exact hash. Existing exposures remain at their recorded
  version when the library has pending edits.
- The boundary is anti-accident, not anti-filesystem-access. An agent with unrestricted
  filesystem tools can read the library worktree; policing that is the harness
  permission model's job. Skillager itself never emits or copies an unaccepted body.

### Import

Import is the single trust doorway.

- `import` copies a skill tree into the library, records provenance
  (`imported_from`: source key + content hash + timestamp), runs lint/static checks,
  and — for content not already reviewed — runs the existing review flow at import
  time.
- After import, the copy is `authored`. The user owns it; upstream no longer updates
  it. Provenance exists for attribution and for optional manual diffing later
  (`import --refresh <skill>` shows upstream drift and lets the user merge on their
  own terms; it never auto-applies).
- Import never modifies the origin. Collections remain registered and usable
  un-imported; import is for skills the user wants to own and evolve.

This is why `auto-accept-source` is deleted rather than deferred: there is no longer a
category of "source I trust to change under me." Either you own it (library) or you
review changes (external). A company collection you trust is still one `import
--refresh` away from any update you actually want.

### Version Identity

Carried over from v1, condensed:

- `content_hash` (agent-visible tree hash, excluding sidecars, evidence files, caches,
  symlinks, transient editor files) remains the version identity everywhere.
- Exposures record the `source_hash` they were materialized from; library skills
  additionally have git history, so any prior hash is recoverable.
- Sidecar integrity is reported separately (`sidecar_status`); sidecar bytes never
  fold into `content_hash`.
- Hot-path rule unchanged: `working` uses cheap tree fingerprints
  (`(relative_path, size, mtime_ns)` sets) and only full-hashes on fingerprint miss.
  Fingerprints are advisory; every mutating path computes the full hash and never
  uses fingerprint equality to authorize a write.
- Library history is append-only from Skillager's perspective. Restoring an older
  version creates a new head commit with the old content; Skillager never resets or
  rewrites the user's Git history.

### Latest Is Not Always Best: Pin vs Fork

Two different problems, two different tools, and the distinction is a product rule:

- **Pin (temporal):** "this exposure is deliberately behind." A per-exposure sidecar
  field binding the exposure to a specific library hash. `sync` skips pinned
  exposures and reports them. Pins are the backstop, not the primary mechanism.
- **Fork (structural):** "these are two living variants." Separate library skills
  (`lib/pandas-2`, `lib/pandas-3`), each with its own head, history, and — critically
  — its own name and description. Lineage (`forked_from`: skill + hash) is recorded in
  `provenance.json`.

Variants must be forks, not pins, because selection is the binding constraint at
scale. Agents route on names and descriptions; a pinned hash is invisible to
activation, while `pandas-2` with a description that says "for pandas 2.x codebases"
is legible to both the agent and the user browsing the library. For the same reason,
`fork` requires a description change before the fork is complete: two variants with
identical descriptions are an activation bug by construction.

A pinned exposure is frozen; a forked variant keeps improving. If a fix is needed on
the old lineage, fork it — pin is only for "this project should not move yet."

## User Flows

### Edit An Exposed Library Skill (the core loop)

1. User edits `some-project/.claude/skills/lib-brainstorm/SKILL.md` mid-task, because
   that is where the skill is visible and active.
2. Next `skillager working --agent claude --json` reports a local edit (advisory,
   metadata-only, one line in human output).
3. User (usually via the agent relaying choices) runs `skillager reconcile`.
4. Because the source is `lib/brainstorm`, reconcile offers: **promote** to library,
   keep-local, restore from library, quarantine, or ignore.
5. Promote shows the diff against the library version at the recorded `source_hash`,
   re-runs lint, commits to the library, and offers to update the current project's
   exposure to match. Other projects pick it up on their next `sync`.

Total ceremony for the common case: one command, one diff, one yes.

### Adopt A Third-Party Edit

Same flow, but the source is external (say `superpowers/brainstorm`). Promote is not
offered; **import as a library skill** is: the edited copy enters the library as
`lib/<name>` with provenance pointing at the upstream skill and hash. The upstream
collection is untouched and still available un-forked.

### Two Living Variants (the pandas case)

```bash
skillager fork lib/pandas --as pandas-2 --from <hash-before-pandas3-rewrite>
# fork requires editing the description before completing:
#   pandas-2: "Dataframe patterns for pandas 2.x codebases (pre-copy-on-write)."
#   pandas:   "Dataframe patterns for pandas 3.x."
skillager expose lib/pandas-2 --agent claude --scope project   # in the pandas-2 project
```

Both variants keep evolving independently. A fix to `pandas-2` lands in every
pandas-2 project on its next `sync`. Nothing is pinned; nothing is frozen; the agent
can see which is which.

### Update Propagation

There is no push. Updates propagate lazily:

```bash
skillager sync --agent claude          # in a project; preview by default
skillager sync --agent claude --apply
```

`sync` walks the current project's managed exposures whose source is the library,
compares recorded `source_hash` to the library head, and updates clean targets.
Customized targets are skipped and reported. Pinned targets are skipped and reported.
The mental model: **the library is where truth lives; projects catch up when you next
work in them.** This replaces v1's ledger-backed rollout at a fraction of the
machinery, and matches how a solo user actually moves between projects.

### Rollback

```bash
skillager reconcile rollback lib/brainstorm --yes
```

For library-sourced exposures: restore the exposure to its recorded `source_hash`,
materialized from library git history. A dirty target is saved to quarantine first.
`reconcile <skill> --json` reports whether history is available. For external
exposures, rollback reports `unavailable` with the suggestion to re-expose from the
source. (This is the v1 snapshot cache's entire job, done by git, for the skills that
matter.)

### Surprising Change

Unchanged from v1: `reconcile quarantine` moves the target out of the agent-visible
path into a recoverable project-local quarantine location, blocks that hash for that
exposure only, and touches nothing upstream. Deletion requires `--confirm-delete`.
`review block` remains the broader escalation.

## Commands

New:

```bash
skillager library init [--path <dir>] [--no-git]
skillager library status [<skill>] [--json]
skillager library new <name>
skillager library accept <skill> [--yes]
skillager library history <skill> [--json]
skillager library diff <skill> [--from <hash>] [--to <hash>]
skillager library restore <skill> --to <hash> [--yes]
skillager import superpowers/brainstorm [--as <name>]
skillager import --refresh lib/brainstorm        # manual upstream diff, never auto
skillager fork lib/pandas --as pandas-2 [--from <hash>]
skillager sync [--agent <a>] [--apply]
skillager pin lib/pandas [--to <hash>] / skillager unpin ...
skillager where lib/brainstorm                   # canonical path, exposures, hashes
skillager edit lib/brainstorm [--open]           # prints canonical path; --open launches $EDITOR
```

`library new` is additive. The existing project-oriented `skillager new` contract
does not silently change during the pivot.

Extended (v1 shapes carried over):

```bash
skillager working --agent <a> --json     # + exposure_changes block, advisory only
skillager reconcile [<skill>] [--json]   # read-only view
skillager reconcile keep-local <skill> --yes
skillager reconcile quarantine <skill> --yes
skillager reconcile repair <skill> --yes             # generated stubs/routers only
skillager reconcile rollback <skill> --yes       # library-sourced only
skillager reconcile promote <skill>              # library-sourced only
skillager reconcile import <skill> --as <name>   # external local edit -> library
```

Conduct rules carried over verbatim from v1: `working` is read-only and
non-interactive; mutating `reconcile`/`sync`/`import`/`fork` commands preview and
require interactive confirmation or `--yes`; agents pass `--yes` only after the user
has chosen; destructive deletion requires a stronger flag than quarantine.

The `working --json` `exposure_changes` shape, drift-state precedence rules
(`customized` / `customization_decision` / `customized_hash`), and minimal-noise
agent behavior are adopted from v1 unchanged, with one addition: each item carries
`ownership: "library" | "external"` so the agent can name the right next action.

## Data Model

### Drift States

MVP states unchanged from v1: `current`, `local_edit`, `kept_local`,
`target_missing`, `blocked`, `sidecar_error`, `unmanaged`.

One source-aware state becomes cheap enough to keep instead of defer: `behind`
(clean target, library head is newer). Resolving it requires only reading the local
library index — no collection refresh, no network — but it still stays out of the
`working` hot path until profiled; `sync` and `reconcile` compute it. `working` may
gain a `behind` count later behind the existing fingerprint short-circuit, only if it
proves cheap in practice.

`source_needs_review` and `source_missing` are deleted along with source policy.

### Sidecar Extensions

Additive, old sidecars stay valid:

- `ownership` (`library` | `external`)
- `materialized_fingerprint`, `customized_hash`, `customized_fingerprint`,
  `customization_decision`, `customized_at` (v1)
- `exposure_blocked_hashes`, `quarantine_path`, `quarantined_at` (v1)
- `pin_hash` (v2, replaces v1 pin/hold)

Dropped from the v1 list: `exposure_uid`, `source_policy`, `version_label`,
`snapshot_hash`, `lineage` (lineage lives in the library's `provenance.json`, not per
exposure).

The sidecar remains the sole source of truth for per-exposure state. There is no
ledger for it to compete with.

### Library Metadata

`<library>/.skillager/provenance.json`, keyed by skill name:

```json
{
  "schema": "skillager.library-provenance.v1",
  "skills": {
    "pandas-2": {
      "forked_from": { "skill": "pandas", "hash": "abc123" },
      "created_at": "2026-07-02T00:00:00Z"
    },
    "brainstorm": {
      "imported_from": { "source": "collection:superpowers", "skill": "brainstorm", "hash": "def456" },
      "imported_at": "2026-07-02T00:00:00Z"
    }
  }
}
```

Review records for library skills work exactly as for any authored source: exact
hashes, streamlined acceptance, lint gates intact.

## Engineering Findings Feeding The Implementation Plan

A codebase review (2026-07) found structural debt that gets strictly worse once the
library lands. These findings inform the authoritative execution sequence in
`PERSONAL_SKILL_LIBRARY_IMPLEMENTATION_PLAN.md`; they are not all gates on the first
library release. In particular, locking and a narrow command/service seam come first,
while the broad command split and facade cleanup proceed incrementally.

### G1: Incremental Index (the critical one)

`working` currently rebuilds the world on every invocation: `cmd_working` →
`_build_visible_skill_view` → `build_index(persist=False)` runs full discovery,
then per skill a full `content_hash` (every byte of the tree), a full scan, and a
full lint — and throws the result away. The `index.json` cache in
`skills/index.py` is bypassed on this path. Cost is linear in total skill bytes,
on the flagship agent-hot-path command, and the library's explicit goal is to grow
skill count. Without this fix, the pivot's success degrades its own front door.

- Add fingerprint-gated index reuse: persist per-skill tree fingerprints
  (`(relative_path, size, mtime_ns)` sets, same rules as `content_hash`) alongside
  the index; rehash/rescan/relint only entries whose fingerprint misses.
- This is the same mechanism the plan's Hot-Path Rule requires for exposure drift
  (Phase 2). Build it once, share it: one fingerprint helper serving both the index
  and the drift scan.
- Fingerprints remain advisory: mutating paths and review/approval always compute
  full hashes.
- Tests: fingerprint hit skips hashing (observable via timing or instrumentation
  hooks), fingerprint miss rehashes, mtime-only touch invalidates cheaply, index
  results identical to a cold build.

### G2: Split The Command Monolith

`commands/impl.py` is 6,863 lines and 343 functions — nearly half the codebase —
holding parser construction, every command handler, and shared helpers.
`exposure/impl.py` and `catalog/impl.py` repeat the pattern at smaller scale: the
packages exist, but each contains a single `impl.py`, a reorg that stalled at the
directory step. The v2 phases add roughly eight commands; landing them in this
file is how it becomes 10k lines.

- Split `commands/impl.py` into per-command modules (`commands/working.py`,
  `commands/review.py`, `commands/expose.py`, ...) plus `commands/parser.py` and
  a small `commands/shared.py` for genuinely cross-command helpers.
- Behavior-preserving only: the 261-test behavior suite is the safety net and the
  reason now is the right time — refactor while green and frozen, before new
  commands are born into the monolith rather than migrated out of it later.
- New v2 commands each get their own module from day one.

### G3: Delete The Facade Modules

`cli.py`, `trust.py`, `paths.py`, `discovery.py`, `materialize.py`, and similar
top-level modules are dynamic shims: they copy attributes from the real modules at
import time and install a custom module class intercepting `setattr` so
monkeypatching resolves across the alias. Since the CLI is the declared public API
(AGENTS.md), these Python-level facades serve only internal and test imports.
They are exactly the indirection you do not want under a large feature — a patch
applied to the wrong alias fails silently.

- Update internal and test imports to the real module paths; delete the shims and
  the `_FacadeModule` machinery.
- Do this together with or immediately after G2, since both touch import paths.

### G4: Cross-Process Locking

State writes are atomic (temp file + fsync + `os.replace`) but read-modify-write
sequences on `trust.json` and friends race freely across processes. Tolerable
today; not once promote/sync/import write to one shared library and a git repo,
with multiple concurrent agent sessions being the realistic usage pattern. The v1
plan's test list demanded "concurrent reconcile commands serialize per target" but
never assigned the mechanism.

- Add a small file-lock helper (lock file + `flock`-style advisory lock, timeout
  with a clear error) in `state/`.
- Wrap read-modify-write state updates and, later, all library-writing commands
  (promote, import, fork, sync, rollback) in per-resource locks.
- Tests: two concurrent mutators serialize; a held lock times out with a clear
  message rather than corrupting or deadlocking.

### G5: Spend The JSON Break Once

`working.v1` already carries dead hardcoded fields (`auto_approved_project_count:
0`, `auto_approved_project_skills: []`, `new_external_review_count: 0`,
`new_external_review: []`). The migration stance permits breaking JSON changes
now. Cut the dead fields and bump to `skillager.working.v2` in the same release
that adds `exposure_changes` (Phase 2) — one documented break instead of two.

### G6: Small Repairs (batch opportunistically)

- `uv run pytest` silently picks up a system pytest and fails with
  `ModuleNotFoundError`; add pytest to the dev dependency group (it runs unittest
  suites fine) or make the failure loud. Contributor trap, cheap fix.
- Relax aggressive dependency floors (`rich>=15`, `packaging>=26.2`) to the oldest
  versions actually required; a `uv tool install`-distributed CLI should be a
  polite resolver citizen.
- Exclude the product and implementation plan documents from the wheel's forced
  `docs/` include; today internal plans ship to PyPI.

## Product Milestones

These capability groups explain product dependencies and reversibility. The detailed
implementation order, including work that intentionally crosses these groups, lives in
`PERSONAL_SKILL_LIBRARY_IMPLEMENTATION_PLAN.md`.

### Phase 0: Library Foundation

- `library init`: create the directory, `git init` unless `--no-git`, write
  `library.json`, and register it as the reserved `lib` collection. Registration
  grants identity and ownership, not acceptance.
- `library status`, `library new`, `library accept`, `where`, and `edit` establish the
  direct authoring loop without changing the existing project-oriented `skillager new`.
- Almost no new machinery: registration, discovery, review, and exposure reuse
  existing collection paths.
- Keep the schema artifact-agnostic: library provenance records carry an
  `artifact_kind` field (only value for now: `skill`), and no new code path bakes
  `SKILL.md` into identity, hashing entry points, or provenance in ways that would
  block managing other agent-context artifacts later (see Parked: agent
  definitions and packs). Zero speculative machinery — just don't close the door.
- Tests: init idempotence, no-git degradation, library skills discoverable and
  exposable like any collection, authored acceptance records exact hashes,
  lint-blocked content still refused.

### Phase 1: Import

- `import` from any discovered skill (collection, package, project, native dir) and
  from an exposed edited copy (via `reconcile import`).
- Provenance records; review-at-import for unreviewed content; origin never modified.
- `import --refresh`: manual upstream diff for previously imported skills.
- Because import inherits the review flow, do an ergonomics pass over review's
  listing/filtering alongside this phase: at four-digit skill counts, the import
  gate is only as pleasant as `review`'s selection UX. `--bulk-approve` and
  `--yolo` already exist; the gap to check is filtering and preview at scale.
- Tests: import from each source type, provenance written, review gate enforced,
  refresh shows diff without writing, sidecar exclusion from imported trees.

### Phase 2: Drift Detection In `working`

v1 Phase 1, adopted as written: exposure scanning helper, fingerprint short-circuit
(reusing the G1 fingerprint helper), `exposure_changes` in `working --json`,
advisory-only semantics, quiet human output, no source resolution on the hot path,
body-leak tests. Plus the `ownership` field.

This phase's release carries the one planned JSON break (G5): dead `working.v1`
fields removed, schema bumped to `skillager.working.v2`, `exposure_changes` added.

### Phase 3: Reconciliation

v1 Phase 2, adopted as written: read-only listing, `keep-local`, `quarantine`,
sidecar-recorded decisions, `--yes` gating, isolated-home tests. Plus
ownership-aware next actions (promote offered only for library sources).

### Phase 4: Versions, Promote, And Restore (closes the loop)

- `library history`, `library diff`, and `library restore` make content-addressed Git
  history an explicit user workflow. Restore creates a new head; it never rewrites
  history.

- `reconcile promote`: diff against the library version at the recorded
  `source_hash`; fast-forward case (library head unchanged since exposure) commits
  directly; diverged case shows both diffs and refuses until the user resolves —
  no silent merges in the first cut.
- `reconcile rollback` from library git history; dirty targets quarantined first.
- Refuse library writes when the library repo has conflicts or unrelated staged
  changes; report clearly instead of guessing.
- Tests: fast-forward promote, diverged refusal, rollback after head moved, dirty
  target preservation, promote excludes sidecars/evidence, no-git library reports
  `unavailable`.

**The authoring loop — edit anywhere, promote once, restore anytime — is complete
here. This is the pivot's MVP and the main decision checkpoint.**

### Phase 5: Fork, Sync, Pin

- `fork` with lineage recording and mandatory description edit.
- `sync` (preview-first, `--apply`, clean-only, skips customized and pinned,
  current project only).
- `pin` / `unpin` per exposure.
- Tests: fork lineage, description-change enforcement, sync skips
  customized/pinned/dirty, sync updates clean targets, variant exposure and
  activation metadata distinctness.

### Parked (explicitly not planned, revisit only on demonstrated need)

- Cross-project rollout and any exposure ledger.
- Any `behind` reporting inside `working`.
- Automated upstream tracking of imported skills.
- Multi-library, shared libraries, or any team-distribution features beyond
  today's collections.
- **Agent definitions and packs.** Sub-agent definitions have the same context
  economics as skills: every exposed definition's name and description is injected
  into the orchestrator's system prompt each session, while the body loads only on
  spawn (into the subagent's own context). The same selection, curation, review,
  and drift problems apply, so they are a natural future library artifact — with
  one upgrade: a common authoring pattern is the skill-agent **pair** (the skill
  tells the orchestrator how to invoke and validate; the agent carries the
  execution process). Pairs are one versioned unit — the skill references the
  agent by name, so promote/fork/sync must move both and rewire the reference —
  which argues for **packs** (multi-artifact library entries) rather than agents
  as coincidental second entries. Differences to respect when this is picked up:
  review stakes are higher (an agent definition grants tools and permissions —
  closer to a permission grant than a document, so lint needs agent-specific
  rules like flagging `tools: *`); exposure is native-only (no stub/router
  equivalent) and target formats vary more per harness. Scope litmus for any
  future artifact kind: *lives in an agent-visible directory, costs context, is
  prose or config the model consumes, evolves through use.* Skills and agent
  definitions pass. Hooks and settings files fail — arbitrary shell execution and
  harness config are a different safety claim Skillager must not make.

## Reversibility And Delivery Checkpoints

The direction is accepted, but delivery remains staged so every release is useful and
recoverable:

- **Infrastructure is behavior-preserving.** Locking, atomic state mutation, and the
  incremental index improve the current CLI independently.
- **Foundation and import are additive.** The library is a reserved collection; the
  overlay model is untouched. A user can stop using it and retain an ordinary Git
  repository of plain skill folders.
- **Phases 2–3 are v1.** They were justified independently of the pivot and survive
  either decision.
- **Phase 4 is the commitment.** Promote/rollback make the library the place your
  skills live. Docs and README re-center on it. This is the checkpoint to sit on.
- **Phase 5 is elaboration**, cheap once Phase 4 has proven itself.

Signals that the delivered workflow is succeeding:

- You stop hand-copying skill edits between projects.
- New skills default into the library without deliberation.
- You reach for `import` when a third-party skill needs your changes.

Signals that later lifecycle work should pause for redesign:

- You keep authoring in project repos and `import`/`promote` feel like ceremony.
- The library becomes a junk drawer you do not trust, while collections remain
  where real curation happens.
- You want to share the library more than you want to use it — the hub itch. If
  that happens, stop and re-read Not A Skill Hub before building anything.

## Adversarial Review And Pre-Mortem

Carried-over v1 failure modes that still apply: `working` becoming a mutation
surface; exposed artifacts confused with source (promote must diff, lint, and
exclude sidecars/evidence); rollback/quarantine losing user work (quarantine before
replace, always); stub/router edits gaining false authority (repair/keep-local only,
never promote); additive JSON changing readiness semantics (drift stays advisory).

New or reshaped for v2:

- **Hub creep.** The library grows sharing/publishing gravity feature by feature.
  Mitigation: the Not A Skill Hub section is normative; features failing its litmus
  test are rejected by citation, not debate.
- **Promote clobbers concurrent library edits.** User edited the library copy
  directly and an exposed copy in parallel. Mitigation: promote is fast-forward-only
  in the first cut; divergence refuses with both diffs shown.
- **Library repo state surprises Skillager.** Merge conflicts, mid-rebase, unrelated
  staged changes, or a dirty tree at promote time. Mitigation: library-writing
  commands check repo state first and refuse with instructions rather than
  committing around the user.
- **Multi-machine divergence.** Two machines promote different edits; user syncs the
  library repo and hits a conflict. Mitigation: this is deliberately git's problem —
  Skillager refuses to write through conflicts and otherwise stays out of it. No
  sync engine.
- **The library becomes a review bypass.** If import ever stages unreviewed
  content under the library path — a temp dir, a pending queue, a refresh cache —
  the well-known library location turns into the easiest place to find and load
  unapproved bodies, defeating the review gate at its strongest point.
  Mitigation: external candidate content stays at origin, and tests assert nothing
  appears under the library path before review passes. Direct owned edits may be
  present as pending worktree content, but Skillager refuses to emit or copy them.
- **Import provenance rot.** Upstream collection renames or vanishes; `--refresh`
  can't resolve. Mitigation: refresh degrades to "provenance unresolvable," the
  library copy is unaffected — it was owned from the moment of import.
- **The library becomes precious without backup.** Mitigation: `library init`
  prints a one-line nudge to add a private remote; `doctor` reports a library with
  no remote and uncommitted history. Nothing automatic.
- **Fork sprawl.** Dozens of stale variants degrade selection quality — the exact
  problem variants were meant to solve. Mitigation: lineage makes families visible
  in `where`/`list`; culling stays a manual, human act.

## Migration Stance

Unchanged spirit from v1 — clean semantics over compatibility shims, one-way
migrations, no data loss — with v2 specifics:

- Existing sidecars remain valid; `ownership` is inferred as `library` only when the
  source resolves to the reserved registration's `library_id`, else `external`.
- Existing exposures from external sources keep working untouched. Users bring
  skills into the library one `import` at a time; there is no bulk migration step
  and none should be encouraged.
- Existing collections keep their current semantics. Nothing defaults to the
  library.
- Exposures created before Phase 4 have valid `source_hash` provenance and gain
  rollback automatically wherever library history contains that hash.
- The accepted v2 document records the v1 dispositions; the superseded v1 document is
  deleted so two apparent plans cannot drift.

## Safety Boundary

Unchanged from v1: Skillager does not claim to prove who changed a file. Its job is
to prevent accidental use of unreviewed third-party skill bodies, make provenance
and versions visible, never silently overwrite local work, and provide rollback,
quarantine, and block when changes are surprising. Metadata commands never print
skill bodies. The import gate is where third-party trust is decided, and it is the
only place.

## Test Strategy

Behavior tests via public CLI subprocesses with isolated temp `HOME`, library,
project, catalog, and cache directories. All v1 scenarios for drift detection,
reconcile semantics, body-safety, fingerprint short-circuits, and `--yes` gating
carry over. New core scenarios:

- `library init` with and without git; degraded no-git behavior.
- Import from each source type writes provenance and enforces review at the gate.
- Promote fast-forward commits exactly the exposed content minus sidecars/evidence.
- Promote against a moved head refuses and shows both diffs.
- Promote/import refuse on conflicted or dirty-in-the-wrong-way library repos.
- Rollback restores a prior hash from history after the head moved; dirty target
  quarantined first.
- Fork records lineage and refuses to complete without a description change.
- Sync updates clean, skips customized/pinned/dirty, current project only.
- Two variants expose distinct activation metadata.
- A deleted upstream does not affect imported library copies.
- An import that fails or awaits review leaves nothing under the library path;
  `import --refresh` diffs read from origin without staging into the library.
- Every body Skillager emits or copies from `<library>/skills/` matches an accepted
  exact hash; pending direct edits remain metadata-only.
- Symlinks and path traversal cannot write outside the library or approved targets.

## Accepted Initial Decisions

- Default library path: `~/.skillager/library`, configurable once at init and always
  discoverable through `library status` and `where`.
- Namespace: fixed, reserved `lib` prefix.
- Git plumbing: shell out to the system `git`; never push, pull, reset, or rewrite
  history automatically.
- Import granularity: one skill at a time until real usage demonstrates a safe need
  for bulk import.
- Creation compatibility: add `skillager library new`; preserve `skillager new` as the
  existing project-local authoring command.
- Fork editing: opening descriptions in `$EDITOR` is optional convenience, not part of
  the first implementation contract.
