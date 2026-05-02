from __future__ import annotations

import contextlib
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .compatibility import compatibility_problem, compatibility_warnings
from .simple_yaml import dumps, load_mapping
from .trust import content_hash

MATERIALIZED_SCHEMA = "skillager.materialized.v1"
TRUSTED_STATES = {"reviewed", "trusted", "pinned"}
ROUTER_SCHEMA = "skillager.router.v1"
WORKING_SKILL_ID = "skillager/working"
AGENT_NOTE = (
    "Run `skillager status` at session start. Use only reviewed/materialized Skillager-managed skills; "
    "ask the user to run `skillager setup` if review is needed."
)
AGENT_NOTE_SECTION = f"## Skillager\n{AGENT_NOTE}\n"
LEGACY_AGENT_NOTES = (
    "Skillager-managed skills are installed for this project; run `skillager --help` "
    "for review/materialization commands, and only use approved materialized skills.",
    "Skillager-managed skills are installed for this project; at session start run `skillager status`, "
    "and if it reports new/unreviewed skills ask the user to run `skillager setup` before using them.",
)


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
    include_working: bool = True,
    allow_incompatible: bool = False,
) -> list[dict[str, Any]]:
    if mode not in {"native", "stub"}:
        raise ValueError("mode must be native or stub")
    results: list[dict[str, Any]] = []
    for skill in skills:
        if skill.get("trust") == "blocked":
            results.append(_result(skill, None, "skipped", "blocked"))
            continue
        if reviewed_only and skill.get("trust") not in TRUSTED_STATES:
            results.append(_result(skill, None, "skipped", "not reviewed or trusted"))
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
            except OSError as exc:
                results.append(_result(skill, target, "skipped", str(exc), agent=agent, scope=scope))
    if include_working and scope == "project" and not dry_run and any(item["status"] == "materialized" for item in results):
        ensure_agent_notes((project_dir or Path.cwd()).resolve(), agents=agents)
        results.extend(materialize_working_skill(agents=agents, scope=scope, project_dir=project_dir, force=force))
    return results


def materialize_router(
    tag: str,
    skills: list[dict[str, Any]],
    *,
    agents: list[str],
    scope: str,
    dry_run: bool = False,
    force: bool = False,
    project_dir: Path | None = None,
) -> list[dict[str, Any]]:
    reviewed = [skill for skill in skills if skill.get("trust") in TRUSTED_STATES and skill.get("trust") != "blocked"]
    router_skill = {
        "id": f"skillager/{tag}",
        "name": f"Skillager {tag} Router",
        "summary": f"Route {tag} tasks to reviewed Skillager-managed skills.",
        "source": {"type": "skillager-router", "tag": tag},
        "content_hash": content_hashes(reviewed),
        "trust": "reviewed",
    }
    results: list[dict[str, Any]] = []
    if not reviewed:
        for agent in agents:
            target = target_dir(agent=agent, scope=scope, skill=router_skill, project_dir=project_dir)
            results.append(_result(router_skill, target, "skipped", "no reviewed skills in tag", agent=agent, scope=scope))
        return results
    for agent in agents:
        target = target_dir(agent=agent, scope=scope, skill=router_skill, project_dir=project_dir)
        try:
            results.append(
                materialize_router_one(tag, reviewed, target=target, agent=agent, scope=scope, dry_run=dry_run, force=force)
            )
        except OSError as exc:
            results.append(_result(router_skill, target, "skipped", str(exc), agent=agent, scope=scope))
    if scope == "project" and not dry_run and any(item["status"] == "materialized" for item in results):
        ensure_agent_notes((project_dir or Path.cwd()).resolve(), agents=agents)
        results.extend(materialize_working_skill(agents=agents, scope=scope, project_dir=project_dir, force=force))
    return results


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
    if scope == "project" and not dry_run and any(item["status"] == "materialized" for item in results):
        ensure_agent_notes((project_dir or Path.cwd()).resolve(), agents=agents)
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
                return _result(skill, target, "skipped", "target has local customizations", agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
            if not force and _source_hash_matches(sidecar, skill.get("content_hash")):
                return _result(skill, target, "skipped", "already up to date", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_working_skill(agent), encoding="utf-8")
        (target / "skillager.yaml").write_text(dumps(_working_manifest()), encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar.write_text(dumps(_working_sidecar(agent=agent, scope=scope, materialized_hash=materialized_hash)), encoding="utf-8")
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


def render_working_skill(agent: str = "agent") -> str:
    return f"""# Skillager Working

Use when starting work in a project that has Skillager-managed skills, when `skillager status` reports available approved skills, or when the user asks you to set up, expose, route, activate, or review skills.

This skill is a protocol for using Skillager safely. It does not approve third-party skills and it does not contain any third-party skill bodies.

## Session Start

1. Run `skillager status` once.
2. If status reports new or changed unreviewed skills, ask the user to run `skillager setup` from the project directory. Do not activate or rely on unreviewed skills.
3. If status reports unattached registered collections, ask the user whether to enable one with `skillager collection enable <name>` before setup. Do not assume collection skills are available until the collection is enabled, reviewed, and materialized/router-exposed.
4. If status reports `lookback_pending`, ask whether the user wants to review `skillager lookback` before starting. Do not apply recommendations without user approval.
5. If status is clean, ask what the user plans to do in this repo before materializing additional skills.

## Query Cadence

Do not search Skillager on every user message. Search only when:

- The user starts a new domain or task.
- The current task would benefit from specialized skills not already materialized.
- You are unsure how to approach the task and an approved skill may contain the right workflow.
- `skillager status` reports newly reviewed skills.
- The user asks about available skills.

Once you choose a native skill or router path for a task, keep using that choice until the task changes. Keep Skillager checks quiet unless review, materialization, activation, or user approval is needed.

## Safe Metadata Commands

These commands are safe because they do not reveal full skill bodies:

```bash
skillager status --json
skillager list --json
skillager search "<user goal>" --trusted-only --json
skillager show <skill-id> --json
skillager tag show <tag> --json
skillager project tags --json
```

Use these to decide which approved skills are relevant.

## Exposure Policy

- Prefer router exposure for broad attached collections:
  `skillager materialize --tag <tag> --mode router --agent {agent} --scope project`
- Prefer native exposure for narrow, high-signal project skills:
  `skillager materialize <skill-id> --agent {agent} --scope project`
- Ask before running materialization commands unless the user has clearly asked you to handle Skillager setup/exposure.
- Never use `--include-unreviewed` or `--force` unless the user explicitly asks for that exact override.

## Activation Policy

- Activate only reviewed, trusted, or pinned skills.
- For router-listed skills, use:
  `skillager activate <skill-id> --from-router skillager-<tag>`
- Do not activate skills outside a router tag unless approved metadata clearly matches the task.
- If no approved skill fits, continue without activating a Skillager-managed skill.

## Lookback

At the end of substantial work, run:

```bash
skillager lookback
```

Lookback recommendations consider the recent session window plus active sessions, so do not promote or demote shared project-native skills from a single isolated session unless the user explicitly asks.
Observed overlap is a behavioral hint from repeated search/session co-occurrence, not a decision. Ask the user whether to pin a winner, keep route-only, stub commands, block old skills, or ignore.
Session logs are compact local metadata only and are auto-pruned by retention limits.

If the user gives feedback on a skill, record it:

```bash
skillager lookback --feedback useful --skill-id <skill-id>
skillager lookback --feedback route-only --skill-id <skill-id>
skillager lookback --feedback block --skill-id <skill-id>
```
"""


def materialize_router_one(
    tag: str,
    skills: list[dict[str, Any]],
    *,
    target: Path,
    agent: str,
    scope: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    router_skill = {
        "id": f"skillager/{tag}",
        "source": {"type": "skillager-router", "tag": tag},
        "content_hash": content_hashes(skills),
        "trust": "reviewed",
    }
    with _target_lock(target):
        sidecar = target / "skillager.materialized.yaml"
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(router_skill, target, "skipped", "target has local customizations", agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(router_skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(router_skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_router_skill(tag, skills, agent=agent), encoding="utf-8")
        (target / "skillager.yaml").write_text(dumps(_router_manifest(tag)), encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar.write_text(dumps(_router_sidecar(tag, skills, agent=agent, scope=scope, materialized_hash=materialized_hash)), encoding="utf-8")
        return _result(router_skill, target, "materialized", None, agent=agent, scope=scope)


def render_router_skill(tag: str, skills: list[dict[str, Any]], *, agent: str | None = None) -> str:
    lines = [
        f"# Skillager {tag} Router",
        "",
        f"Use when the task is related to the `{tag}` skill tag or one of the reviewed skills exposed by this router.",
        "",
        "This router exposes compact reviewed metadata only. It does not approve new skills.",
        "",
        "When a reviewed skill exposed by this router is relevant:",
        "",
        f"1. Run `skillager activate <skill-id> --from-router skillager-{slugify(tag)}`.",
        f"2. Activate only skills listed below or returned by `skillager search --tag {tag} \"<query>\" --approved-only`.",
        "3. Never use `--force`.",
        "4. If no exposed skill fits, continue without activating another skill.",
        "",
        "Available reviewed skills:",
        "",
    ]
    if not skills:
        lines.extend(["No reviewed skills are currently available for this tag.", ""])
        return "\n".join(lines)
    if len(skills) > 20:
        lines.extend(
            [
                f"This tag contains {len(skills)} reviewed skills.",
                f"Use `skillager search --tag {tag} \"<query>\" --approved-only` to find the right skill, then activate it through this router.",
                "",
            ]
        )
        return "\n".join(lines)
    for skill in skills:
        lines.append(f"- `{skill['id']}`")
        lines.append(f"  - Use when: {skill.get('summary', '').strip()}")
        lines.append(f"  - Risk: {skill.get('scan', {}).get('risk', 'unknown')}")
        lines.append(f"  - Trust: {skill.get('trust', 'unknown')}")
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
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(skill, target, "skipped", "target has local customizations", agent=agent, scope=scope)
            if not force and (target / "SKILL.md").exists() and not sidecar.exists():
                return _result(skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(render_stub_skill(skill), encoding="utf-8")
        (target / "skillager.yaml").write_text(dumps(_stub_manifest(skill)), encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar.write_text(dumps(_stub_sidecar(skill, agent=agent, scope=scope, materialized_hash=materialized_hash)), encoding="utf-8")
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


def render_stub_skill(skill: dict[str, Any]) -> str:
    skill_id = skill["id"]
    name = _stub_display_name(skill)
    summary = str(skill.get("summary") or "Use this Skillager-managed skill when it matches the user's task.").strip()
    lines = [
        f"# {name}",
        "",
        summary,
        "",
        "This is a Skillager stub. It exposes only approved metadata, not the full skill body.",
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
            "Before following the skill instructions, activate the full reviewed skill body:",
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
    if not name or name.lower() in {"arguments", "argument", "skill", "untitled"}:
        return skill_id
    return name


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
        if target.exists():
            if _is_customized(sidecar, target) and not force:
                return _result(skill, target, "skipped", "target has local customizations", agent=agent, scope=scope)
            if not force and target_skill.exists() and not sidecar.exists():
                return _result(skill, target, "skipped", "target exists without Skillager provenance", agent=agent, scope=scope)
        if dry_run:
            return _result(skill, target, "would_write", None, agent=agent, scope=scope)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        _copy_skill_tree(Path(skill["root"]), target)
        if not (target / "skillager.yaml").exists():
            (target / "skillager.yaml").write_text(dumps(_skill_manifest(skill)), encoding="utf-8")
        materialized_hash = content_hash(target)
        sidecar.write_text(dumps(_sidecar(skill, agent=agent, scope=scope, materialized_hash=materialized_hash)), encoding="utf-8")
        return _result(skill, target, "materialized", None, agent=agent, scope=scope)


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


def agent_note_paths(project_dir: Path | None = None, *, agents: list[str] | None = None) -> list[Path]:
    project = (project_dir or Path.cwd()).resolve()
    targets = set(agents or ["codex"])
    paths: list[Path] = []
    if "codex" in targets or not targets:
        codex_existing = [path for path in [project / "AGENTS.md", project / "agents.md"] if path.exists()]
        paths.append(codex_existing[0] if codex_existing else project / "AGENTS.md")
    if "claude" in targets:
        paths.append(project / "CLAUDE.md")
    if not paths:
        paths.append(project / "AGENTS.md")
    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def ensure_agent_notes(project_dir: Path | None = None, *, agents: list[str] | None = None) -> list[Path]:
    paths = agent_note_paths(project_dir, agents=agents)
    for path in paths:
        _ensure_agent_note(path)
    return paths


def _ensure_agent_note(path: Path) -> None:
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if "## Skillager" in content and AGENT_NOTE in content:
            return
        for legacy in LEGACY_AGENT_NOTES:
            if legacy in content:
                path.write_text(content.replace(legacy, AGENT_NOTE_SECTION.rstrip()), encoding="utf-8")
                return
        prefix = "" if content.endswith("\n") or not content else "\n"
        path.write_text(f"{content}{prefix}{AGENT_NOTE_SECTION}", encoding="utf-8")
        return
    path.write_text(AGENT_NOTE_SECTION, encoding="utf-8")


def slugify(skill_id: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in skill_id.lower()).strip("-")


def _slug_hash(skill_id: str) -> str:
    return hashlib.sha256(skill_id.encode("utf-8")).hexdigest()[:8]


@contextlib.contextmanager
def _target_lock(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / ".skillager-materialize.lock"
    with lock.open("a+b") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            yield


def _sidecar(skill: dict[str, Any], *, agent: str, scope: str, materialized_hash: str) -> dict[str, Any]:
    return {
        "schema": MATERIALIZED_SCHEMA,
        "id": skill["id"],
        "source_id": skill["id"],
        "source_type": skill.get("source", {}).get("type"),
        "source_package": skill.get("package") or skill.get("source", {}).get("package"),
        "source_entrypoint": skill.get("entrypoint"),
        "source_hash": skill.get("content_hash"),
        "materialized_hash": materialized_hash,
        "source_trust": skill.get("trust"),
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
        "customized": False,
    }


def _skill_manifest(skill: dict[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": "skillager.skill.v1",
        "id": skill["id"],
        "name": skill["name"],
        "summary": skill["summary"],
        "source": {"type": "project-override", "source_id": skill["id"]},
        "audience": skill.get("audience", ["user"]),
        "activation": {"default": skill.get("activation", "manual")},
        "entrypoint": "SKILL.md",
        "safety": skill.get("safety", {"min_trust": "reviewed", "allow_tools": False}),
    }
    if skill.get("triggers"):
        manifest["triggers"] = skill["triggers"]
    if skill.get("context"):
        manifest["context"] = skill["context"]
    if skill.get("compatibility"):
        manifest["compatibility"] = skill["compatibility"]
    return manifest


def _router_manifest(tag: str) -> dict[str, Any]:
    return {
        "schema": "skillager.skill.v1",
        "id": f"skillager/{tag}",
        "name": f"Skillager {tag} Router",
        "summary": f"Route {tag} tasks to reviewed Skillager-managed skills.",
        "source": {"type": "skillager-router", "tag": tag},
        "audience": ["user"],
        "activation": {"default": "suggested"},
        "entrypoint": "SKILL.md",
        "safety": {"min_trust": "reviewed", "allow_tools": False},
    }


def _stub_manifest(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "skillager.skill.v1",
        "id": skill["id"],
        "name": skill.get("name") or skill["id"],
        "summary": skill.get("summary") or "",
        "source": {"type": "skillager-stub", "source_id": skill["id"]},
        "audience": skill.get("audience", ["user"]),
        "activation": {"default": "suggested"},
        "entrypoint": "SKILL.md",
        "safety": {"min_trust": "reviewed", "allow_tools": True},
        "compatibility": skill.get("compatibility", {}),
    }


def _working_skill(agent: str) -> dict[str, Any]:
    source_hash = _working_source_hash(agent)
    return {
        "id": WORKING_SKILL_ID,
        "name": "Skillager Working",
        "summary": "Use Skillager safely from an agent: status first, approved metadata only, narrow router/native materialization, guarded activation, and lookback.",
        "source": {"type": "skillager-working"},
        "content_hash": source_hash,
        "trust": "reviewed",
    }


def _working_source_hash(agent: str) -> str:
    return hashlib.sha256(render_working_skill(agent).encode("utf-8")).hexdigest()[:16]


def _working_manifest() -> dict[str, Any]:
    return {
        "schema": "skillager.skill.v1",
        "id": WORKING_SKILL_ID,
        "name": "Skillager Working",
        "summary": "Use Skillager safely from an agent: status first, approved metadata only, narrow router/native materialization, guarded activation, and lookback.",
        "source": {"type": "skillager-working"},
        "audience": ["dev", "user"],
        "activation": {"default": "always"},
        "entrypoint": "SKILL.md",
        "safety": {"min_trust": "reviewed", "allow_tools": True},
    }


def _working_sidecar(*, agent: str, scope: str, materialized_hash: str) -> dict[str, Any]:
    return {
        "schema": MATERIALIZED_SCHEMA,
        "id": WORKING_SKILL_ID,
        "source_id": WORKING_SKILL_ID,
        "source_type": "skillager-working",
        "source_package": "skillager",
        "source_entrypoint": "generated",
        "source_hash": _working_source_hash(agent),
        "materialized_hash": materialized_hash,
        "source_trust": "reviewed",
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
        "customized": False,
    }


def _router_sidecar(
    tag: str,
    skills: list[dict[str, Any]],
    *,
    agent: str,
    scope: str,
    materialized_hash: str,
) -> dict[str, Any]:
    return {
        "schema": ROUTER_SCHEMA,
        "id": f"skillager/{tag}",
        "source_id": f"skillager/{tag}",
        "source_type": "skillager-router",
        "tag": tag,
        "skill_ids": [skill["id"] for skill in skills],
        "source_hash": content_hashes(skills),
        "materialized_hash": materialized_hash,
        "source_trust": "reviewed",
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
        "customized": False,
    }


def _stub_sidecar(skill: dict[str, Any], *, agent: str, scope: str, materialized_hash: str) -> dict[str, Any]:
    return {
        "schema": MATERIALIZED_SCHEMA,
        "id": skill["id"],
        "source_id": skill["id"],
        "source_type": "skillager-stub",
        "source_package": skill.get("package") or skill.get("source", {}).get("package"),
        "source_entrypoint": skill.get("entrypoint"),
        "source_hash": skill.get("content_hash"),
        "materialized_hash": materialized_hash,
        "source_trust": skill.get("trust"),
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "scope": scope,
        "customized": False,
    }


def _copy_skill_tree(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if _copy_excluded(relative):
            continue
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


def _copy_excluded(relative: Path) -> bool:
    for part in relative.parts:
        if part in {".git", "__pycache__", ".pytest_cache", "skillager.materialized.yaml"}:
            return True
        if part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return False


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
