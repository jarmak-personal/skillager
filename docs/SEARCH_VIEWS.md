# Known-skill search views

The existing `search` output and defaults are unchanged. An explicit `--view`
selects the metadata-only `skillager.search.v1` contract:

```bash
skillager search --view skills --limit 50 --json -- "merge"
skillager search --view copies --include-installed --limit 50 --json -- "merge"
```

`skills` returns one representative per proven logical identity. It prefers the
current accepted canonical library entry. `copies` returns separately identified
current accepted sources and associated project copies of each matching identity.
Unknown or conflicting identity relationships are never joined by name or content
hash. Different versions can share a proven identity. A pending canonical working
tree is not replaced by historical content.

Unless `--include-installed` is passed, every version of a known identity present
in the project is excluded. Presence includes project originals and qualified
Full/Stub/router copies, across Codex and Claude Code, including older or modified
copies. `--agent` remains a preference and compatibility context; it does not limit
presence to that agent or merge name-based variant families. Explicit
`--compatible-only` still requires `--agent`.

Grouping, installed exclusion and separate-copy projection happen before the one
ranked window. The view accepts limits from 1 through 50. A group can match through
an original even if the preferred canonical body does not contain the query. Each
row's `search.match` identifies the exact accepted source/hash that supplied its
score and reasons. Top-level reasons are empty when the selected occurrence is not
that match source. Project projection bytes are never searched in place of an
accepted source, and no bodies or scanner excerpts are returned.

## Personal-library and caller-supplied presence

Standalone `--scope library` does not discover a project or use project approval
state. Without an installed observation, use `--include-installed`; otherwise the
view explicitly refuses with `installed-state-unknown`.

An explicit canonical local project path can request separate presence observation:

```bash
skillager search --scope library --view skills --installed-project /absolute/project --json -- "merge"
```

The path must resolve to that exact CLI project, not an ancestor or symlink alias.
The project observation never supplies personal-library approval authority.

Alternatively `--scope library --installed-identities /absolute/input.json` reads
a regular, non-symlink JSON file of at most 2 MiB and 10,000 unique identities:

```json
{
  "schema": "skillager.search-installed.v1",
  "identities": [
    {"library_id": "12345678-1234-1234-1234-123456789012", "skill_id": "lib/example"}
  ]
}
```

UUIDs must be canonical lowercase values and skill IDs canonical library names.
Extra fields, duplicates, malformed values and oversized input are refused. This
input only excludes identities from presentation. It contains no project paths,
transport instructions, approval records or mutation grants. The caller owns the
truth and lifetime of its observation; the CLI does not independently establish
that a caller's list is complete. It cannot be combined with project observation
or workspace candidate scope.

## Result contract

The JSON envelope has `schema`, `status`, `reason_code`, `policy`, `context`,
`limit` and `results`. `policy` echoes `view`, `include_installed`, `scope`,
`preferred_agent` and `compatible_only`. `context.project_root` is an absolute local
path only when explicitly observed; `installed_observation` is `observed`,
`provided` or `unknown`.

Each result preserves existing public full metadata and adds `search`:

- `group_id`: opaque stable logical identity.
- `canonical`: proven `{library_id, skill_id}` or null.
- `occurrence`: stable `id`, `kind`, exact `path`/`entrypoint`, `agent`, and
  opaque `source_identity` when applicable. Kinds are `library`, `source`,
  `project-original`, `full`, `stub` and `router-member`. Managed occurrences
  include the concrete exposure selector, never the complete router member graph.
- `group_occurrences`: observed source/copy references, not a count of search hits
  or accepted versions.
- `installed`: true, false, or null (unknown).
- `match`: matching accepted source's `occurrence_id`, `skill_id`, `content_hash`,
  `score`, metadata-only `reasons`, and its bounded `occurrence` descriptor, so the
  matching location, source kind and agent can be displayed without guessing.

For a Full/Stub/router occurrence, top-level source metadata describes the
accepted definition supplying search evidence; `occurrence` identifies the actual
installed file. Reading or acting on that occurrence must retain this distinction.
Search does not create authority for either operation. Current files may change
after the observation, so a later ordinary file read is not an acceptance snapshot.

`status: completed` exits 0. An unavailable observation exits 2 with empty results
and a bounded reason code. Incomplete installed observation cannot silently produce
an uninstalled-only result: the caller must show the unavailable state or explicitly
request `--include-installed`. With that option, unproven associations remain
separate and installed state stays null where necessary.

This includes an ordinary unsynchronized native skill: it may also be a copy whose
sidecar was deleted. Its own source identity and native presence remain known, but
its relationship to the library is unknown. Such a project needs explicit
`--include-installed` until the relationship can be observed. Search never imports,
synchronizes, or guesses a relationship to remove this uncertainty.

The request admits at most 10,000 observed source/copy references and the result
envelope is bounded to 4 MiB. Excess is an explicit refusal, never a silently
truncated inventory or lineage graph. These bounds do not replace a caller's
process deadline and cancellation.
