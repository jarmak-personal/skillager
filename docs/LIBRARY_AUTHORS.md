# Library Author Guide

Libraries can ship skills alongside package code. Skillager discovers package-provided `.skills` and `skills` directories without importing arbitrary packages.

## Recommended Layout

```text
your_package/
  __init__.py
  .skills/
    data-cleaning/
      SKILL.md
      skillager.yaml
      references/
      scripts/
```

`SKILL.md` contains the agent-facing instructions. Supporting files may live beside it.

## Minimal Metadata

```yaml
schema: skillager.skill.v1
id: your-package/data-cleaning
name: Data Cleaning
summary: Use your-package APIs to clean tabular data.
source:
  type: python-package
audience:
  - user
activation:
  default: suggested
entrypoint: SKILL.md
safety:
  min_trust: reviewed
  allow_tools: false
```

`skillager.yaml` is parsed with PyYAML `safe_load`. Keep metadata short and plain; put long agent-facing prose in `SKILL.md`.

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
  warning:
    codex: This workflow assumes a Claude-only command surface.
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
  warning:
    codex: Use fewer parallel workers and adapt Claude-specific paths if needed.
```

Skillager may also infer compatibility warnings from inert text, such as Claude skill paths, Codex skill paths, agent-team language, file-writing workflows, shell command language, or agent-specific environment variables. Inferred warnings do not block approval, search, router materialization, or stub materialization.

## Audience

Use `audience: user` for skills that help consumers use your library.

Use `audience: dev` for maintainer workflows, release processes, internal development rules, review gates, or commit workflows.

This distinction matters because setup asks the user what audience they want before approval.

## Safety Notes

- Do not request hidden prompts, developer messages, or system instructions.
- Do not ask agents to read or reveal secrets.
- Avoid shell execution unless the skill explicitly needs it.
- If shell commands are expected, set `safety.allow_tools: true` and keep commands concrete.
- Keep summaries accurate; router skills reuse author summaries instead of rewriting intent.

## Test Locally

From a fresh project with your package installed:

```bash
skillager setup --fresh
skillager review --package your-package --summary
skillager materialize --agent codex --scope project
```

Interactive setup installs Skillager's bootstrap skill and may optionally materialize a narrow native set. Use the explicit `materialize` command when testing that a package-provided skill copies correctly with its supporting files.
