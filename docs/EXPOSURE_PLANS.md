# Local exposure lifecycle plans

`expose --request-json` adds one explicit, complete local-project action. It does
not approve, synchronize, install Skillager, or select a remote host. Keep using
ordinary `expose` for direct library additions and existing `expose --remove` for
managed direct/router removal.

Every request uses `schema: "skillager.exposure-request.v1"`. The action is one of:

| Action | Required fields besides schema/action | Effect |
| --- | --- | --- |
| `adopt-native` | `origin_id`, `source: {library_id, skill_id}`, `mode: native\|stub` | Convert one preserved unmanaged native directory in place. |
| `remove-native` | `origin_id`, `source: {library_id, skill_id}` | Remove only that preserved native directory. |
| `group` | `name`, `library_id`, `members`, `replace` | Create a new named project tag/router. |
| `set-members` | `router_id`, `library_id`, `members`, `replace`, `departures` | Replace the complete desired membership of one named router. |
| `ungroup` | `router_id`, `mode: native\|stub` | Restore recorded members as direct copies and remove the router. |

Members are exact `lib/name` IDs in the selected registered library UUID.
`replace` contains only explicitly selected `{origin_id}` or `{exposure_id}`
objects. `departures` contains exactly one `{skill_id, mode}` for every departing
member, with mode `native`, `stub`, or `remove`. Remove creates no direct copy and
never consumes the departing body; unrelated copies and canonical content stay.
Omitted replacements remain. Restoring a member can retain an already-current
matching direct copy; a different version/mode in an unselected standalone refuses
rather than implicitly updating it.

For example, supply actual public identities in place of the placeholders:

```bash
skillager expose --request-json '{"schema":"skillager.exposure-request.v1","action":"adopt-native","origin_id":"ORIGIN_ID","source":{"library_id":"LIBRARY_UUID","skill_id":"lib/review"},"mode":"stub"}' --agent codex --scope project --dry-run --json
```

Preview requires `--dry-run --json`, exactly one `--agent codex|claude`, and project
scope. Apply repeats the same request and context with `--yes --confirmation-token
TOKEN --json`. Positional IDs, selection filters, force/compatibility overrides,
list/remove, and other mode flags cannot be combined with a request. The JSON
preview provides the fixed `next_command_argv`. Read the complete effects before
confirming; no text-only request confirmation is offered.

## Preservation and names

Native selectors come from the metadata-only `skillager.library-lineage.v1`
relation (`library sync --status --json` or the existing skill metadata). The
origin is a physical occurrence, separate from its logical source identity.
Skillager rechecks the registered library UUID, canonical acceptance, current
origin approval, lineage and full current tree. Every removed file, directory and
entry permission must match its preserved canonical counterpart. Extra excluded
files, symlinks or special entries refuse; filtered approval hashes alone never
make them disposable. In-place adoption preserves and binds the original root
permissions. Library preservation does not recreate the deployment directory.

An unwanted pending, blocked or unpreserved native directory needs no approval
merely to be removed. Use the host's ordinary file manager or a client's explicit
Files deletion/Trash flow, with that tool's own confirmation and recovery policy.
These commands do not introduce an archive or alternate approval mechanism.

A group name is normalized by the existing project-tag rules and becomes a visible
project tag. Preview never writes the tag. Existing tag/router names refuse
without a suffix or implicit adoption. Set-members refuses when independent tag
curation differs or another agent/router shares that tag. Ungroup and managed
router-only removal retain tag curation. Empty set-members removes the router and
retains the reviewed empty tag. Legacy explicit-set routers can be ungrouped when
all recorded member UUIDs and IDs resolve to the selected canonical library, or removed through
the existing managed-removal command; they cannot change membership in place.

## Exact copies

An adopted copy keeps its native location during ordinary library Full/Stub
changes. A unique matching managed project library copy can be reused. Multiple
copies are ambiguous until one returned exposure ID is selected explicitly:

```bash
skillager expose lib/review --exposure-id review --mode stub --agent codex --scope project --dry-run --json
```

`--exposure-id` supports only complete project/library native/stub preview and
bound apply. The selected ID joins the existing `skillager.exposure-preview.v1`
payload as `selected_exposure_id` and its confirmation token. Missing, foreign or
ambiguous copies refuse. This is an identity selector, never a path argument.

## Public source identity

`expose --list --json` and managed Remove records expose the recorded identity:

- Direct native/stub records have `source_library_id: UUID | null` alongside
  `skill_id`. A canonical association requires both the UUID and skill ID.
- Router records have `member_sources: [{skill_id, source_library_id}]` alongside
  `skill_ids`. New router sidecars persist these fields in the complete effects.
  The array follows `skill_ids` order and must align uniquely and completely.
- Missing legacy member metadata produces the same member IDs with null UUIDs.
  A valid explicit null is an unknown qualifier for that member. A malformed
  member array, duplicate/missing/extra member, unexpected field, or invalid
  non-null UUID invalidates every qualifier in that array: all become null.
  Invalid or duplicate `skill_ids` cannot supply member identity proof.
- UUIDs must use their canonical lowercase spelling. Missing or invalid direct
  UUIDs become null. No current library, tag, path, name or content hash fills a
  missing identity. Existing non-library source policy stays unchanged.

Drift, inventory and library status compare canonical UUID/ID before bytes. An
old library's copy cannot count as the connected library's same-ID copy, even
when both accepted hashes match. Foreign or unknown canonical sources report
`source_unavailable` without a re-expose command. Same-UUID library relocation
preserves association. Compact per-skill `exposed_via` and `exposure_targets`
references contain only that member's `source_library_id`; the full member array
is on the router record, avoiding repeated whole-router metadata per skill.

Ungroup, retained members and Full/Stub departures refuse when recorded canonical
identity is foreign or unproven. Router activation uses the same identity rule.
Membership-only Remove departures and target-owned managed Remove still require
no approval of the departing body. Legacy canonical routers without identity
proof can be removed; their members cannot be restored by guessing the current
library. Metadata reads neither migrate nor rewrite old sidecars.

## Complete public payload

Lifecycle preview uses `skillager.exposure-plan.v1`, `status: would_apply`, with:

- canonical request, project, agent, scope and library UUID;
- selected canonical source identities/hashes, public approval evidence, and
  selected origin/lineage/preservation evidence;
- group/tag membership before and after, and the explicit tag policy;
- every affected or consumed `targets[]` entry: stable plan-local `target_id`,
  kind/path/exposure identity, action, before/after root mode and state hash,
  and complete `file_effects` with relative path, action, before and after;
- absent-state bindings and required created/existing parent directories;
- complete inert generated-sidecar/tag metadata, with explicitly named generated
  timestamp/fingerprint/integrity fields instead of guessed runtime values;
- one token binding the entire deterministic payload.

Entry effects describe bytes by size/SHA-256 and permissions, never instruction
bodies. They include supporting files, removed directories and deployment
sidecars. `keep` effects disclose unchanged entries. Parent entries describe the
parent's own mode, not unrelated descendants. Hidden lock/staging files belong to
existing CLI coordination; they are not skill content. Preview creates none in the
project. Apply can retain coordination directories. After a failed publication,
an otherwise empty created parent containing only the action's stable lock
artifacts (or their parent directories) reports `applied` with reason
`coordination-retained` and its actual observed state, without a recovery path.
Locks are never unlinked during rollback. Empty parents report `rolled_back`;
parents containing changed or unknown material report `recovery_required`.

Apply returns the confirmed `plan_hash`, the same plan context/effects, and one
compact result for every admitted target: target identity/path/action,
`status: applied|unchanged|refused|rolled_back|recovery_required`, reason code,
observed state hash, and optional recovery path. Success is exit 0 and top-level
`applied` only after all installations verify. Preflight stale/refusal is exit 2
with no target/tag writes. A refusal before target admission has an empty results
array; a built plan reports all its targets as refused.

After mutation starts, failure returns exit 2 and `partial` with actual outcomes.
Originals remain detached until all targets verify; their complete bytes/modes
are checked again before disposal. Rollback never overwrites a concurrently
changed destination. Recovery paths can retain originals and interrupted current
copies. This coordinates one bounded exposure action; it does not promise
simultaneous atomic replacement across directories.

Never retry automatically. A timeout, killed process or lost/malformed stdout is
uncertainty. Existing exposure/tag/library metadata can help inspect actual state,
but does not prove every lost apply outcome or retained recovery path. A client
must keep writes unavailable when it cannot reconcile every admitted target/tag;
a generic Refresh is not proof of successful completion.

Limits are 64 KiB request JSON, 64 members, 128 total targets, 2,048 aggregate
entry effects, 64 KiB per metadata object, 128 MiB peak staging including candidates, retained originals and the largest
concurrent cross-filesystem transfer copy, and 4 MiB complete JSON output. Overflow refuses without
truncating members/effects. Same-filesystem candidates transfer by rename; cross-filesystem candidates use a
verified copy then removal of the private original. Remaining staging/recovery
directories stop subsequent lifecycle actions until inspected and resolved.
Existing finite resource-lock deadlines apply. These
are explicit action bounds, separate from the browsable inventory size.
