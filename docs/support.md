# Supported behavior and reproducibility boundaries

## Rules and configurations

The shipped engine uses the `BB2016` resource name and provides tested board-size
configurations 1, 3, 5, 7 and 11. That name is a reference to the implemented
resource, not certification of complete official edition conformance. Pregame
inducements and league progression are not implemented, small boards are Bot
Bowl variants, and pathfinding parity is covered by a bounded corpus rather than
every possible skill combination. `LRB5-Experimental.xml` is shipped legacy
data, not a separately supported complete rules line. The authoritative detailed
inventory and evidence limits are in [rules identity](lab/rules.md).

## Reproducibility

A `create_game(seed=N)` seed owns engine randomness. Lab `EpisodeContext` can
derive separate scenario, engine, home-policy, away-policy and observation
streams from a recorded master seed and recipe. Policy code that reads global
randomness, clocks or external state is not made reproducible by the engine seed.
Reproduction also requires the same effective configuration/rules digest,
backend contract, versioned seed recipe, actions and compatible runtime. No claim
is made across arbitrary package, NumPy, RNG-algorithm or rules changes. See
[reproducible lab episodes](lab/randomness.md).

## Checkpoint, save, replay and snapshot are different

| Mechanism | Intended use | Continue simulation? | Trust/portability boundary |
| --- | --- | --- | --- |
| Legacy `Replay` | Action/report history and visual seeking | Not a general branch point | Pickled local file; trusted input only |
| Web save/load | Resume an inherited local game | Yes, within compatible trusted code | Pickled local file; trusted input only |
| `Game.capture_checkpoint()` / `Game.restore_checkpoint()` | Rewind engine/RNG during one live process | Yes, for tested in-memory branches | Not a persistent or cross-process format |
| `Game.to_json()` / replay page | UI observation | No | Data view, not enough to restore the simulator |
| `SnapshotFileV1` | Validated JSON engine/episode graph | Yes, in compatible processes | Exact engine/NumPy/backend/schema identities; bounded closed codec |
| `ReplayV1` | Snapshot plus verified decisions/events | Yes, seek and branch | JSON manifests/checkpoints; same snapshot compatibility requirements |

A replay looking correct in the browser does not prove that a simulation can be
restored and continued. The portable [snapshot codec](lab/snapshot-files.md) and
[executable replay](lab/replays.md) are separate from legacy visual data; neither
automatically converts old pickle files. Checkpoints do not own external I/O,
policy state or wall clocks. Loading pickle can execute code; accept only files and
peers controlled by the same trusted user. See [forward model](forward-model.md),
[web API](web-api.md), and [transport](docker.md).
