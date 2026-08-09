# Skillager

[![PyPI](https://img.shields.io/pypi/v/skillager?label=skillager&color=2563eb)](https://pypi.org/project/skillager/)
[![Agents](https://img.shields.io/badge/agents-Codex%20%7C%20Claude-0f766e)](docs/AGENT_CLI_GUIDE.md)
[![Packages](https://img.shields.io/badge/packages-Python%20%7C%20npm%20%7C%20Cargo-c2410c)](docs/LIBRARY_AUTHORS.md)
[![License](https://img.shields.io/badge/license-MIT-7c3aed)](LICENSE)

Skillager is a local personal library and approval/exposure layer for agent skills. It
gives skills you own a canonical, versioned home while keeping external skills
discoverable without loading every skill into every chat.

```text
own or discover -> accept an exact hash -> search metadata -> expose only what the task needs
```

Owned skills live as plain files in one personal Git-backed library. External skills
remain in project folders, Python environments, npm packages, Cargo crates, native
agent skill directories, or shared collections until you explicitly import one.
Skillager keeps accepted skills searchable, then writes native, stub, or router
exposures only when you choose.

## Quickstart

```bash
uv tool install skillager
# or: pipx install skillager

# Initialize the canonical library for skills you own.
skillager library init

# Run per project.
skillager setup --agent codex
```

Then restart your agent in the project and have it run:

```bash
skillager working --agent codex --json
```

`setup` discovers local/project skills, package-provided skills, collections, and native agent skills. It scans them and only makes content available after your review. Register an external personal/team repository with `skillager collection add ~/skills/workflows --name workflows` when you want it in reusable inventory. Skillager is installed once as a user tool; it does not need to live inside every project environment.

`working --json` uses the `skillager.working.v2` schema. Its advisory `exposure_changes` block reports current-project managed copies that are locally edited, intentionally kept local, partially missing, blocked, malformed, or unmanaged. Additive `inventory` and `curation` blocks distinguish source entries from agent-collapsed choices and suggest goal search without turning it into a required readiness action. When a router is already exposed, curation points to it first instead of recommending another router. Drift does not change readiness or the command's exit code. Plain `working` prints only a concise human status and next hint. Fully deleted exposure directories cannot be detected because Skillager intentionally keeps no cross-project exposure ledger.

## Core Model

Skillager keeps these choices separate:

| Choice | Meaning |
| --- | --- |
| Approval | You reviewed this skill at its current content hash. |
| Curation | A project groups approved skills into tags like `gis`, `workflows`, or `release`. |
| Exposure | Skillager writes native, stub, or router skills for an agent and project. |

> [!TIP]
> Approval is not exposure. Approved skills are searchable; expose only what a project or task needs.

## Exposure Modes

| Mode | Best For | What The Agent Sees |
| --- | --- | --- |
| `native` | Normal agent skills, as if Skillager were not involved | Full reviewed skill body |
| `stub` | Named skills you want available without loading the body | Tiny activation handle |
| `router` | Larger tags or one-off skill sets | One compact multi-skill router with supporting metadata |

Routers do not load full skill bodies. They list reviewed members and activate one on demand:

```bash
skillager expose --tag workflows --mode router --agent codex --scope project
skillager activate workflows/pr-review --from-router workflows
```

Metadata commands stay metadata-only: `working`, `list`, `search`, `show` without `--content`, `tag show`, `tag list`, `doctor`, read-only `reconcile`, and summary JSON do not print full skill bodies.

## Common Commands

| Task | Command |
| --- | --- |
| Review or refresh a project | `skillager setup --agent codex` |
| Check readiness and get a compact next hint | `skillager working --agent codex` |
| Diagnose state | `skillager doctor --agent codex` |
| Initialize your personal library | `skillager library init` |
| Inspect personal library and Git state | `skillager library status --json` |
| Create a pending personal skill | `skillager library new my-skill` |
| Locate or edit a personal skill | `skillager where lib/my-skill` / `skillager edit lib/my-skill` |
| Accept the exact current personal-skill hash | `skillager library accept lib/my-skill --yes` |
| Adopt a discovered external skill | `skillager import workflows/pr-review --yes` |
| Preview imported-skill upstream drift | `skillager import --refresh lib/pr-review --json` |
| Inspect verified personal-skill versions | `skillager library history lib/pr-review --json` |
| Restore a verified version as a new commit | `skillager library restore lib/pr-review --to <content-hash> --yes` |
| Create a living library variant | `skillager fork lib/pr-review --as pr-review-legacy --description "Review legacy branches" --yes` |
| Inspect current-project exposure changes | `skillager reconcile --agent codex --json` |
| Preview/apply clean library exposure updates | `skillager sync --agent codex --json` / `skillager sync --agent codex --apply` |
| Freeze/unfreeze one project exposure | `skillager pin lib/pr-review --agent codex` / `skillager unpin lib/pr-review --agent codex` |
| Keep an exact project-local edit | `skillager reconcile keep-local lib/pr-review --yes` |
| Promote an edited library exposure | `skillager reconcile promote lib/pr-review --yes` |
| Adopt an edited external exposure | `skillager reconcile import workflows/pr-review --as pr-review-local --yes` |
| Recover an exposure from library history | `skillager reconcile rollback lib/pr-review --yes` |
| Repair Skillager working artifacts | `skillager doctor --agent codex --fix` |
| Approve a skill | `skillager review approve workflows/pr-review` |
| Expose a tag as a router | `skillager expose --tag workflows --mode router --agent codex --scope project` |
| Expose explicit skills as one router | `skillager expose workflows/pr-review workflows/release-check --mode router --agent codex --scope project` |
| Expose one skill as a stub | `skillager expose workflows/pr-review --mode stub --agent codex --scope project` |

Read-only allowlist examples for agent permission prompts: [`codex`](examples/codex-allowlist.json), [`claude`](examples/claude-allowlist.json). Keep mutating commands user-run unless you intentionally delegate them.

## Personal Library

The personal library is the canonical home for skills you own. By default it lives at `~/.skillager/library`, uses an ordinary Git repository for history, and is registered internally as the protected `lib` collection.

```bash
skillager library init
skillager library status

# Choose the location once, or explicitly operate without Git history.
skillager library init --path ~/skills/personal
skillager library init --no-git
```

Initialization can adopt an existing directory without moving files. Existing skill bodies are indexed as pending metadata: initialization does not approve them, reveal their contents, or expose them to an agent. `library status` is read-only and reports identity, path, Git health, and an optional skill's working hash.

The no-Git form is useful for disposable environments and is also an opt-in,
isolated runnable documentation example:

<!-- skillager-test fixture=empty_project -->
```bash
skillager library init --no-git --json
skillager library status --json
skillager sync --agent codex --json
```

Create, edit, and accept an owned skill with an exact-hash workflow:

```bash
skillager library new orbital-review
skillager edit lib/orbital-review
skillager library accept lib/orbital-review --yes
skillager expose lib/orbital-review --mode stub --agent codex --scope project
```

`library new` never overwrites an existing skill. A new or directly edited body remains pending and unavailable to `show --content`, activation, exposure, stubs, and routers until `library accept` records its current hash. Acceptance runs lint and static scanning, requires `--override-lint --reason "..."` for blocking or high-risk findings, and creates a path-scoped Git commit when Git is enabled. In non-interactive use, omitting `--yes` prints a body-safe hash/risk/lint preview and the exact confirmed command without changing state. `where`, `library status`, and plain `edit` are metadata-only and read-only; `edit --open` launches `$EDITOR` and may make an accepted skill pending.

The machine-readable contracts are versioned as `skillager.library-init.v1`, `skillager.library-status.v1`, `skillager.library-new.v1`, `skillager.library-accept.v1`, `skillager.library-history.v1`, `skillager.library-restore.v1`, `skillager.library-fork.v1`, and `skillager.where.v1`.

Adopt one project, collection, environment, package, editable-source, or native skill through the explicit import boundary:

```bash
skillager import workflows/pr-review --json
skillager import workflows/pr-review --as pr-review --yes
skillager import --refresh lib/pr-review --json
```

The first command is a read-only preview. Import re-resolves and rehashes the origin after confirmation, copies only the canonical agent-visible tree, records provenance, commits the skill and provenance paths when Git is enabled, and accepts only the resulting library hash. It never imports or executes the surrounding package and never modifies the origin. `import --refresh` is preview-only: it compares the imported base, current upstream, and owned library hashes, and degrades safely if the upstream source was removed or renamed. Its JSON contracts are `skillager.import.v1` and `skillager.import-refresh.v1`.

Inspect and recover verified library versions by Skillager content hash:

```bash
skillager library history lib/pr-review --json
skillager library diff lib/pr-review --from <hash> --to <hash> --stat
skillager library diff lib/pr-review --from <hash> --to <hash>
skillager library restore lib/pr-review --to <hash> --yes
```

History is path-specific, deduplicates commits with identical agent-visible content, and never prints bodies. `diff --stat` is also metadata-only; plain `diff` is deliberately content-bearing. Restore accepts a unique content-hash prefix, reconstructs and verifies that exact historical tree outside the library, re-runs lint/static checks, and creates a new descendant commit before recording acceptance. It never resets, checks out over the worktree, rewrites history, or contacts remotes. No-Git libraries report history as unavailable while remaining otherwise usable. History and restore JSON use `skillager.library-history.v1` and `skillager.library-restore.v1`.

Create a living variant from the accepted head or a verified historical hash:

```bash
skillager fork lib/pr-review --as pr-review-legacy --description "Review legacy release branches" --json
skillager fork lib/pr-review --as pr-review-legacy --description "Review legacy release branches" --from <hash> --yes
```

Fork requires a distinct destination and description, writes exact `forked_from` lineage to library provenance, scans/lints the resulting tree, and accepts its new content hash. The preview is metadata-only and does not create the destination.

Propagate accepted library heads lazily in the project where you are working:

```bash
skillager sync --agent codex --json
skillager sync --agent codex --apply
skillager pin lib/pr-review --agent codex
skillager unpin lib/pr-review --agent codex
```

Bare sync is read-only. `--apply` replaces only clean, unpinned native or stub exposures in the current project. Customized, dirty, blocked, missing, malformed, external, pinned, and unaccepted-source entries are reported with stable JSON skip reasons, readable human explanations, and no changes. Previewed next commands preserve `--agent` scope. A pin freezes the exposure's exact current source hash; `--to` may identify that same hash but never rewrites a body. Use rollback or re-exposure to change versions. Sync and pin JSON use `skillager.sync.v1` and `skillager.pin.v1`.

### Reconcile Project Edits

`working` detects exposure drift; `reconcile` is the explicit edit-anywhere workflow:

```bash
skillager reconcile --agent codex --json
skillager reconcile lib/pr-review --agent codex --json
skillager reconcile keep-local lib/pr-review --agent codex --yes
skillager reconcile promote lib/pr-review --agent codex --yes
skillager reconcile rollback lib/pr-review --agent codex --yes
```

Bare `reconcile` and action previews are read-only and metadata-only. `keep-local` records only the exact customized hash, so another edit reappears as drift. `quarantine` moves every target file to a recoverable `.skillager-quarantine/exposures/` directory outside agent-visible roots and blocks that exact hash for the exposure; ordinary re-exposure cannot silently restore it. `repair` regenerates edited stubs and routers while first preserving their local bytes in quarantine.

For a native library exposure, `promote` succeeds only when the accepted library working tree and Git HEAD still equal the exposure's recorded base. It scans and accepts the edited exposure as a new path-scoped library commit, then advances the sidecar. Divergence reports both base-to-library and base-to-exposure file changes and leaves both sides untouched. For an edited external native exposure, use `reconcile import ... --as <name>`; the external source remains unchanged. `rollback` reconstructs the exposure's recorded source hash from verified library Git history and quarantines a dirty target before replacement. No-Git and external rollback report unavailable without writing.

Every mutation recomputes full hashes after confirmation under bounded resource locks. Non-interactive writes require `--yes`; blocking lint or high scanner risk on promote/import additionally requires `--override-lint --reason "..."`. Reconcile JSON uses `skillager.reconcile.v1`, `skillager.reconcile-action.v1`, and action-specific `skillager.reconcile-promote.v1`, `skillager.reconcile-import.v1`, and `skillager.reconcile-rollback.v1` schemas.

## Collections

Collections are external user-global skill sources. A collection can be a company-maintained repo or a public skill repo like [Superpowers](https://github.com/obra/superpowers). Use the personal library for canonical skills you own. Tags are project-local curation, usually maintained by the agent after setup.

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --collection workflows --agent codex
skillager tag add workflows --from-collection workflows --sync
skillager expose --tag workflows --mode router --agent codex --scope project
```

For fully trusted personal or company collections:

```bash
skillager setup --collection workflows --bulk-approve --agent codex
# same path, more fun:
skillager setup --collection workflows --yolo --agent codex
```

After review, collection skills are searchable from any project on your machine.

## Package Authors

Python libraries, npm packages, and Cargo crates can ship skills in `.agents/skills/`:

```text
your-package/
  .agents/skills/
    fastapi-usage/
      SKILL.md
      skillager.yaml
      references/
      scripts/
```

Skillager discovers installed package skills from project Python environments, `node_modules`, and `Cargo.lock`-selected crates without importing packages, running package scripts, or invoking Cargo. Users still review skills before activation or exposure.

`skillager.yaml` is optional and structured-only. Put searchable prose in `SKILL.md`; use the manifest for audience, activation, compatibility, and package-target metadata. For CI:

```bash
uvx --from skillager-linter skillager-lint .
```

See the [package author guide](docs/LIBRARY_AUTHORS.md) for details.

## Safety

Skillager's scanner is deterministic, local, and imperfect. It flags common agent-risk patterns such as instruction overrides, hidden prompt requests, credential paths, download-and-execute flows, secret exfiltration language, encoded blobs, and oversized content.

Setup reports how many current hashes were scanned, newly reviewed, or matched reusable global approvals. Scanner finding totals and per-skill risk distribution are labeled separately; medium/high skill IDs and rule codes are listed without bodies. `--fresh-project` clears project-local decisions and curation, not reusable approvals; an approval is reused only when both its logical source identity and exact content hash still match.

Human review decides availability. Signatures are provenance evidence, not safety signals: a verified signed skill still needs normal review before activation or exposure.

## Docs

- [User guide](docs/USER_GUIDE.md)
- [Agent CLI guide](docs/AGENT_CLI_GUIDE.md)
- [Skill repositories](docs/SKILL_REPOSITORIES.md)
- [Package author guide](docs/LIBRARY_AUTHORS.md)
- [Safety model](docs/SAFETY_MODEL.md)
- [Release notes](docs/RELEASE_NOTES.md)
- [Release runbook](docs/RELEASE.md)
- [Security policy](SECURITY.md)

External contributions are not being accepted yet while the early API and workflow settle.

## Development

```bash
uv run python -m unittest discover -s tests
uv run python -m unittest discover -s packages/skillager-linter/tests
uv run --python 3.13 python scripts/check.py
uv build packages/skillager-linter
uv build
```

Skillager is released under the [MIT License](LICENSE).
