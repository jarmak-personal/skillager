# Skillager

[![PyPI](https://img.shields.io/pypi/v/skillager?label=skillager&color=2563eb)](https://pypi.org/project/skillager/)
[![Agents](https://img.shields.io/badge/agents-Codex%20%7C%20Claude-0f766e)](docs/AGENT_CLI_GUIDE.md)
[![Packages](https://img.shields.io/badge/packages-Python%20%7C%20npm%20%7C%20Cargo-c2410c)](docs/LIBRARY_AUTHORS.md)
[![License](https://img.shields.io/badge/license-MIT-7c3aed)](LICENSE)

Skillager is a local skill library and approval layer. It discovers skills, records
the exact content you review, and helps your agent use the smallest relevant set for
each project.

- Create and maintain personal skills in one versioned library.
- Use project, package, and shared skills without moving them.
- Keep reviewed skills on demand until your agent needs them.

```text
discover or create → review exact content → let the agent find what helps
```

## Quickstart

Install Skillager once, then set it up in a project:

```bash
uv tool install skillager
# or: pipx install skillager

cd my-project
skillager setup --agent codex
```

Use `--agent claude` for Claude.

Setup finds skills for the current project and asks which ones you trust. It then
installs a small project skill that teaches your agent how to use Skillager. It does
not modify `AGENTS.md` or `CLAUDE.md`.

Restart your agent in the project, then describe your goal:

> Check Skillager, then help me build a Python GIS service. Use reviewed skills where
> they help, and keep one-off skills on demand.

Your agent checks readiness, searches reviewed skills when useful, and avoids adding
one-off skills to every session. If Skillager needs your help, the agent gives you the
exact command to run.

## You Decide; Your Agent Operates

| You control | Your agent handles |
| --- | --- |
| Deciding which new or changed skills may run | Checking Skillager after restarts |
| Approving imports and restored versions | Finding reviewed skills that match your goal |
| Approving risky instructions or discarded edits | Keeping one-off skills out of every session |
| Deciding what recurring help the project needs | Organizing a small reusable project set |

Reviewing a skill does not add it to every chat. Tell your agent whether the work is
one-off or recurring; it will keep the skill on demand or make a small recurring set
easy to reach.

## Work With Your Agent

Use prompts like these:

- **Find help for a task:** “Find the best available skills for `<goal>`. Show me the
  shortlist before changing the project.”
- **Prepare recurring work:** “We will do `<workflow>` repeatedly in this project.
  Create the smallest useful Skillager setup and leave everything else on demand.”
- **Create a skill you own:** “Create a personal skill for `<purpose>`. Show me the
  draft, then ask before approving it for use.”
- **Adopt an external skill:** “Find the external skill called `<name>` and prepare to
  add it to my personal library. Show me what you found and ask before importing it.”
- **Recover an older version:** “Show me the saved versions of my `<name>` skill,
  explain what changed, and ask before restoring anything.”

## Your Personal Library

When you create your first personal skill or confirm your first import, Skillager
creates `~/.skillager/library` and starts Git history. You do not need to initialize
it first.

Run `library init` before your first skill only to choose a different location or
skip Git history:

```bash
skillager library init --path ~/skills/personal
skillager library init --no-git
```

Run `skillager library status` to see the library location and health.

After you or your agent edits a personal skill, Skillager asks you to review the
change before the skill can run.

Importing copies one external skill into your library and leaves the original where
it is.

Edit personal skills at the library path Skillager shows you. If you edit a project
copy instead, Skillager stops before replacing it; it does not merge that edit back
into your library.

## Add Existing Skill Repositories

Setup discovers skills in the current project and installed environments. Register a
separate skill repository when you want its reviewed skills available across projects:

```bash
skillager collection add ~/skills/workflows --name workflows
skillager setup --collection workflows --agent codex
```

Registering a repository does not copy it into your personal library. Import an
individual skill only when you want to maintain your own copy.

## Before Skillager Changes Anything

Skillager asks you before it:

- lets a new or changed skill run;
- imports or restores a personal skill;
- discards an edit made to a project copy;
- approves content that its checks flag as risky; or
- uses a skill that explicitly excludes your agent.

Skillager never silently moves an external skill, replaces a locally edited copy, or
pulls from and pushes to Git remotes.

Codex and Claude can also load skills installed directly in their own folders.
Skillager cannot review or block those skills.

Run this when something looks wrong:

```bash
skillager doctor --agent codex
```

## Learn More

- [Use Skillager](docs/USER_GUIDE.md)
- [Understand how agents operate Skillager](docs/AGENT_CLI_GUIDE.md)
- [Publish skills in packages and repositories](docs/LIBRARY_AUTHORS.md)
- [Review the trust and safety model](docs/SAFETY_MODEL.md)
- [See command and behavior changes](docs/RELEASE_NOTES.md)

## Development

Run the full local check:

```bash
uv run --python 3.13 python scripts/check.py
```

External contributions are not being accepted yet while the API and workflow settle.

Skillager is released under the [MIT License](LICENSE).
