"""Deterministic on-disk search workload; all registration/approval uses the CLI."""
from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .support import BODY_SENTINEL, CliResult, SkillagerCli, make_basic_workspace


DOMAINS = ("database", "cache", "schema", "telemetry", "geometry", "packaging", "queues", "rendering")
SENTENCES = (
    "Inspect the recorded examples and compare the observed behavior with the expected result.",
    "Describe the relevant inputs, assumptions, and verification steps in the project notes.",
    "Use a small representative dataset to explain the reasoning behind the recommendation.",
    "Review the boundary conditions before drawing conclusions from a single observation.",
    "Keep the explanation concise and include a reproducible example for the reader.",
    "Examples may include café labels, naïve comparisons, and multilingual notes: 東京.",
)
LIBRARY_NAMES = ("title-match", "description-match", "body-match", "long-body")


@dataclass(frozen=True)
class SearchCase:
    name: str
    query: str
    expected_ids: tuple[str, ...] | None
    options: tuple[str, ...] = ()
    reason: str | None = None
    count: int | None = None

    @property
    def argv(self) -> tuple[str, ...]:
        return ("search", self.query, "--json", "--no-session-record", *self.options)


CASES = (
    SearchCase("title", "amberneedle", ("lib/title-match",), reason="name:amberneedle"),
    SearchCase("description", "cobaltneedle", ("lib/description-match",), reason="summary:cobaltneedle"),
    SearchCase("body", "deadlockneedle", ("lib/body-match",), reason="body:deadlockneedle"),
    SearchCase("ranking", "rankneedle", (
        "synthetic-b/rank-title", "synthetic-a/rank-summary", "synthetic-b/rank-body",
    )),
    SearchCase("global-scope-competition", "scopeprobe", ("synthetic-a/priority-match",), ("--limit", "1")),
    SearchCase("tag-before-limit", "scopeprobe", ("lib/body-match",), ("--tag", "library-probes", "--limit", "1")),
    SearchCase("library-before-limit", "scopeprobe", ("lib/body-match",), ("--scope", "library", "--limit", "1")),
    SearchCase("library-body", "deadlockneedle", ("lib/body-match",), ("--scope", "library"), reason="body:deadlockneedle"),
    SearchCase("broad", "catalogneedle", None, ("--limit", "7"), reason="summary:catalogneedle", count=7),
    SearchCase("no-match", "absentneedle", ()),
    SearchCase("pending", "pendingneedle", ()),
    SearchCase("long-body", "edgeinside", ("lib/long-body",), reason="body:edgeinside"),
)


@dataclass
class SearchCatalog:
    root: Path
    cli: SkillagerCli
    size: int
    seed: int
    manifest: dict[str, object]

    def check(self, result: CliResult, case: SearchCase) -> list[dict]:
        require_success(result)
        if BODY_SENTINEL in result.stdout + result.stderr:
            raise AssertionError(f"{case.name}: search leaked the body sentinel")
        rows = result.json()
        if not isinstance(rows, list):
            raise AssertionError(f"{case.name}: expected the current JSON result list")
        ids = tuple(row["id"] for row in rows)
        if case.expected_ids is not None and ids != case.expected_ids:
            raise AssertionError(f"{case.name}: expected {case.expected_ids}, got {ids}")
        if case.count is not None and len(rows) != case.count:
            raise AssertionError(f"{case.name}: expected {case.count} rows, got {len(rows)}")
        if len(set(ids)) != len(ids):
            raise AssertionError(f"{case.name}: duplicate result IDs")
        for row in rows:
            if not row["available"] or not row["reasons"]:
                raise AssertionError(f"{case.name}: unavailable result or missing match reasons")
            if {"body", "content", "skill_md"}.intersection(row):
                raise AssertionError(f"{case.name}: content-bearing search field")
            if case.reason and case.reason not in row["reasons"]:
                raise AssertionError(f"{case.name}: expected reason {case.reason}, got {row['reasons']}")
        return rows

    def verify_inventory(self) -> None:
        result = self.cli.run("search", "", "--limit", "0", "--json", "--no-session-record")
        require_success(result)
        if BODY_SENTINEL in result.stdout + result.stderr:
            raise AssertionError("inventory leaked the body sentinel")
        rows = result.json()
        ids = {row["id"] for row in rows}
        expected = set(self.manifest["approved_ids"])
        if ids != expected or len(rows) != self.size:
            raise AssertionError(f"expected {self.size} approved skills; missing {len(expected - ids)}, extra {len(ids - expected)}")

    def verify_edit_invalidation(self, progress: Callable[[str], None] = lambda _: None) -> None:
        before = SearchCase("before-edit", "revisionbefore", ("lib/body-match",))
        after = SearchCase("after-acceptance", "revisionafterx", ("lib/body-match",))
        progress("Checking the accepted body before editing")
        self.check(self.cli.run(*before.argv), before)
        path = self.root / "library" / "skills" / "body-match" / "SKILL.md"
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("revisionbefore", "revisionafterx"), encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if path.stat().st_size != stat.st_size:
            raise AssertionError("freshness probe must preserve file size")
        for query in (before.query, after.query):
            progress(f"Checking that the edited pending body cannot match {query}")
            pending = SearchCase("edited-hash-is-pending", query, ())
            self.check(self.cli.run(*pending.argv), pending)
        progress("Accepting the edited hash through the CLI")
        require_success(self.cli.run_confirmed("library", "accept", "body-match", "--yes", "--json"))
        progress("Checking the new body match after acceptance")
        self.check(self.cli.run(*after.argv), after)
        old = SearchCase("old-body-term-removed", before.query, ())
        progress("Checking that the old body term is absent after acceptance")
        self.check(self.cli.run(*old.argv), old)


def require_success(result: CliResult) -> None:
    if result.code:
        raise AssertionError(f"CLI exited {result.code}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")


def build_catalog(
    root: Path,
    *,
    size: int = 5000,
    seed: int = 1729,
    timeout: float = 600,
    progress: Callable[[str], None] = lambda _: None,
) -> SearchCatalog:
    if size < 16:
        raise ValueError("size must be at least 16")
    project, cli = make_basic_workspace(root)
    cli.timeout = timeout
    # Do not inherit agent-native roots, package projects, or user Git/config state.
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "CODEX_SESSION_ID", "CLAUDE_SESSION_ID", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH"):
        cli.env.pop(key, None)
    cli.env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    for key, name in (
        ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "xdg-cache"),
        ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "xdg-state"),
        ("CODEX_HOME", "codex"), ("CLAUDE_CONFIG_DIR", "claude"),
    ):
        path = root / name
        path.mkdir()
        cli.env[key] = str(path)
    cli.env["GIT_CONFIG_NOSYSTEM"] = "1"
    cli.env["GIT_CONFIG_GLOBAL"] = str(root / "no-global-gitconfig")
    library = root / "library"
    progress("Registering the isolated personal library")
    require_success(cli.run("library", "init", "--path", str(library), "--no-git", "--json"))
    rng = random.Random(seed)
    generated: dict[str, dict[str, object]] = {}

    def write(base: Path, collection: str, slug: str, title: str, summary: str, body: str, *, target_chars: int = 1024) -> None:
        folder = base / slug
        folder.mkdir(parents=True)
        text = f"---\nname: {title}\ndescription: {summary}\n---\n\n# {title}\n\n{BODY_SENTINEL}\n\n{body}\n\n"
        lines = []
        length = len(text)
        while length < target_chars:
            line = rng.choice(SENTENCES) + "\n"
            lines.append(line)
            length += len(line)
        text += "".join(lines)
        if slug == "long-body":
            text = text[:49_960] + "\nedgeinside\n" + " " * 80 + "\nedgeoutside\n"
        data = text.encode("utf-8")
        (folder / "SKILL.md").write_bytes(data)
        generated[f"{collection}/{slug}"] = {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "characters": len(text),
        }

    library_specs = (
        ("title-match", "amberneedle catalog", "Use catalogneedle guidance for database reviews.", "Compare the examples."),
        ("description-match", "Description Catalog", "Use cobaltneedle and catalogneedle guidance for schema reviews.", "Review the examples."),
        ("body-match", "Body Catalog", "Use catalogneedle guidance for queue reviews.", "Inspect deadlockneedle, scopeprobe, and revisionbefore in the examples."),
        ("long-body", "Long Catalog", "Use catalogneedle guidance for long reference notes.", "Review the complete reference notes."),
    )
    for slug, title, summary, body in library_specs:
        write(library / "skills", "lib", slug, title, summary, body, target_chars=51_000 if slug == "long-body" else 1024)
        require_success(cli.run_confirmed("library", "accept", slug, "--yes", "--json"))
    require_success(cli.run("tag", "create", "library-probes"))
    require_success(cli.run("tag", "add", "library-probes", *(f"lib/{name}" for name in LIBRARY_NAMES)))

    progress(f"Generating {size - len(LIBRARY_NAMES)} external skills in two local collections")
    special = (
        ("priority-match", "scopeprobe catalog", "Use catalogneedle guidance for competing source matches.", "Compare the examples."),
        ("rank-title", "rankneedle catalog", "Use catalogneedle guidance for title matches.", "Compare the examples."),
        ("rank-summary", "Summary Ranking", "Use rankneedle and catalogneedle guidance for description matches.", "Compare the examples."),
        ("rank-body", "Body Ranking", "Use catalogneedle guidance for body matches.", "Compare rankneedle examples."),
    )
    for index in range(size - len(LIBRARY_NAMES)):
        collection = "synthetic-a" if index % 2 == 0 else "synthetic-b"
        if index < len(special):
            slug, title, summary, body = special[index]
        else:
            domain = rng.choice(DOMAINS)
            slug = f"{domain}-{index:05d}"
            title = f"{domain} review {index:05d}"
            summary = f"Use catalogneedle guidance for {domain} examples and review notes."
            body = f"## {domain.title()} examples\n\n| Input | Observation |\n| --- | --- |\n| Small dataset | Compare the recorded output |"
        write(root / collection, collection, slug, title, summary, body, target_chars=rng.choice((1024, 4096, 12_000, 32_000)))
    synchronized_ids: list[str] = []
    for collection in ("synthetic-a", "synthetic-b"):
        progress(f"Registering and approving generated low-risk content in {collection}")
        require_success(cli.run("collection", "add", str(root / collection), "--name", collection, "--json"))
        reviewed = cli.run("setup", "--collection", collection, "--accept-low", "--no-packages", "--summary-json")
        require_success(reviewed)
        synchronized_ids.extend(item["canonical_skill_id"] for item in reviewed.json()["action"]["library_sync"]["items"]
                                if item["outcome"] in {"created", "updated", "unchanged"})
    # This workload tests freshness/ranking of the original external sources.
    # Explicitly block their newly preserved copies in this private catalog so a
    # still-approved copy cannot legitimately satisfy a stale-original query.
    # The sync behavior fixture separately proves original/canonical coexistence.
    if synchronized_ids:
        require_success(cli.run("--state-dir", str(root / "state" / "catalog"), "review", "block", *synchronized_ids, "--json"))
        require_success(cli.run("review", "block", *synchronized_ids, "--json"))
    approved_ids = sorted(generated)
    # Add pending probes only after the explicit low-risk approval pass.
    for collection, base in (("lib", library / "skills"), ("synthetic-a", root / "synthetic-a")):
        write(base, collection, "pending-probe", "Pending Catalog", "Use catalogneedle guidance for draft notes.", "Inspect pendingneedle in the draft.")
        require_success(cli.run("collection", "refresh", collection, "--json"))
    digest = hashlib.sha256()
    for skill_id, entry in sorted(generated.items()):
        digest.update(f"{skill_id}\0{entry['sha256']}\n".encode())
    manifest: dict[str, object] = {
        "schema": "skillager.synthetic-search-catalog.v1",
        "seed": seed,
        "approved_count": size,
        "pending_count": 2,
        "blocked_sync_copy_ids": synchronized_ids,
        "source_counts": {"library": 5, "external_collections": size - 3},
        "approved_ids": approved_ids,
        "skill_md_bytes": sum(int(entry["bytes"]) for entry in generated.values()),
        "fixture_sha256": digest.hexdigest(),
        "files": generated,
    }
    return SearchCatalog(root, cli, size, seed, manifest)
