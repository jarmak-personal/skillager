# Skill Repositories

Use a skill repository when another person or team maintains skills you want to use.
Skillager reviews those skills where they are; it does not move them into your
personal library. Skillager calls a repository registered across projects a
collection.

## Choose How To Use The Repository

| What you want | What to do |
| --- | --- |
| Use a repository in one project | Clone it inside that project, then run setup. |
| Use a repository across projects | Register it as a collection, then review it. |
| Maintain your own copy of one skill | Import that skill into your personal library. |
| Create skills you own | Use `skillager library new <name>`. |

Keep a skill external when you want to follow its upstream changes. Import it only
when you intend to maintain an independent copy.

## Use A Repository In One Project

Clone the repository inside your project, then run normal setup:

```bash
cd my-project
git clone <repo-url> agent-workflows
skillager setup --agent codex
```

Use `--agent claude` for Claude.

Skillager discovers repositories cloned directly inside a project when their skills
use common directories such as `skills/` and `.agents/skills/`. Each skill needs a
`SKILL.md`; `skillager.yaml` is optional. You do not need to register the repository
separately.

After review, tell your agent what you want to accomplish:

> Check Skillager for any reviewed skills in `agent-workflows` that help with
> `<goal>`. Keep one-off help on demand.

## Use A Repository Across Projects

Clone the repository once, then register it from a project that will use it:

```bash
git clone <repo-url> ~/skills/community

cd my-project
skillager collection add ~/skills/community --name community
skillager setup --collection community --agent codex
```

Registration makes the repository discoverable across projects. It does not approve
skills, copy files, or add skills to every agent session.

Normal project setup includes registered collections. Use `--collection community`
when you want to review only this repository.

Skillager can reuse your review of an unchanged collection skill in other projects.
Keep a decision local to the current project with:

```bash
skillager setup --collection community --agent codex --project-only
```

If you control every skill in the repository, setup also offers a bulk approval path.
Use it only when you intend to approve flagged skills as well as ordinary ones:

```bash
skillager setup --collection community --agent codex --bulk-approve
```

Use the normal setup flow for repositories you do not fully control.

## Ask Your Agent To Use Repository Skills

Reviewed collection skills stay available without entering every conversation. Give
your agent the goal and how often you expect to repeat the work:

- “Find the best reviewed `community` skills for `<goal>`. Show me the shortlist
  before changing the project.”
- “Use any relevant `community` skill for this one-off task, but keep it on demand.”
- “We will repeat `<workflow>`. Create the smallest useful project shortcut to the
  reviewed `community` skills and report what you add.”

The agent handles skill search and focused project shortcuts. You retain approval of
new or changed skill content.

## Adopt One Skill

Import one repository skill when you want to edit, version, or maintain your own copy:

```bash
skillager import community/release-check
```

Choose a different personal name when needed:

```bash
skillager import community/release-check --as team-release-check
```

The preview shows the source and personal-library destination without copying files.
Confirmation copies only that skill and leaves the repository unchanged. Your copy
then follows the personal-library review and version workflow.

Ask your agent to handle the same workflow with:

> Show me the `community/release-check` source and proposed personal copy. Ask before
> importing anything.

See the [user guide](USER_GUIDE.md) for editing and restoring personal skills.

## Update A Registered Repository

Skillager never runs `git pull`. Update the checkout yourself, then refresh and review
it from the project:

```bash
git -C ~/skills/community pull
skillager collection refresh community
skillager setup --collection community --agent codex
```

Unchanged skills keep their existing review. Changed skills wait for approval before
your agent can use the new content.

List or remove registrations with:

```bash
skillager collection list
skillager collection remove community
```

Removing a collection does not delete its repository or any skill you previously
imported into your personal library.

## Publish A Skill Repository

Put each skill in its own directory with a `SKILL.md`:

```text
my-skills/
  skills/
    release-check/
      SKILL.md
      references/
      scripts/
```

Give `SKILL.md` a clear name and description so agents can select it from metadata:

```markdown
---
name: release-check
description: Check release readiness, changelog coverage, and rollback steps.
---

# Release Check

Follow the repository's release checklist.
```

Validate the repository before sharing it:

```bash
uvx --from skillager-linter skillager-lint .
```

See the [author guide](LIBRARY_AUTHORS.md) for package layouts, optional metadata,
compatibility, and CI examples.

## Collection Commands

| Goal | Command |
| --- | --- |
| Register a repository | `skillager collection add <path> --name <name>` |
| List registrations | `skillager collection list` |
| Rescan local files | `skillager collection refresh <name>` |
| Review one collection | `skillager setup --collection <name> --agent codex` |
| Remove a registration | `skillager collection remove <name>` |
| Adopt one skill | `skillager import <collection>/<skill>` |

Your agent normally handles searches and project curation after review. See the
[agent CLI guide](AGENT_CLI_GUIDE.md) for those commands and
`skillager collection --help` for the full collection interface.
