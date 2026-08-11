# Skillager User Guide

Skillager is a local personal skill library with an approval and exposure gate between
all skill sources and agent-native skill directories. Skills you own use the reserved
`lib/<name>` namespace; discovered external skills stay at their source unless you
explicitly import them.

There are two connected loops:

```text
own:     library new or import -> edit canonical files -> accept exact hash -> expose
project: setup --agent <agent> -> restart -> working --json -> describe goal -> curate
```

The personal library is optional for users who only consume external skills, but it
is the canonical path for skills you create, adopt, version, and maintain.

## First Run In A Project

Run this from the directory where you will start Codex or Claude:

```bash
skillager setup --agent codex
```

Use `--agent claude` instead for Claude projects. `setup` is the user approval flow. It discovers skills, asks for audience scope when needed, scans selected skills, and prompts before approving anything. Audience scope uses only declared manifest metadata; skills without it are grouped as "everything else." When setup applies review changes with `--agent` or `--all-agents`, it also refreshes each selected agent's first-party working skill unless artifact refresh is explicitly disabled.

Install Skillager as a global user tool with `uv tool install skillager` or `pipx install skillager`. It scans the current project's `.venv`, `venv`, `.conda`, project-local active conda environments, top-level `node_modules`, and `Cargo.lock`-selected Cargo crates for installed package skills, but ordinary projects do not need Skillager installed inside their own Python, JavaScript, or Rust environment.

At the end of interactive setup with approved inventory, Skillager asks which agent target you use and installs one small first-party `skillager-working` skill into that agent's project skill directory. A truly empty project needs no working artifact and can still be ready. The skill covers both quiet agent operation and explicit user-directed personal-library work; a second administration skill is not installed. Setup can also expose a small one-by-one set of approved skills that you want available in every session. Restart the agent in the same project directory, then tell it what you plan to do. The agent runs `skillager working --agent <agent> --json` after context resets and can use available metadata to add useful skills to project-local tags and expose narrow native skills, stubs, a compact router skill for a tag or explicit skill set, or nothing.

### Read-Only Exposure Drift

`working --json` retains schema `skillager.working.v1` and adds an advisory `exposure_changes` block. It classifies live current-project native, stub, and router targets as current, behind newly approved source content, locally edited, partially missing, exposure-blocked, malformed-sidecar, or unmanaged. Source updates receive exact re-expose guidance and no longer count as current/exposed; Skillager does not refresh them automatically. Drift never changes readiness, `can_proceed`, or the exit code. Additive `inventory` metadata reports approved source entries, agent-visible choices, collapsed alternate-agent variants, and source-entry exposure counts; first-party Working/router artifact counts remain separate. The `curation` block offers optional inventory/search commands when on-demand choices exist, explains a genuinely empty inventory, and points to exposed router tags before suggesting new curation; readiness-required repairs remain exclusively in `next`.

The check is metadata-only and read-only. It does not refresh external collections, write index entries, update sidecars, or alter target files. It does recompute personal-library hashes and complete managed-target state in memory so stale bytes, executable-mode changes, or extra excluded files cannot remain invisible. Discovery may reuse persisted fingerprints as cache-invalidation hints, but fingerprints never establish approval, drift, or mutation authority. Because there is no exposure ledger, a fully deleted exposure directory is not discoverable—partial deletion remains visible while its sidecar directory exists.

Setup does not expose every approved skill by default. Approval makes a skill available for consideration; tagging and exposure are reversible project ergonomics based on what you are doing.

Run `skillager doctor --agent codex` when the state seems off or the agent is stuck. Use `skillager doctor --agent codex --fix` to repair the first-party `skillager-working` skill. Skillager does not create or modify `AGENTS.md`, `agents.md`, or `CLAUDE.md`; the installed skill carries the agent protocol. An exact `## Skillager` section injected by an older release is no longer used and may be removed manually. `doctor` does not approve skills or expose third-party skills; it reports exact setup, repair, migration, and pending-owned acceptance commands. A pending owned edit remains advisory and does not make otherwise ready external work fail. Lint override reasons remain structured user input rather than executable placeholders. Use `skillager doctor --json` when you want a broader machine-readable diagnostic report. These commands avoid printing skill bodies.

## Trust States

- `discovered`: found and scanned, not approved.
- `reviewed`: approved for the current content hash.
- `trusted`: stronger user trust for recurring use.
- `pinned`: approved for an exact content hash.
- `blocked`: hidden from normal search, activation, and exposure.
- `lint_blocked`: manifest or structure failed a blocking lint rule; hidden from normal list/search/expose flows until fixed or explicitly overridden. `show <id>` can display a quarantined metadata-only record and safe lint findings, but `show --content` remains blocked.

Agent-facing commands hide `discovered` and `lint_blocked` skills from normal use. Use setup, review, doctor, `show <id>`, or `review --collection <name> --include-lint-blocked --json` yourself when you want to inspect why a skill is not available.

For diagnostics, full JSON and review output split this into `approval` plus `review_gates`: scan risk, lint status, signature verification status, and availability reason. For example, an unreviewed low-risk signed skill may show `approval=unreviewed scan=low lint=ok signature=not_checked availability=blocked_until_review`.

Approvals for portable sources, such as git-backed skill repositories, registered collections, Python packages, npm packages, and Cargo packages, are reusable across projects by default. Skillager stores the logical source key and current content hash in the reusable catalog state. If the same skill content appears in another clone or project, it is treated as already approved; if the content changes, the approval no longer matches and the skill returns to review. Use `--project-only` with `setup`, `review approve`, or `review pin` when an approval should stay local to the current project.

Direct native skills are not automatically approved. If you place a skill in a project or global agent skill directory, Skillager discovers and scans it, but it remains `discovered` until reviewed. For self-authored project skills, create `.agents/skills/<slug>/SKILL.md` manually or with your authoring tools, then run setup and review the discovered content before approval.

## Personal Library

The personal library is the canonical home for skills you own. It defaults to `~/.skillager/library`, uses an ordinary Git repository unless you choose `--no-git`, and stays usable as plain skill directories without Skillager.

External discovery remains independent: project, child-repository, environment,
package, collection, and native-agent skills continue to be indexed and reviewed in
place. Import only when you want to take ownership of a particular external skill.

```bash
skillager library init
skillager library relocate --path <moved-library-path>
skillager library new orbital-review
# Edit the SKILL.md path returned by library new.
skillager library accept lib/orbital-review --json
# Review the preview, then execute its next_command_argv exactly.
skillager library status lib/orbital-review --json
skillager expose lib/orbital-review --mode stub --agent codex --scope project
```

Creation and direct edits produce a pending exact hash. Pending library content is visible through path and diagnostic metadata, but Skillager will not emit or copy its body through `show --content`, activation, native/stub exposure, or routers—even with generic unreviewed or force flags. Run `library accept` after reviewing the current files. A non-interactive call without `--yes` prints a body-safe scanner/lint/hash preview, exits zero like other successful previews, and gives the exact confirmed command without changing state. That command contains an opaque confirmation token bound to the previewed state; direct or stale `--yes` commands are refused. Symlinks and excluded files refuse before Git or trust changes. Lint-blocking or high-risk findings require a real `--override-lint --reason "..."` before Skillager emits a confirmation command. When Git is enabled, Skillager commits only the selected skill path before recording acceptance and refuses conflicts, in-progress repository operations, or unrelated staged files.

`library new` leaves its generated placeholder uncommitted and returns the canonical `SKILL.md` path. The first meaningful version is created by `library accept`. `library status [<skill>]` reports the working/accepted/HEAD hashes, import attribution, Git state, and current-project exposures without printing the body. Scanner findings on metadata-only surfaces contain codes and locations, not matched body excerpts. Any out-of-band content change immediately stops matching the accepted hash and returns the skill to pending. If the library directory moves, `working` and doctor report degraded optional-library health without blocking external project work. Status asks for the missing path as structured recovery input rather than placing a fake value in executable argv; preview `library relocate --path <new-root>`, then add `--yes` to update only the registration after the stored library UUID is verified.

There is intentionally no general delete, rename, or unregister lifecycle in this release. To abandon a draft that has never been accepted or committed, first verify its pending state with `library status lib/<name>`, delete only that draft directory with normal file tools, then run `skillager library init` to refresh the registered library index. For an accepted skill, preserve or restore it through Git/history instead of treating manual deletion as a supported lifecycle operation.

### Import An External Skill

Import is the one-way boundary for adopting a discovered external skill as your own:

```bash
skillager import workflows/pr-review --json
# Review the preview, then execute its next_command_argv exactly.
```

The preview identifies the source, exact hash, destination, scanner/lint state, and whether owner review is required without writing library files. If multiple distinct discovered roots claim the requested external ID, import refuses the ambiguity instead of selecting a representative. Its returned token-bound command confirms an unambiguous reviewed state for a non-interactive import; a direct or stale `--yes` command is refused. Blocking lint or high scanner risk requires `--override-lint --reason "..."`; blocked sources must be unblocked separately. Destination-name collisions refuse unless you choose a free `--as` name.

After confirmation, Skillager discovers and rehashes the source again under the library mutation lock. It copies only the selected skill directory—not its surrounding Python/npm/Cargo package—and excludes evidence, generated sidecars, caches, symlinks, and transient editor files using the same rules as content hashing and exposure. The origin remains unchanged. The library copy records the source skill ID, imported hash, source type, and timestamp in `.skillager/provenance.json` for attribution and audit.

### Version History And Restore

Git-backed libraries expose verified Skillager versions without treating Git commit IDs as content identities:

```bash
skillager library history lib/orbital-review --json
skillager library diff lib/orbital-review --from <content-hash> --to <content-hash> --stat
skillager library diff lib/orbital-review --from <content-hash> --to <content-hash>
skillager library restore lib/orbital-review --to <content-hash> --json
# Review the preview, then execute its next_command_argv exactly.
```

History walks only the selected skill path, reconstructs eligible regular files from each commit, verifies their full Skillager content hash, and deduplicates commits with the same agent-visible tree. Its output includes unique short hashes, commit IDs and times, known operations, and HEAD/current/accepted markers without body text. Git commit IDs are never accepted in place of Skillager content hashes.

`library diff` defaults to comparing Git HEAD with the working tree. With `--to` but no `--from`, it compares the selected version with its predecessor. `--stat` reports only paths and counts; plain diff is intentionally content-bearing and suitable for direct human review.

Restore is preview-first. After confirmation it reconstructs the version outside the library, re-runs scanner/lint checks, verifies the exact hash and transaction tree fingerprint again under the library lock, replaces the selected working tree, and creates a new descendant commit. Blocking or high-risk historical versions require `--override-lint --reason "..."`. Conflicts, in-progress Git operations, changed previews, unsafe historical symlinks, current symlinks or excluded files, missing hashes, and unavailable history refuse before mutation. Preserve or remove noncanonical current files before restoring. No-Git libraries remain usable but report history-dependent commands as unavailable.

### Managed Exposure Edits

Exposed native skills, stubs, and routers are managed project projections, not alternate canonical sources. Skillager detects live edits through `working`, reports them as advisory metadata, and refuses to overwrite them during ordinary exposure. It does not guess whether an edit should be kept, promoted, imported, or discarded.

For an intentional edit to an owned skill, compare the exposed copy with the canonical path from `library status`, move the intended work into the library, preview and accept that exact hash, then expose it again. Drift and replacement checks cover every target entry, including caches, bytecode, editor files, and other entries excluded from canonical source identity. Removal is preview-first with `skillager expose --remove <exposure-id> --json`; Skillager will not produce a normal removal command for a locally edited target. After preserving anything needed, an explicit `--force` preview produces a confirmation bound to the complete target, including the raw sidecar; any subsequent change invalidates it. Use `--force` only when you explicitly choose to discard or replace the local target. Nothing updates a sibling project or performs an automatic merge.

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

Interactive setup has a separate lint-blocked review lane. Choosing an override requires a non-empty reason and stores the same audited override as the CLI command above. When setup or review approves a lint-blocked skill with `--override-lint`, `--bulk-approve`, or `--yolo`, output includes an "Approved with audited lint override" receipt with the finding, reason, revisit command, and revoke command. `doctor` also reports how many lint overrides are currently in effect.

## Useful Commands

```bash
skillager doctor
skillager working --agent codex --json
skillager library init
skillager library status --json
skillager library new <name>
skillager library accept lib/<name> --json
skillager library status lib/<name> --json
skillager import <external-skill-id> --json
skillager import <external-skill-id> --as <name> --json
skillager library history lib/<name> --json
skillager library diff lib/<name> --from <hash> --to <hash> --stat --json
skillager library restore lib/<name> --to <hash> --json
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

Use `--json` when another program needs stable output. `working --agent <agent> --json`, normal `list --json`, `show --json`, `tag show --json`, `tag list --json`, and `search --json` are compact and available-only for agent use; `--full-json` implies JSON and is reserved for explicit user-directed diagnostics. Full metadata JSON may add diagnostic fields and paths, but scanner findings remain body-safe codes and locations and internal approval keys are omitted. `show --json` for a lint-blocked ID returns quarantined metadata and safe lint findings, not content. Agents should use `working --agent <agent> --json`, `search --agent <agent> --json`, `list --summary-json --agent <agent>`, and project tag metadata to build their own candidate slate before deciding whether router, stub, native, or no new exposure fits the task. Use `doctor --json` and `setup --summary-json` for owner-run diagnostics and setup automation.

For a project-local automation smoke flow:

<!-- skillager-test fixture=basic_project -->
```bash
skillager working --agent codex --json
skillager setup --source project --accept-low --agent codex --no-packages --summary-json
skillager search "spatial" --json
```

The setup summary JSON includes compact first-party working-skill details when setup attempted or skipped its refresh. Automation should use `skillager working --agent <agent> --json` as the agent readiness contract and `skillager doctor --agent <agent> --json` for owner diagnostics.

Skillager does not require git. In a plain directory, it treats the current directory as the project root. Project state is user-local at `${XDG_STATE_HOME:-~/.local/state}/skillager/projects/<sha256(project_path)>/`, or `SKILLAGER_STATE_DIR` when explicitly set. Reusable catalog state is separate at `${XDG_CONFIG_HOME:-~/.config}/skillager/`, or `SKILLAGER_CATALOG_STATE_DIR` / `--catalog-state-dir` when explicitly set.

Legacy in-tree `<project>/.skillager/` trust state is ignored by ordinary commands. If you are upgrading from an older Skillager version, review any old decisions you still trust, remove the obsolete legacy state after review, and rerun setup so current content hashes are reviewed through the normal flow.

Use `--bulk-approve` only for fully trusted sources. It marks all selected skills reviewed, including medium, high-risk, and lint-blocked findings, and records the current content hashes. For lint-blocked skills it writes an audited shortcut override reason and prints the override receipt. `--yolo` is the fun alias for the same serious bulk approval path.

Use `skillager setup --fresh` to clear only project-local trust decisions for the selected setup scope. Reusable global approvals still apply if the source key and content hash match. Use `skillager setup --fresh-project --agent codex` when you want to reset project-local Skillager state and refresh the Codex working skill in one run: it clears project-local decisions, project tags, legacy session records, and saved setup scope for the selected scope. It reports, but does not delete, retained reusable global approvals, global catalog collections, and exposed skill files. Setup's `approval_provenance` summary separates current hashes scanned locally, approvals newly recorded this run, and unchanged exact hashes accepted through reusable global approval. Human output explicitly says when owner prompts were not repeated for those exact matches.

`skillager list` shows the effective project inventory and hides global native skills unless you pass `--include-global`. Plain TTY output says when lint-blocked skills are hidden; pass `--include-lint-blocked` to include quarantined metadata-only rows. Use `skillager list --no-packages` when you want local project, registered collection, and project-tag inventory without installed package skills. Use `skillager list --summary-json --agent codex` when an agent needs bounded orientation: it includes counts, source-group IDs, small per-ID availability/exposure/tag rows, and duplicate native-variant hints—but omits repeated names, summaries, paths, and compatibility detail. Use targeted search/list JSON for detail and `skillager list --full-json` only for verbose Skillager diagnostics. “Metadata-only search” describes the output boundary: approved bodies may be read locally to improve ranking, but their text is not returned. Unreviewed bodies are not searchable evidence. Deliberately curated tag matches remain eligible search evidence even in a longer goal query.

Collection repositories are user-global catalog inventory for source administration, review, refresh, and debugging. Ordinary `skillager setup` includes registered collection skills; `skillager setup --collection <name> --agent codex` narrows review to one collection. For a fully trusted collection, use `skillager setup --collection <name> --bulk-approve --agent codex`; `--yolo` is the optional alias. After review, available collection skills are searchable from any project using the same catalog. Use project-local tags when you want task/project curation or router/stub exposure.

Tags are project-local curation. Users can curate them manually, and agents can maintain them after setup by adding available skills that match the current project or task. `tag add` accepts available registered collection skill IDs and available IDs from the current project inventory, including skills from auto-discovered child repositories. Use `skillager tag add <tag> --from-collection <collection> --sync` to create or refresh a project tag from a reviewed collection; use `tag show`, `tag list`, `tag delete`, and `tag sync` for ongoing tag management.

Setup and doctor repair keep a best-effort registry of known project paths in the user catalog. It is only for tag discovery/sync convenience; missing or stale entries do not affect normal project operation. Use `skillager tag sync --from <project> --to .` to copy tag curation explicitly between projects, or recreate older global tag attachments with `skillager tag add` after review.

`skillager doctor` is the human diagnostic command. It includes personal-library registration and path health as a separate advisory axis. A broken optional library is shown as degraded with recovery guidance, but does not make otherwise healthy external project discovery or working artifacts non-ready. It reports cached Skillager update information when present, but it does not contact PyPI or write update-check cache files unless the selected diagnostic path explicitly says it will.

Use `skillager doctor --agent <agent> --fix` when review is already complete but the `skillager-working` skill is missing or stale. Use `skillager expose` directly when you already know a reviewed skill or tag should be exposed to the agent. Normal exposure uses explicit skill IDs or `--tag`; owner/admin bulk exposure can use `--all-reviewed --mode stub`, while native exposure still requires explicit IDs or a tag and valid host frontmatter with non-empty `name` and `description`. A manifest-free external skill may still use stub, router, or on-demand activation. `expose` does not install or repair Skillager Working and never edits agent instruction files.

Removed pre-pruning command names such as `trust`, `block`, `bootstrap`, `status`, `state`, `project`, `new`, `manifest`, `index`, `scan`, and runtime `lint` now fail with the normal argparse invalid-choice error. Use the current surfaces: `review approve/pin/block/unblock`, `doctor`, `working`, `tag`, `setup`, and the standalone `skillager-lint` package for author linting.

Use `--mode stub` for skills you want visible by name without loading the full skill body into every session. A stub contains only the skill summary and an activation command; the full body still comes through Skillager's approval gate. After setup, Skillager prints up to 12 numbered available-but-hidden stub candidates so you can say “please stub 1, 5, 8.” With `--agent`, both the headline and candidate list use the same agent-collapsed inventory: for example, 49 approved source entries may become 39 Codex-ready choices when 10 Claude alternatives are collapsed.

`skillager.yaml` files can be added manually or by external authoring tools to existing skill directories. They record audience and activation metadata only; identity and searchable prose remain derived from `SKILL.md` and path/source provenance. After changing sidecars for skills already reviewed, run `skillager setup` again so the new content hashes are reviewed.

Published skill collections may include detached OMS signatures (`skill.oms.sig`) and skill cards, usually `skill-card.md` or `card.yaml`, as release evidence. Skillager keeps these separate from approval: signed release evidence can be inspected with external signing tooling, but verified content still goes through normal setup/review before activation. External verification is read-only, so indexed review metadata continues to show `signature=not_checked` until Skillager has a provenance cache.

## State And Backups

- Owned skill bodies and Git history live in `~/.skillager/library` by default, or the custom path shown by `skillager library status`. Back up the entire library directory, including its `.git` and `.skillager` metadata.
- Reusable approvals, collection registration, library registration, and the project registry live under `${XDG_CONFIG_HOME:-~/.config}/skillager/`, or `SKILLAGER_CATALOG_STATE_DIR` when overridden. Back this up with the library if reviewed-state continuity matters.
- Per-project setup scope, cached index state, and project-only approvals live under `${XDG_STATE_HOME:-~/.local/state}/skillager/projects/<sha256(project_path)>/`, or `SKILLAGER_STATE_DIR` when overridden. This state can be rebuilt, but project-only decisions must be reviewed again if it is lost.
- Project tags live at `<project>/.skillager/tags.json`. Managed exposures live in the agent's native skill directory with `skillager.materialized.yaml` beside each projection. Back up intentional project-local edits before re-exposure or forced removal.
- Current Codex user-scope exposure writes to `~/.agents/skills`. Skillager still discovers and can remove its own sidecar-backed targets under legacy `~/.codex/skills`, but does not migrate them automatically.

Environment overrides:

```bash
SKILLAGER_STATE_DIR=/path/to/project-state
SKILLAGER_CATALOG_STATE_DIR=/path/to/catalog-state
SKILLAGER_CACHE_DIR=/path/to/cache
```
