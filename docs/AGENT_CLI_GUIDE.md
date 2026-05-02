# Agent CLI Guide

This document is for coding agents reading a project that uses Skillager.

Projects may expose a first-party `skillager-working` skill. Treat it as the bootstrap workflow for Skillager-managed projects: check status, respect the user approval gate, ask what the user plans to do, then expose only the narrow router or native skills that fit the session.

## Rules

- Start with `skillager status` once per session.
- If status reports `lookback_pending`, ask the user whether to review `skillager lookback` before starting. Do not apply recommendations without user approval.
- Do not run `skillager setup`, `review`, `trust`, or `block` unless the user asked for setup or approval changes.
- Do not run `skillager materialize` until you have asked what the user plans to do and can justify the narrow router/native exposure.
- Do not run `skillager activate` or `skillager show --content` for a skill that is not already reviewed, trusted, or pinned.
- Do not use `--force` unless the user explicitly instructs you to override Skillager's gate.
- Prefer `--json` when parsing output.
- Do not search Skillager on every user message. Search only when the task/domain changes, specialized help is likely useful, you are unsure how to proceed and an approved skill may contain the right workflow, status changed, or the user asks about skills.
- Once you choose a native skill or router path for a task, keep using it until the task changes.

## Safe Metadata Commands

These commands do not expose full skill bodies. In a project, normal `list`, `search`, and `show` use effective project inventory: project skills, package/environment skills, and attached collection-tag skills with project-local trust state. `list` hides global native skills by default; pass `--include-global` only when the user is asking about global inventory.

```bash
skillager status --json
skillager list --json
skillager list --no-packages --json
skillager search "<query>" --json
skillager show <skill-id> --json
skillager review --summary --json
skillager search "<user goal>" --trusted-only --json
skillager tag show <tag> --json
```

Use `collection search/show` only for catalog management or debugging. For project work, prefer the normal project-aware commands above.
`status --json` and `search --json` are intentionally compact for agent use. Use `--full-json` only when debugging Skillager metadata itself.
`status --json` includes `lookback_pending` and `lookback_summary`; these are next-session hints only. Ask the user before running the full lookback or changing exposure.

Project-aware JSON includes:

- `availability`: where the skill comes from in this project context.
- `trust`: effective project-local trust state.
- `trust_reason`: why Skillager treats a skill as trusted, when relevant. `user-installed` means the user placed it directly in an agent-native skill directory.
- `exposure`: `hidden`, `native`, `stub`, `router`, or `multiple`.
- `materialized_targets`: agent/scope/path/status records for native or router exposure.
- `compatibility`: negative-only compatibility metadata. Missing metadata means "assume usable." `problem` is set only when the skill explicitly excludes the requested `--agent`.

Do not treat `trust_reason=user-installed` as suspicious by itself. The user installed that native skill. Still respect scanner findings and high-risk warnings.

## Compatibility

Skillager defaults to compatibility. Do not hide a skill just because it was written in another agent's style.

Use compatibility metadata this way:

- If `skillager search --agent codex --json` reports `compatibility.problem`, do not activate or materialize that skill for Codex unless the user explicitly approves `--allow-incompatible`.
- If `activation_warnings` are present without `problem`, the skill is still available. Treat the warning as adaptation guidance.
- Prefer `--compatible-only --agent <agent>` only when the user asks for skills that can be used without adaptation.
- Do not infer incompatibility from advisory warnings alone.

Activation and native/stub materialization refuse explicit incompatibility by default:

```bash
skillager activate <skill-id> --agent codex
skillager materialize <skill-id> --agent codex
```

The explicit override is:

```bash
skillager activate <skill-id> --agent codex --allow-incompatible
skillager materialize <skill-id> --agent codex --allow-incompatible
```

## Agentic Setup Flow

After the user approves skills, setup installs or refreshes the `skillager-working` bootstrap skill for the chosen agent. The user may also have materialized a small always-relevant native set during setup. In the next agent session, ask what the user plans to do in the repo. Then use approved metadata to decide whether to expose:

- a narrow native skill for a specific recurring workflow
- a stub for an approved command the user wants easy access to by name
- a router skill for a broad attached collection
- nothing, if the existing project handoff is enough

Prefer router materialization for broad skill repositories:

```bash
skillager materialize --tag workflows --mode router --agent codex --scope project
```

Prefer native materialization for narrow, high-signal project skills:

```bash
skillager materialize project/gis-domain --agent codex --scope project
```

Prefer stub materialization for approved commands the user wants discoverable without loading full instructions:

```bash
skillager materialize personal/deploy-preview --mode stub --agent codex --scope project
```

When a stub tells you to activate a skill, use the exact guarded command from the stub:

```bash
skillager activate <skill-id> --from-stub <stub-slug>
```

Do not materialize every approved skill just because it is approved. Approval means a skill is allowed to be considered; exposure should still be scoped to the user's stated work.

At lookback time, prefer aggregate recommendations over single-session instinct. `skillager lookback` considers the recent session window plus active sessions and reports the sessions behind each recommendation. Do not promote or demote shared project-native exposure based on one isolated session unless the user explicitly asks.

Lookback may include `observed_overlaps`. Treat these as behavioral hints, not decisions. They mean skills repeatedly co-occurred in searches or sessions. Ask the user whether to pin a winner, keep the skills route-only, stub commands, block old skills, or ignore the overlap.

Search records compact local telemetry for lookback by default: query hash, short query preview, top result IDs, and filters. It does not record skill bodies, chat transcripts, or command output. Use `skillager search ... --no-session-record` for one-off searches that should not affect lookback.

## User-Gated Commands

These commands change approval state, write skill files, or expose full instructions:

```bash
skillager setup
skillager setup --source collection --trust-all
skillager review <skill-id> --trust-selected reviewed
skillager trust <skill-id>
skillager block <skill-id>
skillager materialize --agent codex --scope project
skillager activate <skill-id>
skillager show <skill-id> --content
```

## Router Skills

A Skillager router skill is a compact project skill that lists reviewed skill IDs and author summaries. It does not contain the hidden skill bodies.

When a router tells you to activate a skill, use:

```bash
skillager activate <skill-id> --from-router skillager-<tag>
```

This command refuses skills outside the attached tag, blocked skills, and unreviewed skills.

## If Status Reports New Skills

Tell the user exactly what happened and ask them to run setup:

```text
Skillager reports new or changed skills. Please run `skillager setup` from this project directory before I use Skillager-managed skills.
```
