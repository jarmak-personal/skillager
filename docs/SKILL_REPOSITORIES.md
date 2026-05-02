# Skill Repositories

Many users have repositories full of skills. Skillager treats those repositories as collections.

Collections are inventory. Tags are curation. Project attachments are intent.

## Add A Skill Repository

```bash
git clone <repo-url> ~/skills/community
skillager collection add ~/skills/community --name community
skillager collection search community gis
skillager collection show community/gis-domain
```

Adding a collection does not expose skills to agents. It registers inventory only.

To make a collection available to the current project, enable it:

```bash
skillager collection enable community
skillager setup --source collection
```

`collection enable` creates or updates a reusable catalog tag with the collection's skills and attaches that tag to the current project. For fully trusted personal or company repositories, `skillager setup --source collection --trust-all` is the fast path. For untrusted repositories, use the normal review flow.

`setup --source collection` reviews collection skills attached to the current project. Registered collections that have not been enabled or attached stay as catalog inventory.

## Curate With Tags

`collection enable` is the common case. Manual tags are useful when a large collection should be split into smaller project-relevant groups:

```bash
skillager tag create gis
skillager tag add gis community/gis-domain community/topology community/projections
skillager tag add all-community --from-collection community
skillager tag add all-community --from-collection community --sync
skillager tag show gis
```

Tags live in the reusable user catalog by default.

## Attach Tags To A Project

From the project directory:

```bash
skillager project attach-tag gis
skillager setup
```

Attached tag skills become setup candidates for that project. They are still not usable until reviewed.
When a project attaches a tag from an external catalog location, Skillager records that catalog path in the project state so later `search`, `show`, and guarded `activate` commands work without repeating `--catalog-state-dir`.

After review, keep most large-repository skills searchable behind Skillager. Materialize only a small native set that is always relevant to the project, use stub mode for approved commands that should be visible by name, or use router mode for a curated tag when the agent needs broad access without loading every skill.

Once a tag is attached, its reviewed skills are part of effective project inventory. Agents can use normal project commands instead of collection-specific commands:

```bash
skillager search "mapping workflow" --trusted-only --json
skillager show community/gis-domain --json
skillager list --json
```

`skillager collection search/show` remains useful for catalog management and debugging.

## Router Mode

For large tags, prefer router mode:

```bash
skillager materialize --tag gis --mode router --agent codex --scope project
```

This writes one compact native router skill. The router includes approved skill IDs and author summaries, then tells the agent to activate a specific skill through Skillager when needed.

For personal command collections where the names themselves are useful, expose selected commands as stubs:

```bash
skillager materialize personal/deploy-preview --mode stub --agent codex --scope project
```

A stub is a tiny native skill containing the author summary and activation command. It does not include the full skill body.

Router and stub skills include compatibility notes when Skillager sees strong harness-specific assumptions. Those notes are advisory unless the source skill explicitly declares `exclusive_to` or `incompatible_with`.

## Updating A Collection

`refresh` re-walks and re-scans the local directory. It does not run `git pull`.

```bash
cd ~/skills/community
git pull
skillager collection refresh community
skillager status --all
```

Changed skill content gets a new content hash and must be reviewed again before activation.
