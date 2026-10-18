# Exact Current Skill Review

Human review clients can request a complete metadata manifest without Git history
or access to pending bodies through agent commands:

```bash
skillager library accept lib/<name> --review-manifest --json
```

This opt-in preview retains the `skillager.library-accept.v1` envelope and adds
`review_manifest` with schema `skillager.library-review-manifest.v1`. It works for
first acceptance, no-Git libraries, and later edits. Ordinary acceptance previews
keep their existing output. `--review-manifest` requires `--json`.

The manifest contains:

| Field | Meaning |
| --- | --- |
| `library_id`, `library_root` | Exact registered library UUID and canonical absolute root. |
| `skill_id`, `skill_root` | Canonical owned ID and absolute directory for confined human reads. |
| `working_hash` | Skillager's existing content identity, equal to `skill.working_hash`. |
| `files` | Complete ordered eligible entries, each with relative `path`, byte `size`, raw-byte SHA-256 `sha256`, and boolean `executable`. |
| `file_count`, `total_bytes` | Complete eligible file count and sum of byte sizes. |
| `limits` | Finite admission limits described below. |
| `confirmation_token` | Opaque token bound to this complete manifest, exact library identity, working hash, relevant provenance and override/reason. |

`executable` means any owner/group/other execute bit is set. This is the existing
normalized executable state in approval identity; other permission bits and file
timestamps are not independently accepted. File SHA-256 hashes cover raw bytes,
including binary files. The manifest contains no file contents. Consumers must not
reproduce Skillager's tree hash, scanner, filtering or approval policy.

Use this sequence for an explicitly authorized human review:

1. Obtain the complete preview. Verify manifest identity, root, working hash and
   token association. When a confirmation command is available, its token equals
   `review_manifest.confirmation_token` and it retains `--review-manifest`.
2. Make every listed file available for inspection using separately authorized,
   confined human file tools under `skill_root`, with relative paths and no symlink
   traversal. Verify the complete byte size and SHA-256 of files presented; show
   executable state alongside them. Binary files remain part of the tree. Clearly
   route unsupported formats to suitable file tools instead of omitting them or
   implying they were inspected. Skillager does not require a per-file opening or
   acknowledgement before one explicit decision about the complete version.
3. Obtain a fresh identical preview before the decision. Compare the entire manifest,
   working hash, identity and opaque token. Any difference requires fresh review;
   unchanged file-count or timestamps alone are insufficient.
4. After human approval of that exact preview, execute its `next_command_argv`
   unchanged. Missing, changed or added eligible files, changed executable state,
   changed identity/provenance or altered override/reason invalidate the earlier
   token. A stale refusal requires a new preview and decision, never an automatic
   retry. The token is association evidence, not permission to read agent bodies.

Scanner/lint gates and audited override requirements still apply. An override
preview includes the same complete manifest but binds the real override reason to
its token. Without required override arguments, a review preview discloses safe
gate metadata and the manifest but offers no confirmation command. Git-backed
acceptance keeps commit-before-trust and existing repository protections.

## Import An External Original

Use the same bounded tree evidence before one explicit Save and accept decision:

```bash
skillager import <external-id> --source-file <canonical-absolute-SKILL.md> --review-manifest --json
```

The existing `skillager.import.v1` preview adds `review_manifest` with schema
`skillager.import-review-manifest.v1`. `--source-file` selects an exact entrypoint
among the existing resolver's candidates for that ID; it never imports an
arbitrary path. Use the ID and `skill_file` from the selected public project
inventory occurrence. The path must be canonical, absolute, existing, and at most
4096 encoded bytes. Without this selector an ambiguous ID continues to fail.
The resolver's selected ID may differ from a shared claimed ID.

| Field | Meaning |
| --- | --- |
| `source_id`, `source_root`, `source_file` | Resolved external ID, canonical source directory and exact instruction file; also exposed by preview `source.id`, `source.path` and `source.file`. |
| `source_hash`, `working_hash` | The same existing content identity as preview `source_hash`. |
| `source_provenance` | The displayed source `type`, `collection`, `package`, `version`, and `editable` metadata, bound even when identical bytes retain a stable approval key. |
| `destination_id`, `destination_path` | The selected library ID and canonical copy destination. |
| `library` | Library `root`, `git_mode`, `status` (`ready` or `will-initialize`), and exact `library_id` when one already exists; otherwise the ID is null. |
| `files`, `file_count`, `total_bytes`, `limits` | The same complete eligible entries and finite bounds as library review. |
| `confirmation_token` | The existing import confirmation bound to this manifest, source provenance, destination/library binding and any audited override. |

Follow the review sequence above using `source_root`. Returned `next_command_argv`
retains `--review-manifest` and the exact `--source-file`, even when the initial
unambiguous preview omitted the selector. Run that command unchanged after the
user's decision. Confirmation revalidates source identity, every eligible file and
executable state, destination and registered library; changed bytes, provenance,
selection, arguments or library binding require a fresh preview and decision.
First-library initialization is disclosed in the preview and happens only during
confirmation. Its expected registration, UUID and Git mode are checked under the
initialization lock. Only an identity actually created by that confirmation can
fill a missing UUID; a pre-existing reviewed identity must remain identical. The
copy rechecks UUID, root and Git mode under the import lock. If the library changes
after setup has completed, the refusal says so: inspect the resulting setup before
obtaining a new preview instead of assuming it was rolled back.

Confirmed import preserves its existing behavior: copy the eligible tree, accept
that exact version, and leave the original unchanged. There is no extra pending
approval step on success. Git remains optional; Git-backed import commits before
trust, and a commit/trust failure still reports the copied but pending result with
the existing repair path. Blocked sources and scanner/lint overrides retain their
existing gates. Neither the manifest nor import acceptance grants ordinary body
access to an unapproved original.

Ordinary import previews and confirmations keep their existing shape and behavior
when neither new option is supplied. `--source-file` can also select an occurrence
without requesting a manifest; it still binds that occurrence to confirmation.

## Eligibility And Limits

The existing canonical tree owner selects regular files, excluding symlinks,
signature/card release evidence (`skill.oms.sig` and recognized root-level skill
card names), `.git`, `__pycache__`, `.pytest_cache`, `skillager.materialized.yaml`,
compiled Python files, and transient `*.tmp`, `*.swp` and `*~` paths. These bytes
are not part of approval identity. Existing authored acceptance refuses present
symlinks or excluded files; it does not silently accept a filtered import/export
projection. Preserve or move those files out of the skill tree before previewing.
Import review describes its existing filtered copy projection: excluded files and
symlinks remain at the original, and are neither copied nor accepted. It requires
the regular canonical `SKILL.md` already required by import. Both explicit review
paths refuse an unreadable eligible directory or file; excluded directories do not
become required import evidence. Ordinary discovery/hash/import traversal is
unchanged.

Limits are 512 eligible files, 2 MiB per file, 32 MiB combined eligible bytes, and
256 KiB for the entire serialized JSON preview response, including gate metadata
and the confirmation command. `limits` exposes these as `files`, `file_bytes`,
`tree_bytes`, and `response_bytes`. Exceeding any limit exits **2** with the
applicable `skillager.library-accept.v1` or `skillager.import.v1` refusal envelope and
`error.code: "review_limit_exceeded"`. No partial manifest or usable confirmation
command is returned. Reduce or split the instruction tree, shorten excessive
paths/metadata where appropriate, then preview and review the resulting tree.

Capture inconsistency or unreadable eligible content exits **2** with
`error.code: "review_changed"`, without a
manifest or confirmation command. Canonical-tree, identity, Git and token refusals
also exit **2** through their existing CLI error path. The limits bound admission,
captured file bytes and disclosed response; canonical enumeration and existing
post-acceptance catalog refresh are not a hard total work/time or directory-walk
cap.

For unreadable content, restore read access to the required tree before retrying.
Never retry a previously confirmed command automatically after repairing or
changing the source; obtain a new complete preview and decision.

Preview uses an ephemeral private bounded capture with the existing candidate,
scanner, lint and hash owners, followed by bounded live comparison. Confirmation
rechecks the binding and current tree under existing mutation locks before trust.
Preview changes no library, approval, exposure or catalog state and silently
initializes nothing. No persistent snapshot or review-receipt subsystem is added.
This remains cooperative local review, not a same-user sandbox or atomic snapshot
against arbitrary external writers.
