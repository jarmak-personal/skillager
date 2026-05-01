# Skill Repositories

Many users have repositories full of skills. Skillager treats those repositories as collections.

Collections are inventory. Tags are curation. Project attachments are intent.

## Add A Skill Repository

```bash
git clone <repo-url> ~/skills/community
skillager collection add ~/skills/community --name community
skillager collection search community gis
skillager collection show community/gis-domain
skillager setup --source collection --yolo
```

Adding a collection does not expose skills to agents.
`setup --source collection` reviews raw collection inventory from the reusable catalog, even before a project attaches any tags. For untrusted repositories, skip `--yolo` and use the normal review flow.

## Curate With Tags

```bash
skillager tag create gis
skillager tag add gis community/gis-domain community/topology community/projections
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
skillager materialize --tag gis --mode index --agent codex --scope project
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
