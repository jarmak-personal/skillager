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

# Run per project. A personal library is optional until you create or adopt a skill.
skillager setup --agent codex
```

Then restart your agent in the project and have it run:

```bash
skillager working --agent codex --json
```

Once setup has approved inventory, it installs one first-party `skillager-working`
skill. It covers quiet agent-owned selection/exposure and explicit user-directed
personal-library work; Skillager does not inject instructions into `AGENTS.md`,
`agents.md`, or `CLAUDE.md`. A genuinely empty project can be ready without that
artifact because there is no managed inventory for the agent to curate yet.

`setup` discovers local/project skills, package-provided skills, collections, and native agent skills. Skillager makes discovered bodies available through its own activation and exposure commands only after review. Agent hosts can independently load directly installed native skills, so Skillager is a cooperative workflow layer rather than a sandbox. Register an external personal/team repository with `skillager collection add ~/skills/workflows --name workflows` when you want it in reusable inventory. Skillager is installed once as a user tool; it does not need to live inside every project environment.

`working --json` keeps the `skillager.working.v1` contract. Its additive advisory `exposure_changes` block reports live current-project managed copies that are locally edited, behind newly approved source content, partially missing, blocked, malformed, or unmanaged. A stale projection is excluded from current exposure counts and receives an explicit re-expose command; it is never refreshed automatically. `inventory` and `curation` distinguish source entries from agent-collapsed choices and suggest goal search without turning it into a required readiness action. Drift does not change readiness or the command's exit code, and Skillager never overwrites a locally edited exposure implicitly.

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

Metadata commands stay metadata-only: `working`, `list`, `search`, `show` without `--content`, `tag show`, `tag list`, `doctor`, `library status`, `library history`, `library diff --stat`, and summary or full metadata JSON do not print full skill bodies. Scanner summaries on those surfaces contain rule codes and locations, never matched instruction excerpts.

## Common Commands

| Task | Command |
| --- | --- |
| Review or refresh a project | `skillager setup --agent codex` |
| Check readiness and get a compact next hint | `skillager working --agent codex` |
| Diagnose state | `skillager doctor --agent codex` |
| Initialize your personal library | `skillager library init` |
| Inspect personal library and Git state | `skillager library status --json` |
| Create a pending personal skill | `skillager library new my-skill` |
| Locate a personal skill | `skillager library status lib/my-skill` |
| Preview acceptance of the exact current personal-skill hash | `skillager library accept lib/my-skill --json` |
| Preview adoption of a discovered external skill | `skillager import workflows/pr-review --json` |
| Inspect verified personal-skill versions | `skillager library history lib/pr-review --json` |
| Compare personal-skill versions | `skillager library diff lib/pr-review --from <hash> --to <hash>` |
| Preview restoring a verified version as a new commit | `skillager library restore lib/pr-review --to <content-hash> --json` |
| Repair the Skillager working skill | `skillager doctor --agent codex --fix` |
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

# If the same library directory is moved later, preview and confirm re-registration.
skillager library relocate --path ~/skills/moved-personal
skillager library relocate --path ~/skills/moved-personal --yes
```

Initialization can adopt an existing directory without moving files. Existing skill bodies are indexed as pending metadata: initialization does not approve them, reveal their contents, or expose them to an agent. Git-backed initialization preflights required metadata against ignore rules and rolls back files and staging if its first commit fails, so retrying does not enter a partial-initialization trap. `library status` is read-only and reports identity, path, Git health, and an optional skill's working hash. If the registered path disappears, `working` and `doctor` report the library as degraded without blocking unrelated project discovery. Status returns structured relocation requirements without inventing a path; once the user supplies the moved root, `library relocate` changes only the registration after verifying the UUID and existing layout there.

The no-Git form is useful for disposable environments and is also an opt-in,
isolated runnable documentation example:

<!-- skillager-test fixture=empty_project -->
```bash
skillager library init --no-git --json
skillager library status --json
```

Create, edit, and accept an owned skill with an exact-hash workflow:

```bash
skillager library new orbital-review
# Edit the SKILL.md path printed by library new.
skillager library accept lib/orbital-review --json
# Review the preview, then execute its next_command_argv exactly.
skillager expose lib/orbital-review --mode stub --agent codex --scope project
```

`library new` never overwrites an existing skill and leaves its generated draft uncommitted. A new or directly edited body remains pending and unavailable to `show --content`, activation, exposure, stubs, and routers until `library accept` records its current hash. Acceptance runs lint and static scanning, rejects symlinks and excluded files, requires `--override-lint --reason "..."` for blocking or high-risk findings, and creates the first meaningful path-scoped Git commit when Git is enabled. Shared import provenance is confirmation-bound and unrelated staged provenance edits are refused. In non-interactive use, omitting `--yes` prints a body-safe hash/risk/lint preview, exits successfully, and gives an exact confirmation command containing an opaque token. The token binds the command to the previewed hash, relevant provenance state, and any audited reason; a direct or stale `--yes` command is refused. Doctor keeps pending owned edits nonblocking but reports their exact `library accept` preview commands.

The machine-readable contracts are versioned as `skillager.library-init.v1`, `skillager.library-relocate.v1`, `skillager.library-status.v1`, `skillager.library-new.v1`, `skillager.library-accept.v1`, `skillager.library-history.v1`, `skillager.library-diff.v1`, and `skillager.library-restore.v1`.

Adopt one project, collection, environment, package, editable-source, or native skill through the explicit import boundary:

```bash
skillager import workflows/pr-review --json
# Review the preview, then execute its next_command_argv exactly.
```

The first command is a read-only preview. Import refuses an ID claimed by multiple discovered roots instead of choosing one representative. It re-resolves and rehashes the unambiguous origin after confirmation, copies only the canonical agent-visible tree, records attribution provenance, commits the skill and provenance paths when Git is enabled, and accepts only the resulting library hash. Mode-only source changes invalidate cached previews. Destination JSON keeps the canonical `id`, stable `slug`/`name`, and retained frontmatter `display_name` separate. Import never executes the surrounding package and never modifies the origin. Its JSON contract is `skillager.import.v1`.

Inspect and recover verified library versions by Skillager content hash:

```bash
skillager library history lib/pr-review --json
skillager library diff lib/pr-review --from <hash> --to <hash> --stat
skillager library diff lib/pr-review --from <hash> --to <hash>
skillager library restore lib/pr-review --to <hash> --json
# Review the preview, then execute its next_command_argv exactly.
```

History is path-specific, deduplicates commits with identical agent-visible content, and never prints bodies. `diff --stat` is also metadata-only; plain `diff` is deliberately content-bearing. Restore accepts a unique content-hash prefix, reconstructs and verifies that exact historical tree outside the library, re-runs lint/static checks, and creates a new descendant commit before recording acceptance. It never resets, checks out over the worktree, rewrites history, or contacts remotes. No-Git libraries report history as unavailable while remaining otherwise usable. History and restore JSON use `skillager.library-history.v1` and `skillager.library-restore.v1`.

### Managed Exposure Edits

The personal library is the source of truth for owned skills; exposed copies are managed projections. `working` reports live local edits as advisory metadata, and normal exposure refuses to overwrite them. Exposure safety tracks every target entry, including cache, bytecode, editor, and other files excluded from canonical skill identity. When an exposure was edited intentionally, compare it with the canonical library skill, move the intended work into the library, accept that exact hash, and expose it again. Removal is also preview-first: `skillager expose --remove <exposure-id> --json` returns a confirmation bound to the complete target state; a locally edited target additionally requires a new preview with explicit `--force`. Use `--force` only when you explicitly choose to discard or replace the local copy. Skillager does not infer whether a project edit should become a library version, silently merge divergent trees, or update other projects.

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
