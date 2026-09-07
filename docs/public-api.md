# Headless game factory

`botbowl.create_game` returns the existing `Game`, initialized at `START_GAME`.
It loads rules, arena, formations and both rosters using the core loaders. It
does not advance a decision, run `act()`, start a service, render, record a
replay or execute a match. Only NumPy and untangle are required at runtime.

```python
from botbowl import Action, ActionType, create_game

game = create_game(size=3, home_team="human", away_team="Human Team 3",
                   seed=17, control="external")
try:
    result = game.advance(Action(ActionType.START_GAME), max_steps=100)
    assert result.actor is not None
    choices = game.get_available_actions()
finally:
    game.close()
```

- `config` accepts a shipped name (with or without `.json`), a loaded
  `Configuration`, or `None`. Omission selects `gym-{size}`, defaulting to 11.
  These configuration names do not import Gym. Overrides on a configuration
  object are retained, subject to the existing loaded-resource validation.
- `size` is 1, 3, 5, 7 or 11. When supplied with a configuration it must equal
  its `pitch_max`; otherwise the configuration determines size. The factory
  uses that variant's shipped `ff-pitch` arena and roster directory.
- Each team is a shipped file stem (e.g. `human`), exact display name, or `Team`
  object. Objects must be coherent initial rosters with distinct team/player
  IDs across seats. To play the same roster against itself, pass its name twice
  or load it twice. The normal per-variant roster limit still applies.
- `seed` is `None` or a Python integer from 0 through `2**32 - 1`; booleans,
  floats and strings are rejected. It seeds the engine, independently of
  policy RNGs. Reproducibility covers semantic decisions/events under the same
  inputs and engine/backend, excluding UUIDs and audit timestamps.
- `control` is required. `"external"` installs two human placeholder `Agent`s
  and rejects policy agents. `"policy"` requires distinct non-human
  `home_agent` and `away_agent` objects, including results of `make_bot(id)`.
  It explicitly opts into their existing `new_game` initialization callbacks.
  No `act()` call occurs until the caller invokes a policy or driver.

Both control modes select `Game.external_control=True`: `advance` handles one
decision, including automatic consequences, and returns the existing
`DecisionResult`. Its `actor`, ordered `events`, and `terminal` fields retain
the [external-control contract](external-control.md). `Policy` describes the
existing `callable(Game) -> Action` accepted by `PolicyDriver`; a registered
agent's bound `act` method satisfies it. No new scheduler or session is added.

All loaded resources and mutable configuration/team state belong to that call.
Changing a game does not mutate input teams/configuration, other games or loader
defaults. Policy agents follow the existing caller-owned `Game` convention:
use fresh instances per game, initialize their own RNG explicitly if needed,
and manage any resources they own. Callback exceptions propagate unchanged.
Invalid factory arguments/resources raise `ValueError` before callbacks.

`game.close()` is idempotent and pauses clocks. It retains inspectable state;
it does not declare `game_over`, invent a winner, call `end_game`, or persist a
replay. Those finalizers still belong to natural match completion. After close,
`init`, `advance`, `step`, `refresh` and `PolicyDriver.run` reject further
execution with `InvalidActionError(code="game_closed")`; `game.closed` is true.
Closure is administrative and is not undone by a forward-model checkpoint.

The two public-only examples each execute three decisions under an engine step
budget, and close in `finally`. The policy example deliberately implements only
the demonstrated pregame choices:

```sh
python examples/public_external.py --max-steps 100
python examples/public_policy.py --max-steps 100
```

The ordinary core CI runs these against its built wheel from another cwd. The
artifact profile also runs them before any optional extras are installed.
`python tools/ci/check_public_types.py` checks the factory, both real examples
and boundary usage, and requires invalid calls to fail type checking. Install
the CI type tool with `pip install -r requirements/public-types.txt`.
Typing is incremental: the package includes `py.typed`, but the check follows
legacy imports silently and checks only these declared roots. It uses no global
ignores and is not a claim that the entire engine is strictly typed.

Existing public imports, direct `Game` construction, loaders and legacy
`init/step` scheduling remain available. No API is deprecated by this addition;
consumers can opt into the factory without migrating unrelated code.

The separate [lab protocols](lab/protocols.md) define structural simulation,
restricted policy, observer, recorder, scenario and evaluator roles. Their
`LegacyBotAdapter` explicitly forwards engine-aware bots to this existing
`PolicyDriver` boundary; `botbowl.Policy` keeps its current meaning.
