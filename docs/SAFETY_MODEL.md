# Safety Model

Skillager is a review and activation gate. It reduces accidental context exposure and catches common malicious skill patterns, but it does not prove a skill is safe.

## Security Goals

- Never expose unapproved skill bodies to agents by default.
- Keep discovery and search metadata-only.
- Never index free-text from `skillager.yaml`; searchable identity comes from reviewed `SKILL.md` text and derived provenance.
- Require explicit user approval before trust changes.
- Require an audited lint override before approving a lint-blocked skill.
- Require reviewed/trusted/pinned state before activation or materialization.
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

`skillager lint` and the standalone `skillager-lint` console script share the same strict loader and manifest validator. The standalone linter is meant for package and skill-repository CI; it reports safe diagnostics without reading or writing trust state, activating skills, materializing files, or emitting skill bodies.

The standalone linter reads `SKILL.md` to validate the canonical entrypoint, infer compatibility warnings, and check description quality, but it never emits body text or body-derived names/summaries in findings or output.

Lint-blocked skills are indexed only as quarantined records with safe derived fields, `trust: lint_blocked`, and safe lint findings. Skillager does not expose hostile manifest values through `search`, `list`, `show`, or `lint` output.

Approving a lint-blocked skill requires:

```bash
skillager review <skill-id> --trust-selected reviewed --override-lint --reason "<why this is acceptable>"
```

For fully trusted sources, `--trust-all` and `--yolo` also approve selected lint-blocked skills and store a standard audited shortcut reason.

The override is stored in `trust.json` with the reason, timestamp, content hash, and the accepted finding identities. Content changes or new blocking finding identities drop the skill back to `lint_blocked`.

## Compatibility Gate

Compatibility is separate from safety. A skill can be safe but awkward or impossible in a specific agent harness.

Skillager uses negative-only compatibility:

- no compatibility metadata means the skill is assumed usable
- advisory assumptions and inferred warnings do not block use
- `exclusive_to` and `incompatible_with` block activation and native/stub materialization for the excluded agent by default
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
- Skillager does not store chat transcripts for lookback.
- User-installed native skills are discovered and scanned, but remain unreviewed until explicitly approved.

Users own the final trust decision.

## Recommended Review Policy

- Approve only the audience you need for the current work.
- Prefer project-scope materialization over global materialization.
- Block skills that request secrets, hidden prompts, or unapproved autonomy.
- Fix lint-blocked manifests instead of overriding when possible.
- Re-run `skillager setup --fresh` after major dependency or skill-repo changes.
- Use router mode for broad skill collections where native materialization would add too much context.
