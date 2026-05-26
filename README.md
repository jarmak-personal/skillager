# Skillager

[![PyPI](https://img.shields.io/pypi/v/skillager?label=skillager&color=2563eb)](https://pypi.org/project/skillager/)
[![Agents](https://img.shields.io/badge/agents-Codex%20%7C%20Claude-0f766e)](docs/AGENT_CLI_GUIDE.md)
[![Packages](https://img.shields.io/badge/packages-Python%20%7C%20npm%20%7C%20Cargo-c2410c)](docs/LIBRARY_AUTHORS.md)
[![License](https://img.shields.io/badge/license-MIT-7c3aed)](LICENSE)

Skillager is a local CLI for discovering, reviewing, searching, and exposing agent skills without loading every skill into every chat.

```text
discover -> review -> search metadata -> expose only what the task needs
```

Skills can come from project folders, Python environments, npm packages, Cargo crates, native agent skill directories, or shared collections. Skillager keeps them searchable after review, then writes native, stub, or router skills for the agent when you choose to expose them.

## Quickstart

```bash
uv tool install skillager
# or: pipx install skillager

# Optional: register a personal/team collection, such as Superpowers.
skillager collection add ~/skills/workflows --name workflows

# Run per project.
skillager setup --agent codex
```

Then restart your agent in the project and have it run:

```bash
skillager working --agent codex --json
```

`setup` discovers local/project skills, package-provided skills, collections, and native agent skills. It scans them and only makes content available after your review. Skillager is installed once as a user tool; it does not need to live inside every project environment.

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

Metadata commands stay metadata-only: `working`, `list`, `search`, `show` without `--content`, `tag show`, `tag list`, `doctor`, and summary JSON do not print full skill bodies.

## Common Commands

| Task | Command |
| --- | --- |
| Review or refresh a project | `skillager setup --agent codex` |
| Diagnose state | `skillager doctor --agent codex` |
| Repair Skillager working artifacts | `skillager doctor --agent codex --fix` |
| Approve a skill | `skillager review approve workflows/pr-review` |
| Expose a tag as a router | `skillager expose --tag workflows --mode router --agent codex --scope project` |
| Expose explicit skills as one router | `skillager expose workflows/pr-review workflows/release-check --mode router --agent codex --scope project` |
| Expose one skill as a stub | `skillager expose workflows/pr-review --mode stub --agent codex --scope project` |

Read-only allowlist examples for agent permission prompts: [`codex`](examples/codex-allowlist.json), [`claude`](examples/claude-allowlist.json). Keep mutating commands user-run unless you intentionally delegate them.

## Collections

Collections are user-global skill sources. A collection can be a personal repo, a company-maintained repo, or a public skill repo like [Superpowers](https://github.com/obra/superpowers). Tags are project-local curation, usually maintained by the agent after setup.

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

Human review decides availability. Signatures are provenance evidence, not safety signals: a verified signed skill still needs normal review before activation or exposure.

## Docs

- [User guide](docs/USER_GUIDE.md)
- [Agent CLI guide](docs/AGENT_CLI_GUIDE.md)
- [Skill repositories](docs/SKILL_REPOSITORIES.md)
- [Package author guide](docs/LIBRARY_AUTHORS.md)
- [Safety model](docs/SAFETY_MODEL.md)
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
