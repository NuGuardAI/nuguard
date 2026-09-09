# Releasing NuGuard

`pyproject.toml` is the canonical source for the NuGuard version. Runtime,
package, plugin, marketplace, lockfile, and Smithery versions must match it.

## Prepare a release

From a clean branch based on the intended release commit, update every version
projection with one command:

```bash
npm run version:bump -- 0.9.8
```

The command uses `uv version` to update `pyproject.toml` and `uv.lock`, then
updates all other NuGuard-owned manifests transactionally. If validation or a
write fails, it restores the original version files.

Validate and commit the complete version change:

```bash
npm run version:check
uv lock --check
uv run pytest tests/test_release_metadata.py tests/test_sync_marketplace_script.py -q
uv build
```

## Publish a stable release

Repository administrators must protect `v*` tags so only maintainers can create
or update them. The `pypi`, `npm`, `smithery`, and `testpypi` environments should
require maintainer approval, and PyPI/TestPyPI trusted publishers must be scoped
to their corresponding workflow and environment.

After the release commit is merged, create and push an annotated version tag
that exactly matches the package version:

```bash
git tag -a v0.9.8 -m "Release v0.9.8"
git push origin v0.9.8
```

The production workflow checks out that tag, verifies its version and all
release metadata, and creates a draft GitHub Release. Build jobs pin the tag
event's immutable commit SHA, disable dependency caches, and prepare the
artifacts and integrity-verified publishing tools before any job receives
registry credentials. Credentialed jobs download those artifacts without
checking out or executing repository code. The workflow then publishes to
PyPI, npm, and Smithery. The GitHub Release becomes public only after every
destination succeeds. A failed run leaves the release as a draft; rerun the
failed workflow jobs after correcting credentials or registry availability.
On retries, existing PyPI files must match the local SHA-256 digests and an
existing npm tarball must match the local SRI integrity value. A mismatch or
registry outage stops the release instead of mixing artifact provenance.

Do not create historical tags as part of a current release. Historical tags
must be reconciled separately from package publication using verified artifact
provenance.

## Publish to TestPyPI

TestPyPI accepts only a committed PEP 440 prerelease version, such as
`0.9.8rc1`. Merge the prerelease version commit into `develop`, select
`develop` in the **Run workflow** branch selector, and run **Publish To
TestPyPI**. The workflow refuses other branches, pins both jobs to the dispatch
commit, disables dependency caching, and validates prerelease metadata before
building or publishing. TestPyPI versions are immutable, so use a new
prerelease version instead of retrying a version that was already published.