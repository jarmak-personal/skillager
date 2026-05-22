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

To make a collection available to the current project, review it, then enable the available skills as a project-local tag:

```bash
skillager setup --source collection --agent codex
skillager collection enable community
```

`collection enable` creates or updates a project-local tag with the collection's available reviewed skills. Blocked, unreviewed, and lint-blocked skills are not added by the default enable flow. For fully trusted personal or company repositories, `skillager setup --source collection --trust-all` is the fast path; `--yolo` is the same trusted-source shortcut with a blunter name. Both trusted-source shortcuts review selected lint-blocked skills with an audited shortcut override. For untrusted repositories, use the normal review flow.

`setup --source collection --agent <agent>` reviews registered collection skills and refreshes that agent's first-party working artifacts after approval. Registered collections remain catalog inventory until reviewed skills are copied into a project tag. If review is complete but status still reports missing or stale artifacts, run `skillager doctor --agent <agent>` for the exact repair command.

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
skillager tag sync --from ../other-project --to .
```

Tags live in `<project>/.skillager/tags.json`. `tag add` accepts available registered collection skill IDs and available current project inventory IDs. This lets agents maintain useful project tags after setup while user-authority review stays in the global trust/catalog state.
Tag show/search commands hide lint-blocked skills unless you pass `--include-lint-blocked` for diagnostics. That flag only changes read-only visibility; it never approves or exposes a skill.

Project tags do not broadcast live across repositories. Use `tag sync --from <project> --to <project>` for an explicit copy, or `--to-all` to copy to known projects recorded by setup/bootstrap.

## Project Tags

From the project directory:

```bash
skillager tag add gis community/gis-domain
skillager project tags
```

A tag belongs to a project by existing in that project's tag file. `project attach-tag` remains as a compatibility alias for old workflows, but new flows should create or update tags directly. When a project tag is created while using an external catalog location, Skillager records that catalog path in the tag file so later `search`, `show`, and guarded `activate` commands work without repeating `--catalog-state-dir`.

For older global-tag installs, run this once from a user shell after setup has recorded your projects:

```bash
skillager state migrate-tags --to projects
```

This copies legacy global tag attachments into project-local tag files and leaves the old global tag data in place for rollback.

After review, keep most large-repository skills searchable behind Skillager. Materialize only a small native set that is always relevant to the project, use stub mode for approved commands that should be visible by name, or use router mode for a curated tag when the agent needs broad access without loading every skill. Agents may update tags and scoped exposure after you tell them what you are working on; they should report the changes they made.

Once a tag is attached, its available skills are part of effective project inventory. Agents can use normal project commands instead of collection-specific commands:

```bash
skillager search "mapping workflow" --json
skillager show community/gis-domain --json
skillager list --summary-json --agent codex
```

`skillager collection search/show` remains useful for catalog management and debugging. Use `--include-lint-blocked` only when diagnosing rejected collection entries.

## Router Mode

For large tags, prefer router mode:

```bash
skillager materialize --tag gis --mode router --agent codex --scope project
```

This writes one compact native router skill. The router includes available skill IDs and author summaries, then tells the agent to activate a specific skill through Skillager when needed.

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
