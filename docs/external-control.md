# External decision control

Construct `Game(..., external_control=True)` to control both teams from outside
the engine. This choice is independent of `Agent.human`. Set it at construction;
changing it midway through a procedure is not supported. Existing games default
to legacy control, including automatic bot play from `init()` and `step()`.

```python
import botbowl as bb

# Teams, agents and config are the usual Game constructor inputs.
game = bb.Game("example", home_team, away_team, home_agent, away_agent,
               config, external_control=True, seed=17)
game.init()  # Always waits at START_GAME, including with two bots.
result = game.advance(bb.Action(bb.ActionType.START_GAME))
choices = game.get_available_actions()
next_agent = result.actor
next_team = game.active_team
new_reports = result.events
finished = result.terminal
```

`advance(action)` accepts one decision, validates it before mutation, resolves
automatic consequences and stops at the next offered decision. It never calls
`Agent.act`, ignores `fast_mode`, and does not enforce competition wall clocks.
Consequences can include several procedures, dice rolls and reports. Two decisions
by the same team are separate calls, even when there are no intervening reports.
The actor owns the offered choices; it can be the defender during the other
team's turn. A sole `END_PLAYER_TURN` choice is also exposed in external mode.

`DecisionResult.events` is an ordered tuple of the new `Outcome` objects from
`game.state.reports`, including final reports when the game ends. It is not a
serialized replay or a copy of the game. Terminal results have `actor=None` and
no available actions. A subsequent `advance(None)` returns an empty terminal
result without invoking callbacks again. Other terminal input raises
`InvalidActionError` with code `game_over`.

Initialize first. Before terminal, `None` or `CONTINUE` is accepted only when
there is no pending decision. Invalid actions leave state, RNG (including forced
dice), reports, procedure stack, clocks, replay and the caller's `Action` intact.
This guarantee covers rejected input, not exceptions inside a procedure or a
user-supplied policy/callback. `step()` in external mode delegates to `advance()`
and retains its historical `None` return value.

## Rerolls and lifecycle

External games offer the same choices and resources for humans and bots:

- Automatic skill rerolls (Dodge, Sure Feet, Catch, Pass, Sure Hands) remain
  automatic under their existing eligibility rules.
- When available, Pro is offered first. Declining Pro exposes the team reroll
  as a separate decision when the team has one. A completed Pro roll resolves
  without a redundant decision. Failed Pro can itself use an eligible team reroll.
- Team rerolls offer `USE_REROLL` and `DONT_USE_REROLL`; block-die selection follows
  afterward. Loner, resource consumption and the prohibition on rerolling a
  rerolled roll are unchanged.
- Undo at player-action selection is available equally to both kinds of agent.

Legacy presentation of reroll choices is retained in default-mode games. Pro's
previously missing dice-report type is now `RollType.PRO_ROLL`; its success target
remains 4. No existing enum value is renumbered.

`init()` is idempotent. Lifecycle callbacks retain the existing convention:
`new_game` and `end_game` are called once per non-human seat, including in external
mode; plain human `Agent` instances need no callback implementations. These
callbacks belong to `Game`, not the policy driver. They and persistent replay
output are not rolled back by forward-model checkpoints. External control does
not introduce new wall-clock/end-time semantics.

## Explicit policies and traces

```python
driver = bb.PolicyDriver(game, {
    game.state.home_team.team_id: home_policy,  # callable(game) -> Action
    game.state.away_team.team_id: away_policy,
})
result = driver.run(max_decisions=100)
trace = driver.trace
```

The driver requires external control. It calls only the supplied policies,
regardless of `human`, and uses `advance()` for each accepted action. A missing
policy pauses at that team's decision. The optional bound counts accepted
decisions; without a bound it continues until a missing policy or terminal.
`run()` returns the last advance result, or an empty-events result when it did
not advance. The trace is cumulative: every entry contains choosing agent/team
IDs, offered choices, normalized action, new events, next actor and terminal flag
as detached JSON snapshots. It does not snapshot the whole game, policy state,
RNG or clocks, and is not a portable replay format. Rejected input adds no entry.

Legacy `init/step` use the separate compatibility scheduler in `core/driver.py`,
which preserves human pauses, slow-mode ticks and competition clock handling.
Existing environment and competition consumers continue using that default path.
