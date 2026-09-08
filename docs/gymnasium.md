# Gymnasium v5

Install `pip install '.[gymnasium]'`. This separate extra uses Gymnasium 1.3;
`.[rl]` retains Gym 0.26.2 and NumPy <2 for historical v4 consumers. Importing the
headless engine does not import either framework. Registration is explicit:

```python
import gymnasium as gym
from botbowl.ai import register_gymnasium_envs

register_gymnasium_envs()
env = gym.make('botbowl-3-v5', max_decisions=1000)
try:
    obs, info = env.reset(seed=17)
    while True:
        action = env.action_space.sample(mask=obs['action_mask'])
        obs, reward, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break
finally:
    env.close()
```

Importing `botbowl.ai.gymnasium_env` also registers all five sized v5 IDs. There
is no change to `botbowl-v4` or `botbowl-{1,3,5,7,11}-v4` in Gym's registry.
`botbowl.GymnasiumEnv` is the direct v5 class; `botbowl.LegacyV4Env` explicitly
names the existing `BotBowlEnv`. Existing top-level wrapper names remain v4.
Import v5 wrappers from `botbowl.ai.gymnasium_env`.

## Decision and reward ownership

The base environment controls **both seats** through `Game.advance()`. Every
`step` accepts exactly one engine decision, including setup, opponent rerolls,
block selection, consecutive decisions by the same actor, and `END_PLAYER_TURN`.
It never schedules a bot via `Agent.human`. Engine formation actions are one
published decision; `END_SETUP` is a subsequent decision. Individual player
placements, including returning a player to reserves, are also encoded. Arbitrary
legacy `EnvConf.extra_formations` macros are not a v5 constructor option; v5 uses
the formations published by its configuration and the accepted engine interface.

The scalar default reward is the submitting team's score delta, including the
last decision of a match. `info['acting_team']` identifies that seat (`home` or
`away`), while `info['next_team']` identifies the next decision owner, or `None`.
`info['rewards']` always contains numeric deltas for **both** seats; the base
reward is not zero-sum. `info['events']` preserves the new ordered engine reports
as detached JSON dictionaries, including terminal events.

Single-player scheduling is an explicit wrapper with a supplied callable:

```python
from botbowl.ai.gymnasium_env import GymnasiumEnv, RewardWrapper, SinglePlayerWrapper
from examples.a2c.a2c_env import A2C_Reward
from examples.a2c.gymnasium_episode import opponent

env = RewardWrapper(GymnasiumEnv(size=1), A2C_Reward('home'), A2C_Reward('away'))
env = SinglePlayerWrapper(env, opponent, learner='home')
obs, info = env.reset(seed=17)
# The learner receives home rewards, including those earned during opponent play.
```

`opponent(game) -> Action` is responsible for its own bounded execution. Policy
exceptions propagate; they are not match outcomes. A stateful opponent may expose
`reset(seed=...)` for reproducibility; its seed is derived from the environment
generator after reset, so unseeded resets also continue reproducibly. An unseedable/random user callback cannot
be made deterministic by the engine seed alone.

`ScriptedActionWrapper(env, scripted_func)` similarly calls a supplied function
until it returns `None`. Every extra decision goes through the inner `step`.
Place `RewardWrapper` **inside** either scheduling wrapper; invalid order raises
`ValueError` at construction. Both reward functions are evaluated at every
intermediate state and must bind their own team, rather than use the next actor.
Functions may implement `reset()` to clear episode state. `A2C_Reward('home'/'away')`
also detects new Game instances and handles terminal states without an actor.
The default score reward and shaped reward add together; omit touchdown shaping
if you want to count each score only once.

Scheduling wrappers return `info['transitions']`: ordered detached observations,
rewards, flags, actions and infos for every accepted decision. Aggregated rewards
and events include all these transitions. Reset can itself run opponent/scripted
decisions: inspect `reset_rewards`, `transitions`, `terminated` and `truncated`
in reset info. If either flag is true, there is no learner action to sample; reset
with a usable budget/policy. Reset rewards are recorded there, never silently
folded into the first v5 step. No automatic reset occurs in the adapter.

## Observation and action encoding

Observations have exactly three keys:

| Key | Space / dtype | Meaning |
| --- | --- | --- |
| `spatial` | Box, float32, `(44, height, width)` | Existing board features, with disabled positional choices removed |
| `non_spatial` | Box, float32, `(128,)` | Game/team resources, turn/action/procedure flags and available types |
| `action_mask` | MultiBinary, int8, `(action_space.n,)` | 1 for an encodable legal decision, 0 otherwise |

Box limits span finite float32 values. Normalizations retain their historic
scales, not a promise of `[0, 1]`: for example ten rerolls are `10/8 = 1.25`, and
pregame half is `-1`. Values are not clipped. The fixed first 50 scalar fields
retain their documented logical ordering; actor-presence flags are at 50/51,
procedure flags begin at 52, and action-type flags follow. Missing players reserve
all six action fields. Without an actor, team statistics use home/away ordering,
actor flags and own/opponent spatial layers are zero, and neutral board features
remain available. Terminal observations are real arrays, not `None`.

When away acts, spatial columns and square action coordinates reverse together;
player slots list away then home. When home acts, slots list home then away.
Within each team, slots follow the loaded roster order. No-actor observations use
unflipped board coordinates. These are v5 features/actions; v4 trained weights
are not compatible and must not be silently reused.

Let `B = width * height`, `P` be the per-team roster slot count, and `A` the
number of distinct engine `ActionType` values excluding automatic `CONTINUE`.
In enum declaration order, each type gets `S = 1 + B + 2P` slots:

- Target 0: no player or position (including an offered `None` position).
- Targets 1 through B: row-major square, reversed horizontally for away.
- Remaining 2P targets: explicit player selection, including off-pitch players.

`PLACE_PLAYER` uses the additional region beginning at `A*S`, indexed as
`player_slot*(B+1) + square_target`; square target 0 means reserves. Unused or
unoffered indices are masked. Existing engine enum aliases retain their engine
identity. The shipped engine's only combined player-and-square choice is setup
placement; a new combined-target action would require an encoding/version review.

| Size | 1 | 3 | 5 | 7 | 11 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Discrete.n | 1979 | 6463 | 13571 | 17615 | 38001 |

`encode_action` accepts the engine's legal player/square shorthand.
`decode_action` reads the current orientation without mutating the game. The mask
comes from enabled offered choices checked with `Game.validate_action`; `END_SETUP`
is masked until the formation is legal. Every mask index decodes to a validated
action. Out-of-range, noninteger and masked inputs raise `ValueError` before
changing game state, RNG, reports, clocks, budgets or step counts. Sample with the
mask: uniform `Discrete.sample()` can return a masked action.

## Lifecycle, checking and examples

`reset(seed=N)` reseeds Gymnasium's generator and derives an engine seed; repeated
seeded decision sequences reproduce observations, reports and rewards. `reset()`
continues the generator. Action-space sampling has its own Gymnasium seed.
`options=None` or `{}` is accepted; unsupported options raise `ValueError`.

Natural completion returns `terminated=True`. `max_decisions` (default 10000)
counts accepted external decisions, including scripted/opponent decisions.
`max_steps` (default 100000) is a shared engine execution budget for the episode.
Either exhaustion returns `truncated=True`, with `truncation_reason` set to
`decision_budget` or `step_budget`; it does not create a winner, end callbacks, or
`game_over`. Partial engine consequences and rewards are retained. Ending masks
are empty even if an interrupted engine still has offered choices. A live
nonterminal state with no encodable actions raises an error instead of presenting
an invalid training episode. Gymnasium also supports an outer `TimeLimit` via `gym.make(max_episode_steps=N)`;
that wrapper counts calls to itself and retains the last live mask. Use
`max_decisions` for the adapter budget, including scheduled decisions.
Step after adapter termination, truncation or close requires reset. `close()` is idempotent, releases the Game without declaring a result, and
a later reset can open a new episode. The supported render mode is textual `ansi`.

The official [Gymnasium checker](https://gymnasium.farama.org/api/utils/) runs in
`tests/ai/test_gymnasium_env.py` for every size, including its render and close
checks. Because it samples before its determinism reset without mask support,
only the test's sample source is restricted to the reset boundary's legal mask;
the production Discrete space and the checker itself are unchanged. Separate
checks enumerate legal indices, verify rejection of masked inputs, and exercise
setup/reroll/actor/terminal observations and seeded complete bounded games.

Run `python -m examples.gym_example`, `python -m examples.multi_gym_example`, or
`python -m examples.a2c.gymnasium_episode`. The latter exercises spawned A2C workers
without torch/training/GPU. `VecEnv` retains its eight stacked result arrays and
exposes `last_infos`, including final observations/infos before auto-reset. Invalid
worker decisions raise `WorkerError`, never a win/loss. Policy mask validation
rejects empty/nonbinary batches before softmax and has no unbounded resampling.
Historical `a2c_example.py`, `a2c_agent.py`, and their checkpoints remain explicitly
v4; migrating a trained policy requires a new feature/action head and training.
Legacy scripted/PPCG steps now retain intermediate returns and sum inner rewards;
legacy reset-script rewards are retained in `reset_transitions` and carried into
the first subsequent step because v4 has no reset-info return slot.

## Runtime boundary

The Gymnasium extra targets CPython 3.11–3.14 and NumPy 1.26.4–2.x where NumPy
supports that interpreter. Targeted installed-artifact evidence covers Gymnasium
1.3.0 with NumPy 2 on 3.11–3.14, and coexistence with legacy Gym/NumPy 1.26.4 on
3.11/3.12. This is headless adapter/worker evidence, not certification of optional
PyTorch training, Tk, or every platform. Legacy `rl` stays limited to 3.11/3.12 and
NumPy <2. No old ID is moved to Gymnasium.

The ordinary gate remains lint plus CPython 3.11 core on Python/native backends,
with all four core shards concurrent. The extra `gymnasium` packaging profile is
available for targeted checks; no retired runtime/extra matrix is restored.
