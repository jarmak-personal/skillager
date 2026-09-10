# Synthetic search benchmark

Generate a repeatable local catalog and exercise Skillager through its public CLI.
No skills are downloaded, no skill instructions are executed, and no private catalog
is needed. This is an opt-in benchmark, separate from the normal test suite.

From a development checkout with dependencies already installed:

```bash
uv run python scripts/benchmark_search.py --output /tmp/skillager-search-benchmark.json
```

The default workload has **5,000 approved skills plus two pending drafts**. Four
approved skills belong to a temporary personal library; the other 4,996 belong to
two temporary external collections. All files are generated from small local prose
templates using a fixed seed. Most files vary from roughly 1,000 to 32,000 characters;
one library probe exceeds 50,000 characters. Content includes overlapping domain
vocabulary, Markdown tables, Unicode, and deliberately placed search terms.

The generator registers the library and collections through the CLI. Library
acceptance uses the CLI's exact preview/confirmation flow; generated external skills
go through `setup --accept-low`. It does not forge trust records or populate a fake
search index. Home, project state, catalog state, cache, XDG paths, and native agent
configuration roots are isolated under the temporary directory. Library Git history
is disabled for this search workload. Generation and approval are timed separately
from search.

For a size comparison, more repeated samples, and retained fixtures:

```bash
uv run python scripts/benchmark_search.py \
  --sizes 100 1000 5000 10000 \
  --repeats 10 \
  --keep-fixture \
  --output /tmp/skillager-search-scaling.json
```

`--seed` controls generated content; `--timeout` bounds each CLI invocation (600
seconds by default). The report records the seed, content fingerprint, file bytes,
catalog size, Python/SQLite/platform versions, and retained fixture paths. Each
retained fixture also contains `fixture-manifest.json` with the initial file hashes.
The final trust check deliberately edits and accepts one library skill, so that
retained working file differs from the initial manifest. Ordinary runs remove their
fixtures on completion or failure. Reports and fixtures should stay outside Git.

## What it verifies

- The searchable inventory contains exactly the generated approved IDs.
- Known title, description, and body terms produce the expected IDs and match reasons.
- A title match outranks a description match, which outranks a body match.
- A higher-ranked external match wins the unrestricted query. A curated tag containing
  the library probes returns its own match with `--limit 1`, exercising filtering
  before the result limit. An explicit `--scope library` query independently
  verifies the same pre-limit restriction using personal-library authority.
- A broad query respects its result limit; repeated searches return identical metadata.
- Absent terms and pending-body terms return no matches.
- JSON results omit bodies, including a sentinel embedded in every generated body.
- A library edit of the same byte length with its original modification time restored
  invalidates approval: neither old nor new body terms match until exact-hash
  acceptance. After acceptance, only the new term matches.

The normal suite runs a 24-approved-skill version of these behavioral checks:

```bash
uv run python -m unittest tests.behavior.test_search_catalog -v
```

## How to read timings

The benchmark measures title, body, broad, and library-only body queries. Every sample starts a **new
CLI subprocess**, including interpreter startup, inventory discovery, trust checks,
search, and JSON serialization. It reports elapsed wall time, output byte counts,
and per-child peak RSS where the standard-library `resource` module is available.
The small measurement launcher is included in those totals. It does not reuse a
long-lived in-process search engine or the parent process's cumulative memory peak.

Only the first body query is the first search after fixture setup. Setup has already
read the files, so this is **not an OS-cold disk benchmark**. Repeated queries reuse
whatever filesystem/application caches the current CLI supports, but always use a
fresh interpreter. The report includes every sample, repeat medians, and nearest-rank
p95 values. Three repeats are a smoke measurement; use more samples for tail latency.

## SQLite comparison

Measured on the same macOS/arm64 machine with Python 3.13.5 and SQLite 3.53.3,
using 5,000 approved skills and seed 1729. These are median warm timings from three
fresh CLI subprocesses per query:

| Query | Original JSON / rebuilt FTS | Persistent SQLite |
| --- | ---: | ---: |
| Body term | 55.470 s | 2.305 s |
| Title term | 76.296 s | 2.308 s |
| Broad query, limit 7 | 48.983 s | 2.381 s |

The final verification ran after the quality checks finished. It reused the generated
fixture after its edit/reacceptance probe, refreshed library metadata, and cleared
only its search cache before measuring. Its first query, including FTS and scoring
token construction, took 7.675 seconds. The original first body query took 55.539
seconds. OS caches were not flushed. The warm improvement is roughly 21–33×;
startup and exact live-source validation remain part of every sample.
Peak child RSS across measured queries fell from 363.7 MiB to 95.2 MiB. A final
profile attributes most remaining latency to inventory construction, native-target
classification, and exact source-tree checks. Search/index lookup itself is now a
small portion of the command.

The original complete run and a fresh complete SQLite run passed all twelve
correctness checks, including the edit with unchanged size and restored mtime. The
fresh SQLite run also reduced catalog generation/registration/approval time from
308.6 to 50.1 seconds. Its timing samples overlapped quality checks, so the table
uses the subsequent isolated timing verification. Reports remain outside the repo:

- `/tmp/skillager-search-benchmark.json`: original complete run.
- `/tmp/skillager-search-sqlite-final.json`: complete SQLite run and trust checks.
- `/tmp/skillager-sqlite-final-verification.json`: final isolated timing samples.

This workload has four approved owned skills and 4,996 approved external skills.
It measures the mixed-source CLI inventory, rather than 5,000 individually accepted
owned skills, Git-history performance, or hvir UI responsiveness. A focused test
separately verifies owned-library metadata cache reuse and content/provenance
invalidation. The measurements are development evidence, not a latency guarantee.
Run without competing benchmarks or test suites when comparing implementations.

### Inventory path follow-up

A follow-up removes repeated resolution of approval-directory paths and prepares
native-directory prefixes once per inventory pass. Native classification still
resolves each candidate path and checks complete path components, including symlink
targets. Approval snapshots remain scoped to one inventory operation and are
invalidated by decision writes.

On the retained 5,000-skill fixture, three repeated fresh-process samples gave:

| Query | Initial SQLite implementation | With inventory path reuse |
| --- | ---: | ---: |
| Body term | 2.305 s | 1.437 s |
| Title term | 2.308 s | 1.491 s |
| Broad query | 2.381 s | 1.523 s |

The follow-up reused the search cache and passed all known-query and inventory
checks. `/tmp/skillager-inventory-optimization.json` holds these samples; its first
query took 2.894 seconds, so the improvement should be read as a warm-cache result.
Profiling reduced native-target classification from about 2.18 to 0.21 seconds;
profiled timings include instrumentation overhead and differ from the wall timings
above. Exact validation of all 5,002 source trees now dominates the profile.

### Ranked candidate verification

Search now discovers collection roots and reconciles approval records with cached
metadata, then queries indexed prose and verifies ranked results before filling the
limit. It scans newly discovered sources and sources with changed accepted hashes.
Project/package discovery remains live; exposed sources, variants, and identity
collisions receive additional verification to preserve ranking and ambiguity rules.
An unchanged mtime or Git HEAD does not establish source approval.

A fresh complete 5,000-approved-skill run with five repeated subprocess samples
per query produced these warm medians:

| Query | Verify every source (path reuse) | Verify ranked candidates |
| --- | ---: | ---: |
| Body term | 1.437 s | 0.749 s |
| Title term | 1.491 s | 0.722 s |
| Broad query, limit 7 | 1.523 s | 0.743 s |
| Library-only body term | — | 0.157 s |

The first query built the search index in **7.363 seconds**; OS caches were not
flushed. Warm per-query p95 values were 0.814, 0.733, 0.771, and 0.160 seconds,
respectively. These are five-sample smoke measurements, not production tail-latency
estimates. Peak child RSS was 96.8 MiB, and the largest measured output was 3,602
bytes (seven broad-query rows). All fourteen correctness checks passed, including
library scope before limit and exact-hash edit/reacceptance. See
`/tmp/skillager-search-candidates-final.json` for the complete run. A retained-fixture
comparison in `/tmp/skillager-candidate-verification.json` measured workspace medians
of 0.612–0.721 seconds before this fresh run.
Its warm single-result body-query profile performed one source-tree hash, down
from 5,002 in the previous inventory profile. Remaining work is primarily live
root discovery and metadata/approval handling.

The library-only result reflects **four approved owned skills**, not 5,000 owned
skills. Its lower latency demonstrates skipping the external/workspace inventory,
not large-library Git or authoring performance. Library results report exposure as
`unknown`, because workspace status is not checked in that scope.

Additional behavioral regressions cover supporting-file additions/removals and
executable-mode edits, refilling a limited query after stale matches, immediate
searchability of accepted external edits, newly discovered sources, preferred
variants, router dependencies outside a search tag, workspace identity collisions,
and library authority independent of damaged project state. Cold-cache/fallback
body reads remain hash-verified. See `tests/behavior/test_search_candidates.py`.

There is no machine-dependent pass/fail latency threshold. A successful run means
the behavioral checks passed; the timings tell us whether search needs optimization.
Missing behavior and failed commands cause a nonzero exit and a failed JSON report.
Each completed timing sample is checkpointed to the report under `active_run` until
that catalog finishes; `status: running` indicates an incomplete run. Progress on
stderr includes each sample's elapsed time and output size. On an ordinary command
failure, completed samples remain available in the failed report.

## Coverage limits

The current search CLI supports personal-library and tag scope before the limit,
but has no paging/total contract. This workload also does not measure
5,000 individually accepted owned skills, library Git history, or package discovery
at scale. Its large collections exercise approved-body search in a mixed catalog.

The benchmark separately records whether a term placed beyond character 50,000 of
`SKILL.md` is found. The current implementation searches only the first 50,000
characters; this observation documents the coverage boundary without requiring
future versions to preserve truncation.

Synthetic known-answer queries validate scale and specific relevance properties.
They cannot establish relevance for a private workplace's terminology. hvir's UI
rendering, IPC, typing responsiveness, and SSH delivery require separate integration
checks.
