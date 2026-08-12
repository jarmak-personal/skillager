# Agent CLI Guide

Use Skillager to find and apply reviewed skills without loading every available skill
into the session. Handle routine selection and project curation yourself. Ask the
user for approval, destructive choices, and personal-library version changes.

## Start Or Resume Work

Run this after a context reset or resumed session:

```bash
skillager working --agent codex --json
```

Use `--agent claude` for Claude.

Read the result this way:

- If `can_proceed` is true, continue quietly.
- If `next` contains an action, ask the user to run its exact command.
- Treat `curation` as optional guidance, not required work.
- Treat `exposure_changes` as advisory. Mention it only when it affects the task or
  the user asks about Skillager state.

Do not run setup or review commands just because Skillager found unavailable skills.
Those commands change what the owner allows. Ask the user to run the command that
`working` provides.

If Skillager looks inconsistent or the suggested action fails, ask the user to run:

```bash
skillager doctor --agent codex
```

Re-run `working` after the user completes a readiness repair.

## Decide Whether To Search

Search when:

- the task enters a specialized domain;
- the workflow or technology changes;
- a reviewed skill could materially improve the work;
- you are unsure how to proceed; or
- the user asks what skills are available.

Do not search on every message. Keep using the selected skill path until the task
changes.

Start with the user’s actual goal:

```bash
skillager search "<user goal>" --agent codex --json
```

Use a few focused searches when the task has distinct parts. Use the summary list
only when you need orientation before searching:

```bash
skillager list --summary-json --agent codex
skillager show <skill-id> --json
skillager tag list --json
skillager tag show <tag> --json
```

These commands return metadata. Normal selection should not require scanner details,
approval internals, source paths, or `--full-json`.

## Select Skills

Availability is the eligibility gate. Use only skills returned as available by normal
`search`, `list`, `show`, or tag commands.

Choose by relevance to the user’s goal. Do not rank available skills by trust labels,
scanner results, native-agent origin, or whether they already have a project shortcut.

Compatibility defaults to usable. If metadata has no explicit compatibility problem,
continue. If it reports `compatibility.problem`, do not activate or expose the skill
for that agent unless the user explicitly authorizes `--allow-incompatible`.

When a skill is unavailable, do not request its body or bypass the gate. Tell the user
that it needs review and provide the setup command Skillager returned.

## Use Skills On Demand

Prefer on-demand use for one-off work. A managed router or stub gives the exact guarded
activation command.

From a router:

```bash
skillager activate <skill-id> --from-router <router-slug> --agent codex
```

From a stub:

```bash
skillager activate <skill-id> --from-stub <stub-slug> --agent codex
```

Use the actual slug in the managed skill. Do not substitute a tag name or guessed
slug. Activation fails if the skill is not available or not listed by that router or
stub.

You may activate an available skill when it is relevant to the task. Availability
already records the owner’s review; activation does not change approval.

## Create Focused Project Shortcuts

After the user states the task, decide whether future sessions need a project
shortcut. Prefer the smallest useful choice:

| Need | Action |
| --- | --- |
| One-off help | Keep the skill on demand. |
| One recurring skill | Expose a stub, or native when its full instructions should always load. |
| A recurring group | Add a focused tag and expose one router. |
| A short temporary group | Expose explicit skill IDs as one router without creating a tag. |

Build a reusable group:

```bash
skillager tag add <tag> <skill-id> [<skill-id> ...]
skillager expose --tag <tag> --mode router --agent codex --scope project
```

Build an ad-hoc router:

```bash
skillager expose <skill-id> <skill-id> --mode router --agent codex --scope project
```

Expose one recurring skill:

```bash
skillager expose <skill-id> --mode stub --agent codex --scope project
skillager expose <skill-id> --mode native --agent codex --scope project
```

Do not expose everything available. Report the tag or project files you changed and
why they fit the stated work.

## Respect Owner Boundaries

Do not change approval state unless the user asked for setup or review. Do not use
`--force`, `--override-lint`, or `--allow-incompatible` without explicit permission
for that action.

The following decisions belong to the user:

- making new or changed skill content available;
- accepting a personal-library edit;
- importing or restoring a personal skill;
- approving flagged or explicitly incompatible instructions;
- discarding edits from a managed project copy; and
- relocating the personal library.

Preview commands are not approval. Show the result, explain the change in plain
language, and wait. When the user approves, execute the returned
`next_command_argv` exactly. Never invent a confirmation token or edit the generated
command. If it goes stale, show the new preview and ask again.

## Manage A User-Requested Personal Skill

A request to create or edit a named personal skill authorizes that draft workflow. It
does not authorize acceptance, exposure, or a lint override.

### Create

Create the draft:

```bash
skillager library new <name> --json
```

This initializes the default library when needed. Run `skillager library init` first
only when the user requests a custom path or no Git history.

Edit the returned canonical `SKILL.md` with normal file tools. Use an applicable
skill-authoring workflow for content guidance, but do not run another scaffold over
the draft. Do not turn an external skill into an owned one unless the user asked to
import it.

Preview acceptance:

```bash
skillager library accept lib/<name> --json
```

Summarize what changed and ask the user to approve the preview. Run its exact next
command only after approval.

### Edit

Find the canonical path before editing:

```bash
skillager library status lib/<name> --json
```

Edit that path, then follow the same acceptance preview. Authorship does not make the
new content available automatically.

### Import

When the user asks to adopt an external skill, preview it:

```bash
skillager import <external-id> --json
```

Report the source, destination, and warnings. The preview is read-only, including on
first use. Confirmation may initialize the default library and copy the reviewed
skill; it leaves the external source unchanged.

### Compare Or Restore

Use metadata-only history and diff summaries first:

```bash
skillager library history lib/<name> --json
skillager library diff lib/<name> --from <hash> --to <hash> --stat --json
```

Use a content-bearing diff only when the user asked to inspect the content. Preview a
restore with:

```bash
skillager library restore lib/<name> --to <hash> --json
```

Explain the selected version and ask before running the returned command.

## Handle Exposure Changes

`exposure_changes` does not block otherwise ready work.

- `source_update`: the managed copy is behind accepted source content. Do not call it
  current. Re-expose only when the user authorizes the suggested refresh.
- `source_unavailable`: do not use or refresh the copy until its exact source becomes
  available again.
- `local_edit`: do not overwrite or remove it. Ask whether the edit should be kept.
- malformed, partial, blocked, or unmanaged target: explain the issue when relevant
  and use Doctor if the repair is unclear.

For an intentional edit to an owned projection:

1. Compare it with the canonical path from `skillager library status`.
2. Move the intended change into the library.
3. Preview and accept the library edit with the user.
4. Re-expose only after the user authorizes the refresh.

Use `--force` only when the user explicitly chooses to discard the project copy.

## Recover Common Problems

| Working reports | Action |
| --- | --- |
| Owner review required | Ask the user to run the exact setup command in `next`. |
| Working helper missing or stale | Ask the user to run the Doctor repair command. |
| Personal library missing | Keep unrelated work moving; ask the user to run `skillager library status`. |
| Personal skill pending | Keep it unavailable; preview `skillager library accept` only for the requested ownership workflow. |
| Skill explicitly incompatible | Choose another skill or ask before using `--allow-incompatible`. |
| Local exposure edit | Preserve it until the user decides whether to keep or discard it. |

Do not guess repair flags. Run the relevant command with `--help` when the generated
guidance is insufficient.

## Command Boundary

Routine agent commands:

```text
working, list, search, show without --content, tag list/show/add,
activate available skills, and focused expose after the user states the task
```

Owner-directed commands:

```text
setup, review, library acceptance/restore/relocation, import confirmation,
show --content outside guarded activation, force, lint override, and compatibility override
```

Prefer `--json` when reading output. Keep successful readiness checks and routine
metadata search out of the conversation; report decisions, changes, and blockers.
