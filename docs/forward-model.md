# Forward Model
Previously, it was difficult to get a fast forward model up and running in botbowl due to the reliance on the slow copy.deepcopy() function. Thanks to amazing work by Mattias Bermell, botbowl now has a built-in forward model that is reasonably fast. At least much faster that what we had before!

It works by tracking changes to non-immutable properties in the game state. Such changes can then be reverted to go back in time, e.g. to reset the state, where we had to completely reinstantiate the entire game object before.

Here's a small example showing how to first enable the forward model, then take some steps in the game, then revert back to the original state and finally revert it forward again: 

```python
import botbowl
from botbowl.core import Action, Agent, ActionType


def print_available_action_types(game):
    for action_choice in game.get_available_actions():
        print(action_choice.action_type.name, end=', ')
    print("\n", "-"*5, sep="")


def main():

    # Setup a game
    config = botbowl.load_config("bot-bowl-iii")
    ruleset = botbowl.load_rule_set(config.ruleset)
    arena = botbowl.load_arena(config.arena)
    home = botbowl.load_team_by_filename("human", ruleset)
    away = botbowl.load_team_by_filename("human", ruleset)
    agent_home = Agent("home agent", human=True)
    agent_away = Agent("home agent", human=True)
    game = botbowl.Game(1, home, away, agent_home, agent_away, config, arena=arena, ruleset=ruleset)
    game.init()

    # Enable forward model
    game.enable_forward_model()
    step_id = game.get_step()

    # Force determinism?
    # game.set_seed(1)  # Force determinism

    # Take some actions
    game.step(Action(ActionType.START_GAME))
    game.step(Action(ActionType.HEADS))
    game.step(Action(ActionType.RECEIVE))

    # Kicking team is random if you didn't set the seed
    print("Home is kicking: ", game.get_kicking_team() == game.state.home_team)
    print_available_action_types(game)

    # Revert state and save the steps
    steps = game.revert(step_id)

    # Print available actions: Should only contain START_GAME
    print_available_action_types(game)

    # step the game forward again
    game.forward(steps)
    print("Home is kicking: ", game.get_kicking_team() == game.state.home_team)
    print_available_action_types(game)


if __name__ == "__main__":
    main()
```
It is important that you initialize the game before you enabled the forward model.

This example script is also available [here](../examples/forward_model_example.py).

With this forward model, we can go forward and the back. After this, we can go forward again, and then back to a previous state on the trajectory. 

We can also forward revert to a state that we previously revert from but the forward model itself does not store _"the history of the future"_. So we have to manage the that ourselves. The forward model makes that easy, `game.revert()` returns the steps that was reverted. And we simply provide them as argument to ´game.forward()` to get back our future state.  

`game.revert(step)` and `game.forward(steps)` undo and redo trajectory-managed
state only. They leave the random generator and forced-dice queues advanced,
preserving the existing search behavior. Reapplying the initial seed restarts
the stream; it does not restore an arbitrary point in that stream.

## Repeating an action sequence

After enabling the forward model, capture an explicit checkpoint to combine the
trajectory position with the complete game RNG and forced-dice queues:

```python
checkpoint = game.capture_checkpoint()
game.step(action)
# Take further explicit actions as needed.
game.restore_checkpoint(checkpoint)
game.step(action)  # Repeats the same game randomness from that checkpoint.
```

Restore is valid only for an ancestor still present in the same game's
trajectory. Checkpoints from another game, a discarded future, or a different
branch at the same step number raise `ValueError`. A checkpoint can be restored
repeatedly while its ancestor remains present. The returned undone steps can be
passed to `forward`, which retains its state-only semantics.

For RNG-only capture, use `state = game.capture_rng_state()` and
`game.restore_rng_state(state)`. This includes NumPy `RandomState`'s MT19937 keys,
position, cached Gaussian, all forced queues, and strict-context modes. Captured
queues and RNG keys are immutable copies; restoring or deep-copying a game does
not share mutable queues with another branch. Outside a forced context, an RNG
state can also be restored into another game's dice source.

These are in-memory replay tools, not persistent or safe cross-process snapshot
formats. They cover the state managed by the trajectory and randomness owned by
the game. They do not restore wall clocks, external I/O, persistent replay
recorders, or bot/policy state. Supply the same decisions to reproduce events;
if a policy chooses those decisions randomly, capture its state separately.
The existing `RandomBot(name, seed=policy_seed)` owns its own RNG.
`game.set_seed(game_seed)` only restarts the game's stream and preserves pending
forced rolls. Drawing policy choices from `game.rng` would deliberately consume
the game stream, so use a separate policy RNG when independence is required.

## Game-local test dice

`game.rng` remains a NumPy `RandomState`; natural dice retain their previous
algorithm and block-die probabilities. Engine dice draw from `game.dice`, which
owns the game's RNG and optional forced queues. Forced results are a testing
facility and must not be interpreted as samples from a natural distribution.

```python
with game.dice.force(d6=[1, 6], block_dice=[botbowl.BBDieResult.PUSH], strict=True):
    # Engine procedures and direct dice consume only this game's current queues.
    assert botbowl.D6(game.dice).value == 1
    checkpoint = game.capture_checkpoint()
    assert botbowl.D6(game.dice).value == 6
    game.restore_checkpoint(checkpoint)
    assert botbowl.D6(game.dice).value == 6
```

`force` replaces all four queues within its context, supports nesting, and
restores the outer queues on exit, including exceptions. Unused inner results
are discarded. Exhaustion raises `ForcedRollExhausted` in strict mode; otherwise
it falls back to the natural RNG. Forced draws never advance that RNG. Natural
draws inside a context remain advanced on exit. Restore a captured RNG state or
checkpoint within the same active forced contexts; expired contexts cannot be
resurrected. Strict mode governs dice, not direct calls to `game.rng`.

For a queue lasting until consumption, explicit clearing, or the game's lifetime,
use `game.dice.fix(botbowl.D6, 1, 6)`. Inspect it with
`game.dice.pending(botbowl.D6)` (an immutable tuple), clear that die with
`game.dice.clear(botbowl.D6)`, or clear all current queues with `game.dice.clear()`.
Numeric dice accept integers in 1–3, 1–6, or 1–8 respectively; booleans and floats
are rejected. Block dice accept `BBDieResult` values. Invalid values raise
`ValueError` before any supplied results are queued.

The former process-global `D3/D6/D8/BBDie.fix`, `FixedRolls`, and
`BBDie.clear_fixes` test hooks are removed. Migrate them to the owning game's
source or a `force` context. Standalone natural dice still accept an ordinary
NumPy `RandomState`; standalone forced-dice tests can construct a `DiceSource`.
