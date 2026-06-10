# Agent CLI Guide

This document is for coding agents reading a project that uses Skillager.

Projects may expose a first-party `skillager-working` skill. Treat `skillager working --agent <agent> --json` as the readiness contract for Skillager-managed projects: run it after context resets, keep no-action readiness out of the user conversation, then curate available skills only when the user's task calls for a narrow router, stub, or native skill.

Availability is the eligibility gate. Agent-facing Skillager commands only surface skills the owner has made available. Choose among them by task relevance; do not ask for or reason about scanner, review, or trust diagnostics unless the user is explicitly doing Skillager administration.

## Rules

- Start resumed work with `skillager working --agent <agent> --json`; only mention it when readiness requires user action or the task calls for Skillager curation.
- If Skillager state seems off mid-session, ask the user to run `skillager doctor --agent <agent>` before guessing. Re-run working after repairs if readiness changes.
- Do not run `skillager setup` or `skillager review ...` unless the user asked for setup or approval changes.
- Do not run `skillager expose` until you have asked what the user plans to do and can justify the narrow router, stub, or native exposure.
- You may add available skills to project-local tags and create scoped router/stub/native exposure after the user states their task. Report what changed.
- Do not run `skillager activate` or `skillager show --content` for unavailable skills. Ask the user to run setup when Skillager says a skill is unavailable.
- Do not use `--force` unless the user explicitly instructs you to override Skillager's gate.
- Prefer `--json` when parsing output.
- Do not search Skillager on every user message. Search only when the task/domain changes, specialized help is likely useful, you are unsure how to proceed and an available skill may contain the right workflow, working state changed, or the user asks about skills.
- Once you choose a native skill or router path for a task, keep using it until the task changes.

## Safe Metadata Commands

These commands do not expose full skill bodies. In a project, normal `list`, `search`, and `show` use effective project inventory: project skills, package/environment skills, and reviewed collection skills that are available to the current project. `list` hides global native skills by default; pass `--include-global` only when the user is asking about global inventory. Plain human `list` and `review` output may include a trailing hint such as `N lint-blocked skill(s) hidden; add --include-lint-blocked to see them.` Treat that as owner-diagnostic guidance, not as available skill inventory.

```bash
skillager working --agent codex --json
skillager list --summary-json --agent codex
skillager show <skill-id> --json
skillager search "<user goal>" --json
skillager tag show <tag> --json
skillager tag list --json
```

Use `review --collection <name> --summary` or `review --collection <name> --json` only for owner-directed collection review/diagnostics. For project work, prefer the normal project-aware commands above.
`working --agent <agent> --json`, `list --json`, `show --json`, `tag show --json`, `tag list --json`, and `search --json` are intentionally compact for agent use. Do not use `--full-json` during normal project work; reserve it for explicit user-directed Skillager diagnostics.
Project-aware JSON includes:

- `availability`: where the skill comes from in this project context.
- `available`: whether this metadata entry is eligible for agent use.
- `exposure`: `hidden`, `native`, `stub`, `router`, or `multiple`.
- `exposed_via`: compact router/stub/native exposure hints in search results.
- `tagging`: available untagged collection skills that may be useful to curate for the current project.
- `authored_pending_owner_review`: status count for user-local authored skills that are not available yet.
- `agent_variant`: duplicate native-variant hints. Matching-agent variants are ranked first when the active agent is known, but alternatives remain visible and usable.
- `compatibility`: negative-only compatibility metadata. Missing metadata means "assume usable." `problem` is set only when the skill explicitly excludes the requested `--agent`.

Pending owner review means Skillager found skills outside the available set. Treat them as unavailable and ask the user to run setup when they want to make more skills available. If `show <id>` returns quarantined lint-blocked metadata, do not activate or request content; ask the user to fix the source or run the audited override command shown by Skillager.

## Compatibility

Skillager defaults to compatibility. Do not hide a skill just because it was written in another agent's style.

Use compatibility metadata this way:

- Use `skillager list --summary-json --agent <agent>` for orientation before targeted searches. It reports compact counts, all listed skill IDs, and duplicate-variant hints.
- If `skillager search --agent codex --json` reports `compatibility.problem`, do not activate or expose that skill for Codex unless the user explicitly approves `--allow-incompatible`.
- If `activation_warnings` are present without `problem`, the skill is still available. Treat the warning as adaptation guidance.
- Prefer `--compatible-only --agent <agent>` only when the user asks for skills that can be used without adaptation.
- Do not infer incompatibility from advisory warnings alone.

Activation and native/stub exposure refuse explicit incompatibility by default:

```bash
skillager activate <skill-id> --agent codex
skillager expose <skill-id> --agent codex
```

The explicit override is:

```bash
skillager activate <skill-id> --agent codex --allow-incompatible
skillager expose <skill-id> --agent codex --allow-incompatible
```

## Agentic Setup Flow

After setup, Skillager installs or refreshes the `skillager-working` readiness skill for the chosen agent. The user may also have exposed a small always-relevant native set during setup. In the next agent session, run `skillager working --agent <agent> --json`; then use available metadata and the user's goal to curate tags and decide whether to expose:

- a narrow native skill for a specific recurring workflow
- a stub for an available command the user wants easy access to by name
- a router skill for a broad project-local tag or explicit short skill set
- nothing, if the existing project exposure is enough

Before changing tags or exposure, build your own slate from available metadata and the user's stated goal. Start with `skillager search "<user goal>" --agent codex --json`; run a few focused searches when the goal has multiple facets, such as domain terms, package/project names, and workflow terms. Search JSON is ranked and includes `score` and `reasons`; use `--limit <n>` to widen or narrow the slate. Use `--full-json` only for explicit diagnostics such as `score_detail`, source paths, and full exposure records. Use `skillager list --summary-json --agent codex` when you need orientation before a targeted search. Consider 5-20 plausible available skills or skill groups when enough relevant options exist. A group can be an existing tag, a collection subset, or a workflow suite such as ideation, review, debugging, release, or domain-specific implementation. Give each candidate a confidence score from 0-100 and a short reason tied to the user's stated task. Include adjacent options the user may reasonably want, such as a brainstorm/research suite for ideation or a review/debugging suite for validation. If fewer than five relevant available candidates exist, say that and continue with the smaller slate. Do not list more than 20 candidates.

Do not use review diagnostics as curation criteria for available skills. Availability is the gate; relevance to the user's stated task decides selection and exposure.

Add relevant available skills to a focused tag when a project or session theme emerges. `tag add` can use registered collection skill IDs or available IDs from the current project inventory, including auto-discovered child repositories:

```bash
skillager tag add gis vibespatial/gis-domain vibespatial/dispatch-wiring
skillager tag add workflows --from-collection community --sync
```

Prefer router exposure for broad tags:

```bash
skillager expose --tag workflows --mode router --agent codex --scope project
```

For a short ad-hoc set that does not need a reusable tag, expose explicit skill IDs. This creates a deterministic explicit router:

```bash
skillager expose workflows/release-check workflows/pr-review --mode router --agent codex --scope project
```

Prefer native exposure for narrow, high-signal project skills:

```bash
skillager expose project/gis-domain --agent codex --scope project
```

Prefer stub exposure for available commands the user wants discoverable without loading full instructions:

```bash
skillager expose personal/deploy-preview --mode stub --agent codex --scope project
```

When a stub tells you to activate a skill, use the exact guarded command from the stub:

```bash
skillager activate <skill-id> --from-stub <stub-slug>
```

Do not expose every available skill just because it is available. Availability means a skill is allowed to be considered; exposure should still be scoped to the user's stated work. User naming, the stated task, and clear relevance decide exposure. Static metadata hints such as `user-invokable`, native agent provenance, clear workflow names, and focused summaries are weak evidence unless they agree with each other.

## User-Gated Commands

These commands change approval state or expose full instructions:

```bash
skillager setup --agent codex
skillager setup --agent claude
skillager setup --collection <name> --agent codex
skillager setup --collection <name> --bulk-approve --agent codex
skillager setup --collection <name> --yolo --agent codex
skillager setup --collection <name> --bulk-approve --project-only --agent codex
skillager review approve <skill-id>
skillager review approve <skill-id> --project-only
skillager review approve <skill-id> --override-lint --reason "<why this is acceptable>"
skillager review pin <skill-id>
skillager review pin <skill-id> --project-only
skillager review block <skill-id>
skillager review unblock <skill-id>
skillager activate <skill-id>
skillager show <skill-id> --content
```

These commands curate or expose available skills. They are agent-managed after the user states the task; report what changed:

```bash
skillager tag add <tag> <skill-id> [<skill-id> ...]
skillager tag add <tag> --from-collection <collection> --sync
skillager tag show <tag>
skillager tag list
skillager tag delete <tag>
skillager tag sync --from <project> --to <project>
skillager expose --tag <tag> --mode router --agent codex --scope project
skillager expose <skill-id> <skill-id> --mode router --agent codex --scope project
skillager expose <skill-id> --mode stub --agent codex --scope project
skillager expose <skill-id> --agent codex --scope project
```

## Router Skills

A Skillager router skill is a compact project skill that lists available skill IDs and author summaries for a tag or explicit selection. It does not contain the hidden skill bodies. Unavailable or incompatible members are skipped.

Tag router:

```bash
skillager expose --tag gis --mode router --agent codex --scope project
```

Ad-hoc explicit router:

```bash
skillager expose <skill-id> <skill-id> --mode router --agent codex --scope project
```

The expose output and JSON include the router exposure id/slug. When a router tells you to activate a skill, use that slug:

```bash
skillager activate <skill-id> --from-router <router-slug>
```

This command refuses skills outside the router and skills that are not available.

## If Working Reports New Skills

Tell the user exactly what happened and ask them to run setup:

```text
Skillager reports new or changed skills. Please run `skillager setup` from this project directory before I use Skillager-managed skills.
```

When you know your agent target, prefer `skillager setup --agent codex` or `skillager setup --agent claude` so setup can refresh the first-party working artifacts after review.

If working reports skills pending owner review, tell the user that Skillager has additional skills which are not available yet and ask them to run setup. If readiness looks broken or stale, ask them to run `skillager doctor --agent <agent> --fix`.
