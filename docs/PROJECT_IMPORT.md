# Save project originals in one decision

`skillager import-project` saves and accepts explicitly selected project skill
versions in the personal library. It leaves originals, their approvals and all
exposures unchanged. There is no automatic import, replacement, rename, update,
lint override or retry. Files may be inspected before the one Save N decision;
individual file acknowledgment is not required.

The command requires `--json`. Its project is the canonical current directory,
matching `list --scope project`: native project discovery roots, including
`.skills`, `.agents/skills` and the supported Codex/Claude roots. It covers every
occurrence in those roots independently of display pages or search. It does not
expand into child repositories, environments, package-only skills, global skills
or SSH hosts. Editable-package provenance for an already present native occurrence
is retained. Existing single import, approved-source sync and Save-and-use remain
separate supported operations.

## Inventory

```bash
skillager import-project --json --limit 100
```

`skillager.project-import-inventory.v1` contains:

| Field | Meaning |
| --- | --- |
| `project_root`, `scope` | Exact project and fixed scope `project`. |
| `library` | Current/prospective `root`, `library_id` (null before first initialization), `git_mode`, and `status` (`ready` or `will-initialize`). |
| `observation` | `status` (`complete` or `partial`), bounded discovery `errors` with fixed codes and paths, and total `error_count`. |
| `summary` | Counts over the entire project: `total`, `eligible`, `already_saved`, `managed`, `duplicate`, `conflict`, `blocked`. |
| `items` | One page of occurrence metadata described below. |
| `default_selection` | All eligible occurrence IDs in sorted order, `[]` when none, or null if the complete eligible set exceeds 32. Never the first page or first 32. |
| `next_cursor` | Opaque continuation, null at the end. Reuse the same limit; changed inventory gives the existing stale-cursor exit 15. |
| `limits` | Published bounds for selection, review, output and status requests. |

Each item has `occurrence_id`, discovery `id`, declared `name`/`description`, exact
`skill_file`, `source_hash`, `source_identity`, `eligibility`, `reason_code`,
`destination_id`, `duplicate_of`, and `canonical`. Managed copies have null source
hash/identity/destination; canonical facts are null unless a destination was
observed. The occurrence ID is the same `project:` plus SHA-256 of the canonical
instruction-file path used by project listing, independent of discovery aliases.
Metadata has no instruction body or scanner/linter source excerpts. Observation
writes no library, catalog, approval or browse-cache state, and does not hash the
whole personal library. Only project originals and directly related canonical
destinations are inspected.

`eligible` means a new collision-free destination. Names come from the physical
skill-directory slug. Two same-slug originals collapse only when complete eligible-tree
manifest, exact approval hash and source provenance agree; the stable path winner is
selected and the other is `duplicate`. Different slugs remain distinct, even if
bytes match. Conflicting same-slug versions/provenance are not eligible. Native
Codex/Claude location alone does not make otherwise identical project provenance
incompatible. A currently accepted, exact import-source, import-preservation or approved-sync
relation is `already_saved`; an occupied name alone never proves preservation.
Pending, customized, missing or ambiguous canonical relations are conflicts.
Managed copies are never originals. Blocked, lint-blocked, high-risk, unreadable,
oversized source trees cannot enter a batch. Eligible files follow the existing
single-import filter; excluded extras and symlinks remain untouched in the
original. Their presence does not grant authority to replace or delete it.

Partial discovery remains visible. An explicit selection of known eligible
versions may proceed, but clients must not describe incomplete coverage as saving
all project skills. Pagination does not change the summary or default selection.

## Review and save

Use the explicit IDs selected from inventory:

```bash
skillager import-project --select '["project:<64-hex-occurrence>"]' --review-manifest --json
```

`skillager.project-import.v1`, `status: preview`, returns canonical sorted
`selection`, `project_root`, `observation`, `library` including history availability,
`items`, `confirmation_token`, `next_command_argv`, and `limits`. Each item contains
`occurrence_id`, `source_identity`, source metadata with canonical `file`,
`source_hash`, destination metadata, scanner/lint counts, physical source identities,
and the existing complete `skillager.import-review-manifest.v1` in `review_manifest`.
Its `files` enumerate every eligible relative path, byte size, SHA-256 and executable
mode. Nested manifests have no independent token. `library_state` and
`project_identity` bind the library metadata/history and project path to the review.

The complete batch must fit 32 items, 4096 UTF-8 selection bytes, 512 files,
2 MiB per file, 32 MiB total tree bytes, 256 KiB output, and 8192 bytes of encoded
command arguments. No truncated review is usable. Refusal returns only its schema,
`status: refused`, `error: {code,message}` and `limits`, with exit 2 and no token.
Select fewer skills and obtain a fresh review if a complete batch exceeds capacity.
Empty or duplicate selection IDs are invalid. No private review-session state is
created. A changed selection needs a fresh decision.

Execute the returned argv after the user approves Save N. It adds `--yes` and the
single `--confirmation-token`. Before any initialization or copy, all selected
sources, destinations, source decisions, project identity, library binding and
history are rechecked under the existing library mutation and selected-skill locks.
The first accepted import may initialize the default Git library. Existing no-Git
libraries work normally; if Git is unavailable, explicitly initialize a no-Git
library first. Imports reuse the existing verified copy, scanner/lint, provenance,
history and acceptance owners. Git commits precede acceptance. Exact `import_source` metadata is stored with canonical acceptance, recording
the original occurrence/provenance and copied eligible hash. This also covers
ordinary `.skills` originals and filtered copies. It grants no native replacement
authority; the existing, stronger `import_preservation` proof remains separate.

A result includes `plan_hash`, exact selection, current/attempted `library`,
`initialization` (null or its status/root/library ID), `items` and `counts`.
Each item carries occurrence ID, source ID/file/hash, destination ID/path,
`status` (`accepted`, `failed`, `skipped`, `uncertain`), `accepted_hash` and
`reason_code`. Overall `completed` exits 0. A failure after an attempted effect
exits 2 with `partial` or `uncertain`. Earlier accepted successes remain accepted;
the attempted item may have pending or accepted effects and is conservatively
uncertain, and later items are skipped. Initialization can also be uncertain.
There is no rollback or automatic retry. A missing response proves no completion.

## Current facts after a missing or partial response

Retain the selection and parallel destination IDs from the reviewed plan:

```bash
skillager import-project --status --select '["project:<64-hex-occurrence>"]' --destinations '["lib/example"]' --json
```

`skillager.project-import-status.v1` returns `project_root`, current `library`,
`observation`, `limits` and selected `items` in occurrence-ID order. Each item has
`occurrence_id`, `destination_id`, current `source` metadata (or null if absent),
and `canonical: {skill_id,path,working_hash,accepted_hash,acceptance}`. Hashes may
be null; acceptance is `accepted`, `pending` or `missing`. Exact destination facts
survive an original disappearing. Noncanonical or aliased canonical targets are
refused, never reported accepted. The supplied occurrence/destination association
comes from the caller's reviewed plan; this command makes no claim that a prior
operation caused the current state. It writes nothing, does not retry, and reads
only the requested canonical trees plus project-native discovery. Selection and
destination arrays together are bounded to 6144 UTF-8 bytes and at most 32 pairs.

Inspect and resolve current facts before requesting another action. Pending copied
versions use ordinary `library accept` review; existing canonical customizations
are preserved. Never infer a completed lost operation from current acceptance or
replay a batch into its occupied destinations.
