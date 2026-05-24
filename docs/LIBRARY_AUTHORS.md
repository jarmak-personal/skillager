# Library Author Guide

Libraries can ship skills alongside package code. Skillager discovers package-provided `.skills` and `skills` directories in project Python environments, including virtualenv and conda environments, without importing arbitrary packages.

## Recommended Layout

```text
your_package/
  __init__.py
  .agents/skills/
    fastapi-usage/
      SKILL.md
      skillager.yaml
      references/
      scripts/
```

`SKILL.md` contains the agent-facing instructions. Supporting files may live beside it.

Optional release evidence files such as `skill.oms.sig` and `skill-card.md` may live at the skill root for published/shared skills. They are not Skillager metadata, and they are not exposed to agents during normal activation or materialization.

## Minimal Metadata

Print the current canonical minimal manifest with:

```bash
uvx --from skillager-linter skillager-lint --print-minimal-manifest
```

The minimal manifest currently contains:

```yaml
schema: skillager.skill.v1
audience:
  - user
activation:
  default: manual
```

Add package targets when the skill is only relevant for specific package ranges:

```yaml
schema: skillager.skill.v1
audience:
  - user
activation:
  default: suggested
targets:
  python_packages:
    - name: your-package
      versions: ">=1,<2"
```

The manifest is intentionally structured-only. It cannot declare `id`, `name`, `summary`, `source`, `entrypoint`, `safety`, `triggers`, `domains`, `tools`, or `references`. Skillager derives identity from the package/path and from the reviewed `SKILL.md` body: simple `name`/`description` frontmatter when present, then top-level heading/first sentence fallbacks.

`skillager.yaml` uses a strict loader: one document, string keys, no duplicate keys, no anchors, no aliases, no merge keys, no custom tags, and a small file-size cap. Unknown keys lint-block the skill.

## Validate In CI

Use the standalone linter before publishing package skills:

`uvx --from skillager-linter skillager-lint .`

It uses the same strict manifest loader and validator as `skillager lint`, but stays dependency-light and does not read trust state, activate skills, materialize files, or emit skill bodies. V1 validates the existing skill root contract: strict `skillager.yaml`, canonical `SKILL.md`, body-derived compatibility warnings, and current description-quality warnings.

GitHub Actions example:

```yaml
name: skillager-lint
on: [push, pull_request]
jobs:
  skillager-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uvx --from skillager-linter skillager-lint .
```

## Release Evidence

For published skill collections, a detached OMS signature at the skill root can provide provenance and integrity evidence:

```text
fastapi-usage/
  SKILL.md
  skillager.yaml
  references/
  skill.oms.sig
  skill-card.md
```

Skillager treats signatures and skill cards as release evidence, separate from approval and risk:

- A verified signature means the current skill root matches what a signer published. It does not mean the skill is safe or approved.
- In full review metadata this evidence appears under `review_gates.signature`; it does not change `approval` or `review_gates.availability`.
- `skill-card.md` is for curious reviewers and auditors. Skillager does not parse card prose, index it for search, or show it in normal agent-facing commands.
- Signature and card files are excluded from Skillager's review content hash, static instruction scan, and native materialized copies.
- Missing cards are not reported in normal `skillager-lint` output. The linter only keeps a debug-level release-evidence note so this can be promoted later if cards become useful publisher hygiene.

Skillager recognizes root-level card files named `skill-card.md`, `Skill Card.md`, `card.yaml`, `card.yml`, `SKILLCARD.yaml`, or `SKILLCARD.yml`. `SKILL.md` is never treated as a card because it is the reviewed instruction entrypoint.

Use `skillager verify-signature <skill-id-or-path> --certificate-chain <pem>` when you want to verify a signed skill locally. Verification is read-only, does not cache `review_gates.signature`, and users still approve the skill through the normal setup/review flow.

## Compatibility Metadata

Omit compatibility metadata unless there is a real exception. Skillager assumes skills are usable by any agent by default.

Use negative metadata only when the skill truly cannot run in a given harness:

```yaml
compatibility:
  exclusive_to: claude
```

or:

```yaml
compatibility:
  incompatible_with:
    - codex
  warnings:
    codex: claude_only_paths
```

For softer assumptions, prefer advisory metadata:

```yaml
compatibility:
  assumptions:
    parallel_subagents:
      required: false
      preferred: 4
    writes_files: true
    env:
      - CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
  warnings:
    codex: parallel_subagents_unsupported
```

Skillager may also infer compatibility warnings from inert text, such as Claude skill paths, Codex skill paths, agent-team language, file-writing workflows, shell command language, or agent-specific environment variables. Inferred warnings do not block approval, search, router materialization, or stub materialization.

## Audience

Use `audience: user` for skills that help consumers use your library.

Use `audience: dev` for maintainer workflows, release processes, internal development rules, review gates, or commit workflows.

This distinction matters because setup asks the user what audience they want before approval. If a skill omits audience metadata, Skillager does not guess from its path or wording; setup groups it under "everything else."

## Safety Notes

- Do not request hidden prompts, developer messages, or system instructions.
- Do not ask agents to read or reveal secrets.
- Avoid shell execution unless the skill explicitly needs it.
- Shell-command guidance is always scanned. Authors cannot suppress scanner findings from `skillager.yaml`.
- Keep the `SKILL.md` heading and first paragraph accurate; Skillager uses them for reviewed metadata.
- Validate manifests with `skillager-lint` or `skillager lint` before publishing.

## Test Locally

From a fresh project with your package installed:

```bash
skillager setup --fresh
skillager review --package your-package --summary
skillager materialize <your-package-skill-id> --agent codex --scope project
```

Interactive setup installs Skillager's bootstrap skill and may optionally materialize a narrow native set. Use the explicit `materialize` command when testing that a package-provided skill copies correctly with its supporting files.
