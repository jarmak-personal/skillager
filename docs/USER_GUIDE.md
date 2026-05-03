# Skillager User Guide

Skillager is a CLI gate between discovered skills and agent-native skill directories.

The normal loop is:

```text
status -> setup -> restart agent -> handoff -> describe goal -> agent chooses narrow exposure -> lookback
```

## First Run In A Project

Run this from the directory where you will start Codex or Claude:

```bash
skillager status
skillager setup
```

`status` is safe for agents. It refreshes metadata and reports whether review is needed. It does not print skill bodies, approve skills, or materialize skills.

`setup` is the user approval flow. It discovers skills, asks for audience scope when needed, scans selected skills, and prompts before approving anything.

At the end of interactive setup, Skillager asks which agent target you use and installs a small first-party `skillager-working` skill into that agent's project skill directory. It can also materialize a small one-by-one set of approved skills that you want available in every session. Restart the agent in the same project directory, then tell it what you plan to do. The agent starts with `skillager handoff` and can use approved metadata to decide whether to expose more narrow native skills, stubs, a compact router skill for a tag, or nothing.

Setup does not materialize every approved skill by default. Approval means a skill is safe to consider; native exposure is still a separate decision based on what you are doing.

## Trust States

- `discovered`: found and scanned, not approved.
- `reviewed`: approved for the current content hash.
- `trusted`: stronger user trust for recurring use.
- `pinned`: approved for an exact content hash.
- `blocked`: hidden from normal search, activation, and materialization.
- `lint_blocked`: manifest or structure failed a blocking lint rule; hidden from normal list/search/show/materialize flows until fixed or explicitly overridden.

Agents must not activate or use `discovered` skills unless the user explicitly overrides the gate.
Agents must not activate or use `lint_blocked` skills. `--force` does not bypass this state.

Manually installed native skills are treated as user-installed only when manifest lint passes. If you place a skill directly in a project or global agent skill directory, Skillager marks it effectively trusted with `trust_reason=user-installed`, scans it, and reports high-risk findings as warnings rather than disabling it automatically.

## Manifest Lint

`skillager.yaml` is structured metadata only. Skill identity and searchable prose come from `SKILL.md`, not from manifest free text.

Use `skillager lint` to inspect safe lint findings:

```bash
skillager lint
skillager lint <skill-id>
skillager lint --json
```

Lint output reports finding codes, fields, and safe details. It does not print skill bodies or raw manifest contents. Fix lint-blocked manifests when possible. To approve one anyway, use an explicit audited override:

```bash
skillager trust <skill-id> --override-lint --reason "Reviewed manifest and accepted the finding"
```

The override is tied to the current content hash and finding identities. Content changes or new blocking lint findings require a new review.

## Useful Commands

```bash
skillager status
skillager setup --fresh
skillager setup --details
skillager setup --summary-json
skillager setup --source collection --trust-all
skillager review --summary
skillager review <skill-id> --trust-selected reviewed
skillager lint
skillager block <skill-id>
skillager materialize --agent codex --scope project
skillager materialize --agent claude --scope project
skillager materialize <skill-id> --mode stub --agent codex --scope project
```

Use `--json` when another program needs stable output. `status --json` and `search --json` are compact for agent use; pass `--full-json` for verbose debugging. Use `setup --summary-json` for setup automation that only needs counts, IDs, summary buckets, and action results.

Skillager does not require git. In a plain directory, it treats the current directory as the project root and stores project state in `./.skillager`.

Use `--trust-all` or `--yolo` only for fully trusted sources. They mark all selected skills reviewed, including medium and high-risk findings, but still record the current content hashes.

`skillager list` shows the effective project inventory and hides global native skills unless you pass `--include-global`. Use `skillager list --no-packages` when you want only local project and attached-tag inventory.

Collection repositories are catalog inventory. `skillager setup --source collection` reviews collection skills attached to the current project. Registered collections that have not been enabled or attached stay as catalog inventory.

`skillager status` checks PyPI for Skillager updates at most once per day and prints `uv tool upgrade skillager` when a newer release is available. Network failures are silent. Set `SKILLAGER_NO_UPDATE_CHECK=1` to disable this check.

Use `skillager materialize` directly when you already know a reviewed skill or tag should be exposed to the agent. Project materialization also refreshes the `skillager-working` bootstrap skill.

Use `--mode stub` for skills you want visible by name without loading the full skill body into every session. A stub contains only the skill summary and an activation command; the full body still comes through Skillager's approval gate. After setup, Skillager prints numbered approved-but-hidden stub candidates so you can say “please stub 1, 5, 8.”

`skillager onboard <path>` can add a minimal structured `skillager.yaml` to existing skill directories. It records audience and activation metadata only; identity and searchable prose remain derived from `SKILL.md` and path/source provenance.

## Lookback

Track a session when the agent exposes a session ID:

```bash
skillager session start --agent codex --external-session-id "$CODEX_SESSION_ID"
skillager lookback --agent codex --external-session-id "$CODEX_SESSION_ID"
```

Lookback recommendations use three actions:

- `materialize`: make this skill native in the project.
- `route-only`: keep it searchable behind a router, but do not load it by default.
- `block`: remove it from the usable set.

By default, lookback computes recommendations from the most recent 10 sessions plus active sessions. This keeps parallel sessions from fighting over shared project-native skills and makes promotion/demotion depend on repeated evidence instead of one isolated task.

You do not need to remember to run lookback before exiting. The next `skillager handoff` reports a compact pending lookback summary when recent sessions contain recommendations or overlap hints. Agents should ask before running the full `skillager lookback` report.

Skillager also records compact local search/materialization events so lookback can spot behavior-based overlap, such as two skills repeatedly appearing in the same searches or sessions. These events do not include skill bodies, chat transcripts, command output, or full search text; search queries are stored as a hash plus a short preview.

Session storage is pruned automatically at session start/end, and can be pruned manually:

- 30 days by default
- 5 MB total session event storage by default
- 200 events per session by default

```bash
skillager session prune
skillager session prune --days 7 --max-mb 1 --max-events-per-session 100
```

Environment overrides:

```bash
SKILLAGER_RETENTION_DAYS=30
SKILLAGER_MAX_EVENT_MB=5
SKILLAGER_MAX_EVENTS_PER_SESSION=200
```
