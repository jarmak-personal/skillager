# Skillager

Agent skills are useful. Loading all of them into every chat is not.

Skillager is a local CLI that lets projects, Python libraries, tools, and personal skill repos ship useful agent skills without turning every session into a wall of instructions. It discovers skills, scans them, asks for human approval, and gives agents a small, fast way to find the right skill only when the task needs it.

```text
discover -> approve -> search metadata -> materialize
```

## Quickstart

```bash
uv tool install skillager
cd my-project
skillager setup --agent codex
```

No uv:

```bash
pipx install skillager
# or
python -m pip install --user skillager
python -m skillager setup --agent codex
```

Use `--agent claude` instead if this project is for Claude. `setup` is the approval gate. It discovers skills in the current project and environment, scans them, asks what audience you care about, and only makes a skill available after you approve it. Audience scope uses declared manifest metadata only; undeclared skills are grouped as "everything else." When review changes are applied with an agent target, setup also refreshes Skillager's first-party working artifacts.

Skillager is intended to be a global user tool. Install it once with `uv tool` or `pipx`; project virtual environments are scanned for package-provided skills, but Skillager does not need to be installed into each project venv.

After setup, restart Codex or Claude in the same directory and tell it what you are doing. Skillager installs a tiny project note so the agent knows to run `skillager working` after context resets, use available metadata, and ask you to run setup if external skills need review. Run `skillager handoff` when you want explicit post-setup curation guidance. If the state looks wrong, run `skillager doctor --agent codex` for the exact next command.

For agent permission prompts, Skillager ships example read-only allowlists in [`examples/codex-allowlist.json`](examples/codex-allowlist.json) and [`examples/claude-allowlist.json`](examples/claude-allowlist.json). They include metadata-only commands such as `status --json`, `handoff --agent <agent> --json`, `list --summary-json --agent <agent>`, `search --agent <agent> --json`, `show --json`, and `tag show --json`; setup, review, doctor, and mutating commands stay user-run diagnostics.

## Upgrading From 0.5.x

This release moves Skillager toward sandbox-friendly project curation. Tags are now project-local artifacts stored in `<project>/.skillager/tags.json`, so agents can curate tags from inside normal project sandboxes while user-authority review stays global.

If you used older global/project tag attachments, run this once from a user shell after setup has recorded your projects:

```bash
skillager state migrate-tags --to projects
```

Then refresh active projects:

```bash
skillager setup --agent codex
# or
skillager bootstrap --agent codex
```

Other user-visible changes:

- Lookback/session telemetry was removed.
- `skillager search --json` is now compact for agents; use `--full-json` for diagnostics such as `score_detail`, source paths, and full materialization records.
- `project attach-tag`/`detach-tag` remain compatibility wrappers, but new workflows should use project-local `tag create`, `tag add`, `tag remove`, and `tag delete`.

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

An approved skill does not have to be loaded into the agent. It can stay in Skillager's index until a task needs it. When it should be agent-available, Skillager writes one of three project-level representations:

- `native`: the full available skill directory copied into the agent's project skill directory
- `stub`: a tiny native handle that activates the full skill through Skillager on demand
- `router`: one compact native skill for a curated tag like `gis`, `workflows`, or `release`

This keeps the default context small while still giving agents a deterministic path to available skills.

`skillager materialize` only exposes available skills. It requires explicit skill IDs, `--tag`, or `--all-reviewed`; use `skillager bootstrap --agent <agent>` to install or repair Skillager's first-party working artifacts.

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
- Tracks approvals by source key and content hash.
- Keeps search/list/show metadata safe, compact, and available-only for agents.
- Materializes only available skills into Codex or Claude native skill directories.
- Supports stubs and routers for large skill collections.
- Keeps direct native skills behind review unless their current content is approved.

## Safety Shape

The built-in scanner is deterministic and local. It looks for common agent-risk patterns like instruction override attempts, hidden prompt requests, secret exfiltration language, credential paths, download-and-execute flows, network callbacks involving secrets, unattended approval language, shell execution requests, hidden control characters, encoded blobs, and oversized content.

**It is not a proof of safety. It is a review aid.**

The hard rule is simpler: human review decides availability. Agent-facing commands only surface available skills, and setup/review/doctor keep the security details on the human side.

## Skill Repos Without Context Flooding

Skill repositories are collections. Collections are inventory; project-local tags are curation.

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --agent codex
skillager setup --source collection --trust-all
skillager collection enable workflows
skillager materialize --tag workflows --mode router --agent codex --scope project
```

`collection enable` creates or updates a project-local tag using available reviewed collection skills. The agent sees one router skill, not the whole repo. It activates a specific available skill only when the task calls for it.

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
