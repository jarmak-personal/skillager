# Skillager

Skillager is a local CLI for discovering, reviewing, and exposing agent skills without loading every skill into every chat.

```text
discover -> review -> approve -> search metadata -> expose on demand
```

Projects, Python libraries, tools, and personal skill repos can ship useful agent guidance. Skillager keeps that guidance searchable and reviewed, but out of the agent context until it is needed.

## Quickstart

```bash
uv tool install skillager
# or: pipx install skillager

cd my-project
skillager setup --agent codex
# or: skillager setup --agent claude
```

`setup` discovers project skills, package-provided skills, virtualenv skills, collections, and native agent skills. It scans them and only marks content available after user approval. Skillager is meant to be installed once as a user tool; it does not need to live inside every project virtualenv.

Restart Codex or Claude in the same directory after setup. Skillager writes a small project note so the agent knows to run `skillager working`, use metadata commands, and ask you to run setup or bootstrap when user-authority review is needed.

Useful first commands:

```bash
skillager handoff --agent codex
skillager list --summary-json --agent codex
skillager search "spatial data validation" --agent codex --json
```

> [!IMPORTANT]
> Upgrading from 0.5.x? Tags are now project-local at `<project>/.skillager/tags.json`, and lookback/session telemetry has been removed. After setup has recorded your projects, run `skillager state migrate-tags --to projects`, then refresh active projects with `skillager setup --agent codex` or `skillager bootstrap --agent codex`.

## Core Model

Skillager keeps three decisions separate:

- **Approval:** the user reviewed a skill at its current content hash.
- **Curation:** a project groups available skills into local tags such as `gis`, `workflows`, or `release`.
- **Exposure:** Skillager writes native, stub, or router skills for a specific agent and project.

> [!TIP]
> Approval is not exposure. Keep useful skills approved and searchable, then expose only what the current project needs. Routers are usually the best fit for larger tags because the agent sees one compact skill and activates specific reviewed skills on demand.

Exposure modes:

- `native`: copy the full reviewed skill into the agent's project skill directory.
- `stub`: write a tiny handle that activates the reviewed skill through Skillager.
- `router`: write one compact skill for a curated tag and let the agent choose from that tag.

Metadata commands stay metadata-only. `status`, `list`, `search`, `show` without `--content`, `handoff`, `lint`, and summary JSON outputs do not reveal full skill bodies.

## Daily Commands

- Review or refresh approvals: `skillager setup --agent codex`
- Repair first-party working artifacts: `skillager bootstrap --agent codex`
- Diagnose state: `skillager doctor --agent codex`
- Create a project tag: `skillager tag create spatial-python`
- Add a skill to a tag: `skillager tag add spatial-python vibespatial/gis-domain`
- Inspect a tag: `skillager tag show spatial-python --agent codex`
- Expose a tag as one router skill: `skillager materialize --tag spatial-python --mode router --agent codex --scope project`
- Expose one skill as a stub: `skillager materialize vibespatial/gis-domain --mode stub --agent codex --scope project`
- Reuse tags across projects: `skillager tag sync --from ../project-a --to ../project-b`

For agent permission prompts, Skillager ships example read-only allowlists in [`examples/codex-allowlist.json`](examples/codex-allowlist.json) and [`examples/claude-allowlist.json`](examples/claude-allowlist.json). They cover metadata commands; setup, review, doctor fixes, and other mutating commands should stay user-run unless you intentionally delegate them.

## Collections

Skill repositories are collections. Collections are inventory; project-local tags are curation.

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --agent codex
skillager setup --source collection --trust-all
skillager collection enable workflows
skillager materialize --tag workflows --mode router --agent codex --scope project
```

`collection enable` creates or updates a project-local tag using available reviewed collection skills. The agent sees one router skill, not the whole repo.

## Library Authors

Python libraries can ship skills inside the package:

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

Skillager discovers package skills after install without importing the package. Users still review and approve them before an agent can activate them.

`skillager.yaml` is optional and structured-only. Put searchable prose in `SKILL.md`; manifests can declare audience, activation, compatibility constraints, and typed package targets. For CI, run `uvx --from skillager-linter skillager-lint .`.

See the [library author guide](docs/LIBRARY_AUTHORS.md) for metadata and packaging details.

## Safety Model

The scanner is deterministic and local. It looks for common agent-risk patterns such as instruction override attempts, hidden prompt requests, secret exfiltration language, credential paths, download-and-execute flows, network callbacks involving secrets, unattended approval language, shell execution requests, hidden control characters, encoded blobs, and oversized content.

It is a review aid, not a proof of safety. Human review decides availability, and agent-facing metadata commands only surface approved metadata.

## Docs

- [User guide](docs/USER_GUIDE.md)
- [Agent CLI guide](docs/AGENT_CLI_GUIDE.md)
- [Skill repositories](docs/SKILL_REPOSITORIES.md)
- [Library author guide](docs/LIBRARY_AUTHORS.md)
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
