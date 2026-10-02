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
5,000 IDs exactly once, and the ownership/trust/Git checks run again. Every yielded
page is counted. Page lengths and continuation presence must match the remaining
owned count, and observed page counts must match the requested page size.

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
Reports also record the source commit, clean product tree identities, and SHA-256
fingerprints of the actual imported benchmark/support files, including any benchmark
edits that have not yet been committed.

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

This refreshed run used the corrected pagination/discovery product source from
`2e25ab976588f8d1be6c5c2a80831ce421697e0b`, integrated into measured checkout
`1b3d8fffee1b390b87bee5991a4e35a3c44fa4c0`. Its clean `src/skillager` Git tree was
`30fec6188fcb0241208716c0002f7e0d3f383c40`; the linter tree was
`52c4f3f2d0d62360e36d8631d0adb16efdad61ab`. The corrected benchmark script's
SHA-256 was `fe0882c0fd78b0904c42bc5f8c91dbde296ecf1b7cbbe7258708518eba253f9f`
and the fixture helper's was
`37103199f00180c42fb4cea47af0638dd344bcb72703234e6287603a5a93860c`.
The JSON report records all five imported harness/support fingerprints. These
benchmark edits were uncommitted during measurement; their bytes are preserved in
the resulting benchmark change. A fresh fixture was created for this source rather
than changing the earlier fixture's recorded identity.

The prepared library had 5,000 physical owned `SKILL.md` files (62,044,055 bytes),
all accepted and Git-tracked, with 43 actual Git commits and clean Git state. Public
metadata proved library ownership and matching registered identity for authored and
synchronized skills. Initial history contained two authored probe versions and one
synchronized version. Retained registered source originals numbered 4,999 and totaled
62,043,859 skill-file bytes. Their deterministic fingerprint was
`a93d644ec9939a0e1c95e61c93c76a352f3fce051924e7f60a6785987c48e441`.

Construction took 393.765 seconds under shared load. Explicit deadline-limited sync
reached 3,072 then 4,480 owned copies. Public metadata refreshes took 14.666 and
21.764 seconds respectively before serial retries. The final public sync copied
the remaining 519 with complete coverage and no conflicts or failures. The final
measurement launch spent another 1.413 seconds validating construction/source
checkpoints, so its accumulated construction field is 395.177
seconds. Preparation and exhaustive verification are separate from command timings.
The recorded 5,000-skill run used prepare/resume, rather than claiming a fresh single
invocation; the default command above executes the complete construction/measurement
protocol, also exercised end-to-end with a small real-CLI fixture in the test suite.

| Command | Cold seconds | Warm median seconds | Maximum stdout bytes | Maximum CLI RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| Whole-library status | 0.381 | 0.367 | 1,216 | 67.7 |
| Single-skill status | 1.829 | 1.864 | 2,380 | 180.7 |
| List first page, 100 rows | 2.202 | 2.200 | 45,025 | 182.4 |
| List continuation, 100 rows | 2.269 | 2.199 | 45,004 | 180.4 |
| Search first page, 100 rows | 11.309 | 7.002 | 54,316 | 260.6 |
| Search continuation, 100 rows | 11.052 | 6.217 | 54,282 | 256.2 |
| Show one, metadata | 3.282 | 3.008 | 1,311 | 224.0 |
| Accept preview | 1.761 | 1.749 | 1,281 | 181.7 |
| History, two versions | 2.227 | 2.230 | 1,470 | 180.6 |
| Diff stat, two versions | 2.013 | 1.989 | 1,382 | 181.2 |
| Diff content, two versions | 2.149 | 2.004 | 1,509 | 181.6 |
| Sync, no changes | 7.101 | 7.017 | 2,410,236 | 265.7 |
| Accept changed, confirmed | 69.781 | 71.804 | 1,664 | 232.6 |
| Accept unchanged, confirmed | 33.503 | 29.797 | 1,479 | 231.5 |

Complete list and search traversals each returned exactly 5,000 matching unique IDs
across 50 actually observed pages, with every page length and continuation checked
against remaining inventory. They took 418.000 seconds after the quiet timing window
ended, under shared load; this is a correctness-check duration, not an isolated
performance measurement. Final physical/accepted/Git-tracked counts remained 5,000,
Git was clean, and Git history contained 47 commits and 6 authored probe versions
after the four changed samples. The synchronized probe still had one version. The
complete refreshed JSON report is retained outside Git at
`/var/tmp/skillager-64-revised-full-results.json`; its SHA-256 is
`1941f61c7d622540109f577e6ab07c9efa68c608ae4c0b180783c1a07249470f`.

The earlier run on product source `5bbfb536eb69ec494ff8dfd3a48ba8efc2c12977`
is preserved as historical evidence at `/var/tmp/skillager-64-full-results.json`;
its SHA-256 remains
`5631372dff95331bb3885bc309571ab5b28b72bf3bf7a068fbdbcd5334dbc801`.
It verified 5,000 matching unique IDs, but its 50-page fields were calculated from
row counts rather than observed, so they are not an observed-page proof. Its original
56 timing samples and 488.865-second construction record remain unchanged. In that
run, warm search first pages took 3.853 seconds and changed acceptance took 66.379
seconds. Those historical timings describe the earlier source; the refreshed table
above describes the corrected source, whose complete discovery observation adds work.

### Interactive implications

Whole-library status is compact and comparatively quick. Other metadata reads take
1.7–3.3 seconds, and 100-row list pages take about 2.2 seconds. Warm 100-row search
pages take about 6.2–7.0 seconds; building their cache takes over 11 seconds. These reads
need visible asynchronous loading and bounded pages. Avoid fetching all 50 pages on
each interaction or invoking search on every keystroke. The measured page outputs
are approximately 44–53 KiB; they are bounded, unlike a full inventory response.

Confirmed acceptance is too slow for a short synchronous interaction: changed
acceptance takes roughly 72 seconds warm, with one observed sample taking 80.702
seconds, while even unchanged confirmation takes roughly 30 seconds. Its preview
is about 1.75 seconds. Clients need separate progress
and timeout handling for confirmation; a preview's latency cannot size that operation.
The changed samples each produced a distinct accepted hash and Git commit; unchanged
samples each retained the hash and produced no commit.

No-change sync takes about 7.02 seconds warm and returns 2,410,236 bytes (2.30 MiB),
mostly per-origin items. It is both too slow and too large for frequent automatic
refresh. Clients should invoke it explicitly and budget its transport/response size
separately from metadata pages. Peak measured CLI RSS ranges from 68 to 266 MiB;
this does not include aggregate native Git memory.

No machine-dependent latency threshold determines benchmark success. Slow commands
are findings, not reasons to discard samples. This benchmark makes no performance
fixes and does not measure hvir rendering, IPC, typing responsiveness, or SSH delivery.
