---
name: "Simulate Skillager Setup"
description: "Run a repeatable black-box Skillager setup and handoff simulation in a fresh temp directory and report installation, discovery, scoped exposure recommendations, and UX findings."
---

# Simulate Skillager Setup

Use when testing a new Skillager build end to end from a user's point of view, especially after changes to discovery, manifests, setup, handoff, materialization, package handling, or collection handling.

The goal is a black-box workflow test. Do not inspect Skillager source while running the simulation. Use command output, generated files, and the materialized Skillager Working skill as evidence.

## Delegation Requirement

Run the simulation in a fresh subagent/worker, not in the main session.

The main session's job is only to launch the worker and report the worker's findings. The worker should receive this skill's workflow, the absolute path to the local Skillager repo under test, and no implementation context about how Skillager works. Do not fork the main session's accumulated Skillager implementation/debugging context into the worker.

If a subagent/worker is unavailable, do not run the simulation locally. Report that the simulation is blocked because running it in the main session would invalidate the black-box test.

## Workflow

1. Create a fresh temporary directory for the full run.
2. In that directory, create a Python 3.13 venv named `.venv`.
3. Install the current local Skillager source tree into the venv, not the released package. Prefer an absolute path to the repo under test.
4. Clone sample skill repositories into the temp directory:

```bash
git clone https://github.com/jarmak-personal/vibeSpatial.git
git clone https://github.com/sjarmak/agent-workflows.git
```

5. Confirm which Skillager is installed:

```bash
.venv/bin/skillager --version
.venv/bin/python -m pip show skillager
```

If needed, inspect the venv's `skillager-*.dist-info/direct_url.json` to verify it points at the local source tree.

6. Run setup from the temp directory. Use `--fresh-all` for repeatable simulations:

```bash
.venv/bin/skillager setup --fresh-all
```

If the command prompts interactively, approve as a user who explicitly trusts these sample repositories for the test. Still review and report scanner warnings. Install Skillager Working for Codex project scope when prompted.

7. Simulate opening the agent after setup by running handoff:

```bash
.venv/bin/skillager handoff --agent codex
.venv/bin/skillager handoff --agent codex --json
```

8. From this point on, do not use this skill as product guidance. Follow only what Skillager exposed through setup output, generated project files, materialized skills, and handoff output.

Use this scripted user answer when Skillager or the agent workflow asks what the user plans to do:

```text
I am going to do large-scale GIS and spatial data work in Python, including workflows where vibespatial may be relevant.
```

Run the commands that Skillager's own handoff and materialized working skill lead you to run. If they do not make the next step discoverable, report that as a product issue instead of filling in missing process from prior knowledge.

Only run `skillager manifest init` when explicitly testing metadata sidecar generation for existing skills. It is not the normal post-setup agent handoff command.

## Report

Include:

- Temp directory path.
- Exact commands run, including any retries or network approvals.
- Whether Skillager was installed from local source or from a release.
- Whether both sample repositories cloned.
- What `skillager setup` indexed, selected, skipped, or blocked.
- Whether no-manifest skills from both cloned repos were discovered without requiring manifest initialization.
- What `skillager handoff` reported, and whether generated Skillager guidance made the next step discoverable.
- The scripted user goal above.
- Which commands you chose after handoff, and what product guidance caused you to choose them.
- What approved inventory or search results were available, if Skillager guided you to inspect them.
- Any candidate skills, groups, tags, or exposure choices Skillager led you to consider.
- The tag/materialization command, if any, and the follow-up handoff result.
- Any lint-blocked or scanner findings that affected the flow.
- Whether the experience was good or bad, with concrete reasons.
- Product changes you would make, ranked by impact.

Separate environmental failures, such as DNS or package index errors, from Skillager behavior.
