# Release Notes

## Unreleased

- Add bounded, complete token-bound local exposure plans for preserved native
  adoption/removal and named router grouping, membership changes and ungrouping.
  Preserve unselected copies and tag curation; report partial recovery explicitly.
- Add exact managed project-library copy selection with `expose --exposure-id`
  for Full/Stub changes, including adopted native locations and ambiguity refusal.

- Preserve identical reusable personal-library copies after source approval and
  setup, including project-only approvals across every effectively discovered
  source type. Keep original scope/evidence and origin files intact.
- Add explicit `library sync --approved` backfill and metadata-only `--status`
  reconciliation, exact-library binding, bounded batches, and public lineage.
  Protect pinned, blocked, customized, and independently authored/imported copies;
  report partial/pending/uncertain effects without automatic retry or exposure.

## skillager 0.9.0

This release recenters Skillager on a canonical personal library for skills the user
owns while preserving discovery and review for external project, environment,
package, native-agent, and collection skills.

Highlights:

- Store approvals, blocks, pins, overrides, and append-only decision history in
  SQLite. Bind library acceptance to verified Git content versions. Keep collection
  metadata and approved-body FTS in separate, rebuildable SQLite caches so search
  reuses indexed content between commands.
- Verify ranked search candidates before filling the result limit, reuse stable
  collection metadata, and refresh newly discovered or newly accepted source
  versions. Preserve exact tree checks, stale-result refill, exposure ranking,
  agent variants, and identity collision handling. Add `search --scope library`
  for personal approval authority without workspace discovery; its exposure
  status is explicitly `unknown`.
- Initialize the default Git-backed personal library on the first owned draft or
  confirmed import, while keeping import previews read-only. Explicit initialization
  still selects a custom path or disables Git. Diagnose a missing registered path and
  re-register the same UUID after the directory moves.
- Create or explicitly import one skill, then accept only its exact scanned and linted
  content hash. The version-2 hash is domain-separated and length-framed over canonical
  paths, bytes, and normalized executable bits.
- Inspect content-addressed history and diffs, including deleted working trees, and
  restore an old version as a new descendant commit.
- Detect current-project exposure drift and accepted-source updates without writes,
  exclude stale projections from current inventory, and refuse to overwrite local
  edits or remove them unless the user explicitly confirms a bound force preview.
- Preserve concurrent exposure-target edits during replacement, including edits made
  while a candidate is prepared or installed. If another writer occupies the target
  during recovery, retain the previous tree at a reported recovery path.
- Validate release notes before modifying version files or pushing release commits
  and tags, so a mismatched version fails without publishing partial release state.
- Keep metadata commands body-safe and keep ownership separate from approval and
  exposure.
- Remove the public activation and exposure review-bypass flags. Availability now
  gates every skill body and projection; exposure `--force` remains limited to an
  explicitly confirmed overwrite or removal of local target edits.
- Fail closed on display-ID and projection-slug collisions, source/exposure races,
  same-size timestamp spoofing, mode-only cache changes, invalid native host format,
  ignored initialization metadata, and noncanonical acceptance trees.
- Revalidate exact project and collection source hashes before readiness or searchable
  availability, including cached collections whose skill roots were added or removed.
- Keep every managed projection kind in one collision-safe host namespace, require an
  actual agent-bound exposed router for guarded activation, and refuse repository
  tag-state or project exposure-base symlinks/non-files with locked atomic writes.
- Bind non-interactive accept/import/restore commands to their exact previewed state
  and output mode, and never emit executable placeholder reasons.
- Report paused or skipped interactive setup as incomplete; do not install Working,
  issue restart guidance, or publish a false completion while review remains.
- Keep supported `.skillager/tags.json` plus its exact lock artifact out of the legacy
  state gate while preserving that gate for old approval/session state. Repeated setup
  retains full-scope block/discovery counts, includes existing verified routers in
  exposure totals, and skips redundant Working-install confirmation.
- Keep summary inventory bounded, retain curated-tag search matches, and make
  successful non-mutating previews exit zero.

Compatibility and migration:

- Legacy JSON approvals migrate on the next approval write, preserving the original
  as `trust.json.migrated`. Read-only commands do not migrate them. Back up
  `trust.sqlite3` in each state root alongside the library/Git repository; stale JSON
  backups are never consulted once migrated. Small identity, provenance, tag, and
  configuration documents retain their existing formats.
- Search may populate `search-v1.sqlite3` in the configured cache directory.
  Metadata-only output, ranking, filters, and the 50,000-character body search
  window remain unchanged. Other metadata/readiness commands remain read-only.
- Existing project, package, environment, native-agent, and collection discovery
  remains available; no source is automatically moved into the personal library.
- Version-2 content hashes intentionally do not reuse older ambiguous hash approvals.
  On upgrade, review the newly computed hashes before activation or exposure. Saved
  history hash prefixes must be refreshed from `library history`; no source is moved.
- Older exposure sidecars remain discoverable but may be reported non-current after
  the hash migration. New sidecars authenticate their own canonical metadata as well
  as the complete projected target, so inspect and explicitly refresh an old clean
  projection rather than silently trusting it.
- New Codex user-scope exposures use `~/.agents/skills`; legacy `~/.codex/skills`
  remains discoverable and manageable without automatic migration.
- A legacy ordinary collection named `lib` must be renamed or removed explicitly
  before initializing the reserved library; Skillager never claims it silently.
- Git-less libraries remain usable, with history-dependent operations reported as
  unavailable.
- `working` retains `skillager.working.v1`; exposure drift/source freshness is additive
  and advisory rather than a readiness change, established zero-value compatibility
  fields remain present, and `next` is empty whenever readiness is satisfied.

The library performs no automatic Git network operation, no history rewrite, no
cross-project rollout, automatic merge, exposure synchronization, or upstream import refresh.
