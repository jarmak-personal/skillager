"""Benchmark-owned Git library, constructed and verified through the public CLI."""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from .search_catalog import DOMAINS, SENTENCES, require_success
from .support import BODY_SENTINEL, REPO_ROOT, SkillagerCli


MARKER = "owned-library-benchmark.json"
SCHEMA = "skillager.owned-library-fixture.v1"
PROBE = "benchmark-probe"
QUERY = "catalogneedle"


def source_tree(repo: Path = REPO_ROOT) -> str:
    paths = ["src/skillager", "packages/skillager-linter"]
    changes = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths], cwd=repo, text=True)
    if changes:
        raise ValueError("benchmark requires clean tracked and untracked product source")
    return subprocess.check_output(["git", "rev-parse", *(f"HEAD:{path}" for path in paths)], cwd=repo, text=True).strip()


def generated_sources(size: int, seed: int):
    rng = random.Random(seed)
    for index in range(size - 1):
        name = f"owned-{index:05d}"
        domain = rng.choice(DOMAINS)
        text = f"---\nname: {name}\ndescription: Use catalogneedle guidance for {domain} reviews.\n---\n\n# {name}\n\n{BODY_SENTINEL}\n\n"
        target = rng.choice((1024, 4096, 12_000, 32_000))
        while len(text) < target:
            text += rng.choice(SENTENCES) + "\n"
        yield name, text.encode("utf-8")


def probe_text(revision: int) -> str:
    return f"---\nname: {PROBE}\ndescription: Use catalogneedle guidance for benchmark reviews.\n---\n\n# Benchmark probe\n\n{BODY_SENTINEL}\n\nCompare the recorded examples.\n\nRevision: {revision:08d}\n"


class OwnedLibraryCatalog:
    def __init__(self, root: Path, state: dict, *, timeout: float = 600):
        self.root, self.state = root.resolve(), state
        for name in ("project", "state", "home", "cache", "library", "sources", "config", "xdg-cache", "data", "xdg-state", "codex", "claude"):
            target = self.root / name
            if target.is_symlink() or not target.resolve().is_relative_to(self.root):
                raise ValueError("benchmark state paths must stay inside the owned fixture")
        self.cli = SkillagerCli(self.root / "project", state=self.root / "state" / "project",
                               catalog_state=self.root / "state" / "catalog", home=self.root / "home",
                               cache=self.root / "cache", timeout=timeout)
        for key in list(self.cli.env):
            if key.startswith("GIT_") or key in {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "CODEX_SESSION_ID", "CLAUDE_SESSION_ID"}:
                self.cli.env.pop(key)
        for key, path in {
            "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "xdg-cache", "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "xdg-state",
            "CODEX_HOME": "codex", "CLAUDE_CONFIG_DIR": "claude",
        }.items():
            (self.root / path).mkdir(exist_ok=True)
            self.cli.env[key] = str(self.root / path)
        self.cli.env.update({"PYTHONPATH": str(REPO_ROOT / "src"), "GIT_CONFIG_NOSYSTEM": "1",
                             "GIT_CONFIG_GLOBAL": str(self.root / "no-global-gitconfig")})

    @classmethod
    def open(cls, *, size: int, seed: int, resume: Path | None = None, timeout: float = 600):
        if resume is None:
            root = Path(tempfile.mkdtemp(prefix="skillager-owned-library-")).resolve()
            (root / "project").mkdir()
            (root / "project" / "pyproject.toml").write_text('[project]\nname = "owned-library-benchmark"\n', encoding="utf-8")
            state = {"schema": SCHEMA, "root": str(root), "size": size, "seed": seed,
                     "source_tree": source_tree(), "stage": "new", "probe_revision": 0,
                     "construction_seconds": 0.0, "construction_commands": 0}
        else:
            if resume.is_symlink():
                raise ValueError("benchmark resume root must not be a symlink")
            root = resume.resolve()
            marker = root / MARKER
            if marker.is_symlink() or not marker.is_file():
                raise ValueError("resume requires a benchmark-owned fixture marker")
            state = json.loads(marker.read_text(encoding="utf-8"))
            expected = {"schema": SCHEMA, "root": str(root), "size": size, "seed": seed, "source_tree": source_tree()}
            if any(state.get(key) != value for key, value in expected.items()):
                raise ValueError("resume fixture, seed, size, or product source does not match")
        instance = cls(root, state, timeout=timeout)
        instance.save()
        return instance

    def save(self) -> None:
        temp = self.root / (MARKER + ".tmp")
        temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.root / MARKER)

    def run(self, *argv: str):
        result = self.cli.run(*argv)
        self.state["construction_commands"] += 1
        require_success(result)
        return result

    def build(self, progress: Callable[[str], None] = lambda _: None) -> None:
        started = time.perf_counter()
        try:
            if self.state["stage"] == "new":
                self.run("library", "init", "--path", str(self.root / "library"), "--json")
                self.state["stage"] = "initialized"
                self.save()
            sources = self.root / "sources"
            expected_paths = set()
            digest = hashlib.sha256()
            byte_count = 0
            for name, data in generated_sources(self.state["size"], self.state["seed"]):
                target = sources / name / "SKILL.md"
                if target.parent.is_symlink():
                    raise ValueError("generated source directory must not be a symlink")
                expected_paths.add(target)
                digest.update(name.encode() + b"\0" + data)
                byte_count += len(data)
                if target.exists():
                    if target.is_symlink() or target.read_bytes() != data:
                        raise ValueError(f"generated source changed: {name}")
                elif self.state["stage"] == "initialized":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                else:
                    raise ValueError(f"generated source missing: {name}")
            if {path for path in sources.rglob("*") if path.is_file() or path.is_symlink()} != expected_paths:
                raise ValueError("unexpected files in benchmark sources")
            self.state.update(source_sha256=digest.hexdigest(), source_skill_md_bytes=byte_count)
            if self.state["stage"] == "initialized":
                self.run("collection", "add", str(sources), "--name", "benchmark-origin", "--json")
                self.state["stage"] = "registered"
                self.save()
            if self.state["stage"] == "registered":
                progress(f"Approving {self.state['size'] - 1} generated sources through setup")
                self.run("setup", "--collection", "benchmark-origin", "--accept-low", "--no-packages", "--summary-json")
                self.state["stage"] = "approved"
                self.save()
            if self.state["stage"] == "approved":
                for attempt in range(self.state["size"]):
                    result = self.cli.run("library", "sync", "--approved", "--json")
                    self.state["construction_commands"] += 1
                    payload = result.json()
                    counts = payload.get("counts", {})
                    progress(f"Sync {attempt + 1}: {payload.get('status')}, {counts}")
                    self.state["last_sync"] = {"status": payload.get("status"), "counts": counts, "coverage": payload.get("coverage")}
                    self.save()
                    if result.code == 0 and payload.get("status") == "completed":
                        break
                    if payload.get("status") != "partial" or any(counts.get(key, 0) for key in ("failed", "conflict", "uncertain")):
                        raise AssertionError("sync did not safely advance; inspect retained fixture and report")
                    if any(item.get("reason_code") not in (None, "time-limit") for item in payload.get("items", [])):
                        raise AssertionError("sync partial result requires recovery rather than automatic retry")
                    owned_count = counts.get("unchanged", 0) + counts.get("created", 0)
                    if not counts.get("created", 0) + counts.get("updated", 0) and self.state.get("refreshed_owned_count") == owned_count:
                        raise AssertionError("sync did not advance after public refresh; inspect retained fixture")
                    # Deadline-limited sync may leave its rebuildable metadata cache
                    # stale. Use its public refresh owner, never fabricate that cache.
                    refresh_started = time.perf_counter()
                    self.run("collection", "refresh", "lib", "--json")
                    self.state["refreshed_owned_count"] = owned_count
                    self.state.setdefault("public_refreshes", []).append({"owned_count": owned_count, "seconds": time.perf_counter() - refresh_started})
                    self.save()
                    progress(f"Public library collection refresh completed for {owned_count} owned skills")
                else:
                    raise AssertionError("sync retry bound reached")
                self.state["stage"] = "copied"
                self.save()
            if self.state["stage"] == "copied":
                target = self.root / "library" / "skills" / PROBE / "SKILL.md"
                if not target.exists():
                    self.run("library", "new", PROBE, "--json")
                    target.write_text(probe_text(0), encoding="utf-8")
                if target.read_text(encoding="utf-8") not in (probe_text(0), probe_text(1)):
                    raise ValueError("authored benchmark probe changed")
                self.run_accept()
                self.state["stage"] = "probe"
                self.save()
            if self.state["stage"] == "probe":
                target = self.root / "library" / "skills" / PROBE / "SKILL.md"
                if target.read_text(encoding="utf-8") not in (probe_text(0), probe_text(1)):
                    raise ValueError("authored benchmark probe changed")
                target.write_text(probe_text(1), encoding="utf-8")
                self.run_accept()
                self.state.update(stage="ready", probe_revision=1)
                self.save()
            if self.state["stage"] != "ready":
                raise ValueError("unknown benchmark construction stage")
            if self.state.get("pending_probe_revision") is not None:
                revision = self.state["pending_probe_revision"]
                target = self.root / "library" / "skills" / PROBE / "SKILL.md"
                if target.read_text(encoding="utf-8") not in (probe_text(self.state["probe_revision"]), probe_text(revision)):
                    raise ValueError("interrupted authored benchmark probe changed")
                target.write_text(probe_text(revision), encoding="utf-8")
                self.run_accept()
                self.state["probe_revision"] = revision
                self.state.pop("pending_probe_revision")
                self.save()
            if (self.root / "library" / "skills" / PROBE / "SKILL.md").read_text(encoding="utf-8") != probe_text(self.state["probe_revision"]):
                raise ValueError("authored benchmark probe changed since checkpoint")
        finally:
            self.state["construction_seconds"] += time.perf_counter() - started
            self.save()

    def run_accept(self):
        preview = self.run("library", "accept", PROBE, "--json").json()
        return self.run(*preview["next_command_argv"][1:]).json()

    def pages(self, command: str, *, limit: int):
        cursor = ""
        seen = set()
        remaining = self.state["size"]
        while True:
            argv = ["list"] if command == "list" else ["search", QUERY]
            result = self.run(*argv, "--scope", "library", "--json", "--limit", str(limit), "--cursor", cursor)
            if BODY_SENTINEL in result.stdout + result.stderr:
                raise AssertionError("metadata page leaked a skill body")
            payload = result.json()
            rows = payload["skills" if command == "list" else "results"]
            if len(rows) != min(limit, remaining):
                raise AssertionError("page row count does not match remaining owned inventory")
            for row in rows:
                if row["id"] in seen:
                    raise AssertionError("duplicate skill across pages")
                seen.add(row["id"])
            remaining -= len(rows)
            cursor = payload["next_cursor"]
            if (cursor is None) != (remaining == 0):
                raise AssertionError("continuation does not match remaining owned inventory")
            yield rows
            if cursor is None:
                break

    def verify(self, *, page_size: int = 100, progress: Callable[[str], None] = lambda _: None) -> dict:
        status = self.run("library", "status", "--json").json()
        library = Path(status["library"]["root"])
        if library != self.root / "library" or status["counts"]["skills"] != self.state["size"] or status["git"]["mode"] != "system" or not status["git"]["clean"]:
            raise AssertionError("benchmark must have the exact clean Git-backed owned library")
        rows = []
        page_count = 0
        for page_count, page in enumerate(self.pages("list", limit=page_size), 1):
            rows.extend(page)
            if page_count % 10 == 0:
                progress(f"Owned inventory proof: {len(rows)}/{self.state['size']} accepted rows traversed")
        if page_count != (self.state["size"] + page_size - 1) // page_size:
            raise AssertionError("observed list page count does not match owned inventory")
        paths = {Path(row["skill_file"]) for row in rows}
        actual = set((library / "skills").glob("*/SKILL.md"))
        if len(rows) != self.state["size"] or paths != actual or any(path.is_symlink() for path in paths):
            raise AssertionError("owned file count/path proof failed")
        if any(row["status"] != "accepted" or not row["accepted_hash"] for row in rows):
            raise AssertionError("all owned skills must be accepted")
        history = self.run("library", "history", PROBE, "--json").json()
        synced = next(row["id"] for row in rows if row["id"] != f"lib/{PROBE}")
        synced_history = self.run("library", "history", synced, "--json").json()
        if not history["available"] or len(history["versions"]) < 2 or not synced_history["available"] or not synced_history["versions"]:
            raise AssertionError("authored and synchronized Git history proof failed")
        for skill_id in (f"lib/{PROBE}", synced):
            source = self.run("show", skill_id, "--full-json").json()["skill"]["source"]
            if source.get("ownership") != "library" or source.get("library_id") != status["library"]["library_id"]:
                raise AssertionError("public source identity is not owned by the selected library")
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=library, env=self.cli.env).split(b"\0")
        tracked_skills = sum(path.startswith(b"skills/") and path.endswith(b"/SKILL.md") for path in tracked)
        commit_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=library, env=self.cli.env))
        if tracked_skills != self.state["size"]:
            raise AssertionError("Git must track every actual owned skill")
        self.state["proof"] = {"owned_files": len(actual), "accepted_count": len(rows), "owned_skill_md_bytes": sum(path.stat().st_size for path in actual), "library": status["library"],
                              "git": status["git"], "git_tracked_skills": tracked_skills, "git_commit_count": commit_count,
                              "history_versions": len(history["versions"]),
                              "synced_history_versions": len(synced_history["versions"]), "list_pages": page_count,
                              "source_registrations": self.run("collection", "list", "--json").json(),
                              "previous_probe_hash": history["versions"][1]["content_hash"]}
        self.save()
        return self.state["proof"]
