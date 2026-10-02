"""Opt-in public CLI benchmark of a Git-backed, owned and accepted personal library."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_measurement import measure_cli  # noqa: E402
from tests.behavior.library_catalog import OwnedLibraryCatalog, PROBE, QUERY, probe_text  # noqa: E402
from tests.behavior.search_catalog import require_success  # noqa: E402
from tests.behavior.support import BODY_SENTINEL  # noqa: E402


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def clear_search_cache(catalog: OwnedLibraryCatalog) -> None:
    cache = catalog.root / "cache"
    if cache.is_symlink():
        raise ValueError("benchmark cache must not be a symlink")
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir()


def check_sample(name: str, result, catalog: OwnedLibraryCatalog):
    require_success(result)
    if name != "diff_content" and BODY_SENTINEL in result.stdout + result.stderr:
        raise AssertionError(f"{name}: metadata leaked a skill body")
    payload = result.json()
    if name.startswith("list_"):
        if len(payload["skills"]) != catalog.state["page_size"] or any(row["status"] != "accepted" for row in payload["skills"]):
            raise AssertionError("list page is not a complete accepted-metadata page")
    if name.startswith("search_"):
        if len(payload["results"]) != catalog.state["page_size"] or any(not row["available"] for row in payload["results"]):
            raise AssertionError("search page is not a complete available-metadata page")
    if name == "sync_unchanged":
        counts = payload["counts"]
        if (payload["status"] != "completed" or not payload["coverage"]["complete"]
                or counts["unchanged"] != catalog.state["size"] - 1
                or any(counts[key] for key in ("created", "updated", "conflict", "failed", "uncertain"))):
            raise AssertionError("no-change sync must report every retained origin unchanged")
    return payload


def run_measurements(catalog: OwnedLibraryCatalog, args: argparse.Namespace, report: dict) -> None:
    page_size = args.page_size
    catalog.state["page_size"] = page_size
    metrics = catalog.root / "child-metrics.json"
    history = catalog.run("library", "history", PROBE, "--json").json()
    previous = history["versions"][1]["content_hash"]
    current = history["versions"][0]["content_hash"]
    cases = [
        ("status_library", ["library", "status", "--json"]),
        ("status_skill", ["library", "status", PROBE, "--json"]),
        ("list_first", ["list", "--scope", "library", "--json", "--limit", str(page_size)]),
        ("list_next", None),
        ("search_first", ["search", QUERY, "--scope", "library", "--json", "--limit", str(page_size), "--cursor", ""]),
        ("search_next", None),
        ("show_metadata", ["show", f"lib/{PROBE}", "--json"]),
        ("accept_preview", ["library", "accept", PROBE, "--json"]),
        ("history", ["library", "history", PROBE, "--json"]),
        ("diff_stat", ["library", "diff", PROBE, "--from", previous, "--to", current, "--stat", "--json"]),
        ("diff_content", ["library", "diff", PROBE, "--from", previous, "--to", current, "--json"]),
        ("sync_unchanged", ["library", "sync", "--approved", "--json"]),
    ]
    for name, argv in cases:
        if name.endswith("_next"):
            command = name.partition("_")[0]
            first = ["list"] if command == "list" else ["search", QUERY]
            first.extend(["--scope", "library", "--json", "--limit", str(page_size), "--cursor", ""])
            first_page = catalog.run(*first).json()
            if not first_page["next_cursor"]:
                raise AssertionError("workload must have a continuation page")
            argv = [*first[:-1], first_page["next_cursor"]]
        assert argv is not None
        if name.startswith("search_"):
            clear_search_cache(catalog)
        case = {"name": name, "argv": argv, "samples": []}
        report["commands"].append(case)
        reference = None
        for index in range(args.repeats + 1):
            progress(f"{name}: {'first' if index == 0 else f'repeat {index}/{args.repeats}'}")
            result, sample = measure_cli(catalog.cli, argv, metrics)
            sample.update(phase="cold" if index == 0 else "warm", exit_code=result.code)
            case["samples"].append(sample)
            write_report(args.output, report)
            payload = check_sample(name, result, catalog)
            if "status" in payload:
                sample["response_status"] = payload["status"]
            if reference is not None and payload != reference:
                raise AssertionError(f"{name}: repeated metadata response changed")
            reference = payload
            write_report(args.output, report)
            progress(f"  {sample['seconds']:.3f}s; {sample['stdout_bytes']} bytes; peak CLI RSS {sample['peak_rss_bytes']}")
        summarize(case)
        write_report(args.output, report)

    # Mutations follow read/paging samples so they cannot invalidate those cursors.
    for changed in (True, False):
        name = "accept_changed" if changed else "accept_unchanged"
        case = {"name": name, "argv": ["library", "accept", PROBE, "--yes", "--json", "--confirmation-token", "<preview-token>"], "samples": []}
        report["commands"].append(case)
        for index in range(args.repeats + 1):
            before = catalog.run("library", "status", PROBE, "--json").json()["skill"]["accepted_hash"]
            if changed:
                revision = catalog.state["probe_revision"] + 1
                catalog.state["pending_probe_revision"] = revision
                catalog.save()
                (catalog.root / "library" / "skills" / PROBE / "SKILL.md").write_text(probe_text(revision), encoding="utf-8")
            preview = catalog.run("library", "accept", PROBE, "--json").json()
            result, sample = measure_cli(catalog.cli, preview["next_command_argv"][1:], metrics)
            sample.update(phase="cold" if index == 0 else "warm", exit_code=result.code, changed=changed, before_hash=before)
            case["samples"].append(sample)
            write_report(args.output, report)
            payload = check_sample(name, result, catalog)
            digest = payload["skill"]["accepted_hash"]
            if (changed and (not payload["commit"] or digest == before)) or (not changed and (payload["commit"] is not None or digest != before)):
                raise AssertionError("acceptance sample mutation/no-op proof failed")
            if changed:
                catalog.state["probe_revision"] = revision
                catalog.state.pop("pending_probe_revision")
                catalog.save()
            sample.update(accepted_hash=digest, commit=payload["commit"])
            write_report(args.output, report)
            progress(f"{name}: {sample['seconds']:.3f}s; {sample['stdout_bytes']} bytes")
        summarize(case)
    progress("All timing samples complete; starting exhaustive correctness traversals")
    started = time.perf_counter()
    list_ids = {row["id"] for page in catalog.pages("list", limit=page_size) for row in page}
    search_ids = {row["id"] for page in catalog.pages("search", limit=page_size) for row in page}
    if len(list_ids) != args.size or search_ids != list_ids:
        raise AssertionError("complete library/search traversals must contain the same exact owned inventory")
    report["traversal_proof"] = {"owned_count": len(list_ids), "search_count": len(search_ids),
                                 "list_pages": (args.size + page_size - 1) // page_size,
                                 "search_pages": (args.size + page_size - 1) // page_size,
                                 "seconds": time.perf_counter() - started}


def summarize(case: dict) -> None:
    samples = case["samples"]
    repeated = [sample["seconds"] for sample in samples[1:]]
    case.update(cold_seconds=samples[0]["seconds"], warm_median_seconds=statistics.median(repeated),
                max_stdout_bytes=max(sample["stdout_bytes"] for sample in samples),
                max_peak_rss_bytes=max((sample["peak_rss_bytes"] for sample in samples if sample["peak_rss_bytes"] is not None), default=None))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path, help="Reuse only a matching benchmark-owned fixture.")
    parser.add_argument("--prepare-only", action="store_true", help="Construct and verify without timing samples.")
    parser.add_argument("--keep-fixture", action="store_true")
    args = parser.parse_args()
    if not (2 <= args.size <= 5000 and 0 < args.page_size < args.size and args.repeats >= 1 and args.timeout > 0):
        parser.error("size must be 2..5000, page size positive and smaller than size, repeats >=1, timeout >0")
    report = {"schema": "skillager.library-benchmark.v1", "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "environment": {"python": platform.python_version(), "platform": platform.platform()},
              "size": args.size, "seed": args.seed, "page_size": args.page_size, "commands": [],
              "measurement": {
                  "latency": "Fresh public CLI interpreter including startup, discovery/verification, Git, and JSON output.",
                  "cold": "First measured invocation after setup; search cache cleared before each search case. Catalog/approval state remains intact. OS caches are not flushed.",
                  "warm": "Repeated fresh interpreters reuse persisted caches and OS state. Changed accept samples create successive comparable revisions, not identical inputs.",
                  "memory": "Peak RSS of the individual CLI interpreter including minimal measurement launcher; native Git subprocess and aggregate process-tree peaks are not measured.",
                  "accept": "Preview timed separately. Confirmed sample excludes its preceding preview and file edit; changed vs unchanged commit/hash proofs are recorded.",
              }}
    catalog = None
    write_report(args.output, report)
    try:
        catalog = OwnedLibraryCatalog.open(size=args.size, seed=args.seed, resume=args.resume, timeout=args.timeout)
        report["fixture"] = str(catalog.root)
        write_report(args.output, report)
        progress(f"Fixture: {catalog.root}")
        catalog.build(progress)
        progress("Verifying exact owned-file/acceptance/Git inventory through public CLI pages")
        report["fixture_proof"] = catalog.verify(page_size=args.page_size, progress=progress)
        report["construction"] = {key: catalog.state[key] for key in ("construction_seconds", "construction_commands", "source_sha256", "source_skill_md_bytes", "last_sync")}
        report["construction"]["public_refreshes"] = catalog.state.get("public_refreshes", [])
        report["skillager_version"] = catalog.run("--version").stdout.strip()
        if args.prepare_only:
            report["status"] = "prepared"
        else:
            run_measurements(catalog, args, report)
            report["status"] = "passed"
            report["final_fixture_proof"] = catalog.verify(page_size=args.page_size, progress=progress)
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_report(args.output, report)
        if not args.prepare_only and not args.keep_fixture:
            shutil.rmtree(catalog.root)
            report["retained_fixture"] = None
        else:
            report["retained_fixture"] = str(catalog.root)
        write_report(args.output, report)
    except (AssertionError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        report.update(status="failed", error=str(error))
        write_report(args.output, report)
        progress(f"Benchmark failed; retained fixture {catalog.root if catalog else None}: {error}")
        return 1
    print(f"Report: {args.output}; status: {report['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
