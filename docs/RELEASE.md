# Release Runbook

Skillager is released as two distributions from this repository:

- `skillager-linter`: standalone manifest linter and shared validator package
- `skillager`: core CLI, approval, discovery, search, and materialization runtime

Publish `skillager-linter` first. The core `skillager` wheel depends on the
tested linter minor series, so the linter artifact must be available before the
core artifact is installed from PyPI.

## Local Rollback Check

Run the full local gate before publishing:

```bash
uv run --python 3.13 python scripts/check.py
```

The full check clears `dist/`, builds both packages, installs the built
`skillager-linter` wheel into a fresh virtual environment, installs the built
`skillager` wheel against that local wheelhouse, then smoke-tests both module
entrypoints and lint commands.

For just the wheel pairing check after a manual build:

```bash
rm -rf dist
uv build packages/skillager-linter
uv build
uv run python scripts/check_wheelhouse.py --python 3.13
```

## First Split Release Rehearsal

Before the first PyPI split release, publish to TestPyPI in dependency order:

```bash
rm -rf dist
uv build packages/skillager-linter
uv build
uv publish --publish-url https://test.pypi.org/legacy/ dist/skillager_linter-*
uv publish --publish-url https://test.pypi.org/legacy/ dist/skillager-*
```

Then smoke-test TestPyPI installs. Keep PyPI as the extra index so third-party
runtime dependencies can resolve from the real index:

```bash
uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ --from skillager-linter skillager-lint --version
uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ --from skillager skillager --version
```

## PyPI Release

Use the GitHub release workflow after the local and TestPyPI checks pass. The
workflow:

- builds both distributions
- runs the local wheelhouse smoke check
- uploads both artifacts to the draft GitHub Release
- skips publishing a package version that already exists on PyPI
- publishes `skillager-linter` before `skillager`
- publishes the GitHub Release only after PyPI jobs complete

Release notes must name both package versions. Include this compatibility note
while the V1 extraction is fresh:

```text
Compatibility shims remain for existing imports from
skillager.skills.simple_yaml and skillager.skills.lint. New integrations should
import from skillager_linter.
```
