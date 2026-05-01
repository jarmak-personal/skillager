# Safety Model

Skillager is a review and activation gate. It reduces accidental context exposure and catches common malicious skill patterns, but it does not prove a skill is safe.

## Security Goals

- Never expose unapproved skill bodies to agents by default.
- Keep discovery and search metadata-only.
- Require explicit user approval before trust changes.
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

Findings include severity, line number, matched text, explanation, and review recommendation.

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

Users own the final trust decision.

## Recommended Review Policy

- Approve only the audience you need for the current work.
- Prefer project-scope materialization over global materialization.
- Block skills that request secrets, hidden prompts, or unapproved autonomy.
- Re-run `skillager setup --fresh` after major dependency or skill-repo changes.
- Use router mode for broad skill collections where native materialization would add too much context.
