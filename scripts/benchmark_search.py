"""Opt-in, offline benchmark of the public search CLI against generated skills."""
from __future__ import annotations

import argparse
import json
import math
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.behavior.search_catalog import CASES, SearchCase, build_catalog, require_success  # noqa: E402
from tests.behavior.support import CliResult  # noqa: E402


# A fresh interpreter runs the real module entrypoint. Peak RSS comes from that
# one child, not the cumulative high-water mark of earlier benchmark children.
MEASURE_CLI = """\
import json, runpy, sys
metrics_path = sys.argv.pop(1)
sys.argv[0] = 'skillager'
try:
    runpy.run_module('skillager', run_name='__main__')
finally:
    peak = None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != 'darwin':
            peak *= 1024
    except ImportError:
        pass
    with open(metrics_path, 'w', encoding='utf-8') as handle:
        json.dump({'peak_rss_bytes': peak}, handle)
"""


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def measure(catalog, case: SearchCase) -> tuple[CliResult, dict]:
    metrics = catalog.root / "child-metrics.json"
    metrics.unlink(missing_ok=True)
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", MEASURE_CLI, str(metrics), *case.argv],
        cwd=catalog.cli.project,
        env=catalog.cli.env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=catalog.cli.timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    result = CliResult(completed.returncode, completed.stdout.decode("utf-8"), completed.stderr.decode("utf-8"))
    require_success(result)
    sample = {
        "seconds": elapsed,
        "stdout_bytes": len(completed.stdout),
        "stderr_bytes": len(completed.stderr),
        **json.loads(metrics.read_text(encoding="utf-8")),
    }
    return result, sample


def percentile95(samples: list[float]) -> float:
    return sorted(samples)[math.ceil(len(samples) * 0.95) - 1]


def run_size(root: Path, size: int, args: argparse.Namespace, checkpoint: Callable[[dict], None]) -> dict:
    started = time.perf_counter()
    catalog = build_catalog(root, size=size, seed=args.seed, timeout=args.timeout, progress=progress)
    preparation_seconds = time.perf_counter() - started
    (root / "fixture-manifest.json").write_text(json.dumps(catalog.manifest, indent=2) + "\n", encoding="utf-8")
    version = catalog.cli.run("--version")
    require_success(version)
    timed = []
    # Time the actual first search before inventory enumeration or correctness
    # searches. Setup has necessarily touched files; this is not an OS-cold run.
    for name in ("body", "title", "broad", "library-body"):
        case = next(case for case in CASES if case.name == name)
        samples = []
        expected_rows = None
        for repetition in range(args.repeats + 1):
            progress(f"{size} skills: {name}, search {repetition + 1}/{args.repeats + 1}")
            result, sample = measure(catalog, case)
            rows = catalog.check(result, case)
            if expected_rows is not None and rows != expected_rows:
                raise AssertionError(f"{name}: repeated CLI searches returned different metadata")
            expected_rows = rows
            samples.append(sample)
            progress(f"  {sample['seconds']:.3f}s; {sample['stdout_bytes']} output bytes")
            checkpoint({"case": name, "sample_index": repetition, **sample})
        repeated = [sample["seconds"] for sample in samples[1:]]
        timed.append({
            "case": name,
            "argv": list(case.argv),
            "first_seconds": samples[0]["seconds"],
            "repeat_median_seconds": statistics.median(repeated),
            "repeat_p95_seconds": percentile95(repeated),
            "repeat_count": len(repeated),
            "returned_count": len(expected_rows),
            "samples": samples,
        })
    progress(f"{size} skills: checking inventory, field matches, ordering, scope, and trust changes")
    catalog.verify_inventory()
    checks = ["exact-approved-inventory"]
    timed_names = {search["case"] for search in timed}
    for case in CASES:
        if case.name not in timed_names:
            progress(f"{size} skills: checking {case.name}")
            catalog.check(catalog.cli.run(*case.argv), case)
        checks.append(case.name)
    # Observe the existing body-search boundary without making truncation a
    # permanent desired behavior in the correctness suite.
    outside = catalog.cli.run("search", "edgeoutside", "--json", "--no-session-record")
    require_success(outside)
    catalog.verify_edit_invalidation(progress)
    checks.append("same-size-restored-mtime-edit-and-reacceptance")
    return {
        "approved_count": size,
        "pending_count": catalog.manifest["pending_count"],
        "source_counts": catalog.manifest["source_counts"],
        "fixture_sha256": catalog.manifest["fixture_sha256"],
        "skill_md_bytes": catalog.manifest["skill_md_bytes"],
        "skillager_version": version.stdout.strip(),
        "preparation_seconds": preparation_seconds,
        "searches": timed,
        "passed_checks": checks,
        "observations": {
            "body_term_after_50000_characters_ids": [row["id"] for row in outside.json()],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[5000], help="Approved catalog sizes, each at least 16. Default: 5000.")
    parser.add_argument("--seed", type=int, default=1729, help="Deterministic content seed. Default: 1729.")
    parser.add_argument("--repeats", type=int, default=3, help="Repeated searches after each case's first search. Default: 3.")
    parser.add_argument("--timeout", type=float, default=600, help="Per-CLI-command timeout in seconds. Default: 600.")
    parser.add_argument("--output", type=Path, required=True, help="Write the JSON benchmark report here.")
    parser.add_argument("--keep-fixture", action="store_true", help="Retain generated temporary directories for inspection.")
    args = parser.parse_args()
    if any(size < 16 for size in args.sizes) or args.repeats < 1 or args.timeout <= 0:
        parser.error("sizes must be >=16, repeats >=1, and timeout >0")
    report = {
        "schema": "skillager.search-benchmark.v1",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        "seed": args.seed,
        "measurement": {
            "latency": "Wall time of a fresh CLI subprocess, including interpreter startup, discovery, trust checks, search, and JSON output.",
            "cache": "First body search follows fixture setup; later queries reuse OS/application cache state. OS caches are not flushed. Every sample starts a new process.",
            "memory": "Per-child peak RSS; null when resource.getrusage is unavailable. Includes the minimal measurement launcher.",
            "percentile": "Nearest-rank p95 of repeated samples; use more repeats for a meaningful tail estimate.",
            "preparation": "Generation, registration, and CLI approval are excluded from search timings and reported separately.",
        },
        "coverage_gaps": [
            "Library and tag scope are checked before --limit; the CLI has no cursor/offset/total result contract.",
            "This is a mixed-source catalog with four approved owned skills; it does not benchmark 5000 individually accepted personal-library skills or Git history.",
            "Synthetic content validates known matches and scale, not relevance to a private real-world vocabulary.",
            "These measurements cover the CLI, not hvir rendering, IPC, typing responsiveness, or SSH.",
        ],
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save_report() -> None:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def checkpoint(sample: dict) -> None:
        report["active_run"]["samples"].append(sample)
        save_report()

    try:
        for size in args.sizes:
            temporary = None
            if args.keep_fixture:
                root = Path(tempfile.mkdtemp(prefix=f"skillager-search-{size}-"))
            else:
                temporary = tempfile.TemporaryDirectory(prefix=f"skillager-search-{size}-")
                root = Path(temporary.name)
            progress(f"Fixture: {root}")
            report["active_run"] = {"approved_count": size, "fixture": str(root), "samples": []}
            save_report()
            try:
                run = run_size(root, size, args, checkpoint)
                run["retained_fixture"] = str(root) if args.keep_fixture else None
                report["runs"].append(run)
            finally:
                if temporary:
                    temporary.cleanup()
            report.pop("active_run")
            save_report()
    except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        save_report()
        progress(f"Benchmark failed; report: {args.output}: {error}")
        return 1
    report["status"] = "passed"
    save_report()
    print(f"Report: {args.output}")
    for run in report["runs"]:
        for search in run["searches"]:
            print(f"{run['approved_count']} skills / {search['case']}: first {search['first_seconds']:.3f}s, repeated median {search['repeat_median_seconds']:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
