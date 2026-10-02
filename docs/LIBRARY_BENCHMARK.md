# Git-backed owned-library benchmark

This opt-in benchmark constructs 5,000 owned, accepted personal-library skills and
measures the public CLI needed by a library browser. It complements the mixed-source
[search benchmark](SEARCH_BENCHMARK.md), whose large catalog has only four owned skills.
No generated skill instructions execute, no files are downloaded, and no existing
user library changes.

From a development checkout with dependencies installed, one command generates the
fixed-seed fixture, verifies its ownership and trust, measures all commands, and
writes the report:

```bash
uv run --python 3.13 python scripts/benchmark_library.py \
  --seed 1729 --output /tmp/skillager-library-benchmark.json
```

Run without other benchmarks or test suites. Defaults are 5,000 owned skills,
100 rows per page, three warm repeats, and a 600-second per-command timeout.
`--keep-fixture` retains the completed fixture; otherwise successful runs remove it.
Reports and fixtures belong outside Git.

## Construction and verification

The generator registers 4,999 deterministic source skills as a collection. Public
`setup --collection benchmark-origin --accept-low --no-packages --summary-json`
approves those low-risk sources and synchronizes verified copies into the registered
personal library. Public `library sync --approved --json` completes deadline-limited
copying. After safe time-limit partial results, the script refreshes library metadata
through `collection refresh lib --json` before retrying. Failed, conflicting,
uncertain, unexpected partial, or repeated zero-progress results stop the run and
retain the fixture; they never become successful measurements.

The final skill is an authored probe created with `library new`, with two initial
versions accepted using the real preview and its exact confirmation arguments.
The 4,999 source originals remain registered so the no-change sync measurement has
real origins to check. The fixture therefore has 9,999 source/owned skill files in total, below
the existing 10,000-source admission cap; sync reports 4,999 discovered/approved
external origins after excluding its owned copies. Generated source files vary
from approximately 1,000 to 32,000 characters and contain a shared metadata search
term. The seed fixes their bytes; temporary paths and library UUIDs vary between runs.

Every operation uses a benchmark-owned temporary root, isolated current directory,
HOME, project/catalog state, caches, XDG directories, native agent roots, and Git
configuration. The CLI uses its fallback local commit identity and no remote.
The script writes generated skill files and its own checkpoints, but never writes
approval databases or search indexes. Git proof uses read-only Git commands.

Before measuring, public library status, list pages, metadata show, and history must
prove exactly 5,000 actual owned files, all accepted, with matching registered library
identity and Git history. Physical files and Git-tracked `SKILL.md` counts must agree.
After measurements, complete list and search traversals must each return the same
5,000 IDs exactly once, and the ownership/trust/Git checks run again.

To prepare without measurements, then resume in a quiet window:

```bash
uv run --python 3.13 python scripts/benchmark_library.py \
  --prepare-only --output /tmp/skillager-library-prepared.json
uv run --python 3.13 python scripts/benchmark_library.py \
  --resume /path/from/prepared-report --keep-fixture \
  --output /tmp/skillager-library-benchmark.json
```

Construction and resume require clean tracked/untracked product source trees, so
the recorded committed identity describes the imported CLI and linter source.
Resume accepts only a matching benchmark marker, root, seed, size, and product source
tree. It checks every generated source against deterministic expected bytes and
refuses changed or unknown sources. Construction checkpoints retain progress through
public operations. Interrupted confirmed probe accepts recover only a journaled,
known benchmark revision. A failed report retains completed sample evidence and the
fixture for inspection; an incomplete report has `status: running`. Preparation has
`status: prepared`; only a complete measured and verified run has `status: passed`.

## Measurement definitions

Each sample starts a fresh public CLI interpreter and includes startup, discovery,
verification, Git work, and JSON serialization. Reports contain wall seconds, exit
code, stdout/stderr byte counts, and peak CLI RSS. Sync also records its response
status. Completed samples are checkpointed before success assertions.

**Cold** means the first measured invocation of each command after construction and
fixture verification. Search's private benchmark cache is cleared before each search
case, including the continuation-page case. Catalog and approval state remain intact;
setup and verification already read files. OS caches are never flushed, so these are
not disk-cold measurements. **Warm** means repeated fresh interpreters reuse persisted
caches and OS state. Three repeats are development smoke evidence, not tail-latency
estimates or production guarantees.

Peak RSS uses the individual CLI interpreter's `resource.getrusage(RUSAGE_SELF)`,
including the small measurement launcher. It excludes native Git subprocess memory
and aggregate process-tree peaks; unavailable RSS is reported as null. The same
launcher and definition serve the existing search benchmark.

Acceptance preview is measured separately. Confirmed acceptance excludes the preceding
preview and file edit. Changed samples accept successive comparable probe revisions
and must record a new accepted hash and non-null Git commit; unchanged samples must
retain the hash and report no new commit. These two cases must not be conflated.
History and both diff forms use the two initial versions. Paging and read samples
precede changed acceptance so measured cursors remain valid.

## Recorded results

Measured on 2026-10-02 UTC with Python 3.13.5, Skillager 0.9.3, and
macOS 15.7.3 on arm64, using seed 1729 and three warm repeats. Timing ran in a
coordinated quiet interval with other local quality checks/builds held. All 56 timed
commands exited zero; stderr was empty. These are one-machine development results.

The prepared library had 5,000 physical owned `SKILL.md` files (62,044,055 bytes),
all accepted and Git-tracked, with 43 actual Git commits and clean Git state. Public
metadata proved library ownership and matching registered identity for authored and
synchronized skills. Initial history contained two authored probe versions and one
synchronized version. Retained registered source originals numbered 4,999 and totaled
62,043,859 skill-file bytes. Their deterministic fingerprint was
`a93d644ec9939a0e1c95e61c93c76a352f3fce051924e7f60a6785987c48e441`.

Construction took 488.865 seconds, including the initial stopped attempt and resumed
work. The initial deadline-limited sync stalled after 4,352 copies; a public metadata
refresh took 24.405 seconds, then public sync copied the remaining 647 with complete
coverage. The final measurement launch spent another 1.247 seconds validating
construction/source checkpoints, so its accumulated construction field is 490.112
seconds. Preparation and exhaustive verification are separate from command timings.
The recorded 5,000-skill run used prepare/resume, rather than claiming a fresh single
invocation; the default command above executes the complete construction/measurement
protocol, also exercised end-to-end with a small real-CLI fixture in the test suite.

| Command | Cold seconds | Warm median seconds | Maximum stdout bytes | Maximum CLI RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| Whole-library status | 0.347 | 0.348 | 1,216 | 67.2 |
| Single-skill status | 1.725 | 1.724 | 2,380 | 180.6 |
| List first page, 100 rows | 1.953 | 1.962 | 44,995 | 181.6 |
| List continuation, 100 rows | 1.960 | 1.960 | 44,974 | 178.8 |
| Search first page, 100 rows | 8.258 | 3.853 | 54,316 | 198.1 |
| Search continuation, 100 rows | 8.338 | 3.846 | 54,282 | 193.9 |
| Show one, metadata | 2.890 | 2.830 | 1,311 | 223.8 |
| Accept preview | 1.676 | 1.662 | 1,281 | 179.2 |
| History, two versions | 1.830 | 1.833 | 1,470 | 179.5 |
| Diff stat, two versions | 1.900 | 1.880 | 1,382 | 179.7 |
| Diff content, two versions | 1.887 | 1.882 | 1,509 | 179.8 |
| Sync, no changes | 6.595 | 6.577 | 2,410,236 | 263.0 |
| Accept changed, confirmed | 69.088 | 66.379 | 1,664 | 240.3 |
| Accept unchanged, confirmed | 27.379 | 27.033 | 1,479 | 238.5 |

Complete list and search traversals each returned exactly 5,000 matching unique IDs
across 50 pages. They took 328.246 seconds after the quiet timing window ended,
under shared load; this is a correctness-check duration, not an isolated performance
measurement. Final physical/accepted/Git-tracked counts remained 5,000, Git was clean,
and Git history contained 47 commits and 6 authored probe versions after the four
changed samples. The synchronized probe still had one version. The complete JSON
report is retained outside Git at `/var/tmp/skillager-64-full-results.json` for this
run; its SHA-256 is
`5631372dff95331bb3885bc309571ab5b28b72bf3bf7a068fbdbcd5334dbc801`.

### Interactive implications

Whole-library status is compact and comparatively quick. Other metadata reads take
1.7–2.9 seconds, and 100-row list pages take about 2 seconds. Warm 100-row search
pages take about 3.85 seconds; building their cache takes over 8 seconds. These reads
need visible asynchronous loading and bounded pages. Avoid fetching all 50 pages on
each interaction or invoking search on every keystroke. The measured page outputs
are approximately 45–54 KiB; they are bounded, unlike a full inventory response.

Confirmed acceptance is too slow for a short synchronous interaction: changed
acceptance takes roughly 66 seconds warm, while even unchanged confirmation takes
roughly 27 seconds. Its preview is about 1.66 seconds. Clients need separate progress
and timeout handling for confirmation; a preview's latency cannot size that operation.
The changed samples each produced a distinct accepted hash and Git commit; unchanged
samples each retained the hash and produced no commit.

No-change sync takes about 6.58 seconds warm and returns 2,410,236 bytes (2.30 MiB),
mostly per-origin items. It is both too slow and too large for frequent automatic
refresh. Clients should invoke it explicitly and budget its transport/response size
separately from metadata pages. Peak measured CLI RSS ranges from 67 to 263 MiB;
this does not include aggregate native Git memory.

No machine-dependent latency threshold determines benchmark success. Slow commands
are findings, not reasons to discard samples. This benchmark makes no performance
fixes and does not measure hvir rendering, IPC, typing responsiveness, or SSH delivery.
