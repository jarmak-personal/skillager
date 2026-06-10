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
