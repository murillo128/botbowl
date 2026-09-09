# PettingZoo AEC v1

Install `pip install -e '.[multiagent]'`. The optional extra pins **PettingZoo
1.25.0**; validation uses Gymnasium **1.3.0**. Importing `botbowl` or
`botbowl.lab` does not import either optional dependency. The existing
`botbowl.lab.adapters.LegacyBotAdapter` import remains supported.

```python
import numpy as np
from botbowl.lab import SessionConfig
from botbowl.lab.adapters.pettingzoo_aec import BotBowlAECEnv

env = BotBowlAECEnv(SessionConfig(size=3, max_decisions=100))
try:
    env.reset(seed=17)
    for agent in env.agent_iter():
        obs, reward, terminated, truncated, info = env.last()
        action = None if terminated or truncated else int(
            np.flatnonzero(obs['action_mask'])[0])
        env.step(action)
finally:
    env.close()
```

The executable [two-policy example](../../examples/lab/pettingzoo_aec.py) runs
on CPU, uses separate policy random streams, and defaults to 40 decisions.
It performs no training.

## Scheduling and lifetime

`possible_agents` is exactly `['home', 'away']`: two coaches, not individual
players. Every playable `step` submits one absolute `ActionV1` through
[SimulationSession](session.md), using its current revision. The session resolves
automatic consequences and reports `next_actor`. That value selects the next
coach, including repeated setup decisions, rerolls, and defender interruptions.
There is no chance agent, hidden opponent policy, simultaneous action bundle,
Parallel adapter, or conversion claim. `is_parallelizable` is false: later
choices depend on the state changed by each preceding decision.

`reset(seed=None, options=None)` returns `None` and replaces the session. Its
engine seed is exactly `SeedSpec(seed, 'aec-v1')` (default engine purpose and
component). `None` obtains new OS entropy. Policy and space sampling RNGs are
separate; seed them explicitly when reproducible action sampling is needed.
`options` accepts a dictionary for AEC compatibility but ignores its entries;
it cannot override construction-time configuration or spaces. Invalid seeds
are rejected before replacing the current session.

Natural completion sets both `terminations` to true and both `truncations` to
false. A session decision budget (including zero on reset), execution-step
budget or no-progress stop sets both truncations to true without inventing a
winner. `infos[agent]['end_reason']` preserves the session reason. Session
`max_steps` bounds each decision's engine advancement; `max_decisions` bounds
the episode. These are counts, not wall-clock deadlines. Unexpected engine
execution failures propagate the session exception; callers should close or
reset after such a failure.

After either completion kind, the adapter schedules home then away for one
final `last()` and `step(None)` each. These two calls remove both coaches from
`agents` and the AEC dictionaries; `agent_iter()` then ends. A non-None terminal
action fails. None during live play fails without altering state, rewards or
RNG. Stepping before reset, after draining, or after close requires a reset.
`close()` is idempotent, closes the owned session, makes remaining live coaches
truncated with `session_closed`, and leaves final observations readable. Reset
can reopen the adapter. No process, thread, policy or GUI is started or owned.

## Fixed action indices

`ActionIndexCodecV1` is the shared pure layout extracted from Gymnasium v5;
Gymnasium's existing indices and behavior are preserved. Given arena width W,
height H and the maximum configured team roster size P, let B = W*H and
S = 1+B+2P. Enumerate `ActionType` in declaration order, excluding automatic
CONTINUE. The ordinary index is `type_ordinal*S + target`, with target:

- 0 for a type-only action;
- `1 + y*W + x` for a square;
- `1 + B + player_slot` for a player, with own roster first and opponent second.

PLACE_PLAYER uses the tail after `len(action_types)*S`, with index
`offset + player_slot*(B+1) + square`. Square 0 means the reserves. The Discrete
size is `offset + 2P*(B+1)`, fixed across resets for the configured teams/arena.
Coordinates use the [V1 view frame](views.md): the selected coach attacks toward
decreasing x, reflecting x on the supplied away side, and keeping y unchanged.

The session enumerates complete legal ActionV1 payloads. Player IDs map to their
public roster slots. Target IDs (BLOCK, STAB, HANDOFF, FOUL, HYPNOTIC_GAZE) map
to the target's recorded square. Path and skill options are retained in the
exact offered payload associated with the index, not inferred from the integer.
`decode_action(index)` returns a detached **currently legal absolute ActionV1**;
`encode_action(action)` performs the inverse for current legal actions. They
are decision-local operations, not persistent action tokens. Unknown, masked,
non-integer and boolean indices fail. Distinct offered semantic actions that
collide in this layout fail explicitly; they are never silently dropped. This
matches the supported endpoint/skill uniqueness of the session's action codec.

## Primary observations and controller masks

`observe(agent)` returns independent arrays in a Gymnasium Dict space:

- `observation`: a fixed float32 vector, containing only numeric primary-view
  data, for that observer's explicit coordinate frame;
- `action_mask`: int8 MultiBinary, with ones only at the selected coach's legal
  indices and zeros for every non-deciding or finished coach.

This follows PettingZoo's [AEC action-mask convention](https://pettingzoo.farama.org/api/aec/).
The mask is **control**, despite its location beside the model vector. Feed only
`obs['observation']` to a default encoder. Including the mask as a model input
is a separate explicit opt-in under the [channel contract](channels.md); no
implicit enriched features, snapshots, RNG, future outcomes or evaluation
labels enter this vector.

The vector concatenates these [#55 views](views.md) in C order:
entity features, entity value-presence bits, entity row mask, context,
context-presence bits, raw grid features, grid-presence bits, grid playability.
Booleans become float32 0/1. `observation_layout` records each segment's name,
shape and slice. `infos[agent]['view_metadata']` carries view versions, channel
names, units, frame, entity IDs and categorical entity metadata separately.
Decision/revision/next-actor/end-reason fields also remain in info.

The fixed roster and arena determine dimensions. The entity table pads to
`2P+1` rows, reserving one ball row even before kickoff creates the ball. The grid retains geometry;
the entity table retains off-pitch players lost by grid aggregation. These are
reduced numeric views of PRIMARY_PROFILE, **not a lossless ObservationV1 or a
Markov-state claim**. Their documented categorical omissions, float32 rounding,
identity metadata, and context limits still apply. In particular, default
numeric inputs omit categorical weather, phase, skills, role and explicit
actor/team IDs. Clients needing other primary representations can use the
session/view APIs directly. Gymnasium's enriched observation space is not
reused because it includes tactical helper features beyond this primary profile.

## Rewards and verification

For coach i, `rewards[i]` is the change in its own public team score caused by
one accepted session decision, matching Gymnasium v5's default reward. An
opponent score does not subtract reward; a casualty does not generate reward.
Reports, casualty counters and internal procedure steps are not separate reward
sources, so multiple internal reports cannot count the same event twice.

`last()` and `agent_iter()` inherit PettingZoo 1.25.0's AEC implementation.
`last()` returns reward accumulated **since that coach last acted**. Only the
submitting coach's accumulator is cleared before adding the latest reward to
both coaches; consecutive decisions therefore do not replay its old reward,
and an interrupted/waiting coach retains intervening reward. Terminal None
steps clear instantaneous rewards but preserve the other coach's accumulator
until it receives its final reward. Reset clears all rewards.

Run `pytest tests/lab/test_pettingzoo_aec.py tests/ai/test_gymnasium_env.py`.
The adapter tests execute the official
[api_test and seed_test](https://pettingzoo.farama.org/main/content/environment_tests/)
for sizes 1/3/5/7/11, plus full bounded matches with both sides, every enabled
index, masked-action atomicity, direct-session trace equivalence, forced reroll
and defender decisions, numeric reward accumulation, touchdowns, administrative
limits, terminal draining and spawn worker cleanup. Official tests retain their
upstream advisory warnings for Dict observations, the required home/away names,
and absence of a renderer; those are not failures.
