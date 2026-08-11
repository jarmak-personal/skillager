# Personal Library Hardening Tracker

Status: complete; retain through branch review, then archive or remove
Source: agentic-power-user, senior-engineer, and skeptical-reader reviews of
`d75e926..318d832`
Created: 2026-08-10

This file prevents review findings from being lost during the hardening cycle. Remove
it after every accepted item has a regression test and every deferred item has a durable
home in normal product documentation.

## P0 — Release Blockers

- [x] Bind non-interactive `library accept`, `import`, and `library restore`
  confirmation to the exact previewed state. Acceptance must bind the skill hash;
  import must bind source identity, source hash, and destination; restore must bind the
  selected historical version and current tree.
- [x] Never place executable placeholders such as `<why>` in `next_command_argv`.
  Return structured required inputs until a real override reason is supplied, and
  preserve supplied override arguments in confirmation commands.
- [x] Keep pending or edited personal-library skills out of external-review readiness
  gates. `working`, `doctor`, and `show` must point owned changes to `library accept`
  without blocking unrelated approved skills or creating a `setup` loop.
- [x] Refuse to remove a locally edited, partial, malformed, or otherwise non-current
  managed exposure without explicit discard authorization. Removal must be previewed
  and confirmation-bound.
- [x] Make accepted content identity cover executable-mode changes, or narrow the
  integrity claim and prevent mode-only changes from crossing exposure. The selected
  design is to include normalized executable bits in the canonical content hash.
- [x] Ensure native projections are valid host skills. New drafts must contain required
  `name` and `description` frontmatter; native exposure must refuse invalid source
  format rather than reporting success.

## P1 — Readiness And Exposure Correctness

- [x] Revalidate personal-library hashes read-only during `working`. Edited or missing
  canonical sources must not remain in available inventory, while optional library
  problems remain advisory for unrelated work.
- [x] Treat legitimate discovered `.agents/skills` sources as native inventory, not as
  unmanaged Skillager drift. `list`, `working`, and exposure counts must agree.
- [x] Make all exposure dry-runs filesystem-pure, including lock-directory creation.
- [x] Target current Codex user scope at `$HOME/.agents/skills`; continue conservative
  discovery/management of legacy `$HOME/.codex/skills` without silently migrating it.
- [x] Make high-risk accept/import/restore preview guidance consistent and executable
  once all required user input exists.
- [x] Correct collection availability so `attached-tag` is present only when a skill is
  actually attached to a project tag.

## P2 — CLI And Documentation Consistency

- [x] Make `--full-json` behavior consistent across `list`, `search`, and `show`.
- [x] Make missing-library recovery visible in plain doctor output while keeping the
  optional library non-blocking for unrelated discovery.
- [x] Correct docs that claim optional-library degradation makes doctor non-ready.
- [x] Update `SECURITY.md` and the safety model: Skillager is a cooperative local
  workflow/approval layer, not a sandbox or same-user authorization boundary; directly
  installed native skills are outside enforcement.
- [x] Remove or correct stale session-retention documentation and make clear that
  metadata-only search describes output boundaries even though approved bodies may be
  read for ranking.
- [x] Document where library bodies, approval state, project tags, setup state, and
  exposure sidecars live and what must be backed up.
- [x] Align release-version language when the release is actually cut; planned 0.9 notes
  may coexist with the current 0.8 package during development.

## Product Decisions — Track, Do Not Smuggle Into Hardening

- [x] Defer an explicit `library delete`/`unregister`/rename lifecycle. Safe manual
  abandonment is documented only for never-accepted drafts; accepted history stays
  recoverable through Git.
- [x] Defer a compact library enumeration view until actual use demonstrates that
  `library status`, `list`, and `search` leave a recurring gap.
- [x] Preserve stable 0.8 `review pin`, tag, and compatibility contracts during this
  hardening cycle; do not add new related surface without usage evidence.
- [x] Keep reconcile, upstream refresh, automatic sync/merge, variants/forks, exposure
  pins, and cross-project rollout out of this release.
- [x] Treat malformed inferred names, long router summaries, and search ranking quality as
  a separate discovery-quality effort rather than re-expanding this safety patch.

## Required Regression Experiments

- [x] Preview → mutate → execute returned argv refuses for accept, import, and restore.
- [x] Risk override preview never emits executable placeholders and records a real reason.
- [x] Pending owned drafts leave unrelated `working` ready and receive a resolving command.
- [x] Mode-only changes invalidate acceptance and cannot be exposed under the old hash.
- [x] Local-edit removal refuses without explicit force and bound confirmation.
- [x] `expose --dry-run` leaves a byte-for-byte/filesystem-path clean project.
- [x] Frontmatter-free native exposure refuses; `library new` produces a host-valid skill.
- [x] Existing project-native skills are counted once as exposed and do not create drift.
- [x] Edited/missing library sources disappear from ready inventory without making the
  optional library a global blocker.
- [x] Global Codex exposure writes the current user-scope directory and legacy inventory
  remains discoverable.
- [x] Normal suite, Python 3.13 release check, and fresh no-context setup workflow pass.

## Fresh User Workflow Result

The final fresh-worker run used Skillager 0.8.1 in an ordinary temporary directory,
cloned `vibeSpatial` and `agent-workflows`, and completed interactive Codex setup without
retries or environmental failures. Setup discovered 60 manifest-free skills in place,
selected 49, approved all 49 after review, and collapsed them to 39 Codex-facing
choices. Generated Working guidance led the agent from the user's GIS/Python goal to
focused search, a four-skill project tag, one compact router, and a clean final working
result with 35 choices left on demand.

The pass also confirmed the intended product boundary: `next` remains reserved for
required readiness repair, while optional search and exposure live under `curation`.
Overlapping inventory facets, scanner-family grouping, inferred-name/search ranking,
and tag follow-up prose remain discovery-quality opportunities rather than reasons to
expand the personal-library lifecycle in this release. `--fresh-project` retention is
already explicit in command help and setup output, so no additional flag or schema was
added.
