# Reproducible lab episodes (SIM-01)

`botbowl.lab.randomness.SeedSpec` derives an owned NumPy `RandomState` stream.
`botbowl.lab.episodes.EpisodeContext` composes five such sources with the existing
Game, [rules identity](rules.md), [public observation](observations.md), and
externally supplied decision boundary. Existing `Game(seed=...)`, bot, Gym and
competition defaults are unchanged. No worker pool, recorder, dataset, portable
snapshot, or operations service is introduced.

## Exact seed recipe

The recipe has exactly five fields: `master_seed`, `episode_key`, `purpose`,
`component_id`, and `derivation_version`. The master seed is a Python integer
in `[0, 2**256)`, excluding booleans. `None` selects 256 bits using
`secrets.randbits(256)` once and retains the chosen integer in `SeedSpec` and the
episode manifest. Record that value to reproduce an initially unseeded run.
No global Python or NumPy generator is seeded or consumed.

Episode and component IDs are public labels matching
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. They must not contain secrets. Paths, URLs,
arbitrary object representations and configuration dictionaries are not labels.
The five purposes, in declaration order, are `scenario`, `engine`, `policy-home`,
`policy-away`, `observation`. Identical component IDs in different purposes still
produce separate recipes. The default component ID is `default`; applications
should declare meaningful versioned IDs for their scenario, policies and observer.

Algorithm ID `sha256-json-uint32be-v1` means:

1. Serialize the five fields as a JSON object with sorted keys, separators
   `(',', ':')`, ASCII escaping, finite values, decimal integer spelling and no
   whitespace or trailing newline (`json.dumps(..., sort_keys=True,
   ensure_ascii=True, allow_nan=False)`). Encode as UTF-8.
2. Compute SHA-256 over those bytes, without a salt or further prefix.
3. Split the complete 32-byte digest into eight consecutive **big-endian uint32**
   words (`struct.unpack('>8I', digest)`). Pass all eight words, in that order,
   to `numpy.random.RandomState`. Do not reduce to a single 32-bit seed.

For `(0, 'episode-7', 'engine', 'dice', 1)`, the canonical bytes are:

```text
{"component_id":"dice","derivation_version":1,"episode_key":"episode-7","master_seed":0,"purpose":"engine"}
```

The words are `(2768529318, 666394704, 3105048887, 814156850, 3866148329,
3914011016, 3738053069, 21235154)`.

`derivation_version` is an integer namespace revision in `[1, 2**32)`, default
1. It separates experiments within this exact algorithm; it does not select
unimplemented code. An algorithm change requires a new algorithm ID and tests.
Replay requires an exact match of both the algorithm ID and namespace revision.
Python `hash()`, clock, PID, worker count, creation order and scheduling are never
recipe inputs.

SHA-256 has a finite output space, and MT19937 initialization also maps seeds
into finite state. Collisions are possible. Distinct recipes provide practical
stream separation, not a proof of mathematical independence or disjoint
subsequences. A recipe is not a statistical quality certificate.

## Construction and authority

Pass the exact loaded `(config, ruleset, arena, home_team, away_team)` resources
accepted by `describe_rules`, an `episode_key`, and optionally `master_seed`,
`component_ids` (exactly five entries), `derivation_version`, `max_decisions`,
`max_steps`, `scenario`, and `policies`.

The context copies resources. An optional **trusted** scenario callback receives
that private resource tuple and only its dedicated scenario RNG. It can modify
initial resources before configuration identity is captured. The engine retains
#14's game-owned MT19937 and dice queues. The other four streams are separate
objects; no mutable generator is shared between episodes.

`policies` maps `home`/`away` to restricted [lab policies](protocols.md).
`context.act()` invokes the active policy's `act(view, control, rng)`. The view
is a detached `ObservationV1` JSON object. Control contains `team` and `choices`;
choices retain engine order and copied `ActionChoice.to_json()` data with team
IDs replaced by `home`/`away` and player IDs by initial roster `side:slot` IDs.
Choices are candidate descriptions, not an exhaustive new legal-action schema;
the existing engine validates setup and all other action constraints.

Policies return plain action dictionaries with required `action_type` (enum
name), optional `player_id` (roster-local ID or null), and optional `position`
(`{'x': int, 'y': int}` or null). Unknown fields and malformed values raise
`InvalidActionError(code='lab_action')`; engine legality errors retain their
existing codes. `context.step(action)` uses the same boundary for supplied
actions. Results contain copied events with roster-local participant IDs,
`terminal`, `truncated`, and the count of accepted decisions, with no Game alias.

`context.observe(side)` never consumes randomness. Explicit
`context.transform(observer, side)` calls `observer.transform(view, rng)` with
only a new authorized view and the observation stream. Neither callback receives
the context, game, manifest, seed recipe, engine RNG, or checkpoint.
The context itself, including `.game`, belongs to privileged controller code.
These are API ownership boundaries, not a Python security sandbox. Callbacks
must not capture the game, use global randomness/time, or modify their internal
state in ways the caller cannot reproduce. Component IDs declare caller-owned
implementation/configuration identity; they do not hash arbitrary Python code.

`LegacyBotAdapter.requires_game_access = True` explicitly grants mutable Game
and RNG access, as documented in [protocols](protocols.md). Such adapters cannot
be installed as restricted episode policies. Existing legacy drivers still
accept them, without SIM-01 source isolation or scheduling guarantees. The caller
continues to own legacy bot RNG, lifecycle and callbacks.

## Manifest, clocks and replay

`context.manifest()` returns fresh data containing the #30 rules/config/backend
descriptor, algorithm ID, NumPy/Python and generator versions, all five recipes
and derived words, each policy's component ID and restricted/supplied-action
mode, scenario mode, clock mode and decision/automatic-step limits. It uses
explicit fields; paths, credentials, UUIDs, wall-clock timestamps and arbitrary
resource attributes are excluded. Seeds are reproducibility **metadata**, never
model features or policy inputs.

Lab clock mode is `decision-budget`. Exactly `max_decisions` accepted actions
produce truncation if the game is still live; natural termination takes priority.
A zero limit truncates immediately. No winner, loss, score or game-over flag is
manufactured by a limit. Further calls at the limit return the unchanged status
without consuming an action or calling a policy. `Game.advance` ignores
competitive clock enforcement even when configuration enables competition;
the configuration identity still records those settings. Audit timestamps and
clock objects are outside the semantic event/view guarantee.

The existing #16 `max_steps` liveness guard applies per engine advance. Its
`GameTruncatedError` propagates with its cause and may leave a partial automatic
continuation; it is not a loss. Invalid actions do not spend a decision. Callback
exceptions propagate, with their dedicated stream left at the point of failure;
there is no implicit retry. Use an explicit checkpoint to rewind owned state.

`context.replay(actions, manifest)` checks exact provenance before consuming the
iterable, requires a fresh/reset context, and applies the supplied prefix until
exhaustion, terminal, or decision truncation. It does not call policies. A mismatch
raises `EpisodeCompatibilityError(code='episode_incompatible')`. Configuration,
rules or actual bound backend changes since creation also fail compatibility
checks, using #30 with pristine initial rosters. Matching package versions are
necessary, not proof of identical engine source; the experiment owner must pin
the engine code and declared callback implementations too. No cross-version,
cross-backend or arbitrary platform equivalence is promised.

`reset()` builds a fresh game and five streams from the **same recorded seed**,
then closes the previous game. A scenario failure during construction leaves
the previous episode intact. To choose a new episode or random seed, construct
a new context. Reset/close do not invoke, reset or close caller-owned policies
or observers. `close()` is idempotent and preserves inspectable game state.

## Privileged in-memory checkpoints

Only the controller's `capture_checkpoint()` / `restore_checkpoint()` channel
contains advanced RNG state. `EpisodeCheckpoint` wraps the accepted #14
`GameCheckpoint`: trajectory ancestor plus the complete game RNG, every forced
dice queue/frame and active scope identity. It adds the other four streams'
complete MT keys, position and Gaussian cache, accepted decision count and
provenance. Capture/restore does not reseed or touch global RNGs.

The checkpoint is trusted **in-memory** data tied to that game's live ancestor
and active forced-roll contexts. It cannot restore a different/reset game,
discarded branch or expired dice scope. Invalid provenance/stream state is
checked before restoring the game; #14 then validates ancestry and scope before
changing state. Mutable stream state is copied on capture/restore. This is not a
portable file format, deserialization boundary, full process snapshot, or a
replacement for #14. It inherits #14's exclusion of audit clocks, external bot
state and persistent replay side effects. Legacy `Game.revert` remains state-only.

Never insert checkpoints into primary observations, derived features or policy
control. The [channel projector](channels.md) ignores privileged/evaluation
channels, even if the controller separately holds a checkpoint and manifest.
No implicit checkpoint export or durable storage is provided here.

## Executable evidence

```sh
python examples/lab_reproducibility.py
pytest tests/lab/test_reproducibility.py
pytest tests/framework/test_rng_isolation.py tests/framework/test_forced_action.py
```

Tests cover fixed derivation vectors, every recipe axis, omitted seeds, malformed
inputs, reverse creation, permuted worker doubles, worker counts 1/3 with
spawn/fork/forkserver where available and two interpreter hash seeds, all five
sizes, both seats, pathfinding off/on under the actual Python/native test backend,
source consumption isolation, forced GFI/reroll continuation, complete checkpoint
state, reset and exception residue, clock independence, natural termination,
metadata allowlisting and feature exclusion. Process probes compare a bounded
natural pregame prefix; movement fixtures explicitly install micropositions and
forced dice. These tests make no exhaustive all-game/all-rule claim.
