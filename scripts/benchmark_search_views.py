"""Opt-in offline public-CLI evidence for bounded known-skill search views."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.behavior.support import BODY_SENTINEL, make_basic_workspace  # noqa: E402


def run(size: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="skillager-search-view-capacity-") as tmp:
        root = Path(tmp).resolve()
        project, cli = make_basic_workspace(root)
        # Fixture construction may explicitly sync a large approved batch. Search
        # measurements below retain a separate 30-second public request budget.
        cli.timeout = 180
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PYTHONPATH"):
            cli.env.pop(name, None)
        cli.env["PYTHONPATH"] = str(ROOT / "src")
        for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            cli.env[name] = str(root / name.lower())
        cli.env["GIT_CONFIG_NOSYSTEM"] = "1"
        cli.env["GIT_CONFIG_GLOBAL"] = str(root / "no-git-config")

        def checked(*args):
            result = cli.run(*args)
            if result.code or BODY_SENTINEL in result.stdout + result.stderr:
                raise AssertionError(f"Public fixture command failed: {args[:3]} (exit {result.code})")
            return result.json()

        library = root / "library"
        initialized = checked("library", "init", "--path", str(library), "--no-git", "--json")
        outside = root / "outside"
        original = hashlib.sha256()
        for index in range(size):
            folder = outside / f"example-{index:05}"
            folder.mkdir(parents=True)
            text = (f"---\nname: Catalog example {index:05}\ndescription: Use catalogneedle guidance for examples.\n---\n\n"
                    f"{BODY_SENTINEL}\n\nCheck capacitybodyneedle against the documented examples.\n")
            (folder / "SKILL.md").write_text(text)
            original.update(text.encode())
        print(f"Approving and preserving {size} external sources through the CLI", flush=True)
        checked("collection", "add", str(outside), "--name", "capacity", "--json")
        approved = checked("review", "approve", "--collection", "capacity", "--bulk-approve", "--json")
        sync = approved["action"]["library_sync"]
        setup_batches = []
        # The mutation owner's ordinary soft deadline can return a partial batch.
        # Complete fixture setup explicitly; never extend production limits or
        # treat these setup mutations as measured search requests.
        for attempt in range(4):
            setup_batches.append({"status": sync["status"], "counts": sync["counts"]})
            if sync["status"] == "completed":
                break
            if (attempt == 3 or sync["status"] != "partial" or not sync["counts"]["created"]
                    or any(item["outcome"] not in {"created", "unchanged"} and item["reason_code"] != "time-limit" for item in sync["items"])):
                raise AssertionError(f"Fixture batch incomplete: {sync['counts']}; status {sync['status']}")
            print("Completing the fixture's remaining time-limited sync through library sync --approved", flush=True)
            output = cli.run("library", "sync", "--approved", "--expected-library-id", initialized["library"]["library_id"],
                             "--expected-library-root", str(library), "--json")
            if output.code not in {0, 2} or BODY_SENTINEL in output.stdout + output.stderr:
                raise AssertionError("Public fixture continuation failed")
            sync = output.json()
        assert sync["counts"]["created"] + sync["counts"]["unchanged"] == size
        ids = sorted(item["canonical_skill_id"] for item in sync["items"])
        exclusions = root / "installed.json"
        excluded_ids = ids[:size // 2]
        exclusions.write_text(json.dumps({"schema": "skillager.search-installed.v1", "identities": [
            {"library_id": initialized["library"]["library_id"], "skill_id": key} for key in excluded_ids]}))
        cli.timeout = 30
        measurements = []
        # The first sample constructs the search index; setup has read source bytes,
        # so it is not an operating-system cold-cache claim.
        for label, scope, view, options in (
            ("library-first-index", "library", "skills", ("--include-installed",)),
            ("library-warm", "library", "skills", ("--include-installed",)),
            ("library-provided-exclusions", "library", "skills", ("--installed-identities", str(exclusions))),
            ("library-separate-copies", "library", "copies", ("--include-installed",)),
            ("workspace-grouped", "workspace", "skills", ("--include-installed",)),
            ("workspace-separate-copies", "workspace", "copies", ("--include-installed",)),
        ):
            start = time.perf_counter()
            output = cli.run("search", "--scope", scope, "--view", view, "--limit", "50", "--json", *options, "--", "capacitybodyneedle")
            elapsed = time.perf_counter() - start
            if output.code or BODY_SENTINEL in output.stdout + output.stderr:
                raise AssertionError(f"{label} failed (exit {output.code})")
            result = output.json()
            rows = result["results"]
            expected = size - len(excluded_ids) if label == "library-provided-exclusions" else size * (2 if scope == "workspace" and view == "copies" else 1)
            assert result["status"] == "completed" and len(rows) == min(50, expected)
            assert len({row["search"]["occurrence"]["id"] for row in rows}) == len(rows)
            assert all("body:capacitybodyneedle" in row["search"]["match"]["reasons"] for row in rows)
            if label == "library-provided-exclusions":
                assert all(row["id"] not in excluded_ids for row in rows)
            groups = {row["search"]["group_id"] for row in rows}
            if scope == "workspace":
                assert all(row["search"]["group_occurrences"] == 2 for row in rows)
                if view == "skills":
                    assert len(groups) == len(rows)
                    assert all(row["search"]["occurrence"]["kind"] == "library" for row in rows)
                else:
                    assert len(groups) * 2 == len(rows)
                    for group in groups:
                        pair = [row for row in rows if row["search"]["group_id"] == group]
                        assert {row["search"]["occurrence"]["kind"] for row in pair} == {"library", "source"}
                        match = pair[0]["search"]["match"]
                        assert pair[1]["search"]["match"] == match
                        matching = next(row for row in pair if row["search"]["occurrence"]["id"] == match["occurrence_id"])
                        assert matching["id"] == match["skill_id"] and matching["content_hash"] == match["content_hash"]
                        assert matching["search"]["occurrence"] == match["occurrence"]
                for row in rows:
                    match = row["search"]["match"]
                    assert Path(match["occurrence"]["entrypoint"]).read_text().find("capacitybodyneedle") >= 0
                    if row["search"]["occurrence"]["id"] != match["occurrence_id"]:
                        assert row["reasons"] == []
            sample = {"case": label, "scope": scope, "view": view, "seconds": elapsed, "stdout_bytes": len(output.stdout.encode()), "stderr_bytes": len(output.stderr.encode()), "rows": len(rows), "logical_groups": len(groups)}
            assert elapsed < 30 and sample["stdout_bytes"] <= 4 * 1024 * 1024
            measurements.append(sample)
            print(json.dumps(sample), flush=True)
        after = hashlib.sha256()
        for path in sorted(outside.glob("*/SKILL.md")):
            after.update(path.read_bytes())
        assert after.hexdigest() == original.hexdigest()
        return {"schema": "skillager.search-view-benchmark.v1", "owned_sources": size, "external_sources": size,
                "fixture_source_bytes_unchanged": True, "setup_batches": setup_batches,
                "search_deadline_seconds": 30, "measurements": measurements}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=5000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.size <= 5000:
        parser.error("--size must be from 2 through 5000")
    result = run(args.size)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
