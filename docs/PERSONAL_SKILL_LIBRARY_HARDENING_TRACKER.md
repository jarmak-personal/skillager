# Personal Library Hardening Tracker

Status: closure complete; final implementation and UX commits are release-check clean,
fresh no-context workflows are ready, and independent closure personas returned SHIP
Source: three review passes by agentic-power-user, senior-engineer/maintainer, and
skeptical security-reader personas, followed by three corrective worktrees through the
current branch
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
- [x] Keep repository-controlled tag metadata from selecting the user catalog, reserved
  library registration, or approval authority. Portable catalog hints require a
  matching user-owned per-project binding; explicit CLI/environment configuration wins.
- [x] Refuse import when distinct discovered roots claim the requested external display
  ID. Preserve explicit access to unambiguous collision-suffixed IDs.
- [x] Remove scanner-matched instruction excerpts from metadata-only status, accept,
  import, and restore JSON. Findings may expose rule codes and locations only.
- [x] Protect every managed exposure target entry, including files excluded from the
  canonical skill hash. Refresh and removal require explicit force for any local entry,
  and removal confirmation binds the complete target plus sidecar.
- [x] Prompt for accept/import/restore only when both input and output are interactive;
  captured-output commands and the prescribed suite must never hide a prompt.
- [x] Keep verbose `list`, `search`, and `show --full-json` body-safe. Scanner matches
  remain codes/locations and internal approval keys are not public metadata.
- [x] Include normalized executable state in advisory discovery fingerprints so a
  mode-only source change cannot trap import in a permanently stale preview loop.
- [x] Require host-valid `name` and `description` frontmatter for every native
  projection, not only library-owned sources; manifest-free external sources remain
  usable through stub, router, and on-demand paths.
- [x] Never silently replace a different managed projection whose ID normalizes to the
  same host slug. Allocate a deterministic alternate and fail closed if it is occupied.
- [x] Preflight Git-backed initialization metadata against ignore rules and roll back
  partial files/staging on commit failure so retry is clean and history never claims
  missing identity/provenance.
- [x] Bind imported-skill acceptance to relevant shared provenance state and refuse
  unrelated staged or working provenance edits.
- [x] Recompute exact current source hashes before readiness/search availability for
  project and collection inventory. Preserved size/mtime cannot reuse old approval,
  and added/removed collection skill roots invalidate the cached collection view.
- [x] Give direct, router, and first-party Working projections one kind-aware host
  namespace. Cross-kind collisions preserve both artifacts, while Working's reserved
  target refuses replacement even with force.
- [x] Refuse repository-controlled symlink/non-file project tag state and make tag
  mutations locked and atomic.
- [x] Treat skipped or paused interactive setup as incomplete: no Working install,
  restart handoff, or completion claim while selected review remains unresolved.
- [x] Do not classify supported `.skillager/tags.json` and its exact regular lock
  artifact as obsolete in-tree state. Preserve the readiness gate for true legacy or
  unexpected entries.
- [x] Make canonical content identity unambiguous. Version-2 hashes domain-separate and
  length-frame canonical paths, payloads, file boundaries, and executable state;
  ambiguous legacy approvals fail closed to one-time review.
- [x] Refuse project exposure through symlinked/non-directory agent skill bases or
  outside-project ancestors before lock or target creation, for every projection kind.
- [x] Refuse internal and escaping library skill symlink aliases before `library new`
  writes a draft; a failed command must not mutate the alias destination.
- [x] Suppress scanner-triggering derived descriptions on compact library metadata
  surfaces, not only matched scanner finding messages.
- [x] Authenticate canonical sidecar metadata and classify any raw sidecar field or
  formatting edit as local customization requiring explicit force.
- [x] Bind router activation to one agent's actual managed exposure and refuse
  disagreeing same-slug sidecars rather than unioning their memberships.
- [x] Close the removal confirmation race by atomically detaching and re-hashing the
  confirmed target before deletion, restoring and refusing a changed snapshot.

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
- [x] Keep a missing/moved optional library nonblocking while directing Working through
  `library status` and structured relocation requirements, never an impossible accept
  command or placeholder-bearing executable argv.
- [x] Compare managed native, stub, and router sidecar source hashes with current
  approved hashes. Report clean stale projections as `source_update`, exclude them from
  current/exposed inventory, and provide exact re-expose guidance without auto-sync.
- [x] Keep doctor ready for unrelated work while surfacing structured resolving actions
  for pending owned changes; never publish an executable placeholder override reason.
- [x] Keep `working.next` exclusive to required readiness work and retain established
  zero-value fields in the version-1 JSON contract.
- [x] Make `expose --list` classify current-project source freshness rather than calling
  a clean but stale projection exposed/current.
- [x] Preserve the caller's JSON output mode in generated accept/import/restore/removal
  confirmation commands and keep the confirmation token last.
- [x] Return the newly created restore commit as the accepted/current/head receipt,
  rather than relabeling the selected historical commit.
- [x] Require an actual managed router exposure for `--from-router`; a similarly named
  attached tag alone does not authorize activation.
- [x] Make repeated setup summaries use full-scope manifest-free and prior-block counts,
  include existing verified router/native exposures, and avoid a redundant Working
  installation prompt when the selected artifact is already current.
- [x] Suppress setup router suggestions when that tag's exact approved membership is
  already exposed through a current, unmodified router for the selected agent.
- [x] Build native, stub, router, and Working projection refreshes as verified
  same-filesystem candidates with rollback on ordinary write/install/verification
  failure.
- [x] Keep accepted Git-backed skill history and restore available when its live
  canonical directory is accidentally missing, without adding delete lifecycle or an
  empty recovery commit.

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
- [x] Keep import destination identity stable across preview/result (`id`, slug/name,
  and retained frontmatter display name are distinct) and make restore receipts report
  post-restore accepted/current/head truth.
- [x] Use the actual `skillager-<tag>` router slug in the primary README activation
  example and bind the example to its agent.
- [x] Document the fail-closed version-2 hash/sidecar migration instead of promising
  that older approvals or projections remain silently current.

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
- [x] Normal suite, Python 3.13 release check, and fresh no-context setup workflow pass
  against the final closure commit and isolated catalog state.
- [x] A repository catalog pointer cannot substitute user-owned trust state.
- [x] Duplicate external IDs refuse import without writing library content.
- [x] Scanner-triggering body sentinels remain absent from status, accept, import, and
  restore metadata.
- [x] Captured-output preview commands do not prompt even when stdin reports a TTY;
  behavior subprocesses close stdin and have a timeout.
- [x] Moved-library Working output stays ready for unrelated work and gives only
  resolving status/relocation guidance.
- [x] Excluded files before refresh/removal are preserved without force, and files added
  or changed after a removal preview invalidate its token.
- [x] Full list/search/show JSON with scanner-triggering bodies contains no matched text
  or internal approval keys.
- [x] Mode-only external changes produce a fresh import hash/token and a succeeding new
  preview rather than an endless stale-preview failure.
- [x] Doctor reports exact `library accept` actions for pending owned mode/content
  changes while remaining exit-zero ready for unrelated work.
- [x] Accepted source changes mark native, stub, and router projections stale, remove
  them from current inventory, and become current only after explicit re-exposure.
- [x] Colliding direct projection slugs preserve both skills deterministically and an
  occupied fallback refuses even with force.
- [x] Git-ignored required metadata fails before writes, initialization commit failure
  rolls back cleanly, and unrelated staged provenance never enters an acceptance commit.
- [x] Same-size/restored-mtime edits revoke project and collection searchable
  availability; new collection skill roots enter owner review without manual refresh.
- [x] Working/router, direct/router, and tag/explicit-router host collisions preserve
  both artifacts in either creation order; occupied fallbacks and reserved Working
  replacement fail closed even with force.
- [x] Project tag symlinks/non-files cannot redirect writes, failed replacements retain
  the prior file, and concurrent additions are not lost.
- [x] Skipping or pausing interactive setup immediately leaves Working review-needed
  and produces only the resolving setup command.
- [x] Router activation before exposure refuses, while activation through an allocated
  alternate router slug succeeds.
- [x] With default XDG project state, setup → tag add → tag show → router exposure →
  Working stays ready while `.skillager/tags.json` and its real lock exist; adding a
  true legacy `.skillager/trust.json` still makes Working non-ready.
- [x] Repeated setup reports full-scope blocked/manifest-free counts, keeps its public
  selection/schema unchanged, counts an existing router, and skips reinstall prompting
  for current Working.
- [x] A current router is not offered again by repeated setup; source, membership, or
  local target drift keeps the explicit re-exposure path discoverable.
- [x] Executable-marker bytes and shifted file boundaries cannot collide with canonical
  hashes; directory and reconstructed Git-tree hashes still agree.
- [x] Project projection roots that are symlinks/non-directories create no outside
  files; valid legacy in-project native roots remain usable.
- [x] Internal/escaping library draft aliases fail before either alias target receives
  a `SKILL.md`.
- [x] Scanner-triggering frontmatter descriptions remain absent from status, accept,
  import, restore preview, and restore receipt metadata; scan coverage counts survive.
- [x] Editing only a managed sidecar produces `local_edit`, blocks ordinary refresh,
  and requires a force removal preview.
- [x] Codex and Claude same-slug routers authorize only their own member sets; an
  unspecified multi-agent slug fails as ambiguous.
- [x] A target changed after removal confirmation is restored and refused after the
  atomic detach/re-hash step.
- [x] Stub, router, and Working refresh failure injection at candidate write, install,
  and final verification preserves the prior clean target.
- [x] Deleting an accepted Git-backed skill leaves body-safe status/history available;
  restore recovers it as accepted/current and refuses a newly occupied or symlink target.

## Fresh User Workflow Result

A fresh no-context worker ran committed checkout `8a9c11b` through its branch-local
Skillager 0.8.1 executable in an ordinary temporary directory with isolated HOME and
XDG config/cache/data/state. Both live repositories from the bundled workflow cloned
without retry. Setup discovered 49 manifest-free skills in place (`agent-workflows=28`,
`vibespatial=21`), approved 41 exact hashes, explicitly blocked 8 warned skills, skipped
none, and collapsed the available set to 33 Codex-facing choices. Initial Working was
ready with an empty required `next` block.

Generated Working guidance led from the user's GIS/Python goal through focused metadata
search to a six-member `vibespatial-gis-dev` tag and one compact router at
`skillager-vibespatial-gis-dev`. That exposed a real integration defect: the supported
tag file's lock directory was misclassified as legacy in-tree state. The worker could
recover and finish ready with 6 routed choices and 27 on demand, but only after an
unacceptable manual tag-directory move.

A second fresh no-context worker then ran committed checkout `6c931fd` with the same
isolation and live repositories. It discovered all 49 manifest-free skills without
retry, approved 42, explicitly blocked 7, skipped none, and produced 34 Codex-facing
choices. Working guided the GIS/Python goal into a four-member `spatial-python` router.
The supported `.skillager/tags.json` and lock remained in place, Working stayed ready,
and repeated setup was fully non-interactive: it reused 42 approvals, reported all 7
blocked skills and both complete manifest-free collection counts, counted the existing
router's 4 exposed choices, and described Working as current without a prompt. Final
Working remained ready with 4 routed choices and 30 on demand.

That clean rerun revealed one smaller inconsistency: repeat setup printed an exposure
suggestion for the already-current router. The final corrective change suppresses only
an exact, target-current router; stale membership, source hashes, or local target drift
remain actionable.

A third no-context worker ran committed checkout `7208f2b` after that correction. It
again discovered all 49 live manifest-free skills without retry, resolved every review
decision (47 approved, 2 blocked), stayed ready, curated an eight-member
`vibespatial-gis-python` router, and completed repeated setup non-interactively. The
supported project tag state remained in place; all collection/block/exposure counts
were retained; Working was current; and the existing router was not suggested again.

The final maintainer and skeptical-product reviews then found late integrity issues in
hash framing, project/library symlink boundaries, compact frontmatter summaries,
sidecar edits, concurrent removal, and cross-agent router authorization. Those are now
regression-covered along with the two strongest recovery follow-ups (atomic projection
refresh and missing accepted-skill restore).

A fourth no-context worker ran committed integrity checkout `a5aa533`. It again found
all 49 live manifest-free skills, fully resolved review, stayed ready, used metadata
search to choose one narrow GIS stub, and completed repeat setup without losing or
resuggesting current state. It also smoke-tested the version-2 personal-library path:
`new` → exact-token `accept` → `status` → verified `history` completed with one accepted
current version. Both the skeptical product review and the senior-maintainer/power-user
review returned SHIP with no functional release blockers. They agreed the public
surface is appropriately limited to ownership, exact acceptance, history/diff/restore,
import, and deliberate exposure; deferred sync/fork/reconcile/pin expansion remains
out of scope.

Those reviews identified two remaining setup-output niggles rather than lifecycle
gaps. Final UX commit `cac6f53` clarifies that an empty native suggestion set means no
always-on project-native recommendation while approved skills remain usable through
stub/router/on-demand paths. It also replaces the repeated twelve-item generic stub
list with a compact on-demand count and search/list guidance once Working is already
current. Focused setup coverage passed 55 tests; the normal suite passed 395; and the
Python 3.13 release check passed Ruff, both mypy targets, 395 core tests, 13 linter
tests, both builds/entrypoints, wheel smoke, and whitespace validation.

A final no-context worker then ran `cac6f53` with isolated HOME/XDG state and both live
repositories. It approved all 49 exact hashes, curated a four-member `gis-python`
router from the required GIS/Python goal, and finished ready with 4 routed Codex choices
and 35 on demand. Repeat setup preserved the current router and Working artifact and
printed only the compact on-demand summary. The worker chose standalone `review approve`
after leaving initial setup, so Working correctly diagnosed its still-missing artifact;
the documented `doctor --agent codex --fix` recovery completed successfully. The fully
interactive setup path had already completed without that detour on `a5aa533`.

Search results still include some weak low-score matches. That remains a separate
discovery-ranking opportunity, not evidence for adding lifecycle surface or weakening
the exact-hash gate.
