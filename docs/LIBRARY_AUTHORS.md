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
audience:
  - user
activation:
  default: suggested
targets:
  python_packages:
    - name: your-package
      versions: ">=1,<2"
```

The manifest is intentionally structured-only. It cannot declare `id`, `name`, `summary`, `source`, `entrypoint`, `safety`, `triggers`, `domains`, `tools`, or `references`. Skillager derives identity from the package/path and from the reviewed `SKILL.md` body: simple `name`/`description` frontmatter when present, then heading/first sentence fallbacks.

`skillager.yaml` uses a strict loader: one document, string keys, no duplicate keys, no anchors, no aliases, no merge keys, no custom tags, and a small file-size cap. Unknown keys lint-block the skill.

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

This distinction matters because setup asks the user what audience they want before approval.

## Safety Notes

- Do not request hidden prompts, developer messages, or system instructions.
- Do not ask agents to read or reveal secrets.
- Avoid shell execution unless the skill explicitly needs it.
- Shell-command guidance is always scanned. Authors cannot suppress scanner findings from `skillager.yaml`.
- Keep the `SKILL.md` heading and first paragraph accurate; Skillager uses them for reviewed metadata.
- Validate manifests with `skillager lint` before publishing.

## Test Locally

From a fresh project with your package installed:

```bash
skillager setup --fresh
skillager review --package your-package --summary
skillager materialize --agent codex --scope project
```

Interactive setup installs Skillager's bootstrap skill and may optionally materialize a narrow native set. Use the explicit `materialize` command when testing that a package-provided skill copies correctly with its supporting files.
