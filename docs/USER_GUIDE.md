# Skillager User Guide

Skillager is a CLI gate between discovered skills and agent-native skill directories.

The normal loop is:

```text
setup --agent <agent> -> restart agent -> working -> describe goal
```

## First Run In A Project

Run this from the directory where you will start Codex or Claude:

```bash
skillager setup --agent codex
```

Use `--agent claude` instead for Claude projects. `setup` is the user approval flow. It discovers skills, asks for audience scope when needed, scans selected skills, and prompts before approving anything. Audience scope uses only declared manifest metadata; skills without it are grouped as "everything else." When setup applies review changes with `--agent` or `--all-agents`, it also refreshes the first-party working artifacts unless `--no-bootstrap` is passed.

Install Skillager as a global user tool with `uv tool install skillager` or `pipx install skillager`. It scans the current project's `.venv` and installed packages for skills, but ordinary projects do not need Skillager installed inside their own virtual environment.

At the end of interactive setup, Skillager asks which agent target you use and installs a small first-party `skillager-working` skill into that agent's project skill directory. It can also materialize a small one-by-one set of approved skills that you want available in every session. Restart the agent in the same project directory, then tell it what you plan to do. The agent runs `skillager working` after context resets and can use available metadata to add useful skills to project-local tags and expose narrow native skills, stubs, a compact router skill for a tag, or nothing. Run `skillager handoff` when you want explicit post-setup curation guidance.

Setup does not materialize every approved skill by default. Approval makes a skill available for consideration; tagging and exposure are reversible project ergonomics based on what you are doing.

Run `skillager doctor --agent codex` when the state seems off or the agent is stuck. `doctor` does not approve skills or expose third-party skills; it reports the exact setup, bootstrap, lint, or migration command to run. Use `skillager status` when you want a broader metadata report. Both commands avoid printing skill bodies.

## Trust States

- `discovered`: found and scanned, not approved.
- `reviewed`: approved for the current content hash.
- `trusted`: stronger user trust for recurring use.
- `pinned`: approved for an exact content hash.
- `blocked`: hidden from normal search, activation, and materialization.
- `lint_blocked`: manifest or structure failed a blocking lint rule; hidden from normal list/search/show/materialize flows until fixed or explicitly overridden.

Agent-facing commands hide `discovered` and `lint_blocked` skills from normal use. Use setup, review, lint, or doctor yourself when you want to inspect why a skill is not available.

Approvals for portable sources, such as git-backed skill repositories, registered collections, and Python packages, are reusable across projects by default. Skillager stores the logical source key and current content hash in the reusable catalog state. If the same skill content appears in another clone or project, it is treated as already approved; if the content changes, the approval no longer matches and the skill returns to review. Use `--project-only` with `setup`, `review`, or `trust` when an approval should stay local to the current project.

Direct native skills are not automatically trusted. If you place a skill in a project or global agent skill directory, Skillager discovers and scans it, but it remains `discovered` until reviewed. Use `skillager new <skill-id>` for self-authored project skills; it scaffolds `.agents/skills/<slug>/SKILL.md` by default, records a user-local authored marker, and surfaces a fast `skillager trust <id> --state reviewed` hint after you review the content.

## Manifest Lint

`skillager.yaml` is structured metadata only. Skill identity and searchable prose come from `SKILL.md`, not from manifest free text.

Use `skillager lint` to inspect safe lint findings:

```bash
skillager lint
skillager lint <skill-id>
skillager lint --json
```

Lint output reports finding codes, fields, and safe details. It does not print skill bodies or raw manifest contents. Fix lint-blocked manifests when possible. To approve one anyway, use an explicit audited override:

```bash
skillager trust <skill-id> --override-lint --reason "Reviewed manifest and accepted the finding"
skillager review <skill-id> --override-lint --reason "Reviewed manifest and accepted the finding"
```

The override is tied to the current content hash and finding identities. Content changes or new blocking lint findings require a new review.

## Useful Commands

```bash
skillager status
skillager setup --agent codex
skillager setup --fresh
skillager setup --fresh-project --agent codex
skillager setup --details
skillager setup --summary-json
skillager setup --source project --accept-low --agent codex --summary-json
skillager bootstrap --agent codex
skillager doctor --agent codex
skillager list --summary-json --agent codex
skillager search "spatial workflow" --agent codex --json
skillager setup --source collection --trust-all
skillager setup --source collection --yolo
skillager setup --source collection --trust-all --project-only
skillager review --summary
skillager review <skill-id> --trust-selected reviewed
skillager lint
skillager new <skill-id>
skillager manifest init <path>
skillager state migrate
skillager block <skill-id>
skillager tag add gis vibespatial/gis-domain
skillager materialize --tag gis --mode router --agent codex --scope project
skillager materialize --all-reviewed --agent codex --scope project
skillager materialize --all-reviewed --agent claude --scope project
skillager materialize <skill-id> --mode stub --agent codex --scope project
```

Use `--json` when another program needs stable output. `status --json`, `handoff --json`, `list --json`, `show --json`, `tag show --json`, and `search --json` are compact and available-only for agent use; pass `--full-json` for explicit user-directed diagnostics where available. Agents should use `search --agent <agent> --json`, `list --summary-json --agent <agent>`, and project tag metadata to build their own candidate slate before deciding whether router, stub, native, or no new exposure fits the task. Use `doctor --json` and `setup --summary-json` for owner-run diagnostics and setup automation.

For a project-local automation smoke flow:

<!-- skillager-test fixture=basic_project -->
```bash
skillager status --no-packages --json
skillager setup --source project --accept-low --agent codex --no-packages --summary-json
skillager search "spatial" --json
```

The setup summary JSON includes a compact `bootstrap` object when setup attempted or skipped first-party working artifact refresh. Automation can check `bootstrap.handoff_ready` and follow `bootstrap.next_commands` without parsing human text.

Skillager does not require git. In a plain directory, it treats the current directory as the project root. Project state is user-local at `${XDG_STATE_HOME:-~/.local/state}/skillager/projects/<sha256(project_path)>/`, or `SKILLAGER_STATE_DIR` when explicitly set. Reusable catalog state is separate at `${XDG_CONFIG_HOME:-~/.config}/skillager/`, or `SKILLAGER_CATALOG_STATE_DIR` / `--catalog-state-dir` when explicitly set.

Legacy in-tree `<project>/.skillager/` state is ignored by ordinary commands. If you intentionally want to import reviewed local state from an older Skillager version, run `skillager state migrate` from the project and review the records it will copy. Legacy reusable `global_approvals` require the separate `skillager state import-global-approvals` command.

Use `--trust-all` or `--yolo` only for fully trusted sources. They are aliases: both mark all selected skills reviewed, including medium, high-risk, and lint-blocked findings, and record the current content hashes. For lint-blocked skills they write an audited shortcut override reason.

Use `skillager setup --fresh` to clear only project-local trust decisions for the selected setup scope. Reusable global approvals still apply if the source key and content hash match. Use `skillager setup --fresh-project --agent codex` when you want to reset project-local Skillager state and refresh Codex working artifacts in one run: it clears project-local decisions, project tags, legacy session records, and saved setup scope for the selected scope. It reports, but does not delete, retained reusable global approvals, global catalog collections, and materialized skill files.

`skillager list` shows the effective project inventory and hides global native skills unless you pass `--include-global`. Use `skillager list --no-packages` when you want local project, registered collection, and project-tag inventory without installed package skills. Use `skillager list --summary-json --agent codex` when an agent needs compact orientation: it includes counts, every listed skill ID, and duplicate native-variant hints. Use `skillager list --json --full-json` only for verbose Skillager diagnostics.

Collection repositories are user-global catalog inventory. Ordinary `skillager setup` includes registered collection skills; `skillager setup --source collection` narrows review to collections only. After review, available collection skills are searchable from any project using the same catalog. Use project-local tags only when you want task/project curation or router/stub exposure.

Tags are project-local curation. Users can curate them manually, and agents can maintain them after setup by adding available skills that match the current project or task. `tag add` accepts available registered collection skill IDs and available IDs from the current project inventory, including skills from auto-discovered child repositories.

Setup and bootstrap keep a best-effort registry of known project paths in the user catalog. It is only for tag discovery/sync convenience; missing or stale entries do not affect normal project operation. Use `skillager tag sync --from <project> --to .` to copy tag curation explicitly between projects, or `skillager state migrate-tags --to projects` once when migrating older global tag attachments.

`skillager status` checks PyPI for Skillager updates at most once per day and prints `uv tool upgrade skillager` when a newer release is available. Network failures are silent. Set `SKILLAGER_NO_UPDATE_CHECK=1` to disable this check.

Use `skillager bootstrap --agent <agent>` when review is already complete but working artifacts are missing or stale. Use `skillager materialize` directly when you already know a reviewed skill or tag should be exposed to the agent. `materialize` requires explicit skill IDs, `--tag`, or `--all-reviewed`; it does not install or repair Skillager Working or project working notes.

Use `--mode stub` for skills you want visible by name without loading the full skill body into every session. A stub contains only the skill summary and an activation command; the full body still comes through Skillager's approval gate. After setup, Skillager prints numbered available-but-hidden stub candidates so you can say “please stub 1, 5, 8.”

`skillager manifest init <path>` can add a minimal structured `skillager.yaml` to existing skill directories. It records audience and activation metadata only; identity and searchable prose remain derived from `SKILL.md` and path/source provenance. If it writes sidecars for skills already reviewed, run `skillager setup` again so the new content hashes are reviewed.

Environment overrides:

```bash
SKILLAGER_RETENTION_DAYS=30
SKILLAGER_MAX_EVENT_MB=5
SKILLAGER_MAX_EVENTS_PER_SESSION=200
SKILLAGER_STATE_DIR=/path/to/project-state
SKILLAGER_CATALOG_STATE_DIR=/path/to/catalog-state
```
