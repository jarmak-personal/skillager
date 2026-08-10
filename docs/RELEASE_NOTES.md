# Release Notes

## skillager 0.9.0 (planned)

This release recenters Skillager on a canonical personal library for skills the user
owns while preserving discovery and review for external project, environment,
package, native-agent, and collection skills.

Highlights:

- Initialize one plain-file, optionally Git-backed personal library with the reserved
  `lib/<name>` namespace, diagnose a missing registered path, and explicitly
  re-register the same UUID after the directory moves.
- Create or explicitly import one skill, then accept only its exact scanned and linted
  content hash.
- Inspect content-addressed history and diffs, restore an old version as a new
  descendant commit, and fork current or historical content with exact lineage.
- Detect project exposure drift without writes, then keep, quarantine, repair,
  promote, import, or roll back through explicit reconciliation choices.
- Preview current-project library updates with `sync`, apply only clean unpinned
  native/stub updates, and pin an exposure to its current source hash.
- Keep metadata commands body-safe and keep ownership separate from approval and
  exposure.
- Fail closed on display-ID collisions, source/exposure races, same-size timestamp
  spoofing, incompatible sync updates, legacy sidecars without a library UUID, and
  noncanonical acceptance trees.
- Keep summary inventory bounded, surface fork lineage in ordinary metadata, retain
  curated-tag search matches, and make successful non-mutating previews exit zero.

Compatibility and migration:

- Existing project, package, environment, native-agent, and collection discovery
  remains available; no source is automatically moved into the personal library.
- Existing exposures and older sidecars remain valid through additive ownership,
  fingerprint, reconciliation, and pin metadata.
- A legacy ordinary collection named `lib` must be renamed or removed explicitly
  before initializing the reserved library; Skillager never claims it silently.
- Git-less libraries remain usable, with history-dependent operations reported as
  unavailable.
- `working` uses the previously documented `skillager.working.v2` schema and keeps
  exposure drift advisory rather than changing readiness.

The library performs no automatic Git network operation, no history rewrite, no
cross-project rollout, and no automatic upstream import refresh.
