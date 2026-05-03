# Skill Repositories

Many users have repositories full of skills. Skillager treats those repositories as collections.

Collections are inventory. Tags are reusable curation. Project attachments are intent.

## Add A Skill Repository

```bash
git clone <repo-url> ~/skills/community
skillager collection add ~/skills/community --name community
skillager collection search community gis
skillager collection show community/gis-domain
```

Adding a collection does not expose skills to agents. It registers inventory only.

If you clone a skill repository directly inside a project directory, `skillager setup` also discovers immediate child repositories with common Skillager or agent-native skill roots such as `.skills/`, `skills/`, `.agents/skills/`, `.agents/<agent>/skills/`, `.codex/skills/`, and `.claude/skills/`. A repository like `./agent-workflows/skills/bisect/SKILL.md` works even when the skills do not have `skillager.yaml`; Skillager infers metadata from `SKILL.md`. After review, those project-inventory skills can be added to tags by ID without registering the child repository first.

To make a collection available to the current project, enable it:

```bash
skillager collection enable community
skillager setup --source collection
```

`collection enable` creates or updates a reusable catalog tag with the collection's visible skills and attaches that tag to the current project. Blocked and lint-blocked skills are not added by the default enable flow. For fully trusted personal or company repositories, `skillager setup --source collection --trust-all` is the fast path; `--yolo` is the same trusted-source shortcut with a blunter name. For untrusted repositories, use the normal review flow.

`setup --source collection` reviews collection skills attached to the current project. Registered collections that have not been enabled or attached stay as catalog inventory.

Collection skills use the same manifest hardening as project skills. Invalid `skillager.yaml` files become lint-blocked quarantine records with safe finding summaries. Use `skillager lint` or `skillager collection show <skill-id> --include-lint-blocked` to inspect them without printing hostile manifest contents.

## Curate With Tags

`collection enable` is the common case. Manual or agent-managed tags are useful when a large collection should be split into smaller project-relevant groups:

```bash
skillager tag create gis
skillager tag add gis community/gis-domain community/topology community/projections
skillager tag add gis vibespatial/gis-domain
skillager tag add all-community --from-collection community
skillager tag add all-community --from-collection community --sync
skillager tag show gis
```

Tags live in the reusable user catalog by default. `tag add` accepts registered collection skill IDs and current project inventory IDs. This lets agents maintain useful project tags after setup while users can still rename, split, or remove tag members later.
Tag show/search commands hide lint-blocked skills unless you pass `--include-lint-blocked` for diagnostics. That flag only changes read-only visibility; it never approves or exposes a skill.

## Attach Tags To A Project

From the project directory:

```bash
skillager project attach-tag gis
skillager setup
```

Attached tag skills become setup candidates for that project. They are still not usable until reviewed.
When a project attaches a tag from an external catalog location, Skillager records that catalog path in the project state so later `search`, `show`, and guarded `activate` commands work without repeating `--catalog-state-dir`.

After review, keep most large-repository skills searchable behind Skillager. Materialize only a small native set that is always relevant to the project, use stub mode for approved commands that should be visible by name, or use router mode for a curated tag when the agent needs broad access without loading every skill. Agents may update tags and scoped exposure after you tell them what you are working on; they should report the changes they made.

Once a tag is attached, its reviewed skills are part of effective project inventory. Agents can use normal project commands instead of collection-specific commands:

```bash
skillager search "mapping workflow" --trusted-only --json
skillager show community/gis-domain --json
skillager list --json
```

`skillager collection search/show` remains useful for catalog management and debugging. Use `--include-lint-blocked` only when diagnosing rejected collection entries.

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

Reviewed git-backed collection skills are approved by logical source and content hash, so the same unchanged skill can appear in another clone or project without another approval prompt. Changed skill content gets a new content hash and must be reviewed again before activation. Use `--project-only` during setup/review when a decision should not be reusable.
