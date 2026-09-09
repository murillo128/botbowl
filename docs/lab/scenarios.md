# Versioned laboratory scenarios

`botbowl.lab.scenarios` supplies six bounded, package-owned BB2016 exercises over
`SimulationSession`. Each begins at an externally controlled team-turn decision.
They use an **explicitly synthetic** constructor: no legal pregame, kickoff or
prior possession sequence is claimed. Structural validation is not proof of
reachability through legal match play. These are short laboratory episodes, not
completed matches, and production code does not depend on `tests/util.py`.

```python
from botbowl.lab import create_scenario, scenario_spec

spec = scenario_spec("pickup", size=3, side="home", scenario_seed=17,
                     parameters={"distance": 2, "rerolls": 1},
                     max_decisions=32, max_steps=1000)
session = create_scenario(spec)
try:
    legal = session.legal_actions()
    start = next(a for a in legal.actions if a.type == "START_MOVE")
    result = session.step(start, legal.state_revision)
    # Continue with one offered semantic ActionV1 and its current revision.
finally:
    session.close()
```

Run `python examples/lab/scenarios.py` for a complete movement example, also
usable from another working directory after an editable installation.

## Closed V1 catalogue

All recipes have version `1`; positions mirror horizontally for the away side.
Participants are the first roster slot(s) of the shipped size-specific human
teams. Their roles and skills can differ between sizes; they are not replaced
with generic players. Other roster members remain in reserves. Both teams have
0 apothecaries, 0 scores, fresh player resources, the requested remaining team
rerolls, and NICE weather. The constructor starts the acting team's first turn
of round 1, half 1, with a fixed normal-match continuation stack. The nonacting
team has not yet taken its first turn. The kickoff table and other loaded rules
remain identified by the descriptor; pathfinding is explicitly disabled so
clients choose primitive steps.

| Recipe ID | Compatible sizes | Acting/opposing participants | Objective |
| --- | --- | --- | --- |
| `movement` | 1/3/5/7/11 | 1/0 | Reach the target square standing |
| `pickup` | 1/3/5/7/11 | 1/0 | Acting player obtains the loose ball |
| `pass_receive` | 3/5/7/11 | 2/0 | Pass and give the receiving teammate possession |
| `block` | 1/3/5/7/11 | 1/1 | Resolve a block back to a team-turn decision |
| `possession_recovery` | 1/3/5/7/11 | 1/1 | Recover a loose ball in an opponent's tackle zone |
| `touchdown` | 1/3/5/7/11 | 1/0 | Score from the prescribed distance to the endzone |

Pass/receive at size 1 raises `ScenarioError`: two teammates exceed that pitch
limit. The catalogue does not reuse the baseline test's over-limit size-1 pass
fixture. Possession recovery represents a contested loose ball; it does not
claim a preceding legal strip, turnover or knockdown. A resolved block counts
as completing the block objective even when its sporting result hurts the
attacker; `scenario_success` means the recipe objective, not tactical quality.

`metadata["initial_positions"]` records exact acting/opponent positions and the
target. For non-touchdown recipes, the acting player starts at the central
column and the target lies `distance` squares towards its scoring endzone. The
receiver or block defender occupies the target; recovery's defender stands one
square beyond it. A scenario-purpose stream selects the central row or one of
its neighbors on sizes 3+, and size 1 uses the central row. Touchdown instead
starts `distance` squares from the endzone. Pass/touchdown start with the acting
player carrying the ball; pickup/recovery start with a loose ball at the target.
Movement/block put a loose ball in the opposite corner, outside the exercise.

## Specification, identity and validation

`scenario_spec(...)` derives the actual loaded `RulesDescriptor` (#30).
`ScenarioSpecV1` contains `scenario_id`, recipe `version`, `rules`, `size`, `side`,
`parameters`, integer `scenario_seed`, `end_condition`, `max_decisions` and
`max_steps`. `to_json()` and strict `from_json()` round-trip this data. The only
parameters are `distance` (default 1, range 1–3; exactly 1 for size 1 and block)
and `rerolls` (default 0, range 0–3 for each team). Decision budgets are 1–256;
per-decision automatic engine-step budgets are 1–10,000. The end condition is
fixed by each registered recipe. Seeds are integers in `[0, 2**256)`.

Unknown fields, recipe/version IDs, incompatible rules/backend descriptors,
unsupported size/parameter combinations and inconsistent construction raise
`ScenarioError` before a session is returned. There are no module names,
callbacks, pickle objects, file paths, dynamic imports, attribute assignments
or procedure-stack descriptions in the specification. `SCENARIO_RECIPES` is a
read-only inventory, not a plugin registration mechanism.

Construction uses the #54 session and factory to allocate independent teams,
Game, timeline, dice source and RNG. A private fixed constructor prepares the
turn and settles automatic initialization under its own 1000-step bound. Before
publication it checks the actor/turn boundary, pitch counts, roster/dugout
partition, board occupancy and indexes, team ownership, ball/carrier coherence,
resources, weather and offered legal actions, including #37 snapshot validation.
Scenario clocks use #37 logical time, so waiting between calls does not change
the semantic state or restored continuation.
Failures close the private candidate. No mutable game or RNG is cached/shared.
The requested episode decision budget starts at the first offered decision;
constructor initialization is recorded as synthetic timeline context, not as
played coach actions. Session metadata reports `construction="synthetic-turn-v1"`,
`reachability="structurally_validated"` and `legal_reachability_proven=False`.

Every explicit variant has a SHA-256 `variant_id` over recipe/version, rules,
size, side, normalized parameters, objective and budgets. The scenario seed
identifies a replicate and is retained in the spec, separately from variant
identity. Scenario layout and engine chance use separate #35 purposes derived
from that seed. The facade accepts no policy; independently seeded external
policies can change continuation but cannot alter scenario construction.

## Difficulty factors and limits

Metadata keeps participants, space, distance, obstacles, resources, horizon and
policy separate. Distance and rerolls can be varied within the documented
bounds; horizon is a decision budget, not match duration. Obstacles here mean
the recipe's opposing participant, not arbitrary terrain. Policy difficulty is
external and must be recorded by the experiment owner. Weather is fixed in V1.
The catalogue exposes a small set of exercises, not every possible combination
of difficulty factors or a universal state editor.

**Changing team size is a compound intervention.** It changes arena dimensions,
rosters/roles, setup limits and potentially scatter/throw-in rules. Compare the
full descriptor and metadata; never describe size changes as changing only
participant count or only spatial difficulty. Adding a contested recovery
opponent also changes local tackle zones relative to an uncontested pickup.
Neither these metadata nor structural validation establish a difficulty ranking.

## Episode endings and persistence

`ScenarioResult` extends `SessionResult` with `scenario_terminal` and
`scenario_success`. The objective or the acting team's turn ending stops the
exercise with `end_reason="scenario_terminal"`, no next actor and no available
actions. Ending the turn or losing possession without the objective records a
failed exercise. Objective completion on the last allowed decision takes
precedence over the decision budget. The wrapper never marks the engine's
`game_over` flag, and leaves `terminated=False` and `truncated=False` for a
scenario ending. Repeated steps at that boundary do not advance the game.

Natural engine completion retains `terminated=True, end_reason="game_over"`.
Decision/execution exhaustion and close retain the #54 administrative truncation
semantics, including typed `NoProgress` for execution exhaustion. A block's
intermediate opponent-owned choice is still a decision in the same team turn.
Each step resolves to a normal engine boundary before checking the objective;
for example touchdown may resolve as far as the next setup boundary.

`snapshot()` returns a `ScenarioSnapshot` containing the full spec and the #54
session envelope. `restore(snapshot, expected_revision)` rejects another
recipe/variant/seed, uses the session's atomic restoration and monotonic
revision, and recomputes exercise status from the restored engine. For a fresh
process, persist the existing #39 engine file and a data-only sidecar:

```python
import json
from botbowl.lab import ScenarioSnapshot
from botbowl.lab.snapshot_io import read_snapshot, write_snapshot

saved = session.snapshot()
write_snapshot("exercise.engine.json", saved.session.engine)
with open("exercise.session.json", "w") as stream:
    json.dump(saved.metadata(), stream)

# In a new interpreter using the same implementation and backend:
with open("exercise.session.json") as stream:
    saved = ScenarioSnapshot.from_metadata(json.load(stream),
                                            read_snapshot("exercise.engine.json"))
resumed = create_scenario(saved.spec)
resumed.restore(saved, resumed.state_revision)
```

Keep these privileged files paired. The engine file alone does not retain the
scenario specification and budget envelope. Existing #39 format, integrity,
resource bounds and implementation/backend compatibility checks still apply;
the sidecar is controller data and is not authentication. Neither file is a
player observation. Close sessions when finished.

`tests/lab/test_scenarios.py` and `scenario_scripts.py` demonstrate each family
with explicitly **test-only forced dice**, including fresh-process continuation.
Those controlled rolls establish bounded interaction behavior, not natural
outcome frequencies or legal reachability of the constructed start.
