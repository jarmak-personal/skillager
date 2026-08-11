# Security Policy

Skillager contains a local tool for reviewing and exposing agent skills. It reduces accidental context exposure and catches common risky patterns, but it does not prove a skill is safe.

## Reporting Vulnerabilities

Open a GitHub issue with a minimal report. If the issue is sensitive, avoid posting exploit details publicly; say that you have a security report and include enough context to arrange a safer follow-up.

Useful reports include:

- a minimal reproduction
- the affected Skillager version
- whether the issue affects scanning, trust, activation, exposure, session logs, or package discovery
- whether unreviewed skill content can be exposed
- whether blocked skills can be activated or exposed

## Supported Versions

Only the latest released Skillager version receives security fixes.

## Security Boundaries

Skillager is a cooperative local workflow and approval layer. It is not a sandbox, an operating-system security boundary, or an authorization boundary against another process running as the same user. The same user can edit skill sources, approval files, exposure sidecars, native agent directories, or the Skillager executable itself.

Agent hosts discover native skills independently. A skill placed directly in a host's native directory may be loaded by that host without going through Skillager. Skillager can discover and review those skills, but cannot enforce host behavior outside commands and projections it controls.

Skillager's built-in scanner is deterministic and local. It does not use an agent or external model to classify skill bodies. Exact content identity includes eligible paths, bytes, and normalized executable bits. Preview tokens bind cooperative follow-up commands to the reviewed state, but are not secrets or capabilities against the local user.

Repository metadata is not user-catalog authority. A project may store portable tag hints, but those hints cannot select approval or library state unless matching user-owned project state has already bound that catalog. Explicit command-line and environment configuration remain user-controlled authority.

**The scanner is a review aid, not a guarantee. Users own the final trust decision.**

Skillager should not:

- activate unreviewed skills by default
- expose blocked or lint-blocked skills
- expose full skill bodies through metadata commands
- expose unreviewed manifest free text through search/list/show before review
- allow authors to choose scanner behavior, trust requirements, source identity, package version, or the body file path from `skillager.yaml`
- approve lint-blocked skills without `--override-lint --reason`
- echo hostile manifest contents through lint output
- import arbitrary packages during indexing
- store chat transcripts in session logs

Manually installed native skills are not trusted by default. They remain unavailable through Skillager until the current exact hash is reviewed; lint-blocked native skills remain quarantined until the user fixes the source or records an audited override. This does not prevent the agent host from loading directly installed native content on its own.

For stronger isolation, use operating-system accounts, filesystem permissions, containers or virtual machines, network controls, and the agent host's own sandbox/approval settings. Do not treat a clean Skillager scan as proof that a skill is benign.
