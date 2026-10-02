# Skillager User Guide

Skillager finds agent skills, keeps unreviewed changes from running, and gives skills
you own one versioned home. Set it up once per project, then let your coding agent
handle routine skill selection.

## Set Up A Project

Install Skillager as a user tool:

```bash
uv tool install skillager
# or: pipx install skillager
```

Open the project where you will run your agent:

```bash
cd my-project
skillager setup --agent codex
```

Use `--agent claude` for Claude.

Setup finds skills in the project, its installed packages, child skill repositories,
and registered collections. Review the skills you want to make available. If you
pause, rerun the command that Skillager prints.

When setup finishes, restart your agent in the same project. Skillager installs a
small project skill that teaches the agent how to check readiness and find reviewed
skills. It does not edit `AGENTS.md` or `CLAUDE.md`.

Start with your goal:

> Check Skillager, then help me build a Python GIS service. Use reviewed skills where
> they help, and keep one-off skills on demand.

You do not need to run Skillager before every task. The agent checks it after restarts
and when specialized help may be useful.

## Work With Your Agent

Tell the agent the outcome and whether the work is one-off or recurring:

- “Find the best available skills for `<goal>`. Show me the shortlist before changing
  the project.”
- “Use any reviewed skills that help with `<goal>`, but keep them on demand.”
- “We will repeat `<workflow>` in this project. Create the smallest useful Skillager
  setup and leave everything else on demand.”
- “Create a personal skill for `<purpose>`. Show me the draft, then ask before making
  it available.”
- “Find the external skill called `<name>` and prepare to adopt it. Show me the source
  and destination before importing it.”
- “Show me the saved versions of my `<name>` skill, explain the relevant changes, and
  ask before restoring anything.”

For one-off work, the agent can use a reviewed skill without adding it to every
session. For recurring work, the agent can create a small project shortcut to the
skills you need. It should explain any project files it adds.

## Review Skills

Setup asks before a new skill becomes available. Review its source and purpose, then
approve or reject it.

Approval applies only to the content you reviewed. If that content changes, Skillager
asks again before using the new version. A risky or invalid skill stays unavailable
unless you fix it or approve a clearly explained override.

If setup offers to approve a whole source at once, use that option only for a source
you fully control. For normal setup, review the selected skills individually.

Skillager can reuse your review of an unchanged shared or packaged skill in other
projects. Add `--project-only` when the decision should stay in the current project:

```bash
skillager setup --agent codex --project-only
```

Skillager can govern content used through its commands and managed project files.
Codex and Claude may also load skills installed directly in their own native folders;
Skillager cannot block those independent host paths.

## Storage and Backups

Library skills remain editable files, with Git history unless you chose `--no-git`.
Approvals, blocks, pins, overrides, and decision history live in `trust.sqlite3` in
each project state directory and the user catalog directory. Back up these state
directories while Skillager is idle, along with your library. Git alone does not
back up approval decisions.

Existing `trust.json` approvals remain usable. The next approval write migrates them
to SQLite and retains `trust.json.migrated` as the original backup. The small
`trust.sqlite3.required` marker prevents using stale JSON if the database disappears.
Keep the database and marker together. If the database is lost or damaged, restore
its backup; do not replace it with the old JSON file. Older Skillager releases cannot
manage the new approval database.

Collection metadata in `catalog.sqlite3` and search content in
`search-v1.sqlite3` are rebuildable caches. The latter lives under
`SKILLAGER_CACHE_DIR` (or the default user cache directory). Search may populate it;
other metadata/readiness commands remain read-only. Deleting these caches while
Skillager is idle does not revoke approvals. Search still validates current content
and returns metadata only, using the existing 50,000-character body search window.

## Browse The Personal Library

`list` keeps its existing workspace scope and output by default. To browse all owned
skills from any directory, including pending drafts and blocked skills, use:

```bash
skillager list --scope library --json --limit 100
```

The response is a `skillager.list.v1` object with `scope: "library"`, `skills`,
`next_cursor`, and `discovery_error_count`. The count is a bounded summary of
indexing diagnostics for represented library rows; detailed exceptions and body
text are omitted. Each row contains `id`, `name`, `description`, `status` (`accepted`,
`pending`, or `blocked`), `accepted_hash`, and the canonical `SKILL.md` path in
`skill_file`. `accepted_hash` is null before acceptance; after an edit it retains
the previously accepted hash while the status becomes pending. Lint quarantine is
reported as blocked. Names and descriptions use declared frontmatter metadata;
the name falls back to the library directory name and a missing description is
null. Unterminated frontmatter is ignored. Body paragraphs and headings are never
used as metadata in this response.

Library scope reads the personal catalog selected by `--catalog-state-dir`,
`SKILLAGER_CATALOG_STATE_DIR`, or the user's default catalog. It does not consult
project catalog bindings, discover project/package/native skills, or report
workspace exposure. An uninitialized library returns an empty page. Library scope
requires `--json`; workspace filters, `--summary-json`, and `--full-json` do not
apply to this inventory.

If the registered skills root cannot be completely traversed, or a skill fails
indexing without a represented row, the command exits with **2** and emits the
`skillager.error.v1` envelope with `code: "inventory_unavailable"` instead of a
page. A missing registered skills root is unavailable, rather than an empty
library. Preserve previously loaded rows until the library can be read again.
Readable quarantined skills remain visible with their existing acceptance status;
discovery diagnostics are part of cursor state even when row metadata is unchanged.
Cached failure diagnostics are reobserved before paging, so recovered or removed
failures cannot make an otherwise complete inventory remain unavailable.

Rows are ordered lexically by skill ID. `--limit` defaults to 100 and must be
greater than zero. Pass the opaque `next_cursor` unchanged to `--cursor`, using the
same scope and limit. Null means the last page. Omitting the cursor (or passing an
empty string) starts a new traversal. `list --limit` and `list --cursor` require
library scope and do not change ordinary workspace list output.

Any changed skill tree, inventory membership, acceptance state, or library
registration invalidates an existing cursor. The CLI exits with **15** and emits
`{"schema":"skillager.error.v1","error":{"code":"stale_cursor","message":"..."}}`
instead of a page. Restart without a cursor and replace previously loaded rows.
A malformed cursor or one used with a different request exits with **2** and the
same error envelope with `code: "invalid_cursor"`. Cursors are traversal tokens,
not approval or content-access grants. Paging bounds the output size; verifying
the complete current library state still requires reading the inventory.

## Search The Personal Library

For opt-in grouped results and explicit installed/copy controls, see
[known-skill search views](SEARCH_VIEWS.md). The existing search defaults below
remain unchanged without `--view`.

```bash
skillager search "database migration" --scope library --limit 20 --json
```

`--scope library` searches accepted owned skills using the personal catalog's approval
authority. Scope applies before ranking and limiting. It skips project, package,
native-directory, and workspace-exposure discovery. Results report `exposure: "unknown"`;
this means workspace status was not checked. Project-specific blocks, tags, and
exposure status belong to the default `--scope workspace` query. Library scope cannot
be combined with `--tag` or `--include-global`; agent and compatibility filters work
in either scope. An uninitialized library returns an empty result list.

Search covers titles, descriptions, tags, and approved entrypoint bodies, returning
metadata and match reasons without body excerpts. Pending drafts remain visible
through library metadata commands, but do not match body searches. `--limit` defaults
to 10; `0` returns all matches. Without `--cursor`, results remain the existing JSON
list. A full result window does not establish how many more matches exist.

To traverse a larger ranked result set, explicitly start paging with an empty cursor:

```bash
skillager search "database migration" --scope library --limit 20 --json --cursor ''
skillager search "database migration" --scope library --limit 20 --json --cursor TOKEN
```

The paginated response is a `skillager.search-page.v1` object with `scope`, `results`, and
`next_cursor`. Pass its opaque cursor unchanged with the same query, scope, limit,
and filters; null means the last page. Paging also works in workspace scope and with
[known-skill search views](SEARCH_VIEWS.md). It requires `--json` and a positive
limit. Omitting `--cursor` preserves the existing output even when `--limit` is set.

The shared library-list cursor format binds the complete current search inventory
and request. A changed source tree, membership, approval, or relevant presentation
observation invalidates the traversal, including a change to a nonmatching skill.
The CLI returns the `skillager.error.v1` envelope with `error.code: "stale_cursor"`
and exits **15**. Replace previously loaded rows and restart with `--cursor ''`.
Malformed cursors and cursors used with a different command, query, context, or
filter return `invalid_cursor` and exit **2**. Cursors never grant approval or body
access. Each page bounds output; observing and verifying the complete inventory and
ranked matches still takes work for every cursor request.

Warm searches reuse collection metadata and indexed prose, then verify the exact
current content tree and approval for ranked candidates before filling the result
limit. Changed or deleted matches cannot consume that limit. Source discovery and
accepted-hash changes refresh candidate metadata as needed; library acceptance
refreshes its catalog metadata. An external accepted edit is searchable without
`collection refresh`, though an explicit refresh avoids repeatedly rebuilding that
changed entry's metadata. Search does not write catalog or approval state.

A missing search cache still requires initial body indexing. Exposed skills, agent
variants, and ambiguous identities need additional verification to preserve ranking.
Project/package discovery remains live. Exhaustive queries and inventories still
perform the work needed to verify every returned source. UI integrations should run
the CLI asynchronously, cancel obsolete requests, and refresh after acceptance or
other relevant changes; search is not a watcher or an update notification service.

## Export An Accepted Full Payload

To prepare an artifact for a separately authorized delivery, select an accepted
owned skill's full content hash from `list --scope library --json`, then run:

```bash
skillager export lib/<name> --version <full-content-hash> --agent codex --dest /absolute/empty-directory --json
```

`--version` and `--agent codex|claude` are required. Export copies the same canonical
files and modes as native Full exposure, including nested executable files and
sidecar permissions under the current umask, and
adds authenticated `skillager.materialized.yaml` provenance with `scope: export`.
It requires current accepted content for that exact hash. It does not restore
historical bytes, install into an agent directory, or write approval, exposure,
library, index, cache, or lock state. Personal catalog selection ignores project
catalog hints and project approval state.

The destination may be missing or empty, with an existing parent. Its components
must be directories without symlink aliases or `..`; it must not overlap the
library or catalog in either direction. Canonical file selection matches native
exposure: regular hardlinked files are copied independently, while existing source
symlinks and non-files are excluded. A selected file replaced by a symlink during
copying is refused. A nonempty destination is refused without changes.
Preparation writes once directly into the selected destination using held
directory descriptors. Filesystem identities protect library/catalog overlap even
when path spellings differ by case or Unicode normalization.
Changed paths cannot redirect writes to another directory. A competing writer's
files are preserved; cleanup removes only the exact objects created by export.
Preserved concurrent entries may leave a refused destination nonempty; inspect
it before retrying.

Success exits 0 with `skillager.export.v1`, `status: exported`, the owned ID and
library UUID, `agent`, `scope: export`, `destination`, `content_hash`, and `files`.
Each file has relative `path`, numeric permission `mode`, byte `size`, and `sha256`.
The file list includes provenance. JSON contains no skill bodies. Written bytes,
modes, provenance, current source, and current approval are rechecked before success.

Refusal exits 2 with the same schema, `status: refused`, empty `files`, and an
`error` containing a bounded `code` and message. `pending_content` means the current
hash lacks current acceptance; `blocked_content` and `lint_blocked_content` preserve
their respective gates. `version_not_accepted` means the requested hash lacks
accepted-version evidence. `changed_content` means the currently accepted version
differs from the working tree; `version_content_mismatch` means a previously
accepted historical hash differs from current bytes. Historical acceptance is not
current approval. `incompatible_agent`, `unsafe_destination`,
`destination_not_empty`, `destination_changed`, `source_changed`, `export_changed`,
and `verification_failed` distinguish other preparation refusals. Repair or review
the reported condition before making a fresh request; do not infer an installation
from artifact provenance.

## Create A Personal Skill

Ask your agent:

> Create a personal skill for reviewing database migrations. Show me the draft and
> ask before making it available.

The agent creates the draft in your personal library and edits the canonical
`SKILL.md`. Your first draft also creates the default library at
`~/.skillager/library` with Git history.

To do the same directly:

```bash
skillager library new migration-review
# Edit the SKILL.md path Skillager prints.
skillager library accept lib/migration-review
```

After any edit, the skill waits for review again. Accept it only when the preview
matches the change you intended.

Run initialization yourself only when you want a custom location or no Git history:

```bash
skillager library init --path ~/skills/personal
skillager library init --no-git
```

Check the library or one owned skill with:

```bash
skillager library status
skillager library status lib/migration-review
```

Make lasting changes in the library path that `library status` shows. If a managed
project copy was edited, ask the agent to compare it with the library and preserve the
intended change before replacing anything.

## Adopt An External Skill

Approval and setup now preserve reusable library copies of approved project,
package, environment, native, and collection skills automatically. Originals stay
where they are. An explicit import remains useful when choosing a separate name
and independent copy before source approval.

Ask your agent:

> Find the external skill called `pr-review`. Show me its source and the proposed
> personal-library destination, then ask before importing it.

Or preview it directly:

```bash
skillager import workflows/pr-review
```

Use a different personal name when needed:

```bash
skillager import workflows/pr-review --as team-pr-review
```

The preview does not create the library or copy files. Confirmation copies only that
skill, records where it came from, and leaves the original unchanged. A confirmed
first import creates the default personal library.

## Sync Existing Approvals

Run `skillager library sync --approved` to backfill already approved skills from the
current project's effective discovery context. Even a project-only source approval
produces an identical reusable canonical copy; its original scope remains visible
in provenance. This does not add the skill to any project's agent.

Use `skillager library sync --status --json` to inspect lineage and eligibility
without writes. The `--approved` apply result reports created, updated, unchanged,
conflict, skipped, failed, and uncertain counts. Status reports current lineage and
eligibility; it does not reconstruct a previous apply result. Customized or pinned
copies stay protected. If a
large batch stops at its limit, inspect its result before explicitly running another
batch. A pending copy needs review and `library accept`; an uncertain interruption
needs status inspection rather than an automatic retry.

## Compare And Restore Versions

Skillager records accepted versions when the personal library uses Git. Start by
listing the saved versions:

```bash
skillager library history lib/migration-review
```

Inspect a summary before viewing content:

```bash
skillager library diff lib/migration-review --from <hash> --to <hash> --stat
skillager library diff lib/migration-review --from <hash> --to <hash>
```

Preview a restore with a hash shown by `history`:

```bash
skillager library restore lib/migration-review --to <hash>
```

Restore creates a new version; it does not rewrite history. Ask your agent to choose
the relevant hashes and explain the diff if you do not want to handle them directly.

## Add A Skill Repository

Skillager discovers a skill repository cloned directly inside the current project.
Register a separate repository when you want its skills available across projects:

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --collection workflows --agent codex
```

Registration keeps the repository external. It does not copy its skills into your
personal library. Approving its skills preserves verified individual copies;
registering or browsing it alone does not copy anything.

## Diagnose Problems

Start with:

```bash
skillager doctor --agent codex
```

Use `--agent claude` for Claude. Follow the command Doctor prints; use `--fix` only
when Doctor recommends repairing the project’s Working helper.

| What you see | What to do |
| --- | --- |
| Setup stopped with skills left to review | Rerun the setup command it printed. |
| An owned skill changed | Review it, then run `skillager library accept lib/<name>`. |
| The personal library moved | Run `skillager library status`, then preview `skillager library relocate --path <new-path>`. |
| A managed project copy has local edits | Ask the agent to compare and preserve them before replacement or removal. |
| The agent reports a missing or stale Working helper | Run the `doctor --fix` command Doctor recommends. |

Skillager does not silently overwrite local edits, move external skills, merge
different copies, or contact Git remotes.

## Back Up Your Skills

Back up the complete personal-library directory shown by:

<!-- skillager-test fixture=empty_project -->
```bash
skillager library status
```

Include its hidden `.git` and `.skillager` directories. The default location is
`~/.skillager/library`.

Project groups live in `<project>/.skillager/tags.json` and can be committed with the
project. Reusable approvals and collection registration live in Skillager’s user
configuration directory. Back up `${XDG_CONFIG_HOME:-~/.config}/skillager` too when
you want to preserve those review decisions.

## Commands You May Run

| Goal | Command |
| --- | --- |
| Set up or review a project | `skillager setup --agent codex` |
| Diagnose a project | `skillager doctor --agent codex` |
| Register a shared skill repository | `skillager collection add <path> --name <name>` |
| Check your personal library | `skillager library status` |
| Create a personal skill | `skillager library new <name>` |
| Review an owned change | `skillager library accept lib/<name>` |
| Preview adopting an external skill | `skillager import <external-id>` |
| List saved personal versions | `skillager library history lib/<name>` |
| Compare personal versions | `skillager library diff lib/<name> --from <hash> --to <hash>` |
| Preview restoring a version | `skillager library restore lib/<name> --to <hash>` |

Your agent normally handles readiness checks, skill searches, activation, and small
project shortcuts. See the [agent CLI guide](AGENT_CLI_GUIDE.md) for that contract,
[skill repositories](SKILL_REPOSITORIES.md) for shared sources,
[library authors](LIBRARY_AUTHORS.md) for publishing, and
`skillager <command> --help` for complete flags.
