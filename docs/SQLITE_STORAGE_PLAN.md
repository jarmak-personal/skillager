# Git and SQLite storage implementation

## Ownership and scope

Skill files remain ordinary files. Git remains the personal library's content
history, including scripts, references, and executable modes. The Skillager
content hash identifies a version; a Git commit is a verified retrieval reference.
Libraries created with `--no-git` remain supported.

SQLite owns approval decisions and their append-only audit history. A separate,
rebuildable SQLite cache holds collection metadata and searchable approved prose.
Small portable configuration, library identity, provenance, tag, and exposure
documents keep their existing formats. External skills remain in place. This
change does not introduce a server, upstream synchronization, or an SSH runtime.

## Implementation stages

1. **Approval database.** Introduce `trust.sqlite3` in each existing state root.
   Store current decisions by scope and approval key/skill ID, with indexed state
   and content hash, and append previous/new decision events in the same
   transaction. Preserve structured override/source records without lossy schema
   conversion. Approval writes and individual trust checks use indexed records;
   inventory operations take one approval snapshot per authority.
2. **Migration and recovery.** Read legacy `trust.json` without mutation until
   the next approval write. That write imports all records transactionally and
   retains the original as `trust.json.migrated`. SQLite then becomes the sole
   authority. A missing/corrupt database must not reactivate the legacy backup.
   Pure metadata/working reads do not migrate state. Existing explicitly reviewed
   in-tree state import remains a separate operation.
3. **Git association.** Library acceptance, import, and restore verify the exact
   committed tree before storing the decision. Record an immutable source/hash
   version and its Git reference alongside the decision transaction. A failed
   database write leaves committed content pending and retryable. Index rebuilds
   never infer approval from Git or version records.
4. **Persistent catalog and search.** Replace collection index documents on
   refresh with per-skill SQLite rows. Validate ranked search results against live
   source trees before applying the result limit. Populate a persistent FTS5 cache incrementally, keyed by
   source, content hash, and searchable metadata. Restrict queries to the current
   trusted candidate inventory before returning matches. Persist the existing
   scoring tokens so warm queries need no body reads or re-tokenization. Preserve ranking,
   filters, limits, the current 50,000-character body window, and metadata-only
   output. Unavailable derived caches can fall back to live computation.
5. **Validation and documentation.** Cover migration, concurrent writers,
   rollback, stale backups, overrides/block restoration, Git failure recovery,
   cache rebuilding, search scope, and edited/revoked content. Run the normal
   suite and full local check, then rerun the synthetic 5,000-skill benchmark.

## Recovery boundaries

Approval state is durable data: back up `trust.sqlite3`, not just the Git library.
Git and SQLite do not share a transaction. The safe order is review, commit,
verify committed content, then atomically record version/reference and approval.
The existing explicit acceptance operation repairs a commit without approval.

Collection/search caches are disposable. Deleting them must never delete or
create approvals. An immutable version records that content was accepted at a
point in time; only the current decision plus the verified current source hash
permits use. Decision events supply historical state transitions without
maintaining overlapping `valid_from`/`valid_to` records.

Migration keeps unknown record fields and all audited overrides. SQLite columns
support indexed lookup; per-record structured payloads remain JSON encoded inside
rows for compatibility. This avoids decoding an entire approval document for
each candidate and does not create a second JSON authority.

The `trust.sqlite3.required` marker prevents fallback if an established database is
lost. Schema initialization/import, backup retention, and this marker finish before
new decisions can commit. A failed subsequent decision rolls back independently of
the completed format migration.

## Acceptance criteria

- Existing CLI commands, JSON contracts, trust priority, and exposure behavior
  remain stable.
- Edits with unchanged size/restored mtime revoke availability until accepted.
- Pending or revoked bodies cannot reappear through cached search results.
- Read-only readiness checks leave approval and catalog files unchanged.
- Decision history survives revocation and search-cache deletion.
- Benchmark reports measured latency, memory, and correctness against the
  existing synthetic baseline; remaining source-validation costs stay visible.

## Implementation record

Implemented in `state/approvals.py`, `state/database.py`, `catalog/storage.py`,
the collection/search paths, and library accept/import/restore. Unchanged owned
library metadata for ordinary inventory is reusable only after exact tree, library
identity, path, and provenance verification. Search defers tree verification to
ranked candidates, while reconciling live discovery and current approval hashes.
It refills limited results after stale candidates and preserves exposure/variant
ranking and collision handling. `search --scope library` skips workspace inventory
and reports unchecked exposure as `unknown`. Search stores the existing ASCII scoring tokens alongside
Unicode FTS5 so ranking and reasons do not require warm-query body reads.

The full local check, including ranked-candidate search, passed: 426 Skillager tests,
13 linter tests, Ruff, both typing
checks, entrypoints, source/wheel builds, wheelhouse installation smoke tests, and
whitespace checks. The synthetic benchmark passed its inventory, ranking, filtering,
body-leak, pending-content, and same-size/restored-mtime edit/reacceptance checks.
See [benchmark measurements](SEARCH_BENCHMARK.md#sqlite-comparison) for results and
the workload's limits.
