# Skill Repositories

Many users have repositories full of skills. Skillager treats those repositories as collections.

Collections are user-global source inventory for administration, review, refresh, and catalog debugging. Project-local tags are the project curation surface for routers, stubs, and task-specific grouping.

## Add A Skill Repository

```bash
git clone <repo-url> ~/skills/community
skillager collection add ~/skills/community --name community
skillager review --collection community --summary
```

Adding a collection does not expose skills to agents. It registers inventory only. Run setup once to review the collection; after approval, unchanged collection skills are searchable from any project that uses the same Skillager catalog.

If you clone a skill repository directly inside a project directory, `skillager setup` also discovers immediate child repositories with common Skillager or agent-native skill roots such as `.skills/`, `skills/`, `.agents/skills/`, `.agents/<agent>/skills/`, `.codex/skills/`, and `.claude/skills/`. A repository like `./agent-workflows/skills/bisect/SKILL.md` works even when the skills do not have `skillager.yaml`; Skillager infers metadata from `SKILL.md`. After review, those project-inventory skills can be added to tags by ID without registering the child repository first.

To review only collection skills:

```bash
skillager setup --collection community --agent codex
```

Ordinary `skillager setup --agent <agent>` also includes registered collections. For fully trusted personal or company repositories, `skillager setup --collection community --bulk-approve --agent codex` is the fast path; `--yolo` is the fun alias for the same bulk approval path. Bulk approval reviews selected lint-blocked skills with an audited shortcut override. For untrusted repositories, use the normal review flow.

`setup --collection <name> --agent <agent>` reviews that registered collection and refreshes that agent's first-party working artifacts after approval. If review is complete but `working --agent <agent> --json` still reports missing or stale artifacts, run `skillager doctor --agent <agent> --fix`.

Collection skills use the same manifest hardening as project skills. Invalid `skillager.yaml` files become lint-blocked quarantine records with safe finding summaries. Use `skillager review --collection <name> --include-lint-blocked --json` to inspect them without printing hostile manifest contents. Repository authors can run `uvx --from skillager-linter skillager-lint .` in CI before publishing.

## Curate With Tags

After review, collection skills are already part of effective project inventory. Tags are useful when a large collection should be split into smaller project-relevant groups or exposed through a compact router:

```bash
skillager tag add gis community/gis-domain community/topology community/projections
skillager tag add gis vibespatial/gis-domain
skillager tag add all-community --from-collection community --sync
skillager tag show all-community
skillager tag list
skillager tag delete old-community
skillager tag show gis
skillager tag sync --from ../other-project --to .
```

`tag add <tag> --from-collection <collection> --sync` creates or updates a project-local tag from that collection's available reviewed skills. Blocked, unreviewed, and lint-blocked skills are not added to the synced tag.

Tags live in `<project>/.skillager/tags.json`. `tag add` accepts available registered collection skill IDs and available current project inventory IDs. Use `tag show`, `tag list`, `tag delete`, and `tag sync` for project curation. This lets agents maintain useful project tags after setup while user-authority review stays in the global trust/catalog state.
Tag show/search commands hide lint-blocked skills unless you pass `--include-lint-blocked` for diagnostics. That flag only changes read-only visibility; it never approves or exposes a skill.

Project tags do not broadcast live across repositories. Use `tag sync --from <project> --to <project>` for an explicit copy, or `--to-all` to copy to known projects recorded by setup or doctor repair.

## Project Tags

From the project directory:

```bash
skillager tag add gis community/gis-domain
skillager tag add workflows --from-collection community --sync
skillager tag list
```

A tag belongs to a project by existing in that project's tag file. Create or update tags directly with `skillager tag add`, inspect them with `skillager tag list` and `skillager tag show`, remove them with `skillager tag delete`, and copy reviewed curation explicitly with `skillager tag sync`. When a project tag is created while using an external catalog location, Skillager records that catalog path in the tag file so later `search`, `show`, and guarded `activate` commands work without repeating `--catalog-state-dir`.

For older global-tag installs, review any curation you still want, recreate it with `skillager tag add` or copy from another reviewed project with `skillager tag sync`, then remove obsolete legacy state.

After review, keep most large-repository skills searchable behind Skillager. Expose only a small native set that is always relevant to the project, use stub mode for approved commands that should be visible by name, or use router mode for a curated tag when the agent needs broad access without loading every skill. Agents may update tags and scoped exposure after you tell them what you are working on; they should report the changes they made.

After review, available collection skills are part of effective project inventory whether or not they are in a project tag. Agents can use normal project commands instead of collection-specific commands:

```bash
skillager search "mapping workflow" --json
skillager show community/gis-domain --json
skillager list --summary-json --agent codex
```

Use `skillager review --collection <name> --summary` or `--json` for collection review and diagnostics. Use `--include-lint-blocked` only when diagnosing rejected collection entries.

## Router Mode

For large tags, prefer router mode:

```bash
skillager expose --tag gis --mode router --agent codex --scope project
```

For a one-off set, pass explicit available skill IDs instead of creating a tag:

```bash
skillager expose vibespatial/gis-domain vibespatial/dispatch-wiring --mode router --agent codex --scope project
```

This writes one compact native router skill. The router includes available skill IDs and author summaries, not full skill bodies, then tells the agent to activate a specific skill through Skillager when needed. Unavailable or incompatible members are skipped. The expose output and JSON give the router exposure id/slug for activation:

```bash
skillager activate <skill-id> --from-router <router-slug>
```

For personal command collections where the names themselves are useful, expose selected commands as stubs:

```bash
skillager expose personal/deploy-preview --mode stub --agent codex --scope project
```

A stub is a tiny native skill containing the author summary and activation command. It does not include the full skill body.

Router and stub skills include compatibility notes when Skillager sees strong harness-specific assumptions. Those notes are advisory unless the source skill explicitly declares `exclusive_to` or `incompatible_with`.

## Updating A Collection

`refresh` re-walks and re-scans the local directory. It does not run `git pull`.

```bash
cd ~/skills/community
git pull
skillager collection refresh community
skillager doctor --include-global
```

Reviewed git-backed collection skills are approved by logical source and content hash, so the same unchanged skill can appear in another clone or project without another approval prompt. Changed skill content gets a new content hash and must be reviewed again before activation. Use `--project-only` with `setup`, `review approve`, or `review pin` when a decision should not be reusable.
