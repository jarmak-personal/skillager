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
  content hash, including normalized executable bits.
- Inspect content-addressed history and diffs, and restore an old version as a new
  descendant commit.
- Detect current-project exposure drift without writes and refuse to overwrite local
  edits or remove them unless the user explicitly confirms a bound force preview.
- Keep metadata commands body-safe and keep ownership separate from approval and
  exposure.
- Fail closed on display-ID collisions, source/exposure races, same-size timestamp
  spoofing, and noncanonical acceptance trees.
- Bind non-interactive accept/import/restore commands to their exact previewed state
  and never emit executable placeholder reasons.
- Keep summary inventory bounded, retain curated-tag search matches, and make
  successful non-mutating previews exit zero.

Compatibility and migration:

- Existing project, package, environment, native-agent, and collection discovery
  remains available; no source is automatically moved into the personal library.
- Existing exposures and older sidecars remain readable; new sidecars keep exact
  hashes and stable library identity without redundant ownership labels.
- New Codex user-scope exposures use `~/.agents/skills`; legacy `~/.codex/skills`
  remains discoverable and manageable without automatic migration.
- A legacy ordinary collection named `lib` must be renamed or removed explicitly
  before initializing the reserved library; Skillager never claims it silently.
- Git-less libraries remain usable, with history-dependent operations reported as
  unavailable.
- `working` retains `skillager.working.v1`; exposure drift is additive and advisory
  rather than a readiness change.

The library performs no automatic Git network operation, no history rewrite, no
cross-project rollout, automatic merge, exposure synchronization, or upstream import refresh.
