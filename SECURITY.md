# Security Policy

Skillager contains a local tool for reviewing and exposing agent skills. It reduces accidental context exposure and catches common risky patterns, but it does not prove a skill is safe.

## Reporting Vulnerabilities

Open a GitHub issue with a minimal report. If the issue is sensitive, avoid posting exploit details publicly; say that you have a security report and include enough context to arrange a safer follow-up.

Useful reports include:

- a minimal reproduction
- the affected Skillager version
- whether the issue affects scanning, trust, activation, materialization, session logs, or package discovery
- whether unreviewed skill content can be exposed
- whether blocked skills can be activated or materialized

## Supported Versions

During early 0.1 development, only the latest release is supported.

## Security Boundaries

Skillager's built-in scanner is deterministic and local. It does not use an agent or external model to classify skill bodies.

**The scanner is a review aid, not a guarantee. Users own the final trust decision.**

Skillager should not:

- activate unreviewed skills by default
   - caveat: manually installed skills are trusted by default
- materialize blocked skills
- expose full skill bodies through metadata commands
- import arbitrary packages during indexing
- store chat transcripts in session logs
