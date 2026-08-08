# Safety Model

Skillager is a review and activation gate. It reduces accidental context exposure and catches common malicious skill patterns, but it does not prove a skill is safe.

## Security Goals

- Never expose unapproved skill bodies to agents by default.
- Keep discovery and search metadata-only.
- Never index free-text from `skillager.yaml`; searchable identity comes from reviewed `SKILL.md` text and derived provenance.
- Require explicit user approval before approval-state changes.
- Require an audited lint override before approving a lint-blocked skill.
- Require approve or pin state before activation or exposure.
- Assume agent compatibility by default; block only explicit agent exclusions unless the user overrides them.
- Copy skills into project-local native directories so users can inspect and customize them.
- Preserve content hashes so changed skills require fresh review.

## Personal Library Acceptance

Library ownership does not grant body availability. Every new or edited `lib/<name>` skill remains pending until `skillager library accept` records its exact current tree hash. Pending bodies stay unavailable through `show --content`, activation, native/stub exposure, and routers; generic force or include-unreviewed flags do not bypass this boundary.

Acceptance previews scanner and lint metadata, recomputes the hash under a bounded lock, and requires explicit confirmation. Blocking lint or high scanner risk requires `--override-lint --reason "..."`, stored as an audited exact-hash override. Git-backed libraries commit the selected skill path before trust is recorded and refuse conflicts, in-progress repository operations, and unrelated staged changes. Adding a remote does not alter approval identity: library approvals use `library:<library_id>#<skill-name>` plus the accepted content hash.

## Import Boundary

Import is the only Skillager operation that turns an external skill into an owned library skill. Preview is read-only. After explicit confirmation, Skillager re-resolves and rehashes the source, applies the same scanner/lint and audited-override rules as authored acceptance, and only then prepares a filtered candidate outside the library. Source changes invalidate the preview.

Only the canonical content tree crosses the boundary: regular files below the selected skill root, excluding evidence, generated materialization sidecars, Git/cache data, symlinks, bytecode, and transient editor files. Skillager neither imports nor executes the surrounding package. The candidate hash must reproduce the reviewed source hash before it moves into the library. Git commit precedes trust recording; a later failure leaves a pending copy with an explicit `library accept` repair path. The external origin is never modified or approved as a side effect.

Import provenance stores a source identity, source skill ID, imported hash, source type, and timestamp. Refresh resolves that identity and compares hashes without applying files. Missing or changed provenance degrades refresh only and never makes the owned copy unavailable.

## Content-Addressed History

Git stores library history, but Git commit IDs are never accepted as Skillager version identities. History walks only the selected skill path, reconstructs eligible regular files from Git objects, rejects symlinks, submodules, unsafe paths, and unsupported modes, then verifies the full Skillager content hash. Commits producing the same agent-visible hash are deduplicated. History, status, `where`, restore previews, and `diff --stat` remain metadata-only; plain `library diff` is explicitly content-bearing.

Restore is append-only. It resolves a unique Skillager content-hash prefix, reconstructs and scans the tree outside the library, rechecks the selected historical commit and current working hash under the mutation lock, and then creates a new descendant commit. It never runs reset, checkout over the worktree, rebase, force-push, fetch, pull, or another remote/history-rewriting operation. Trust is recorded only after the new commit succeeds. Git or trust failure leaves an exact pending tree with a documented `library accept` repair path.

Conflicts, in-progress operations, unrelated staged files, missing or ambiguous hashes, changed previews, unsafe historical trees, and current symlinks or excluded files fail closed. A transaction-only tree fingerprint also catches executable-mode changes that do not affect the public Skillager content hash. No-Git libraries explicitly report history as unavailable without affecting ordinary authored/imported library use.

## Incremental Index And Exposure Drift

Skillager stores an advisory fingerprint with each local index entry. The fingerprint covers the same agent-visible file set as `content_hash`, but uses relative path, byte size, and nanosecond modification time rather than reading file bodies. A hit may reuse the prior full hash, scanner result, and lint result for metadata/readiness work. A miss recomputes all three. Approval paths bypass the advisory cache, and exposure paths recompute the source content hash before writing, so fingerprint equality never authorizes trust or mutation.

New materialization sidecars record `materialized_fingerprint` and `ownership`. `working --json` compares only live current-project targets and emits metadata-only drift in `exposure_changes`; it does not refresh source/library state, change readiness, or write state, sidecars, or target files. Exact kept-local and exposure-blocked decisions take precedence over ordinary local-edit classification. Unreadable or incomplete sidecars fail into an explicit error state. Fully deleted exposure directories remain unknowable without a ledger; Skillager does not infer their existence from global state.

## Reconciliation And Recovery

Read-only `reconcile` turns exposure drift records into source-aware metadata and valid next actions. Mutations require interactive confirmation or `--yes`, acquire bounded locks by canonical resource path, then recompute the full target/source hashes. Advisory fingerprints never authorize keep-local, quarantine, repair, promote, import, or rollback.

Keep-local records the exact customized hash and fingerprint; it does not suppress later edits. Quarantine atomically moves the complete target directory—including excluded evidence, local notes, and sidecar—to `.skillager-quarantine/exposures/`, writes a sidecar-only tombstone outside agent discovery, and records the exact blocked hash and recovery path. Ordinary native/stub/router materialization carries those decisions forward and refuses the blocked exact output hash. There is no destructive reconcile delete path.

Generated stub/router repair resolves only accepted sources reproducing the recorded source hash. Edited generated bytes are quarantined before regeneration. Native external edits cannot be promoted: explicit `reconcile import` filters and scans the edited tree, records upstream base provenance, commits it under a collision-free library identity, and leaves the external origin unchanged.

Library promotion is fast-forward-only. The sidecar base must equal the current accepted library working hash and, for Git-backed libraries, the verified library HEAD hash. The exposure is copied through canonical content-tree rules, linted/scanned, committed, accepted, and only then recorded as the exposure's new base. Any library/exposure divergence reports both metadata-only file comparisons and leaves both trees unchanged; no merge, reset, stash, pull, or push occurs.

Exposure rollback reconstructs the sidecar's recorded source hash from verified path-specific library Git history. Dirty project content is quarantined before the historical candidate replaces it. External sources, no-Git libraries, missing history, malformed sidecars, and changed previews fail closed without writing.

## Static Scanner

The scanner runs locally and does not use an agent. It scans the full skill directory, including `SKILL.md`, supporting docs, scripts, templates, and references.

Current rule families:

- instruction override attempts
- hidden system/developer prompt requests
- secret exfiltration language
- credential path references such as `.env`, `.ssh/id_rsa`, and cloud credential files
- download-and-execute flows such as `curl ... | bash`
- network callbacks involving secrets or environment data
- shell execution requests in skills that do not declare tool use
- unattended approval language
- hidden control characters
- HTML comments and hidden markdown text
- encoded payload-like blobs
- oversized content

Scanner findings include severity, line number, matched text, explanation, and review recommendation.

## Manifest Lint

`skillager.yaml` is structured-only metadata. Unknown keys, invalid enum values, unsafe YAML features, invalid package specifiers, hidden/control characters, missing canonical `SKILL.md`, and invalid derived IDs produce a blocking lint finding.

The standalone `skillager-lint` console script uses the same strict loader and manifest validator as Skillager's runtime review path. It is meant for package and skill-repository CI; it reports safe diagnostics without reading or writing trust state, activating skills, writing exposure artifacts, or emitting skill bodies.

The standalone linter reads `SKILL.md` to validate the canonical entrypoint, infer compatibility warnings, and check description quality, but it never emits body text or body-derived names/summaries in findings or output.

Lint-blocked skills are indexed only as quarantined records with safe derived fields, `trust: lint_blocked`, and safe lint findings. Skillager does not expose hostile manifest values through normal `search` or default `list` output. `show <id>` may display the quarantined metadata record and safe lint findings for diagnosis, but `show --content` remains refused while lint-blocked.

Approving a lint-blocked skill requires:

```bash
skillager review approve <skill-id> --override-lint --reason "<why this is acceptable>"
```

For fully reviewed sources, `--bulk-approve` also approves selected lint-blocked skills and stores a standard audited shortcut reason. `--yolo` is the fun alias for the same bulk approval path. Bulk shortcut overrides are disclosed in command output with the accepted finding, reason, revisit command, and revoke command.

Interactive setup has a lint-blocked review lane. Its override path requires a non-empty user-supplied reason and stores the same audited lint override record as `review approve --override-lint`.

The override is stored in `trust.json` with the reason, timestamp, content hash, and the accepted finding identities. Content changes or new blocking finding identities drop the skill back to `lint_blocked`.

## Review Metadata

`trust` is retained as an internal legacy state bucket for existing callers. Full review metadata also exposes clearer public axes:

- `approval`: the owner decision, such as `unreviewed`, `approve`, `pin`, or `blocked`.
- `review_gates.scan`: the static scanner risk, such as `low`, `medium`, or `high`.
- `review_gates.lint`: manifest/structure lint status, such as `ok`, `warned`, or `blocked`.
- `review_gates.signature`: indexed release-evidence status, such as `missing` or `not_checked`. External signature verification can inform review, but does not approve the skill or write a cached review gate.
- `review_gates.availability`: whether the skill is `available`, `blocked`, `blocked_until_review`, or `blocked_until_lint_override`.

These fields are diagnostics, not independent approvals. A low scan result, passing lint, or valid signature can inform review, but only approval makes a skill available for activation or exposure.

## Signatures And Release Evidence

Detached OMS signatures such as `skill.oms.sig` are provenance and integrity evidence, not safety decisions. A valid signature can show that the current skill root matches what a signer published, but it never replaces user approval and never lowers scanner risk.

Skill cards are treated as optional release evidence for human reviewers. Skillager detects recognized root-level card filenames for diagnostic/full metadata, but does not parse card prose, index it for search, include it in agent activation output, or copy it into native exposed skills.

Signature and card files are excluded from the reviewed content hash and static instruction scan. The reviewed artifact remains the skill instructions and supporting files that an agent may actually use. Use external signing tooling for explicit local verification.

## Compatibility Gate

Compatibility is separate from safety. A skill can be safe but awkward or impossible in a specific agent harness.

Skillager uses negative-only compatibility:

- no compatibility metadata means the skill is assumed usable
- advisory assumptions and inferred warnings do not block use
- `exclusive_to` and `incompatible_with` block activation and native/stub exposure for the excluded agent by default
- `--allow-incompatible` is the explicit user-approved override

Inferred warnings come from inert text only. Examples include agent-specific skill paths, agent-team language, file-writing assumptions, shell command language, and agent-specific environment variables.

## Risk Levels

- `high`: requires careful review; bulk low-risk approval will not approve it.
- `medium`: likely legitimate in some skills, but needs user attention.
- `low`: review still matters, but no strong risk pattern was found.

## Limitations

- Static scanning can miss attacks.
- Benign documentation can trigger false positives.
- A passing scan is not a guarantee of safety.
- Skillager does not inspect runtime behavior after activation.
- User-installed native skills are discovered and scanned, but remain unreviewed until explicitly approved.

Users own the final approval decision.

## Recommended Review Policy

- Approve only the audience you need for the current work.
- Prefer project-scope exposure over global exposure.
- Block skills that request secrets, hidden prompts, or unapproved autonomy.
- Fix lint-blocked manifests instead of overriding when possible.
- Re-run `skillager setup --fresh` after major dependency or skill-repo changes.
- Use router mode for broad skill collections where native exposure would add too much context.
