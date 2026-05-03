# Agent CLI Guide

This document is for coding agents reading a project that uses Skillager.

Projects may expose a first-party `skillager-working` skill. Treat it as the bootstrap workflow for Skillager-managed projects: run handoff, respect the review/trust gate, ask what the user plans to do, then curate approved skills into tags and expose only the narrow router, stub, or native skills that fit the session.

## Rules

- Start with `skillager handoff` once per session.
- If handoff reports lookback pending, ask the user whether to review `skillager lookback` before changing shared exposure. Do not apply recommendations without user approval. Active-session lookback signals are collection-in-progress and should not interrupt first handoff.
- If Skillager state may have changed mid-session, re-run `skillager handoff` before making trust-dependent decisions.
- Do not run `skillager setup`, `review`, `trust`, or `block` unless the user asked for setup or approval changes.
- Do not run `skillager materialize` until you have asked what the user plans to do and can justify the narrow router, stub, or native exposure.
- You may add already-approved skills to tags, attach relevant tags, and materialize scoped router/stub/native exposure after the user states their task. Report what changed.
- Do not run `skillager activate` or `skillager show --content` for a skill that is not already reviewed, trusted, or pinned.
- Do not treat a skill's native project/global location as approval. Native skills still require review unless their effective trust state is reviewed, trusted, or pinned.
- Do not activate, show content for, or materialize `lint_blocked` skills. Ask the user to fix the source or run an audited override.
- Do not use `--force` unless the user explicitly instructs you to override Skillager's gate.
- Do not treat `--include-lint-blocked` as approval. It is a read-only diagnostic visibility flag.
- Prefer `--json` when parsing output.
- Do not search Skillager on every user message. Search only when the task/domain changes, specialized help is likely useful, you are unsure how to proceed and an approved skill may contain the right workflow, handoff state changed, or the user asks about skills.
- Once you choose a native skill or router path for a task, keep using it until the task changes.

## Safe Metadata Commands

These commands do not expose full skill bodies. In a project, normal `list`, `search`, and `show` use effective project inventory: project skills, package/environment skills, and attached collection-tag skills with effective trust state. That state may come from reusable global approval when the logical source key and content hash match. `list` hides global native skills by default; pass `--include-global` only when the user is asking about global inventory.

```bash
skillager handoff --json
skillager status --json
skillager lint --json
skillager list --summary-json --agent codex
skillager list --json
skillager list --no-packages --json
skillager search "<query>" --json
skillager show <skill-id> --json
skillager review --summary --json
skillager search "<user goal>" --trusted-only --json
skillager tag show <tag> --json
```

Use `collection search/show` only for catalog management or debugging. For project work, prefer the normal project-aware commands above.
`handoff --json`, `status --json`, and `search --json` are intentionally compact for agent use. Use `--full-json` only when debugging Skillager metadata itself.
`handoff --json` includes lookback state. Only `pending: true` is interruptive; `collecting: true` means Skillager has active-session signals but not enough completed-session or explicit-feedback evidence to review yet.

Project-aware JSON includes:

- `availability`: where the skill comes from in this project context.
- `trust`: effective trust state.
- `trust_reason`: why Skillager treats a skill as trusted, when relevant. `global-approval` means the same source key and content hash were approved before.
- `exposure`: `hidden`, `native`, `stub`, `router`, or `multiple`.
- `materialized_targets`: agent/scope/path/status records for native or router exposure.
- `tagging`: approved untagged collection skills that may be useful to curate for the current project.
- `authored_unreviewed`: status/handoff count and IDs for user-local skills created with `skillager new` but not reviewed yet.
- `agent_variant`: duplicate native-variant hints. Matching-agent variants are ranked first when the active agent is known, but alternatives remain visible and usable.
- `compatibility`: negative-only compatibility metadata. Missing metadata means "assume usable." `problem` is set only when the skill explicitly excludes the requested `--agent`.

`lint_blocked` means Skillager rejected manifest or structure metadata before approval. Default metadata commands hide lint-blocked skills; `status`, `handoff`, and `lint` surface safe finding summaries so the user can repair or override them. Lint output is safe to inspect because it does not include skill bodies or raw manifest contents.

## Compatibility

Skillager defaults to compatibility. Do not hide a skill just because it was written in another agent's style.

Use compatibility metadata this way:

- Use `skillager list --summary-json --agent <agent>` for orientation before targeted searches. It reports compact counts, all listed skill IDs, and duplicate-variant hints without replacing `list --json`.
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

After the user approves skills, setup installs or refreshes the `skillager-working` bootstrap skill for the chosen agent. The user may also have materialized a small always-relevant native set during setup. In the next agent session, run handoff, ask what the user plans to do in the repo, then use approved metadata and the user's goal to curate tags and decide whether to expose:

- a narrow native skill for a specific recurring workflow
- a stub for an approved command the user wants easy access to by name
- a router skill for a broad attached tag
- nothing, if the existing project handoff is enough

Before changing tags or exposure, build a scored slate from approved metadata. Consider 5-20 plausible approved skills or skill groups when enough relevant options exist. A group can be an existing tag, a collection subset, or a workflow suite such as ideation, review, debugging, release, or domain-specific implementation. Give each candidate a confidence score from 0-100 and a short reason tied to the user's stated task. Include adjacent options the user may reasonably want, such as a brainstorm/research suite for ideation or a review/debugging suite for validation. If fewer than five relevant approved candidates exist, say that and continue with the smaller slate. Do not list more than 20 candidates.

Add relevant approved skills to a focused tag when a project or session theme emerges. `tag add` can use registered collection skill IDs or approved IDs from the current project inventory, including auto-discovered child repositories:

```bash
skillager tag add gis vibespatial/gis-domain vibespatial/dispatch-wiring
skillager project attach-tag gis
```

Prefer router materialization for broad tags:

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

Do not materialize every approved skill just because it is approved. Approval means a skill is allowed to be considered; exposure should still be scoped to the user's stated work. User naming, the stated task, and repeated lookback evidence decide exposure. Static metadata hints such as `user-invokable`, native agent provenance, clear workflow names, and focused summaries are weak evidence unless they agree with each other.

At lookback time, prefer aggregate recommendations over single-session instinct. `skillager lookback` considers the recent session window plus active sessions and reports the sessions behind each recommendation. Do not promote or demote shared project-native exposure based on one isolated session unless the user explicitly asks.

Lookback may include `observed_overlaps`. Treat these as behavioral hints, not decisions. They mean skills repeatedly co-occurred in searches or sessions. Ask the user whether to pin a winner, keep the skills route-only, stub commands, block old skills, or ignore the overlap.

Search records compact local telemetry for lookback by default: query hash, short query preview, top result IDs, and filters. It does not record skill bodies, chat transcripts, or command output. Use `skillager search ... --no-session-record` for one-off searches that should not affect lookback.

## User-Gated Commands

These commands change approval state or expose full instructions:

```bash
skillager setup
skillager setup --source collection --trust-all
skillager setup --source collection --yolo
skillager review <skill-id> --trust-selected reviewed
skillager trust <skill-id>
skillager trust <skill-id> --override-lint --reason "<why this is acceptable>"
skillager trust <skill-id> --project-only
skillager block <skill-id>
skillager activate <skill-id>
skillager show <skill-id> --content
```

These commands curate or expose already-reviewed skills. They are agent-managed after the user states the task; report what changed:

```bash
skillager tag add <tag> <skill-id> [<skill-id> ...]
skillager project attach-tag <tag>
skillager materialize --tag <tag> --mode router --agent codex --scope project
skillager materialize <skill-id> --mode stub --agent codex --scope project
skillager materialize <skill-id> --agent codex --scope project
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

If handoff or status reports authored-but-unreviewed skills, tell the user that self-authored skills still need review before activation. For a clearly self-authored low-risk skill, Skillager may print a paste-ready `skillager trust <id> --state reviewed` hint; otherwise ask the user to review with `skillager review`.

## If Handoff Reports Lint-Blocked Skills

Tell the user that Skillager found blocking manifest lint findings and do not use those skills:

```text
Skillager reports lint-blocked skills. Please run `skillager lint` and fix the source, or approve a specific skill with `--override-lint --reason` after review.
```
