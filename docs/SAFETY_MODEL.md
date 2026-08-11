# Safety Model

Skillager is a cooperative local review and activation gate. It reduces accidental context exposure and catches common malicious skill patterns, but it does not prove a skill is safe. It is not a sandbox or a same-user authorization boundary, and an agent host may load directly installed native skills without consulting Skillager.

## Security Goals

- Never expose unapproved skill bodies to agents by default.
- Keep discovery and search metadata-only.
- Never index free-text from `skillager.yaml`; searchable identity comes from reviewed `SKILL.md` text and derived provenance.
- Require explicit user approval before approval-state changes.
- Require an audited lint override before approving a lint-blocked skill.
- Require approve or pin state before activation or exposure.
- Assume agent compatibility by default; block only explicit agent exclusions unless the user overrides them.
- Copy skills into project-local native directories so users can inspect managed projections while keeping canonical edits at their source.
- Preserve content hashes over eligible paths, bytes, and normalized executable bits so changed skills require fresh review.

Setup always scans the current selected content locally. A reusable global approval suppresses a repeated owner prompt only when the logical source identity and exact current content hash match. Setup reports current hashes scanned, decisions recorded this run, and reusable exact-hash matches separately; scanner finding totals are distinct from the per-skill risk distribution, and non-low rows expose only IDs, risk, counts, and rule codes. `--fresh-project` does not imply deleting reusable catalog approvals.

## Personal Library Acceptance

Library ownership does not grant body availability. Every new or edited `lib/<name>` skill remains pending until `skillager library accept` records its exact current tree hash. Pending bodies stay unavailable through `show --content`, activation, native/stub exposure, and routers; generic force or include-unreviewed flags do not bypass this boundary.

Acceptance previews body-safe scanner and lint metadata, recomputes the hash under a bounded lock, and requires explicit confirmation. Metadata findings include rule identity and location but never the matched instruction text, including on full diagnostic list/search/show JSON. The non-interactive command carries an opaque token bound to the exact previewed hash, relevant shared-provenance state, and any audited reason; missing or stale tokens fail before mutation. Blocking lint or high scanner risk requires a real `--override-lint --reason "..."` before a confirmation command is emitted. Git-backed libraries commit the selected skill path before trust is recorded and refuse conflicts, in-progress repository operations, unrelated staged changes, and unrelated edits inside shared provenance. Initialization preflights required metadata against ignore rules and rolls back partial writes/staging if the first commit fails. Adding a remote does not alter approval identity: library approvals use `library:<library_id>#<skill-name>` plus the accepted content hash.

The user catalog and its approvals are user-owned authority. Repository-controlled tag metadata may retain a portable catalog hint, but Skillager honors it only when matching user state has bound that exact project and catalog through an authorized tag operation. Explicit command-line or environment configuration takes precedence. A repository cannot substitute its own library registration or approval files by committing a catalog path.

## Import Boundary

Import is the only Skillager operation that turns an external skill into an owned library skill. Preview is read-only. An external ID claimed by multiple distinct roots is ambiguous and fails closed; explicit collision-suffixed inventory IDs remain selectable. After explicit confirmation, Skillager re-resolves and rehashes the source, applies the same scanner/lint and audited-override rules as authored acceptance, and only then prepares a filtered candidate outside the library. Source changes invalidate the preview.

Only the canonical content tree crosses the boundary: regular files below the selected skill root, excluding evidence, generated materialization sidecars, Git/cache data, symlinks, bytecode, and transient editor files. Skillager neither imports nor executes the surrounding package. The candidate hash must reproduce the reviewed source hash before it moves into the library. Git commit precedes trust recording; a later failure leaves a pending copy with an explicit `library accept` repair path. The external origin is never modified or approved as a side effect.

Import provenance stores the source skill ID, imported hash, source type, and timestamp for attribution and audit. It does not create an upstream synchronization contract.

## Content-Addressed History

Git stores library history, but Git commit IDs are never accepted as Skillager version identities. History walks only the selected skill path, reconstructs eligible regular files from Git objects, rejects symlinks, submodules, unsafe paths, and unsupported modes, then verifies the full Skillager content hash. Commits producing the same agent-visible hash are deduplicated. History, status, restore previews, and `diff --stat` remain metadata-only; plain `library diff` is explicitly content-bearing.

Restore is append-only. It resolves a unique Skillager content-hash prefix, reconstructs and scans the tree outside the library, rechecks the selected historical commit and current working hash under the mutation lock, and then creates a new descendant commit. It never runs reset, checkout over the worktree, rebase, force-push, fetch, pull, or another remote/history-rewriting operation. Trust is recorded only after the new commit succeeds. Git or trust failure leaves an exact pending tree with a documented `library accept` repair path.

Conflicts, in-progress operations, unrelated staged files, missing or ambiguous hashes, changed previews, unsafe historical trees, and current symlinks or excluded files fail closed. Acceptance applies the same canonical-tree rule before staging, so an ignored symlink cannot poison append-only history. Normalized executable bits are part of the public Skillager content hash; a mode-only change therefore revokes acceptance. No-Git libraries explicitly report history as unavailable without affecting ordinary authored/imported library use.

## Incremental Index And Exposure Drift

Skillager stores an advisory fingerprint with each local discovery index entry. The fingerprint covers the same agent-visible file set as `content_hash`, but uses relative path, byte size, nanosecond modification time, and normalized executable state rather than reading file bodies. A hit may reuse prior discovery metadata; a miss recomputes it. Approval and body-bearing paths bypass the advisory cache. `working` also fully hashes personal-library sources and managed exposure targets before reporting availability or drift, and every exposure write recomputes authoritative hashes. Native exposure requires host-valid frontmatter for every source, then copies into a verified candidate and atomically installs it only if candidate and source still reproduce the accepted hash. Fingerprint equality never authorizes trust, drift classification, body emission, or mutation.

New materialization sidecars record the source identity, exact source/materialized hashes, a full managed-target hash, materialized fingerprint, agent, and scope. The managed-target hash covers every target entry, type, mode, and byte except the self-referential sidecar, including entries excluded from canonical skill identity. Library-owned direct exposures additionally record the stable `source_library_id`; duplicated ownership labels are not authoritative and are no longer written. `working --json` compares live current-project targets, sidecar source hashes, and current approved source hashes while revalidating personal-library hashes read-only. A clean target behind accepted source content becomes `source_update`, is excluded from current/exposed inventory, and receives an exact re-expose command. Owned drafts and edits remain advisory and do not enter the external setup gate. The check does not change readiness decisions, trust, indexes, sidecars, or target files. Exposure-blocked legacy decisions take precedence over ordinary local-edit classification. Unreadable or incomplete sidecars fail into an explicit error state. Fully deleted exposure directories remain unknowable without a ledger; Skillager does not infer their existence from global state.

## Managed Exposure Recovery

Exposed copies are managed projections, not independent canonical sources. Ordinary native, stub, and router exposure fully hashes existing targets and refuses to overwrite local changes unless the user explicitly chooses `--force`. Distinct skill IDs that normalize to the same host slug receive deterministic collision-safe targets; an occupied fallback fails closed even with force. Drift metadata never decides whether a project edit should become a library version.

For an intentional edit to an owned skill, the safe workflow is to compare the exposure with the canonical library tree, move the intended work into the library, accept that exact hash, then explicitly re-expose it. For an accidental edit, preserve anything needed before choosing forced re-exposure. Removal is preview-first and bound to the complete live target, including sidecar and canonically excluded entries; any change after preview invalidates confirmation. Non-current targets do not receive a removal command until the user explicitly previews with `--force`. Skillager performs no automatic merge, promotion, upstream update, or cross-project rollout.

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
