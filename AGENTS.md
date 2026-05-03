# Skillager Agent Guide

This repository builds Skillager, a local CLI approval and activation layer for agent skills.

## Working Rules

- Treat the CLI as the public API. Prefer behavior-preserving changes and keep user-facing command contracts stable.
- Treat discovery as part of the public product contract. Skillager should continue to find project skills, child skill repositories, project `.venv`/`venv` environment skills, installed package skills, and relevant native agent skill directories without users hand-wiring paths.
- Keep approval and exposure separate: approval records reviewed content hashes; exposure writes native, stub, or router skills for an agent.
- Do not expose full skill bodies in metadata commands. `status`, `list`, `search`, `show` without `--content`, `handoff`, `lint`, and summary JSON outputs should stay metadata-only.
- `skillager.yaml` is structured metadata only. Searchable identity and prose come from `SKILL.md` and derived source provenance.
- Lint-blocked skills are quarantined until fixed or approved with an audited override reason.
- Missing compatibility metadata means usable by default. Only explicit incompatibility should block activation or materialization.

## Testing

Run the normal suite before handing off substantive changes:

```bash
uv run python -m unittest discover -s tests
```

For focused checks:

```bash
uv run python -m unittest tests.behavior.test_cli_contracts -v
uv run ruff check
```

Before committing, run the local full check on one interpreter:

```bash
uv run --python 3.13 python scripts/check.py
```

Behavioral tests live under `tests/behavior/`. They should run Skillager through the public CLI with subprocesses and isolated temp `HOME`, project state, catalog state, and cache directories. Prefer asserting stable behavior: exit codes, JSON fields, trust transitions, file creation, and body-leak boundaries.

## Docs Examples

Runnable docs examples should stay opt-in. Use an HTML comment immediately before a normal `bash` fence so rendered GitHub docs keep shell highlighting and hide the marker:

````markdown
<!-- skillager-test fixture=basic_project -->
```bash
skillager status --json
```
````

Do not blindly execute every fenced command in docs. Examples with placeholders, installs, network access, global state, or interactive prompts need explicit fixtures or should remain prose-only examples.

## Product Workflows To Protect

- Fresh project safety gate: unreviewed skills are discoverable as metadata but cannot be activated or shown with content.
- Environment and package discovery: skills shipped in a project `.venv`, editable package source tree, or installed package are discovered without importing the package and still go through review before activation.
- Reviewed project skill path: setup approves low-risk content, search returns trusted metadata, stub/native materialization writes project files, and guarded activation emits the reviewed body.
- Router path: collection or tag inventory can be exposed through one compact router without loading every skill body.
- Handoff loop: agents start with `skillager handoff`, ask what the user plans to do, then curate tags or exposure narrowly.
- Lookback: session signals are compact behavioral hints, not automatic approval or exposure decisions.

## Release Notes

Keep README and docs aligned with CLI behavior when changing command names, flags, JSON schemas, or setup/handoff flow. The package includes `.agents/skills/simulate-skillager-setup`; changes to discovery, manifests, setup, handoff, materialization, packages, or collections should consider that black-box simulation workflow.
