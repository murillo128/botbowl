# Changelog

This file records user-observable changes in the maintained fork. Versions follow
[PEP 440](https://peps.python.org/pep-0440/); a Git tag or built artifact is not
evidence that it was published to a package registry.

## 2.0.0a1 - proposed

This is a reviewable pre-release proposal, not a published release.

### Added

- A headless `create_game` factory and bounded external/policy control examples.
- A versioned Gymnasium v5 adapter with declared spaces, masks, termination and
  truncation semantics; legacy Gym v4 remains a separate compatibility extra.
- Rules/configuration descriptors, isolated RNG recipes, lab protocols and
  observation/channel APIs for reproducible experiments.
- Installed laboratory quickstarts for dataset generation/consumption, authenticated
  HTTP control, portable episode snapshots and isolated alternative branches, with
  wheel acceptance tests and a [compatibility proposal](docs/lab/release-proposal.md).
- Explicit Python/native pathfinding builds and installed-artifact validation.

### Changed

- The maintained fork requires Python 3.11 or newer. Optional dependencies are
  split into `web`, `rl`, `gymnasium`, `multiagent`, `storage`, `competition`,
  `render`, and `dev` extras.
- Invalid public actions are validated before mutation, engine advancement has
  explicit budgets, and web errors use explicit HTTP status contracts.
- The local competition socket has bounded framing and deadlines. Its pickle
  payload remains trusted-local only and is not a public remote protocol.
- The container installs a verified wheel, runs as UID 10001, and defaults to a
  finite headless smoke. Web serving must be selected explicitly.

### Fixed

- Accepted engine regressions across action validation, pathfinding, RNG,
  kickoff/drive state, skill interactions, web restore/replay behavior, and UI
  pause/touchdown rendering are included in this proposed fork line.

### Compatibility notes

- v5 Gymnasium actions and observations are not compatible with trained v4 Gym
  policies. See [migration](docs/migration.md).
- Legacy pickle replays remain trusted visual history. SnapshotFileV1 supports
  compatible-process restore; ReplayV1 adds executable history and hash checks.
  Neither automatically migrates legacy files. In-memory checkpoints remain local.
  See [support boundaries](docs/support.md).
- BB2016 behavior is partial and tested for the shipped configurations; package
  naming does not claim complete official rules conformance.
