# Skillager User Guide

Skillager is a CLI gate between discovered skills and agent-native skill directories.

The normal loop is:

```text
setup --agent <agent> -> restart agent -> working --agent <agent> --json -> describe goal
```

## First Run In A Project

Run this from the directory where you will start Codex or Claude:

```bash
skillager setup --agent codex
```

Use `--agent claude` instead for Claude projects. `setup` is the user approval flow. It discovers skills, asks for audience scope when needed, scans selected skills, and prompts before approving anything. Audience scope uses only declared manifest metadata; skills without it are grouped as "everything else." When setup applies review changes with `--agent` or `--all-agents`, it also refreshes the first-party working artifacts unless artifact refresh is explicitly disabled.

Install Skillager as a global user tool with `uv tool install skillager` or `pipx install skillager`. It scans the current project's `.venv`, `venv`, `.conda`, project-local active conda environments, top-level `node_modules`, and `Cargo.lock`-selected Cargo crates for installed package skills, but ordinary projects do not need Skillager installed inside their own Python, JavaScript, or Rust environment.

At the end of interactive setup, Skillager asks which agent target you use and installs a small first-party `skillager-working` skill into that agent's project skill directory. It can also expose a small one-by-one set of approved skills that you want available in every session. Restart the agent in the same project directory, then tell it what you plan to do. The agent runs `skillager working --agent <agent> --json` after context resets and can use available metadata to add useful skills to project-local tags and expose narrow native skills, stubs, a compact router skill for a tag or explicit skill set, or nothing.

Setup does not expose every approved skill by default. Approval makes a skill available for consideration; tagging and exposure are reversible project ergonomics based on what you are doing.

Run `skillager doctor --agent codex` when the state seems off or the agent is stuck. Use `skillager doctor --agent codex --fix` to repair first-party working artifacts and project notes. `doctor` does not approve skills or expose third-party skills; it reports the exact setup, repair, lint, or migration action to run. Use `skillager doctor --json` when you want a broader machine-readable diagnostic report. These commands avoid printing skill bodies.

## Trust States

- `discovered`: found and scanned, not approved.
- `reviewed`: approved for the current content hash.
- `trusted`: stronger user trust for recurring use.
- `pinned`: approved for an exact content hash.
- `blocked`: hidden from normal search, activation, and exposure.
- `lint_blocked`: manifest or structure failed a blocking lint rule; hidden from normal list/search/show/expose flows until fixed or explicitly overridden.

Agent-facing commands hide `discovered` and `lint_blocked` skills from normal use. Use setup, review, doctor, or `review --collection <name> --include-lint-blocked --json` yourself when you want to inspect why a skill is not available.

For diagnostics, full JSON and review output split this into `approval` plus `review_gates`: scan risk, lint status, signature verification status, and availability reason. For example, an unreviewed low-risk signed skill may show `approval=unreviewed scan=low lint=ok signature=not_checked availability=blocked_until_review`.

Approvals for portable sources, such as git-backed skill repositories, registered collections, Python packages, npm packages, and Cargo packages, are reusable across projects by default. Skillager stores the logical source key and current content hash in the reusable catalog state. If the same skill content appears in another clone or project, it is treated as already approved; if the content changes, the approval no longer matches and the skill returns to review. Use `--project-only` with `setup`, `review approve`, or `review pin` when an approval should stay local to the current project.

Direct native skills are not automatically approved. If you place a skill in a project or global agent skill directory, Skillager discovers and scans it, but it remains `discovered` until reviewed. For self-authored project skills, create `.agents/skills/<slug>/SKILL.md` manually or with your authoring tools, then run setup and review the discovered content before approval.

## Manifest Lint

`skillager.yaml` is structured metadata only. Skill identity and searchable prose come from `SKILL.md`, not from manifest free text.

For author and CI checks, use the standalone linter to inspect safe lint findings:

```bash
uvx --from skillager-linter skillager-lint .
```

At runtime, setup/review diagnostics and `skillager review --collection <name> --include-lint-blocked --json` report finding codes, fields, and safe details. They do not print skill bodies or raw manifest contents. Fix lint-blocked manifests when possible. To approve one anyway, use an explicit audited override:

```bash
skillager review approve <skill-id> --override-lint --reason "Reviewed manifest and accepted the finding"
```

The override is tied to the current content hash and finding identities. Content changes or new blocking lint findings require a new review.

## Useful Commands

```bash
skillager doctor
skillager working --agent codex --json
skillager setup --agent codex
skillager setup --fresh
skillager setup --fresh-project --agent codex
skillager setup --details
skillager setup --summary-json
skillager setup --source project --accept-low --agent codex --summary-json
skillager doctor --agent codex
skillager doctor --agent codex --fix
skillager list --summary-json --agent codex
skillager search "spatial workflow" --agent codex --json
skillager setup --collection workflows --agent codex
skillager setup --collection workflows --bulk-approve --agent codex
skillager setup --collection workflows --yolo --agent codex
skillager setup --collection workflows --bulk-approve --project-only --agent codex
skillager review --summary
skillager review approve <skill-id>
skillager review approve <skill-id> --project-only
skillager review approve <skill-id> --override-lint --reason "Reviewed manifest and accepted the finding"
skillager review pin <skill-id>
skillager review pin <skill-id> --project-only
skillager review block <skill-id>
skillager review unblock <skill-id>
skillager tag add gis vibespatial/gis-domain
skillager tag add workflows --from-collection community --sync
skillager tag show workflows
skillager tag list
skillager tag delete workflows
skillager tag sync --from ../project-a --to .
skillager expose --tag gis --mode router --agent codex --scope project
skillager expose <skill-id> <skill-id> --mode router --agent codex --scope project
skillager expose <skill-id> --mode stub --agent codex --scope project
```

Use a tag router for a named reusable group, or pass explicit skill IDs for a deterministic ad-hoc router without creating a tag. Router exposure writes compact available metadata only, not full skill bodies, and skips unavailable or incompatible members. The expose output and JSON give the router exposure id/slug; activate a listed skill with `skillager activate <skill-id> --from-router <router-slug>`.

Use `--json` when another program needs stable output. `working --agent <agent> --json`, `list --json`, `show --json`, `tag show --json`, `tag list --json`, and `search --json` are compact and available-only for agent use; pass `--full-json` for explicit user-directed Skillager diagnostics where available. Agents should use `working --agent <agent> --json`, `search --agent <agent> --json`, `list --summary-json --agent <agent>`, and project tag metadata to build their own candidate slate before deciding whether router, stub, native, or no new exposure fits the task. Use `doctor --json` and `setup --summary-json` for owner-run diagnostics and setup automation.

For a project-local automation smoke flow:

<!-- skillager-test fixture=basic_project -->
```bash
skillager working --agent codex --json
skillager setup --source project --accept-low --agent codex --no-packages --summary-json
skillager search "spatial" --json
```

The setup summary JSON includes compact first-party working artifact details when setup attempted or skipped artifact refresh. Automation should use `skillager working --agent <agent> --json` as the agent readiness contract and `skillager doctor --agent <agent> --json` for owner diagnostics.

Skillager does not require git. In a plain directory, it treats the current directory as the project root. Project state is user-local at `${XDG_STATE_HOME:-~/.local/state}/skillager/projects/<sha256(project_path)>/`, or `SKILLAGER_STATE_DIR` when explicitly set. Reusable catalog state is separate at `${XDG_CONFIG_HOME:-~/.config}/skillager/`, or `SKILLAGER_CATALOG_STATE_DIR` / `--catalog-state-dir` when explicitly set.

Legacy in-tree `<project>/.skillager/` trust state is ignored by ordinary commands. If you are upgrading from an older Skillager version, review any old decisions you still trust, remove the obsolete legacy state after review, and rerun setup so current content hashes are reviewed through the normal flow.

Use `--bulk-approve` only for fully trusted sources. It marks all selected skills reviewed, including medium, high-risk, and lint-blocked findings, and records the current content hashes. For lint-blocked skills it writes an audited shortcut override reason. `--yolo` is the fun alias for the same serious bulk approval path.

Use `skillager setup --fresh` to clear only project-local trust decisions for the selected setup scope. Reusable global approvals still apply if the source key and content hash match. Use `skillager setup --fresh-project --agent codex` when you want to reset project-local Skillager state and refresh Codex working artifacts in one run: it clears project-local decisions, project tags, legacy session records, and saved setup scope for the selected scope. It reports, but does not delete, retained reusable global approvals, global catalog collections, and exposed skill files.

`skillager list` shows the effective project inventory and hides global native skills unless you pass `--include-global`. Use `skillager list --no-packages` when you want local project, registered collection, and project-tag inventory without installed package skills. Use `skillager list --summary-json --agent codex` when an agent needs compact orientation: it includes counts, every listed skill ID, and duplicate native-variant hints. Use `skillager list --json --full-json` only for verbose Skillager diagnostics.

Collection repositories are user-global catalog inventory for source administration, review, refresh, and debugging. Ordinary `skillager setup` includes registered collection skills; `skillager setup --collection <name> --agent codex` narrows review to one collection. For a fully trusted collection, use `skillager setup --collection <name> --bulk-approve --agent codex`; `--yolo` is the optional alias. After review, available collection skills are searchable from any project using the same catalog. Use project-local tags when you want task/project curation or router/stub exposure.

Tags are project-local curation. Users can curate them manually, and agents can maintain them after setup by adding available skills that match the current project or task. `tag add` accepts available registered collection skill IDs and available IDs from the current project inventory, including skills from auto-discovered child repositories. Use `skillager tag add <tag> --from-collection <collection> --sync` to create or refresh a project tag from a reviewed collection; use `tag show`, `tag list`, `tag delete`, and `tag sync` for ongoing tag management.

Setup and doctor repair keep a best-effort registry of known project paths in the user catalog. It is only for tag discovery/sync convenience; missing or stale entries do not affect normal project operation. Use `skillager tag sync --from <project> --to .` to copy tag curation explicitly between projects, or recreate older global tag attachments with `skillager tag add` after review.

`skillager doctor` is the human diagnostic command. It reports cached Skillager update information when present, but it does not contact PyPI or write update-check cache files unless the selected diagnostic path explicitly says it will.

Use `skillager doctor --agent <agent> --fix` when review is already complete but working artifacts are missing or stale. Use `skillager expose` directly when you already know a reviewed skill or tag should be exposed to the agent. Normal exposure uses explicit skill IDs or `--tag`; owner/admin bulk exposure can use `--all-reviewed`. `expose` does not install or repair Skillager Working or project working notes.

Use `--mode stub` for skills you want visible by name without loading the full skill body into every session. A stub contains only the skill summary and an activation command; the full body still comes through Skillager's approval gate. After setup, Skillager prints numbered available-but-hidden stub candidates so you can say “please stub 1, 5, 8.”

`skillager.yaml` files can be added manually or by external authoring tools to existing skill directories. They record audience and activation metadata only; identity and searchable prose remain derived from `SKILL.md` and path/source provenance. After changing sidecars for skills already reviewed, run `skillager setup` again so the new content hashes are reviewed.

Published skill collections may include detached OMS signatures (`skill.oms.sig`) and skill cards, usually `skill-card.md` or `card.yaml`, as release evidence. Skillager keeps these separate from approval: signed release evidence can be inspected with external signing tooling, but verified content still goes through normal setup/review before activation. External verification is read-only, so indexed review metadata continues to show `signature=not_checked` until Skillager has a provenance cache.

Environment overrides:

```bash
SKILLAGER_RETENTION_DAYS=30
SKILLAGER_MAX_EVENT_MB=5
SKILLAGER_MAX_EVENTS_PER_SESSION=200
SKILLAGER_STATE_DIR=/path/to/project-state
SKILLAGER_CATALOG_STATE_DIR=/path/to/catalog-state
```
