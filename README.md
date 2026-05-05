# Skillager

Agent skills are useful. Loading all of them into every chat is not.

Skillager is a local CLI that lets projects, Python libraries, tools, and personal skill repos ship useful agent skills without turning every session into a wall of instructions. It discovers skills, scans them, asks for human approval, and gives agents a small, fast way to find the right skill only when the task needs it.

```text
install package -> discover skills -> approve safety -> agent uses approved metadata -> expose only what matters
```

## Quickstart

```bash
uv tool install skillager
cd my-project
skillager status
skillager setup
```

No uv:

```bash
pipx install skillager
# or
python -m pip install --user skillager
python -m skillager setup
```

`setup` is the approval gate. It discovers skills in the current project and environment, scans them, asks what audience you care about, and never trusts a skill unless you approve it.

After setup, restart Codex or Claude in the same directory and tell it what you are doing. Skillager installs a tiny project handoff so the agent knows to run `skillager handoff` once, use approved metadata, and avoid loading unapproved skill bodies.

## The Problem

Skills want to live everywhere:

- inside libraries, next to the APIs they explain
- inside projects, next to team workflows
- inside global agent directories
- inside personal or community skill repos

But agents should not see every skill all the time. Irrelevant skills burn context. Unreviewed skills are a safety risk. Similar skills compete. Package-installed skills are hard for agents to discover.

Skillager gives that ecosystem a local registry and approval gate.

## The Mental Model

Skillager keeps two decisions separate:

- **Approval:** the user reviewed a skill at its current content hash.
- **Exposure:** the skill is available to an agent in the current project.

An approved skill does not have to be loaded into the agent. It can stay in Skillager's index until a task needs it. When it should be available, Skillager writes one of three project-level representations:

- `native`: the full reviewed skill directory copied into the agent's project skill directory
- `stub`: a tiny native handle that activates the full skill through Skillager on demand
- `router`: one compact native skill for a curated tag like `gis`, `workflows`, or `release`

This keeps the default context small while still giving agents a deterministic path to approved skills.

## For Library Authors

If you maintain a Python library, Skillager gives you a way to ship agent-facing guidance with the package itself. Users can discover those skills after install, review them locally, and expose all, or just the ones relevant to their project.

[FastAPI](https://github.com/fastapi/fastapi) already does this — its wheel includes a skill at `fastapi/.agents/skills/fastapi/SKILL.md`, which Skillager discovers after a normal `pip install fastapi`.

A complete skillager layout:

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

When a user installs your package, Skillager can discover those skills without importing your library. The user still reviews and approves them before any agent can activate them.

`skillager.yaml` is optional and structured-only. Put prose in `SKILL.md`; manifests can declare audience, activation, compatibility constraints, and typed package targets, but not free-text identity, source, safety policy, or body paths.

For CI, library authors can run `uvx --from skillager-linter skillager-lint .` to validate the same manifest contract without installing the full Skillager runtime.

This lets a library ship:

- user-facing skills for using the API well
- maintainer skills for internal development workflows
- domain skills that explain correctness rules and edge cases
- migration skills for version upgrades

See the [library author guide](docs/LIBRARY_AUTHORS.md) for metadata and packaging details.

## What Skillager Does

- Discovers skills from projects, `.venv`, installed packages, global agent dirs, and skill repos.
- Scans full skill directories before approval.
- Lint-blocks invalid manifests and requires an audited override before approval.
- Tracks trust by skill ID and content hash.
- Keeps search/list/show metadata safe and compact for agents.
- Materializes only reviewed skills into Codex or Claude native skill directories.
- Supports stubs and routers for large skill collections.
- Records compact local usage signals for lookback, without storing transcripts or skill bodies.
- Keeps direct native skills behind review unless their current content is approved.

## Safety Shape

The built-in scanner is deterministic and local. It looks for common agent-risk patterns like instruction override attempts, hidden prompt requests, secret exfiltration language, credential paths, download-and-execute flows, network callbacks involving secrets, unattended approval language, shell execution requests, hidden control characters, encoded blobs, and oversized content.

**It is not a proof of safety. It is a review aid.**

The hard rule is simpler: agents should not activate, materialize, or rely on skills that have not been approved by the user or project trust store.

## Skill Repos Without Context Flooding

Skill repositories are collections. Collections are inventory; tags are curation; project attachment is intent.

```bash
skillager collection add ~/skills/workflows --name workflows
skillager collection enable workflows
skillager setup
skillager materialize --tag workflows --mode router --agent codex --scope project
```

`collection enable` creates or updates a catalog tag for the collection and attaches that tag to the current project. The agent sees one router skill, not the whole repo. It activates a specific reviewed skill only when the task calls for it.

## Lookback

Skillager learns from usage as a local feedback loop. It records compact events such as search result IDs, activations, materialization status, and explicit feedback. It does not store chat transcripts or skill bodies.

The next `skillager handoff` can tell the agent that lookback is pending. Then the user can decide whether to promote a repeatedly useful skill, keep a broad skill route-only, block an unwanted one, or resolve overlapping skills.

## More Docs

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
