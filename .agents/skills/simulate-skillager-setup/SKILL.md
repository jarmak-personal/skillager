---
name: "Simulate Skillager Setup"
description: "Run a repeatable black-box Skillager setup and working-readiness simulation in a fresh temp directory and report discovery, scoped exposure decisions, and UX findings."
---

# Simulate Skillager Setup

Use when testing a new Skillager build end to end from a user's point of view, especially after changes to discovery, manifests, setup, working readiness, exposure, package handling, or collection handling.

The goal is a black-box workflow test. Do not inspect Skillager source while running the simulation. Use command output, generated files, and the exposed Skillager Working skill as evidence.

Do not reveal the test harness to the product flow. The main session may know
this is a black-box UX run; the worker should not be told. Any answers typed
into Skillager prompts or any post-setup "user goal" should read like a
normal user setting up a real project. Do not say this is a simulation, a test,
a sample run, or that the repositories are trusted "for this test." That
framing can change Skillager Working's recommendations and invalidates the UX
signal.

## Delegation Requirement

Run the simulation in a fresh subagent/worker, not in the main session.

The main session's job is only to launch the worker and report the worker's findings. Do not fork the main session's accumulated Skillager implementation/debugging context into the worker.

The worker-facing prompt must be framed as a normal setup task, not as a
simulation or UX test. Start the worker message exactly with:

```text
Please set up skillager for the user. Follow the steps below to do so.
```

Do not use the words "simulation", "test", "black-box", "harness", "sample",
"under test", or similar evaluation framing in the worker-facing prompt. The
worker may receive the concrete setup steps, the normal user goal below, and
the requested report fields. Phrase report requests as "setup report" or
"findings", not "test report" or "simulation report".

If a subagent/worker is unavailable, do not run the simulation locally. Report that the simulation is blocked because running it in the main session would invalidate the black-box test.

## Workflow

1. Create a fresh temporary directory for the full run.
   Do not run `git init` in that temp directory; cloned repositories may keep
   their own `.git` directories, but the setup root itself should be an ordinary
   project directory.
2. Confirm Skillager is already installed and available as `skillager` on `PATH`:

```bash
skillager --version
```

Do not install Skillager as part of this workflow. The setup run is testing the
project onboarding and working-readiness experience, not installation.

3. Clone these skill repositories into the temp directory:

```bash
git clone https://github.com/jarmak-personal/vibeSpatial.git
git clone https://github.com/sjarmak/agent-workflows.git
```

4. Run setup from the temp directory. Use `--fresh-project` for a clean project setup run and pass the Codex agent target so setup can install first-party working artifacts directly:

```bash
skillager setup --fresh-project --agent codex
```

If the command prompts interactively, answer as a normal user who trusts these
repositories for their own project work. Still review and report scanner
warnings.

5. Simulate opening the agent after setup by running working readiness:

```bash
skillager working --agent codex
skillager working --agent codex --json
```

6. From this point on, do not use this skill as product guidance. Follow only what Skillager exposed through setup output, generated project files, exposed skills, and working output.

Use this scripted user answer when Skillager or the agent workflow asks what the user plans to do:

```text
I am going to do large-scale GIS and spatial data work in Python, including workflows where vibespatial may be relevant.
```

Run the commands that Skillager's own working output and exposed working skill lead you to run. If they do not make the next step discoverable, report that as a product issue instead of filling in missing process from prior knowledge.

Only run `skillager manifest init` when explicitly creating metadata sidecars for existing skills. It is not the normal post-setup agent readiness command.

## Report

Include:

- Temp directory path.
- Exact commands run, including any retries or network approvals.
- Which `skillager --version` was available on `PATH`.
- Whether both repositories cloned.
- What `skillager setup` indexed, selected, skipped, or blocked.
- Whether no-manifest skills from both cloned repos were discovered without requiring manifest initialization.
- What `skillager working` reported, and whether generated Skillager guidance made the next step discoverable.
- The scripted user goal above.
- Which commands you chose after working readiness, and what product guidance caused you to choose them.
- What approved inventory or search results were available, if Skillager guided you to inspect them.
- Any candidate skills, groups, tags, or exposure choices Skillager led you to consider.
- The tag/exposure command, if any, and the follow-up working result.
- Any lint-blocked or scanner findings that affected the flow.
- Whether the experience was good or bad, with concrete reasons.
- Product changes you would make, ranked by impact.

Separate environmental failures, such as DNS or package index errors, from Skillager behavior.
