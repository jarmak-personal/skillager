# Exact Current Library Review

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
2. Read every listed file using confined human file tools under `skill_root`, with
   relative paths and no symlink traversal. Verify each file's complete byte size
   and SHA-256; show executable state alongside the reviewed material. Binary files
   remain part of the tree. A client unable to fully present or verify a format
   must refuse that review and route the user to suitable file tools/public CLI.
3. Obtain a fresh identical preview after the reads. Compare the entire manifest,
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

## Eligibility And Limits

The existing canonical tree owner selects regular files, excluding symlinks,
signature/card release evidence (`skill.oms.sig` and recognized root-level skill
card names), `.git`, `__pycache__`, `.pytest_cache`, `skillager.materialized.yaml`,
compiled Python files, and transient `*.tmp`, `*.swp` and `*~` paths. These bytes
are not part of approval identity. Existing authored acceptance refuses present
symlinks or excluded files; it does not silently accept a filtered import/export
projection. Preserve or move those files out of the skill tree before previewing.

Limits are 512 eligible files, 2 MiB per file, 32 MiB combined eligible bytes, and
256 KiB for the entire serialized JSON preview response, including gate metadata
and the confirmation command. `limits` exposes these as `files`, `file_bytes`,
`tree_bytes`, and `response_bytes`. Exceeding any limit exits **2** with the
`skillager.library-accept.v1` refusal envelope and
`error.code: "review_limit_exceeded"`. No partial manifest or usable confirmation
command is returned. Reduce or split the instruction tree, shorten excessive
paths/metadata where appropriate, then preview and review the resulting tree.

Capture inconsistency exits **2** with `error.code: "review_changed"`, without a
manifest or confirmation command. Canonical-tree, identity, Git and token refusals
also exit **2** through their existing CLI error path. The limits bound admission,
captured file bytes and disclosed response; canonical enumeration and existing
post-acceptance catalog refresh are not a hard total work/time or directory-walk
cap.

Preview uses an ephemeral private bounded capture with the existing candidate,
scanner, lint and hash owners, followed by bounded live comparison. Confirmation
rechecks the binding and current tree under existing mutation locks before trust.
Preview changes no library, approval, exposure or catalog state and silently
initializes nothing. No persistent snapshot or review-receipt subsystem is added.
This remains cooperative local review, not a same-user sandbox or atomic snapshot
against arbitrary external writers.
