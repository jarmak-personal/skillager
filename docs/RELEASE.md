# Release Runbook

Skillager is released as two independently versioned distributions from this
repository:

- `skillager-linter`: standalone manifest linter and shared validator package
- `skillager`: core CLI, approval, discovery, search, and exposure runtime

The core `skillager` wheel depends on the supported linter minor series
(`skillager-linter>=0.1,<0.2`). A new install of an existing Skillager release
can resolve a newer linter patch without a Skillager version bump. Publish a
new Skillager patch only when core code needs to change, or when existing
Skillager users need the normal Skillager upgrade path to pull in dependency
changes.

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

Use the GitHub release workflow after the local and TestPyPI checks pass.
Select the package being released:

- `package=skillager`, tag format `vX.Y.Z`
- `package=skillager-linter`, tag format `skillager-linter-vX.Y.Z`

For a pre-committed version bump, use `bump=current`. For a workflow-managed
version bump, use `patch`, `minor`, or `major`.

The workflow:

- builds the selected distribution
- runs package checks before publishing
- runs the local wheelhouse smoke check for Skillager releases
- uploads selected package artifacts to the draft GitHub Release
- skips publishing a package version that already exists on PyPI
- publishes the GitHub Release only after PyPI jobs complete

Release notes must name the package version being released.

## Recovering From a Half-Published Release

If a package publishes successfully but its GitHub Release publishing fails,
fix the workflow or release metadata and rerun with `bump=current`.

If the artifact itself needs to change after PyPI publish, release a new patch.
Do not delete published artifacts; yank only when a published version should no
longer be selected by installers.
