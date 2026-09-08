# Preparing a release

Release preparation never publishes Bot Bowl, creates credentials, pushes tags,
or claims the upstream PyPI project name. Those actions require a later explicit
owner decision and a separately reviewed destination/identity.

## Version source and proposal

`botbowl/_version.py` is the only editable version source. Setuptools reads it
for wheel/sdist metadata and runtime exports it as `botbowl.__version__`.
`pyproject.toml`, Docker and workflows must not duplicate the version.

The current review proposal is `2.0.0a1`, summarized in the
[changelog](../CHANGELOG.md). Before changing it, update the changelog and
migration/support claims in the same review. Versions are changed deliberately,
not on every commit.

## Local verified artifacts

From a clean checkout with the `dev` extra:

```bash
python tools/release/verify_artifacts.py --output dist/release --install-smoke
```

The verifier builds wheel and sdist twice from fresh copies, checks that both
runs contain the same expected files, compares wheel/sdist/source/runtime
versions, rejects tests/caches/graphics/secrets, and installs each artifact into
a clean virtual environment outside the checkout. It writes the canonical
reviewed pair, hashes and a file manifest under `dist/release/`.
Hashes identify this run; equal archive bytes are not claimed because archive
timestamps and generated metadata can differ. The enforced reproducibility claim
is equal normalized file membership from identical source.

## GitHub artifact workflow

`.github/workflows/release-artifacts.yml` runs only by manual dispatch or a tag
matching `v*`. It validates that a tag equals `v` plus the single source version,
runs docs/examples/artifact checks, builds and smokes both non-root container
targets, and uploads artifacts to the workflow run. Its token has read-only
contents permission. It contains no registry upload, package index token,
release creation or Docker push step, and it does not run on pull requests.

Creating a tag is not part of this procedure unless the repository owner
explicitly authorizes it. Downloaded workflow artifacts remain release candidates
until a later review decides identity, destination, signing and publication.
