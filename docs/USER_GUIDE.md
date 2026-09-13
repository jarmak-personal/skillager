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

## Search The Personal Library

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
to 10; `0` returns all matches. Results remain a JSON list with no total count or
paging cursor. A full result window does not establish how many more matches exist.

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
