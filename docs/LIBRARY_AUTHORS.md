# Publish Skills In Packages

Ship skills with a Python, npm, or Cargo package when they help users work with that
package. Skillager discovers the installed files without importing the package,
running package scripts, or invoking Cargo.

## Add A Skill

Use `.agents/skills` inside the published package:

```text
your-package/
  .agents/skills/
    fastapi-usage/
      SKILL.md
      skillager.yaml
      references/
      scripts/
```

Python packages may place this directory inside an import package. npm and Cargo
packages should place it at the package root. Skillager also discovers `.skills` and
`skills`, but `.agents/skills` works directly with current agent conventions.

Always verify that the built wheel, npm tarball, or Cargo package contains the full
skill directory. A source-tree test will not catch missing package data.

Give each skill a focused `SKILL.md`:

```markdown
---
name: fastapi-usage
description: Build and review FastAPI endpoints with this package.
---

# FastAPI Usage

Use this workflow when adding or changing an endpoint.
```

Write a description that tells an agent when to select the skill. Keep instructions
and supporting files inside the skill directory, and use relative paths between
them. Include non-empty `name` and `description` frontmatter so Skillager can expose
the skill natively to Codex or Claude.

## Add Metadata Only When It Helps Selection

`skillager.yaml` is optional. Add it when consumers need audience, activation,
package-target, or compatibility metadata. Print the current minimal manifest
instead of copying one from an older package:

```bash
uvx --from skillager-linter skillager-lint --print-minimal-manifest
```

The current minimal manifest is:

```yaml
schema: skillager.skill.v1
audience:
  - user
activation:
  default: manual
```

Use `audience: user` for skills that help consumers use the package. Use
`audience: dev` for release, maintenance, and contributor workflows. Setup presents
undeclared audiences as “everything else,” so declare one when that distinction
matters.

Add a package target when the skill applies only to a dependency or version range:

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

Use the target key and version syntax for the package ecosystem:

| Ecosystem | Target key | Version value |
| --- | --- | --- |
| Python | `python_packages` | PEP 440 specifier |
| npm | `npm_packages` | npm semver range string |
| Cargo | `cargo_packages` | Cargo semver requirement string |

Skillager stores npm and Cargo version ranges as selection metadata; it does not run
their package managers or resolve those ranges during linting.

Keep searchable prose in `SKILL.md`. `skillager.yaml` accepts only `schema`,
`audience`, `activation`, `targets`, and `compatibility`; unknown fields block the
skill.

## Declare Real Compatibility Limits

Omit compatibility metadata when the skill works across agents. Missing metadata
means usable by default.

Declare an exclusive agent only when the workflow cannot run elsewhere:

```yaml
compatibility:
  exclusive_to: claude
```

Or exclude one agent and explain the known limitation:

```yaml
compatibility:
  incompatible_with:
    - codex
  warnings:
    codex: claude_only_paths
```

Skillager may infer advisory warnings from agent-specific paths, environment
variables, shell commands, file writes, or subagent language. Fix accidental
assumptions in `SKILL.md`; inferred warnings do not block a skill by themselves.

## Lint The Published Files

Run the standalone linter before release:

```bash
uvx --from skillager-linter skillager-lint .
```

The linter checks `SKILL.md`, strict `skillager.yaml` parsing, compatibility hints,
and description quality. It does not read approval state, activate skills, write
agent files, or emit skill bodies.

Add the same check to CI:

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

## Test The Consumer Workflow

Install the built artifact in a fresh project, then run:

```bash
skillager setup --package your-package --agent codex
skillager review --package your-package --summary
skillager expose <skill-id> --mode native --agent codex --scope project
```

Use the skill ID shown during setup. Confirm that setup finds the installed skill,
review shows the expected package and audience, and native exposure copies every
required supporting file.

Discovery differs by ecosystem:

- Python discovery reads installed distribution files and editable package roots in
  the project's virtualenv or conda environment.
- npm discovery scans packages in the project's top-level `node_modules`; it does not
  crawl nested workspace-local `node_modules` directories.
- Cargo discovery reads the project's `Cargo.lock`, then checks selected local,
  registry, and Git crate sources already present on disk.

## Add Release Evidence Only If You Use It

A publishing workflow may place `skill.oms.sig` and `skill-card.md` beside
`SKILL.md`. Skillager reports this evidence to reviewers but does not treat a valid
signature as approval or proof of safety. It excludes signature and card files from
the reviewed instruction hash, search text, activation output, and native copies.

Create and verify signatures with external signing tools. Consumers still approve
the exact skill content through Skillager.

## Before Publishing

- Keep the name, description, and activation guidance specific.
- Do not request hidden prompts, system instructions, or secrets.
- Include shell commands only when the workflow needs them.
- Lint the files inside the built artifact.
- Test discovery and native exposure from a fresh consumer project.
