# Dice experiments (SIM-06)

`botbowl.lab.chance` distinguishes fresh chance, recorded dice replay, fabricated
results, and explicitly matched random variables. `EpisodeContext` installs an
owned `ChancePolicy()` by default. Existing non-lab games retain their ordinary
`DiceSource`, with no recording or sampling changes. The controller owns chance
policies, tapes, seeds, and cursors; restricted policies receive none of them.

## Modes and sources

| Mode | Dice source | Interpretation |
| --- | --- | --- |
| `independent` (default) | Existing engine MT19937 sampler | Natural new draws from an owned stream |
| `replay` | Validated, actually recorded tape | Reproduction of a recorded dice prefix |
| `forced` | Validated test tape with prescribed outcomes | Fabricated case, always `natural: false` |
| `matched` | Explicit source-to-target GFI pairs | Reuse only for declared, verified events |

Pass `chance_policy=ChancePolicy(...)` when constructing an episode. The episode
copies the policy and installs it before `game.init()`. `reset()` rebuilds the
original episode recipe and initial chance configuration; it does not reset to a
branch point. `install_chance(game, policy)` explicitly copies and binds a new
policy at a controller-owned boundary. Use the returned policy (or
`game.dice.chance`) to inspect the actual cursor and consumed tape.

An optional engine-purpose `SeedSpec` selects a new continuation stream. Its
state is copied into the existing `Game.rng` object. No global RNG is consumed;
the existing NumPy `RandomState` and die implementation still select outcomes.
Without this option, installation keeps the game's existing RNG position.

Use `branch_from_snapshot(snapshot, branch_id='left', policy=...)` to clone an
engine or episode snapshot and select continuation semantics. Its default is
`independent`, with a fresh, retained OS-entropy `SeedSpec`. Distinct branches
own every queue, tape, cursor, and RNG. For reproducibility independent of worker
order, supply a distinct engine seed recipe for each branch:

```python
from botbowl.lab.chance import ChancePolicy, branch_from_snapshot
from botbowl.lab.randomness import SeedSpec
from botbowl.lab.snapshots import capture_snapshot

saved = capture_snapshot(episode)
left = branch_from_snapshot(
    saved, branch_id='left',
    policy=ChancePolicy(seed=SeedSpec(44, 'experiment', component_id='left')),
)
right = branch_from_snapshot(
    saved, branch_id='right',
    policy=ChancePolicy(seed=SeedSpec(44, 'experiment', component_id='right')),
)
```

An attached Timeline is forked to the supplied branch ID. Without a Timeline,
the ID names the seed source; matching remains unavailable. The lower-level
`clone_from_snapshot` retains exact captured randomness by design. It does not
claim fresh independent chance. Equal recipes likewise reproduce streams; they
do not establish event correspondence or a statistical independence guarantee.

## Tape V1 and failure semantics

`game.dice.chance.tape()` returns detached JSON with `version: 1` and `rolls`.
Only consumed dice enter this output. Each row records:

- zero-based index, die type, ordered physical-face domain and result;
- semantic context: procedure/rule, roster-local participants, phase, causal
  decision/report position, procedure stack, and occurrence within that context;
- mode, provenance, coupling scope, whether the draw was natural, and
  `rng_advance`: whether the engine die sampler was consumed.

Domains are D3 `[1,2,3]`, D6 `[1,2,3,4,5,6]`, D8 `[1,2,3,4,5,6,7,8]`, and
BBDie's six equiprobable physical faces. BBDie maps face 6 to PUSH, just as the
engine does: PUSH has probability 2/6, each other result 1/6. This is not a
uniform distribution over the five distinct block results. Block results use
`BBDieResult` names in JSON; numeric results exclude booleans and floats.

Replay checks the next record's index, type, exact domain and complete context
before returning its result. A wrong die, shifted event, invalid value, or empty
tape raises `ChanceError(code='chance_divergence')`. It neither advances the
cursor nor silently skips to another record. The engine may already have begun
the enclosing action before reaching the rejected die; recover with a snapshot
or checkpoint, not an implicit retry of the whole action.

Call `policy.finish()` at the end of a declared comparison prefix to reject
unused tape records or unmatched declared pairs. `EpisodeContext` also checks
this when returning a terminal or decision-budget-truncated result. Merely
asking for a tape, context, observation, controls or synthetic grid rendering
does not consume it. Closing an episode does not imply prefix validation.

```python
recorded = factual.game.dice.chance.tape()
# On a fresh episode with the same inputs and relevant logical context:
replayed = EpisodeContext(
    *resources, episode_key='trial', master_seed=44,
    chance_policy=ChancePolicy('replay', tape=recorded),
)
for action in recorded_actions:
    replayed.step(action)
replayed.game.dice.chance.finish()
```

For a replay row with `rng_advance: true`, the original die sampler advances the
engine RNG once and its draw is discarded; the tape result is authoritative.
This preserves the original RNG interleaving when seed/state and execution agree.
Natural matched reuse has `rng_advance: false`: replaying that row, including
nested replay, must also leave RNG untouched. Fabricated rows and `forced` mode
do not advance RNG. Direct `Game.rng` calls (coin tosses and
random selection of positions/players) are **not dice tape events**. Exact
whole-prefix execution therefore also needs the original RNG state, actions,
resources, and relevant logical context. A dice tape alone is not a complete
match replay or a portable RNG algorithm guarantee.

A controller can copy a recorded tape and prescribe valid results for a test,
then select `ChancePolicy('forced', tape=tape)`. Forced mode ignores any claimed
natural status on the supplied rows and marks its output fabricated. The legacy
`DiceSource.fix/force` queues remain supported for independent draws and are
recorded as fabricated. Active strict frames or pending queues conflict with
replay/forced/matched tape consumption and fail before consuming either source.

## Explicit bounded matching

Initial matching supports at most 32 declared **GFI D6** pairs. Attach an API-02
`Timeline` before recording the relevant prefix. A supported GFI context is
captured before its die is sampled and includes the player, source/destination
squares, weather, half/round/drive/turn/activation, decision and event anchors,
procedure stack, and occurrence count. Other procedures have diagnostic tape
contexts but are not eligible for shared variables.

A matching declaration is a list of `{source_index, target}` objects. The
source is a natural GFI record in an actual tape; target is the complete expected
GFI context in the other branch. Rule, participants, phase and GFI details must
agree. Causal anchors and occurrence may differ only through the explicit target
declaration, and the actual target event must match it in full before reuse.
For two alternatives at the same snapshot and action boundary:

```python
from copy import deepcopy

row = factual.game.dice.chance.tape()['rolls'][0]
policy = ChancePolicy(
    'matched', tape=factual.game.dice.chance.tape(),
    matches=[{'source_index': 0, 'target': deepcopy(row['context'])}],
    unmatched='independent',
)
branch = branch_from_snapshot(saved_before_action, branch_id='alternative', policy=policy)
branch.step(action)
branch.game.dice.chance.finish()
```

Each source and target is declared once and consumed at most once per branch.
Ambiguous/repeated pairs, fabricated source draws, unsupported sites, and
incompatible semantics/domains are rejected. This does not infer matches from
draw position, matching seeds, a similar report, or a later successful outcome.
The declaration must be prepared before executing the target event.

The default `unmatched='error'` rejects any undeclared die. Explicit
`unmatched='independent'` permits other events to consume the branch's own RNG
and records `provenance: independent-fallback`, `scope: none`. A shifted or
repeated causal occurrence at a declared GFI site still fails. Different sites
or participants may fall back only under that permission. Shared events record
`scope: declared-gfi` and the source index. Reuse does not consume an independent
fallback draw. Never interpret this bounded facility as universal coupling of
diverging matches or as evidence of a causal treatment effect.

## Provenance, snapshots and authority

Each `EpisodeResult.chance` includes mode, provenance, coupling scope, natural
status and source recipe (or `episode-engine`). Consumed rows retain the same
categories individually. The manifest's `chance` entry describes the installed
configuration; its engine `sources` describe the original episode recipe. A
branch's chance source overrides that initial engine recipe for continuation.
Result metadata supplies the cumulative fabricated status: a forced history
stays fabricated through replay, snapshots, and a later independent branch.
This is provenance declared by trusted controller code, not authenticated proof
that an arbitrary externally edited tape was actually recorded.

The source has explicit adapters in in-memory checkpoints, executable snapshots,
and SnapshotFileV1. They preserve input/output tapes, mode, seed, cursor, used
matches, occurrence counters and fabricated history, along with the complete
engine RNG and forced frames. Restore rebinds the policy to the restored game;
clones share no mutable state. The file codec compatibility digest includes the
chance adapter revision; older snapshots without this state are incompatible.
File validation checks the bounded chance JSON before allocating engine objects.

Future tape contents, matches, RNG state and cursors are privileged snapshot
state. None is added to observations or restricted policy control. The tapes can
grow with episode length; retain only the comparison prefixes needed by the
experiment and respect existing snapshot resource limits.

Validation: `pytest tests/lab/test_chance_modes.py`, plus RNG isolation,
reproducibility and in-memory/file snapshot regressions. Evidence includes exact
finite die enumeration, an actual GFI two-branch fixture, pre-consumption
mismatch checks, unused/exhausted tapes, fabricated labels, branch isolation,
read/render purity and cursor round trips.
