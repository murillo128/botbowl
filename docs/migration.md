# Migrating to the maintained fork

The proposed `2.0.0a1` line makes compatibility boundaries explicit. It preserves
the original project attribution and does not claim publication under the
upstream PyPI identity.

## Python and installation

Python 3.8–3.10 are no longer supported; use CPython 3.11 or newer. A minimal
install contains the headless engine, NumPy and rule-data loader only. Add the
smallest required extra instead of installing the historical all-in-one
requirements file. Library ranges live in `pyproject.toml`; `requirements/` is a
dated development/CI constraint snapshot, not the public dependency contract.

`setup.py install` and compiler autodetection are replaced by PEP 517 builds.
The default is the Python pathfinder. Set `BOTBOWL_BUILD_NATIVE=1` at build time
to require the C++ extension; failure is explicit. Existing imports remain lazy
where possible, and `botbowl.__version__` reports the installed fork version.

## Public Python control

Prefer `botbowl.create_game(..., control="external")` and `Game.advance()` for
bounded, headless control. A policy does not run merely because a game is
constructed. Use `PolicyDriver` explicitly when registered bots should act.
Configuration names, sizes and seeds are validated, and each call owns fresh
team/configuration state.

The older direct `Game(...)`, loaders and bot registry remain available. They
expose more mutable engine detail and are not equivalent to a portable session
or snapshot API.

## Gym to Gymnasium

Legacy Gym IDs end in `-v4` and require the `rl` extra on CPython 3.11/3.12 with
NumPy <2. They keep their historical reset/step shapes and compatibility defects.

Gymnasium IDs end in `-v5` and use the `gymnasium` extra. Reset returns
`(observation, info)` and step returns
`(observation, reward, terminated, truncated, info)`. The action is a masked
discrete decision owned by the current team. Spatial orientation, player slots,
action indices and features differ from v4, so old model weights need a new
input/action head and validation; silently relabeling a v4 policy as v5 is unsafe.
See the complete [Gymnasium contract](gymnasium.md).

## Web and control APIs

The inherited Flask UI remains optional. Reads no longer advance a game; updates
are explicit, rejected client actions use JSON errors, and trusted local
save/load preserves engine RNG rather than reseeding. Those inherited UI routes remain a local interactive interface. The separate
[lab HTTP service and SDK](lab/http.md) provide authenticated JSON control with
explicit roles, revision guards and bounded requests; they do not retrofit those
guarantees onto legacy UI routes.

The competition socket continues to serialize Python objects with pickle. Its
new framing, loopback restriction, deadlines and cleanup improve robustness but
do not make deserialization safe. Never accept arbitrary peers or files. See
[web API](web-api.md) and [trusted transport limits](docker.md).

## Laboratory formats

Use the [installed quickstarts](lab/quickstarts.md) for datasets, authenticated
control, portable snapshots and alternative branches. The [compatibility matrix](lab/release-proposal.md)
names accepted versions and unsupported migrations. Legacy visual pickle replays
do not contain the state needed for automatic conversion to executable ReplayV1.
SnapshotFileV1 requires its exact component/schema identities; upgrading the
package does not promise to migrate an old saved graph.
