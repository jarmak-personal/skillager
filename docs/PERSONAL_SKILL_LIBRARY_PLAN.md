# Personal Skill Library Plan

## Decision

Skillager keeps external discovery in place and adds one canonical personal library
for skills the user creates or explicitly adopts. The release is intentionally narrow:
ownership, exact-hash acceptance, import, verified version inspection, and append-only
restore.

The personal library is optional. Users who only consume project, environment,
package, native-agent, or collection skills continue to use setup, review, search,
activation, and exposure without initializing it.

## Product Rule

> The library is the source of truth for owned skills. Exposed copies are managed
> projections, not alternate editable sources.

Skillager never moves an external skill into the library automatically. Import is
explicit and copies one discovered skill while leaving its origin unchanged.

## Public Surface

```text
skillager library init [--path PATH] [--no-git] [--json]
skillager library relocate --path PATH [--yes] [--json]
skillager library status [SKILL] [--json]
skillager library new NAME [--json]
skillager library accept SKILL [--yes]
    [--override-lint --reason REASON] [--json]
skillager library history SKILL [--json]
skillager library diff SKILL [--from HASH] [--to HASH] [--stat] [--json]
skillager library restore SKILL --to HASH [--yes]
    [--override-lint --reason REASON] [--json]
skillager import EXTERNAL_ID [--as NAME] [--yes]
    [--override-lint --reason REASON] [--json]
```

Existing discovery, review, search, metadata-safe show, guarded activation, tags, and
exposure remain the operational project surface.

## Library Layout

```text
<library-root>/
  .git/                         # when Git is enabled
  .skillager/
    library.json                # stable UUID and Git mode
    provenance.json             # import attribution
  skills/
    <name>/
      SKILL.md
      skillager.yaml            # optional structured metadata
      ...
```

The reserved public ID is `lib/<name>`. Trust uses the stable internal identity
`library:<library-uuid>#<name>`, so moving the library does not transfer or revoke
approval. Relocation requires the same stored UUID and updates only catalog
registration.

## Workflows

### Create

1. `library new` creates a collision-free pending draft and returns its canonical
   `SKILL.md` path.
2. The placeholder is not committed.
3. The user or agent edits the canonical files.
4. `library accept` previews scanner, lint, Git, and exact-hash state.
5. Confirmed acceptance commits the meaningful tree, then records trust for that hash.

### Import

1. `import EXTERNAL_ID` previews one discovered external skill.
2. Confirmation re-resolves and rehashes the source under bounded locks.
3. Only the canonical agent-visible tree crosses the boundary.
4. Skillager records attribution, commits when Git is enabled, and accepts only the
   verified destination hash.
5. The external origin is never modified or implicitly approved.

### Inspect And Recover

- `history` lists verified, path-specific Skillager content hashes without bodies.
- `diff --stat` is metadata-only; plain `diff` is deliberately content-bearing.
- `restore` reconstructs a verified historical tree outside the library, rescans it,
  and records it as a new descendant commit. It never rewrites Git history.

### Expose

Accepted library skills use the existing native, stub, and router exposure paths.
Ordinary exposure refuses to overwrite a locally edited managed target. An intentional
project edit must be moved into the canonical library, accepted, and explicitly
re-exposed. `expose --force` is only for an explicit decision to replace the local
copy.

## Trust And Safety Invariants

- Ownership never grants approval.
- Approval applies only to the exact canonical content hash.
- Pending or changed library bodies cannot cross show-content, activation, exposure,
  stub, or router gates.
- Display-ID collisions across distinct source identities fail closed.
- Fingerprints are advisory cache hints and never authorize trust, body emission, or
  mutation.
- Every mutation rehashes authoritative source and candidate content under bounded
  canonical-path locks.
- Symlinks, excluded files, unsafe paths, and noncanonical trees fail before trust or
  Git mutation.
- Git conflict, in-progress operation, and unrelated staged-file checks fail closed.
- Lint-blocking or high-risk content requires an audited reason.
- No command fetches, pulls, pushes, rebases, resets, force-pushes, or contacts a
  remote.

## Metadata Contracts

The library exposes these versioned JSON schemas:

- `skillager.library-init.v1`
- `skillager.library-relocate.v1`
- `skillager.library-status.v1`
- `skillager.library-new.v1`
- `skillager.library-accept.v1`
- `skillager.library-history.v1`
- `skillager.library-diff.v1`
- `skillager.library-restore.v1`
- `skillager.import.v1`

Public JSON contains user decisions, status, relevant hashes, paths, and one structured
next-command argv when needed. Internal approval keys, source keys, transaction
fingerprints, and derivable mutation booleans are not public contracts.

`working` remains `skillager.working.v1`. Exposure drift, inventory, and curation are
additive advisory fields and do not change readiness.

## Deliberately Deferred

The release does not expose:

- edit-anywhere reconciliation or promotion;
- automatic or manual upstream import refresh;
- library variants/forks;
- exposure synchronization or version pins;
- cross-project exposure ledgers or rollout;
- automatic merge behavior;
- multiple personal libraries, hosted registries, packs, or other artifact kinds.

These require demonstrated user demand and a separately reviewed product design. The
current on-disk identity, exact hashes, and sidecar provenance preserve safety without
promising those workflows.
