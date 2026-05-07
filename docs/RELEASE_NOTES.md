# Release Notes

## 0.5.0

Skillager 0.5.0 is a UX-focused release for setup, handoff, and agent-facing skill discovery. The main theme is separating human review/security diagnostics from the agent's normal working view: users still get review and scanner context, while agents mostly see available skills and task-relevance metadata.

### Highlights

- Added first-party bootstrap and repair flows for agent handoff artifacts.
- Added `skillager doctor` readiness diagnostics with precise next commands.
- Made setup, status, and handoff output clearer about readiness, exposure, inventory, and next steps.
- Made agent-facing `list`, `search`, `show`, `tag show`, compact `status`, and `handoff` surfaces available-only by default.
- Removed trust/risk/scanner details from normal agent guidance and compact metadata.
- Improved router/stub/native exposure guidance so agents curate narrowly after the user states the task.
- Improved collection/tag setup UX, including better support for attached tags and no-manifest skill repositories.
- Added example Codex and Claude permission allowlists for safe metadata-only Skillager commands.
- Improved release automation around draft GitHub releases and wheelhouse smoke checks.

### Agent-Facing Discovery

- `skillager search` now searches available skills by default.
- `skillager list` now lists available effective project inventory by default.
- `skillager show` only shows metadata/content for available skills.
- Compact JSON no longer includes trust-like fields such as `trust`, `trust_reason`, risk summaries, lint IDs, scanner details, or duplicate approval diagnostics.
- `handoff` reports neutral setup state such as `pending_owner_review` instead of exposing review internals.
- Generated Skillager Working/router/stub skills now say "available" instead of telling agents to reason about trust or scanner findings.

Agent scripts should stop using `--trusted-only` / `--approved-only`; availability is now the default search contract. Human diagnostics still live in setup, review, lint, doctor, scan, index, and explicit full/diagnostic views.

### Setup, Handoff, And Lookback

- `skillager setup --agent <agent>` can refresh first-party handoff artifacts after review.
- `skillager bootstrap --agent <agent>` installs or repairs Skillager's own project handoff skill and notes when review is already complete.
- `skillager handoff --agent <agent>` summarizes readiness, available inventory, attached tags, materialized router tags, and recommended next commands.
- Setup completion now explains exposed versus available-on-demand skills and suggests stubs only for available-but-hidden skills.
- Lookback guidance now tells agents to review lookback only when handoff/status reports pending evidence, when the user asks, or when recording explicit feedback.

### Release And Packaging

- Release workflow now finds and updates draft GitHub releases more reliably.
- Release assets upload to draft releases before publish finalization.
- Python 3.10 test compatibility was fixed.
- Wheelhouse smoke checks now use `index --json` for diagnostic discovery so they remain compatible with available-only `list`.

