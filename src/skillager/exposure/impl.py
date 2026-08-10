from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..compatibility import compatibility_problem, compatibility_warnings
from ..simple_yaml import dumps, load_mapping
from ..skills.tree import content_tree_fingerprint, iter_content_files
from ..state.locking import resource_lock
from ..trust import content_hash, content_hash_entries

MATERIALIZED_SCHEMA = "skillager.materialized.v1"
TRUSTED_STATES = {"reviewed", "trusted", "pinned"}
ROUTER_SCHEMA = "skillager.router.v1"
WORKING_SKILL_ID = "skillager/working"
WORKING_REASON_LOCAL_CUSTOMIZATION = "target has local edits"
WORKING_REASON_UNMANAGED = "target exists without Skillager provenance"


def materialize_skills(
    skills: list[dict[str, Any]],
    *,
    agents: list[str],
    scope: str,
    mode: str = "native",
    dry_run: bool = False,
    force: bool = False,
    reviewed_only: bool = True,
    project_dir: Path | None = None,
    allow_incompatible: bool = False,
) -> list[dict[str, Any]]:
    if mode not in {"native", "stub"}:
        raise ValueError("mode must be native or stub")
    results: list[dict[str, Any]] = []
    for skill in skills:
        if skill.get("trust") == "blocked":
            results.append(_result(skill, None, "skipped", "blocked"))
            continue
        if skill.get("trust") == "lint_blocked":
            results.append(_result(skill, None, "skipped", "lint-blocked"))
            continue
        if _is_pending_library_skill(skill):
            results.append(_result(skill, None, "skipped", "pending library hash; run `skillager library accept` first"))
            continue
        if reviewed_only and skill.get("trust") not in TRUSTED_STATES:
            results.append(_result(skill, None, "skipped", _unreviewed_reason(skill)))
            continue
        authoritative_error = _authoritative_source_error(skill) if not dry_run else None
        if authoritative_error:
            results.append(_result(skill, None, "skipped", authoritative_error))
            continue
        for agent in agents:
            target = target_dir(agent=agent, scope=scope, skill=skill, project_dir=project_dir)
            problem = compatibility_problem(skill, agent)
            if problem and not allow_incompatible:
                results.append(_result(skill, target, "skipped", problem, agent=agent, scope=scope))
                continue
            try:
                if mode == "stub":
                    results.append(materialize_stub_one(skill, target=target, agent=agent, scope=scope, dry_run=dry_run, force=force))
                else:
                    results.append(materialize_one(skill, target=target, agent=agent, scope=scope, dry_run=dry_run, force=force))
            except (OSError, ValueError) as exc:
                results.append(_result(skill, target, "skipped", str(exc), agent=agent, scope=scope))
    return results


def _unreviewed_reason(skill: dict[str, Any]) -> str:
    if skill.get("authored") and skill.get("scan", {}).get("risk") == "low":
        return f"not available; to approve authored skill after owner review: skillager review approve {skill.get('id')}"
    return f"not available; owner review first: skillager review {skill.get('id')}"


def _is_pending_library_skill(skill: dict[str, Any]) -> bool:
    source = skill.get("source") or {}
    return source.get("ownership") == "library" and skill.get("trust") not in TRUSTED_STATES


def _authoritative_source_error(skill: dict[str, Any]) -> str | None:
    root = skill.get("root")
    expected = skill.get("content_hash")
    if not isinstance(root, str) or not isinstance(expected, str):
        return "source identity is incomplete; refresh inventory before exposure"
    try:
        actual = content_hash(Path(root))
    except OSError:
        return "source is unavailable; refresh inventory before exposure"
    if actual != expected:
        return "source changed since review; refresh inventory and review the new hash before exposure"
    return None


def materialize_router(
    tag: str | None,
    skills: list[dict[str, Any]],
    *,
    agents: list[str],
    scope: str,
    dry_run: bool = False,
    force: bool = False,
    project_dir: Path | None = None,
    router_slug: str | None = None,
    selection_kind: str = "tag",
) -> list[dict[str, Any]]:
    router_kind = "tag" if selection_kind == "tag" else "explicit"
    if router_kind == "tag" and not tag:
        raise ValueError("tag router requires a tag")
    if router_kind == "explicit" and not router_slug:
        raise ValueError("explicit router requires a router slug")
    reviewed, skipped = _router_member_selection(skills)
    if not dry_run:
        unchanged: list[dict[str, Any]] = []
        for skill in reviewed:
            authoritative_error = _authoritative_source_error(skill)
            if authoritative_error:
                skipped.append(_result(skill, None, "skipped", authoritative_error))
            else:
                unchanged.append(skill)
        reviewed = unchanged
    results: list[dict[str, Any]] = []
    for agent in agents:
        agent_reviewed, agent_skipped = _router_agent_member_selection(reviewed, agent=agent, scope=scope)
        router_skill = _router_skill(tag, agent_reviewed, router_slug=router_slug, router_kind=router_kind)
        target = target_dir(agent=agent, scope=scope, skill=router_skill, project_dir=project_dir)
        if not agent_reviewed:
            results.append(_result(router_skill, target, "skipped", _empty_router_reason(router_kind), agent=agent, scope=scope))
            results.extend(agent_skipped)
            continue
        try:
            results.append(
                materialize_router_one(
                    tag,
                    agent_reviewed,
                    target=target,
                    agent=agent,
                    scope=scope,
                    dry_run=dry_run,
                    force=force,
                    router_slug=router_slug,
                    router_kind=router_kind,
                )
            )
        except OSError as exc:
            results.append(_result(router_skill, target, "skipped", str(exc), agent=agent, scope=scope))
        results.extend(agent_skipped)
    results.extend(skipped)
    return results


def explicit_router_slug(skill_ids: list[str]) -> str:
    canonical = "\n".join(sorted(dict.fromkeys(skill_ids)))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return f"skillager-router-{digest}"


def _router_member_selection(skills: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for skill in skills:
        reason = _router_member_skip_reason(skill)
        if reason:
            skipped.append(_result(skill, None, "skipped", reason))
        else:
            reviewed.append(skill)
    return reviewed, skipped


def _router_agent_member_selection(
    skills: list[dict[str, Any]],
    *,
    agent: str,
    scope: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for skill in skills:
        problem = compatibility_problem(skill, agent)
        if problem:
            skipped.append(_result(skill, None, "skipped", problem, agent=agent, scope=scope))
        else:
            selected.append(skill)
    return selected, skipped


def _router_member_skip_reason(skill: dict[str, Any]) -> str | None:
    trust = skill.get("trust")
    if trust == "blocked":
        return "blocked"
    if trust == "lint_blocked":
        return "lint-blocked"
    if trust not in TRUSTED_STATES:
        return _unreviewed_reason(skill)
    return None


def _empty_router_reason(router_kind: str) -> str:
    return "no available skills in tag" if router_kind == "tag" else "no available skills in router selection"


def _router_skill(
    tag: str | None,
    skills: list[dict[str, Any]],
    *,
    router_slug: str | None,
    router_kind: str,
) -> dict[str, Any]:
    if router_kind == "tag":
        assert tag is not None
        return {
            "id": f"skillager/{tag}",
            "name": f"Skillager {tag} Router",
            "summary": f"Route {tag} tasks to available Skillager-managed skills.",
            "source": {"type": "skillager-router", "tag": tag},
            "content_hash": content_hashes(skills),
            "trust": "reviewed",
        }
    assert router_slug is not None
    return {
        "id": f"skillager/{router_slug.removeprefix('skillager-')}",
        "name": "Skillager Explicit Router",
        "summary": "Route explicitly selected tasks to available Skillager-managed skills.",
        "source": {"type": "skillager-router", "router_kind": "explicit", "router_slug": router_slug},
        "content_hash": content_hashes(skills),
        "trust": "reviewed",
    }


def materialize_working_skill(
    *,
    agents: list[str],
    scope: str = "project",
    project_dir: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for agent in agents:
        skill = _working_skill(agent)
        target = target_dir(agent=agent, scope=scope, skill=skill, project_dir=project_dir)
        try:
            results.append(materialize_working_skill_one(target=target, agent=agent, scope=scope, dry_run=dry_run, force=force))
        except OSError as exc:
            results.append(_result(skill, target, "skipped", str(exc), agent=agent, scope=scope))
    return results


def materialize_working_skill_one(
    *,
    target: Path,
    agent: str,
    scope: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    skill = _working_skill(agent)
    with _target_lock(target):
        sidecar = target / "skillager.materialized.yaml"
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(skill, target, "skipped", WORKING_REASON_LOCAL_CUSTOMIZATION, agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(skill, target, "skipped", WORKING_REASON_UNMANAGED, agent=agent, scope=scope)
            if not force and _source_hash_matches(sidecar, skill.get("content_hash")):
                return _result(skill, target, "skipped", "already up to date", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_working_skill(agent), encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar.write_text(
            dumps(
                _working_sidecar(
                    agent=agent,
                    scope=scope,
                    materialized_hash=materialized_hash,
                    materialized_fingerprint=content_tree_fingerprint(target),
                )
            ),
            encoding="utf-8",
        )
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


def render_working_skill(agent: str = "codex") -> str:
    return f"""---
name: skillager-working
description: "Operate Skillager safely in a managed project: run quiet readiness checks, find and activate available skills, curate narrow exposure, and help the user manage skills in their personal library. Use after context resets, when specialized skills may help, or when the user asks to create, edit, import, or recover skills."
---

# Skillager Working

Use Skillager as the agent's local skill-management layer. Handle routine metadata search,
selection, activation, and narrow exposure yourself. Bring the user in for owner review,
acceptance, overrides, or version-changing decisions.

## Core Contract

- Availability is the eligibility gate. Use only skills returned by normal Skillager
  list, search, show, or tag metadata commands.
- Approval and exposure are separate. Approval records an exact reviewed content hash;
  exposure writes a native skill, stub, or router for an agent.
- Personal-library ownership never approves changed bytes. A pending `lib/<name>` skill
  remains metadata-visible but cannot be activated, exposed, or routed.
- External project, package, environment, collection, and native-agent skills remain at
  their source unless the user explicitly imports one into the personal library.
- Do not inspect owner review or scanner diagnostics during ordinary task selection.

## Session Start

1. Run `skillager working --agent {agent} --json` after context resets or resumed sessions.
2. Treat `next` as required before using or exposing managed skill bodies, and
   `curation` as optional task guidance. An explicit owner request may still create or
   edit a pending personal-library draft while unrelated project review waits; do not
   accept, activate, or expose that draft until its own gates are satisfied.
3. If no user action is needed, continue quietly. Mention Skillager only when a skill,
   owner decision, curation change, exposure, activation, drift, or repair matters.
4. Treat `exposure_changes` as advisory. A local edit is a no-overwrite warning, not
   permission to replace either the exposure or its canonical source.
5. If review is needed, ask the user to run the exact setup/review command. If the
   first-party working skill is stale or missing, ask for
   `skillager doctor --agent {agent} --fix`, then rerun `working`.

## Find And Use Skills

Safe metadata commands do not reveal full skill bodies:

```text
skillager working --agent {agent} --json
skillager list --summary-json --agent {agent}
skillager search "<user goal>" --agent {agent} --json
skillager show <skill-id> --json
skillager tag show <tag> --json
skillager tag list --json
```

Search when the task enters a new domain, a specialized workflow may help, the user
asks what is available, or project-local skills changed. Start with the user's actual
goal and use a few focused searches for multi-part work. Prefer an existing matching
router. Keep using the chosen path until the task changes.

- Activate an available router member with
  `skillager activate <skill-id> --from-router <router-slug>`.
- Add relevant available skills to a focused project tag with
  `skillager tag add <tag> <skill-id>...`.
- Expose a recurring broad tag as a compact router:
  `skillager expose --tag <tag> --mode router --agent {agent} --scope project`.
- Expose a named recurring skill as a stub:
  `skillager expose <skill-id> --mode stub --agent {agent} --scope project`.
- Expose a tiny always-relevant project skill as native:
  `skillager expose <skill-id> --mode native --agent {agent} --scope project`.
- Prefer no new exposure for one-off work. Report any tag or exposure change.
- Never use `--force` or an override flag unless the user explicitly requests that
  exact action.

## Owner-Directed Personal Library

Use these metadata-only commands to orient without exposing bodies:

```text
skillager library status [<lib-skill>] --json
skillager library history <lib-skill> --json
```

When the user asks to manage owned skills, inspect the relevant command's `--help`
before mutating state and follow generated preview/next-command output. Never guess
flags. A request to create or edit a named skill authorizes that narrow draft workflow
and any required library initialization, but not acceptance or an override. A request
to expose a named skill to a named agent and scope authorizes that narrow exposure once
the skill is available; it does not waive the acceptance gate.

1. For a new owned skill, inspect library status, initialize only when required, create
   it with `skillager library new <name> --json`, then edit the returned canonical
   `SKILL.md` path with normal agent tools. Use an available skill-authoring workflow
   for content and validation guidance, but do not run a second scaffold initializer.
   Do not convert external inventory implicitly.
2. Preview acceptance, restore, and import before any version-changing action.
   Preserve and show the exact generated next command.
3. Run confirmation-bearing commands (`--yes`) only after the user authorizes that
   exact preview. Never infer acceptance from authorship or ownership.
4. Exposed copies are managed projections, not alternate sources of truth. If one was
   edited, do not overwrite it. Compare it with the canonical source, copy intentional
   work into the library, accept the new library hash, and re-expose only with the
   user's explicit authorization. Use `--force` only when the user explicitly chooses
   to replace the local copy.
5. If lint blocks a skill, stop for owner review. Use `--override-lint` only when the
   user explicitly approves the exact override and supplies an audited reason.
"""


def materialize_router_one(
    tag: str | None,
    skills: list[dict[str, Any]],
    *,
    target: Path,
    agent: str,
    scope: str,
    dry_run: bool = False,
    force: bool = False,
    router_slug: str | None = None,
    router_kind: str = "tag",
) -> dict[str, Any]:
    router_skill = _router_skill(tag, skills, router_slug=router_slug, router_kind=router_kind)
    with _target_lock(target):
        sidecar = target / "skillager.materialized.yaml"
        actual_router_slug = router_slug or target.name
        rendered = render_router_skill(tag, skills, agent=agent, router_slug=actual_router_slug, router_kind=router_kind)
        prospective_hash = _single_file_content_hash(rendered)
        decisions = _exposure_decisions(sidecar)
        if prospective_hash in decisions.get("exposure_blocked_hashes", []):
            return _result(router_skill, target, "skipped", "exact exposure hash is blocked by prior project policy", agent=agent, scope=scope)
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(router_skill, target, "skipped", WORKING_REASON_LOCAL_CUSTOMIZATION, agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(router_skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(router_skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(rendered, encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar_data = _router_sidecar(
            tag,
            skills,
            agent=agent,
            scope=scope,
            materialized_hash=materialized_hash,
            materialized_fingerprint=content_tree_fingerprint(target),
            router_slug=actual_router_slug,
            router_kind=router_kind,
        )
        sidecar_data.update(decisions)
        sidecar.write_text(
            dumps(sidecar_data),
            encoding="utf-8",
        )
        return _result(router_skill, target, "materialized", None, agent=agent, scope=scope)


def render_router_skill(
    tag: str | None,
    skills: list[dict[str, Any]],
    *,
    agent: str | None = None,
    router_slug: str | None = None,
    router_kind: str = "tag",
) -> str:
    if router_kind == "tag":
        assert tag is not None
        title = f"Skillager {tag} Router"
        description = f"Route {tag} tasks to available Skillager-managed skills."
        use_when = f"Use when the task is related to the `{tag}` skill tag or one of the available skills exposed by this router."
        activation_slug = router_slug or f"skillager-{slugify(tag)}"
        search_instruction = f"Activate only skills listed below or returned by `skillager search --tag {tag} \"<query>\" --agent {agent or 'codex'}`."
    else:
        title = "Skillager Explicit Router"
        description = "Route explicitly selected tasks to available Skillager-managed skills."
        use_when = "Use when the task matches one of the explicit available skills exposed by this router."
        activation_slug = router_slug or "skillager-router"
        search_instruction = "Activate only skills listed below."
    lines = [
        "---",
        f"name: \"{_frontmatter_string(title)}\"",
        f"description: \"{_frontmatter_string(description)}\"",
        "---",
        "",
        f"# {title}",
        "",
        use_when,
        "",
        "This router exposes compact available metadata only. It does not approve new skills.",
        "",
        "When an available skill exposed by this router is relevant:",
        "",
        f"1. Run `skillager activate <skill-id> --from-router {activation_slug}`.",
        f"2. {search_instruction}",
        "3. Never use `--force`.",
        "4. If no exposed skill fits, continue without activating another skill.",
        "",
        "Available skills:",
        "",
    ]
    if not skills:
        empty_label = "tag" if router_kind == "tag" else "router selection"
        lines.extend([f"No skills are currently available for this {empty_label}.", ""])
        return "\n".join(lines)
    if len(skills) > 20:
        if router_kind == "tag":
            lines.extend(
                [
                    f"This tag contains {len(skills)} available skills.",
                    f"Use `skillager search --tag {tag} \"<query>\" --agent {agent or 'codex'}` to find the right skill, then activate it through this router.",
                    "",
                ]
            )
            return "\n".join(lines)
        lines.append(f"This router contains {len(skills)} explicitly selected available skills.")
        lines.append("")
    for skill in skills:
        lines.append(f"- `{skill['id']}`")
        lines.append(f"  - Use when: {skill.get('summary', '').strip()}")
        for warning in compatibility_warnings(skill, agent):
            lines.append(f"  - Compatibility note: {warning}")
        lines.append("")
    return "\n".join(lines)


def materialize_stub_one(
    skill: dict[str, Any],
    *,
    target: Path,
    agent: str,
    scope: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    with _target_lock(target):
        sidecar = target / "skillager.materialized.yaml"
        rendered = render_stub_skill(skill)
        prospective_hash = _single_file_content_hash(rendered)
        decisions = _exposure_decisions(sidecar)
        if prospective_hash in decisions.get("exposure_blocked_hashes", []):
            return _result(skill, target, "skipped", "exact exposure hash is blocked by prior project policy", agent=agent, scope=scope)
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(skill, target, "skipped", WORKING_REASON_LOCAL_CUSTOMIZATION, agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(rendered, encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar_data = _stub_sidecar(
            skill,
            agent=agent,
            scope=scope,
            materialized_hash=materialized_hash,
            materialized_fingerprint=content_tree_fingerprint(target),
        )
        sidecar_data.update(decisions)
        sidecar.write_text(
            dumps(sidecar_data),
            encoding="utf-8",
        )
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


def render_stub_skill(skill: dict[str, Any]) -> str:
    skill_id = skill["id"]
    name = _stub_display_name(skill)
    summary = str(skill.get("summary") or "Use this Skillager-managed skill when it matches the user's task.").strip()
    lines = [
        "---",
        f"name: \"{_frontmatter_string(name)}\"",
        f"description: \"{_frontmatter_string(summary)}\"",
        "---",
        "",
        f"# {name}",
        "",
        summary,
        "",
        "This is a Skillager stub. It exposes only available metadata, not the full skill body.",
        "",
    ]
    warnings = compatibility_warnings(skill)
    if warnings:
        lines.append("Compatibility notes:")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.extend(
        [
            "Before following the skill instructions, activate the full available skill body:",
            "",
            "```bash",
            f"skillager activate {skill_id} --from-stub {slugify(skill_id)}",
            "```",
            "",
            "Never use `--force`. If activation is refused, continue without this skill or ask the user to run `skillager setup`.",
            "",
        ]
    )
    return "\n".join(lines)


def _stub_display_name(skill: dict[str, Any]) -> str:
    skill_id = str(skill["id"])
    name = str(skill.get("name") or "").strip()
    slug_name = name.lower().replace(" ", "-").replace("_", "-")
    source_slug = skill_id.rsplit("/", 1)[-1].lower()
    if not name or name.lower() in {"arguments", "argument", "skill", "untitled"} or slug_name == source_slug:
        return skill_id
    return name


def _frontmatter_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def materialize_one(
    skill: dict[str, Any],
    *,
    target: Path,
    agent: str,
    scope: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    source_root = Path(skill["root"]).resolve()
    with _target_lock(target):
        target = _collision_safe_target(target, skill["id"])
        if scope == "project" and target.resolve() == source_root and (target / "SKILL.md").exists() and not (target / "skillager.materialized.yaml").exists():
            return _result(skill, target, "already_native", "existing unmanaged native skill", agent=agent, scope=scope)
        target_skill = target / "SKILL.md"
        sidecar = target / "skillager.materialized.yaml"
        decisions = _exposure_decisions(sidecar)
        if skill.get("content_hash") in decisions.get("exposure_blocked_hashes", []):
            return _result(skill, target, "skipped", "exact exposure hash is blocked by prior project policy", agent=agent, scope=scope)
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(skill, target, "skipped", WORKING_REASON_LOCAL_CUSTOMIZATION, agent=agent, scope=scope)
            if not force and target_skill.exists() and not sidecar.exists():
                return _result(skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        expected_hash = skill.get("content_hash")
        if not isinstance(expected_hash, str):
            raise ValueError("source identity is incomplete; refresh inventory before exposure")
        with tempfile.TemporaryDirectory(prefix=".skillager-expose-", dir=target.parent) as raw_temp:
            temp_root = Path(raw_temp)
            candidate = temp_root / "candidate"
            candidate.mkdir()
            _copy_skill_tree(source_root, candidate)
            materialized_hash = content_hash(candidate)
            if materialized_hash != expected_hash or content_hash(source_root) != expected_hash:
                raise ValueError("source changed during exposure; review the new hash before exposing it")
            candidate_sidecar = candidate / "skillager.materialized.yaml"
            sidecar_data = _sidecar(
                skill,
                agent=agent,
                scope=scope,
                materialized_hash=materialized_hash,
                materialized_fingerprint=content_tree_fingerprint(candidate),
            )
            sidecar_data.update(decisions)
            candidate_sidecar.write_text(dumps(sidecar_data), encoding="utf-8")
            _install_verified_candidate(candidate, target, temp_root=temp_root, expected_hash=expected_hash)
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


def _install_verified_candidate(candidate: Path, target: Path, *, temp_root: Path, expected_hash: str) -> None:
    backup = temp_root / "previous"
    had_target = target.exists()
    if had_target:
        os.replace(target, backup)
    try:
        os.replace(candidate, target)
        if content_hash(target) != expected_hash:
            raise ValueError("exposed content failed final hash verification")
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if had_target and backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def target_dir(*, agent: str, scope: str, skill: dict[str, Any], project_dir: Path | None = None) -> Path:
    slug = slugify(skill["id"])
    if scope == "project":
        project = (project_dir or Path.cwd()).resolve()
        native_source = _native_source_target(skill, agent=agent, project=project)
        if native_source is not None:
            return native_source
        if agent == "codex":
            base = project / ".agents" / "skills"
        elif agent == "claude":
            base = project / ".claude" / "skills"
        else:
            base = project / ".agents" / agent / "skills"
    elif scope == "global":
        if agent == "codex":
            base = Path.home() / ".codex" / "skills"
        elif agent == "claude":
            base = Path.home() / ".claude" / "skills"
        else:
            base = Path.home() / ".skillager" / "agents" / agent / "skills"
    else:
        raise ValueError("scope must be project or global")
    return base / slug


def _native_source_target(skill: dict[str, Any], *, agent: str, project: Path) -> Path | None:
    root_value = skill.get("root")
    if not root_value:
        return None
    try:
        root = Path(root_value).resolve()
    except OSError:
        return None
    bases = _project_agent_bases(project, agent)
    for base in bases:
        try:
            root.relative_to(base)
        except ValueError:
            continue
        return root
    return None


def _project_agent_bases(project: Path, agent: str) -> list[Path]:
    if agent == "codex":
        return [
            project / ".agents" / "skills",
            project / ".agents" / "codex" / "skills",
            project / ".codex" / "skills",
        ]
    if agent == "claude":
        return [
            project / ".claude" / "skills",
            project / ".agents" / "claude" / "skills",
        ]
    return [project / ".agents" / agent / "skills"]


def content_hashes(skills: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for skill in sorted(skills, key=lambda item: item.get("id", "")):
        digest.update(str(skill.get("id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(skill.get("content_hash", "")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def slugify(skill_id: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in skill_id.lower()).strip("-")


def _slug_hash(skill_id: str) -> str:
    return hashlib.sha256(skill_id.encode("utf-8")).hexdigest()[:8]


@contextlib.contextmanager
def _target_lock(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with resource_lock(target):
        yield


def _sidecar(
    skill: dict[str, Any],
    *,
    agent: str,
    scope: str,
    materialized_hash: str,
    materialized_fingerprint: str,
) -> dict[str, Any]:
    data = {
        "schema": MATERIALIZED_SCHEMA,
        "id": skill["id"],
        "source_id": skill["id"],
        "source_type": skill.get("source", {}).get("type"),
        "source_package": skill.get("package") or skill.get("source", {}).get("package"),
        "source_entrypoint": skill.get("entrypoint"),
        "source_hash": skill.get("content_hash"),
        "materialized_hash": materialized_hash,
        "materialized_fingerprint": materialized_fingerprint,
        "source_trust": skill.get("trust"),
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
    }
    _add_source_library_id(data, skill)
    return data


def _working_skill(agent: str) -> dict[str, Any]:
    source_hash = _working_source_hash(agent)
    return {
        "id": WORKING_SKILL_ID,
        "name": "Skillager Working",
        "summary": "Operate Skillager safely: quiet readiness, available-skill selection, narrow exposure, and owner-directed personal-library workflows.",
        "source": {"type": "skillager-working"},
        "content_hash": source_hash,
        "trust": "reviewed",
    }


def _working_source_hash(agent: str) -> str:
    return working_source_hash(agent)


def working_source_hash(agent: str) -> str:
    return hashlib.sha256(render_working_skill(agent).encode("utf-8")).hexdigest()[:16]


def _working_sidecar(
    *,
    agent: str,
    scope: str,
    materialized_hash: str,
    materialized_fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema": MATERIALIZED_SCHEMA,
        "id": WORKING_SKILL_ID,
        "source_id": WORKING_SKILL_ID,
        "source_type": "skillager-working",
        "source_package": "skillager",
        "source_entrypoint": "generated",
        "source_hash": _working_source_hash(agent),
        "materialized_hash": materialized_hash,
        "materialized_fingerprint": materialized_fingerprint,
        "source_trust": "reviewed",
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
    }


def _router_sidecar(
    tag: str | None,
    skills: list[dict[str, Any]],
    *,
    agent: str,
    scope: str,
    materialized_hash: str,
    materialized_fingerprint: str,
    router_slug: str,
    router_kind: str,
) -> dict[str, Any]:
    router_skill = _router_skill(tag, skills, router_slug=router_slug, router_kind=router_kind)
    data = {
        "schema": ROUTER_SCHEMA,
        "id": router_skill["id"],
        "source_id": router_skill["id"],
        "source_type": "skillager-router",
        "router_kind": router_kind,
        "selection_kind": router_kind,
        "router_slug": router_slug,
        "skill_ids": [skill["id"] for skill in skills],
        "source_hash": content_hashes(skills),
        "materialized_hash": materialized_hash,
        "materialized_fingerprint": materialized_fingerprint,
        "source_trust": "reviewed",
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
    }
    if tag is not None:
        data["tag"] = tag
    return data


def _stub_sidecar(
    skill: dict[str, Any],
    *,
    agent: str,
    scope: str,
    materialized_hash: str,
    materialized_fingerprint: str,
) -> dict[str, Any]:
    data = {
        "schema": MATERIALIZED_SCHEMA,
        "id": skill["id"],
        "source_id": skill["id"],
        "source_type": "skillager-stub",
        "source_package": skill.get("package") or skill.get("source", {}).get("package"),
        "source_entrypoint": skill.get("entrypoint"),
        "source_hash": skill.get("content_hash"),
        "materialized_hash": materialized_hash,
        "materialized_fingerprint": materialized_fingerprint,
        "source_trust": skill.get("trust"),
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
    }
    _add_source_library_id(data, skill)
    return data


def _skill_ownership(skill: dict[str, Any]) -> str:
    return "library" if (skill.get("source") or {}).get("ownership") == "library" else "external"


def _add_source_library_id(data: dict[str, Any], skill: dict[str, Any]) -> None:
    library_id = (skill.get("source") or {}).get("library_id")
    if _skill_ownership(skill) == "library" and library_id:
        data["source_library_id"] = library_id


def _copy_skill_tree(source: Path, target: Path) -> None:
    source = source.resolve()
    for path in iter_content_files(source):
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _collision_safe_target(target: Path, skill_id: str) -> Path:
    sidecar = target / "skillager.materialized.yaml"
    if not sidecar.exists():
        return target
    try:
        data = load_mapping(sidecar)
    except Exception:
        return target
    if data.get("source_id") in {None, skill_id}:
        return target
    return target.with_name(f"{target.name}-{_slug_hash(skill_id)}")


def _is_customized(sidecar: Path, target: Path) -> bool:
    if not (target / "SKILL.md").exists() or not sidecar.exists():
        return False
    try:
        data = load_mapping(sidecar)
    except Exception:
        return True
    if data.get("customized") is True:
        return True
    materialized_hash = data.get("materialized_hash")
    if not isinstance(materialized_hash, str):
        return True
    return content_hash(target) != materialized_hash


def _source_hash_matches(sidecar: Path, source_hash: object) -> bool:
    if not source_hash or not sidecar.exists():
        return False
    try:
        data = load_mapping(sidecar)
    except Exception:
        return False
    return data.get("source_hash") == source_hash


def _single_file_content_hash(content: str) -> str:
    return content_hash_entries([("SKILL.md", content.encode("utf-8"))])


def _exposure_decisions(sidecar: Path) -> dict[str, Any]:
    if sidecar.is_symlink() or not sidecar.is_file():
        return {}
    try:
        data = load_mapping(sidecar)
    except Exception:
        return {}
    decisions: dict[str, Any] = {}
    blocked = data.get("exposure_blocked_hashes")
    if isinstance(blocked, list):
        decisions["exposure_blocked_hashes"] = [str(value) for value in blocked]
    for key in ("quarantine_path", "quarantined_at"):
        value = data.get(key)
        if isinstance(value, str) and value:
            decisions[key] = value
    return decisions


def _result(
    skill: dict[str, Any],
    target: Path | None,
    status: str,
    reason: str | None,
    *,
    agent: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    result = {
        "skill_id": skill.get("id"),
        "target": str(target) if target else None,
        "status": status,
        "reason": reason,
    }
    if agent:
        result["agent"] = agent
    if scope:
        result["scope"] = scope
    return result
