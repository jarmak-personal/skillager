# Skillager

Skillager is a local CLI for discovering, reviewing, organizing, and materializing agent skills without loading every skill into every chat.

```text
Register/discover -> review -> approve -> search metadata -> materialize on demand
```

Projects, Python libraries, npm packages, tools, and personal skill repos can ship useful agent guidance. Skillager keeps that guidance searchable and reviewed, but out of the agent context until it is needed.

## Quickstart

1. Install skillager
```bash
uv tool install skillager
# or: pipx install skillager
```
2. If you have any skill-collection repos like [superpowers](https://github.com/obra/superpowers) add them to skillager
   - `skillager collection add path/to/your/collection --name descriptive_name`
3. Run `skillager setup --agent [your agent]` in your project dir
   - Installation of skillager is global, setup is run per-project
   - Work through any required approvals etc. (don't blindly trust skills from sources you don't know.)
4. Open your agent of choice and tell them to run `skillager handoff --agent [your agent]`

`setup` automatically discovers project skills, package-provided skills from Python environments and project `node_modules`, Python environment skills from project virtualenv or conda environments, collections, and native agent skills. It scans them and only marks content available to your agent after user approval. Skillager is meant to be installed once as a user tool; it does not need to live inside every project environment.

Skillager writes a small project note in `AGENTS/CLAUDE.MD` so the agent knows to run `skillager working`, use metadata commands, and ask you to run setup or bootstrap when user-authority review is needed.


> [!IMPORTANT]
> Upgrading from 0.5.x? Tags are now project-local at `<project>/.skillager/tags.json`, and lookback/session telemetry has been removed.
>
> After setup has recorded your projects, run `skillager state migrate-tags --to projects`, then refresh active projects with `skillager setup --agent your-agent`.

## Core Model

Skillager keeps three decisions separate:

- **Approval:** the user reviewed a skill at its current content hash.
- **Curation:** a project groups available skills into local tags such as `gis`, `workflows`, or `release`.
- **Materialization:** Skillager writes native, stub, or router skills for a specific agent and project.

> [!TIP]
> Approval is not materialization. Keep useful skills approved and searchable, then materialize only what the current project needs.
>
> Routers are usually the best fit for larger tags because the agent sees one compact skill and activates specific reviewed skills on demand.

Materialization modes:

- `native`: copy the full reviewed skill into the agent's project skill directory.
  - Regular working project skills
- `stub`: write a tiny handle that activates the reviewed skill through Skillager.
  - Skills you want to only manually activate -- keep the context out of the agent.
- `router`: write one compact skill for a curated tag and let the agent choose from that tag.
  - Grouped skills to minimize context usage

Metadata commands stay metadata-only. `status`, `list`, `search`, `show` without `--content`, `handoff`, `lint`, and summary JSON outputs do not reveal full skill bodies.

## Daily Commands

- Review or refresh approvals: `skillager setup --agent codex`
- Repair first-party working artifacts: `skillager bootstrap --agent codex`
- Diagnose state: `skillager doctor --agent codex`
- Create a project tag: `skillager tag create spatial-python`
- Add a skill to a tag: `skillager tag add spatial-python vibespatial/gis-domain`
- Inspect a tag: `skillager tag show spatial-python`
- Materialize a tag as one router skill: `skillager materialize --tag spatial-python --mode router --agent codex --scope project`
- Materialize one skill as a stub: `skillager materialize vibespatial/gis-domain --mode stub --agent codex --scope project`
- Reuse tags across projects: `skillager tag sync --from ../project-a --to ../project-b`

For agent permission prompts, Skillager ships example read-only allowlists in [`examples/codex-allowlist.json`](examples/codex-allowlist.json) and [`examples/claude-allowlist.json`](examples/claude-allowlist.json). They cover metadata commands; setup, review, doctor fixes, and other mutating commands should stay user-run unless you intentionally delegate them.

## Collections

Skill repositories, shared skills, any grouping of skills etc are collections to skillager. Collections are a global inventory; project-local tags are optional project-level curation.

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --agent codex
skillager search "release workflow" --agent codex --json  # Usually run by your agent
```
After review, collection skills are searchable from any project on your machine. Use project-local tags only when you want a curated group or router/stub materializations:

```bash
skillager tag add workflows --from-collection workflows
skillager materialize --tag workflows --mode router --agent codex --scope project
```

## Package Authors

Python libraries and npm packages can ship skills inside the package:

```text
your_package/
  __init__.py
  .agents/skills/
    fastapi-usage/
      SKILL.md
      skillager.yaml
      references/
      scripts/
```

```text
your-npm-package/
  package.json
  .agents/skills/
    react-query-usage/
      SKILL.md
      skillager.yaml
```

Skillager discovers package skills after install from project Python environments, including virtualenv and conda environments, and from project `node_modules` without importing packages or running package scripts. Users still review and approve them before an agent can activate them.

`skillager.yaml` is optional and structured-only to support safe skills. Put searchable prose in `SKILL.md`; manifests can declare audience, activation, compatibility constraints, and typed package targets. For CI, run `uvx --from skillager-linter skillager-lint .`.

Published/shared skill roots may also include optional release evidence such as `skill.oms.sig`, `skill-card.md`, or `card.yaml`. Skillager keeps these separate from approval and search; use `skillager verify-signature <skill-id-or-path> --certificate-chain <pem>` when you explicitly want to verify a signed skill.

See the [package author guide](docs/LIBRARY_AUTHORS.md) for metadata and packaging details.

## Safety Model

The scanner is deterministic, local, and not perfect. It looks for common agent-risk patterns such as instruction override attempts, hidden prompt requests, secret exfiltration language, credential paths, download-and-execute flows, network callbacks involving secrets, unattended approval language, shell execution requests, hidden control characters, encoded blobs, and oversized content.

It is a review aid, not a proof of safety. Human review decides availability, and agent-facing metadata commands only surface approved metadata.

Full review metadata separates the decision axes: `approval` records the owner decision, while `review_gates` reports scan, lint, signature, and availability status. The legacy `trust` field remains for compatibility.

Signatures are treated as provenance/integrity evidence, not safety signals. A verified signed skill still requires normal review before activation or materialization.

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
