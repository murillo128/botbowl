# Action validation evidence

This change implements the validation boundary in issue #7. The engine baseline
is `26989d87ac95c8354c68dc08e2d39aad4d995e9e`, including the reviewed issue #4
fixtures. No game rule, Gym encoding, pathfinding backend, or HTTP behavior is
changed.

The implementation branch subsequently advanced to
`036073d69abbbbc7c2bf03434f162ff5ed687036`, which adds only the user's CI workflow
update to that baseline. The engine and test inputs are identical between those
two revisions; the CI change is inherited from the PR base.

## Regressions demonstrated before implementation

The first seven tests in `tests/framework/test_action_validation.py` were run
against the baseline engine before changing its implementation. All seven failed:

| Regression | Baseline observation |
| --- | --- |
| Single eligible player | A different teammate was accepted |
| Repeated ActionChoice type | A legal player in a later choice was rejected |
| Query by square | The query wrote `action.player` |
| None at a pending decision | The query returned true |
| CONTINUE at a pending decision | `step` returned without reporting rejection |
| Rejected step with copied references | Caller references and `game.action` changed |
| Negative x coordinate | `(-1, 3)` aliased the final column, passed validation, and reached the procedure |

The baseline command was `python -m pytest tests/framework/test_action_validation.py
--require-pathfinding=native -q`: **7 failed in 0.27 s**. The snapshot helper
compares semantic state, serialized procedure/game graph, RNG state, trajectory
and report lengths, `game.action` identity, and replay state. Clock JSON is omitted
because it computes elapsed wall time on each read; the raw clock state remains
covered by the serialized graph. Input bytes and relevant object identities are
also compared. No baseline expected game outcome was weakened.

Two additional real uphill-block probes (use or decline the reroll) reproduced
acceptance of a disabled defender die result before the enabled-choice correction:
**2 failed, 1 passed in 0.14 s** for the disabled-choice and same-type-alternative
tests. Disabled dice must wait for the attacker's reroll decision. The corrected
checks require atomic rejection before that decision and acceptance afterward,
and retain an enabled alternative when another choice of its type is disabled.
Gym's existing simple-action mask now excludes disabled choices as the narrow
caller integration; action indices, space shapes, and encoding stay unchanged.

Enforcing those existing disabled flags exposed eight inherited Frenzy/Strip
Ball cases that selected a disabled uphill result without first declining the
reroll. Their sequences now submit `DONT_USE_REROLL` explicitly before each such
selection, including the second Frenzy block. All original fixed dice, resources,
positions, and rule-outcome assertions are retained; the tests no longer rely on
the illegal-input bypass.

The forward-model test policy now samples only enabled choices without changing
its RNG source, PLACE_PLAYER exclusion, player/position sampling, or state/revert
assertions. Clock-forced selection likewise filters disabled choices in its
priority scan and every fallback pool, retaining the existing priority and
sampling structure. The added real uphill forced-action assertion failed in both
reroll branches before this correction (**2 failed, 50 deselected in 0.10 s**):
the forced result was disabled SELECT_DEFENDER_DOWN. It now requires legal
DONT_USE_REROLL while the attacker's decision is pending.

## Environment and commands

Linux x86_64; CPython 3.11.16; pip 24.0; setuptools 79.0.1; wheel 0.40.0;
pytest 7.3.1; NumPy 1.24.3; Gym 0.26.2; Cython 3.0.0b2; Requests 2.30.0;
Packaging 23.1. A separate virtual environment and editable install pointed to
this issue's checkout. Installation used the inherited requirements and the
repository-native `python setup.py build` followed by `python -m pip install -e .`.
`python -m pip check` passed after applying the baseline's Wheel 0.40.0 tooling
constraint. The known requirements/setup Requests pin mismatch remains #5/#6's
work.

```bash
timeout 300 python -m pytest tests/framework/test_action_validation.py tests/framework/test_forward_model.py tests/game/test_block.py tests/game/test_frenzy.py tests/game/test_strip_ball.py tests/ai/test_env.py tests/ai/test_competition.py --require-pathfinding=native -q
timeout 300 python -m pytest --require-pathfinding=native -q
```

The final focused compatibility run passed **94 tests** with 102 inherited Gym
warnings in **34.10 s**. The final full native run includes all **52** new
validation tests: **601 passed, 1 xfailed, 102 warnings in 74.18 s**.

The Python reference run used a fresh process with the compiled extension moved
outside the checkout and restored afterward, following the
[baseline backend procedure](baseline-issue-4.md). Its command was:

```bash
python -m pytest tests/game tests/framework tests/ai --require-pathfinding=python -q
```

Result: **409 passed, 39 skipped, 1 xfailed, 102 warnings in 169.42 s**.
The 39 skips are the baseline's explicitly native-only cases. Both backend
assertions passed before collection. No new skips or xfails were added.

## Compatibility and limits

The public result reports stable error codes/messages, and `InvalidActionError`
carries the same code. The competition test now checks the new stable unavailable
action message instead of the old serialized-action prose; the competition layer
still owns printing the exception. Queries and public rejection do not print.

The focused suite covers zero/one/multiple eligible players, duplicate choices,
copied IDs/teams, malformed types and coordinates, pending/automatic decisions,
setup's `None` destination, legal board edges, implicit singleton skill choices,
caller-reference preservation, equivalent player/square execution, opponent
targets, bot normalization, and propagation of internal errors. A separate
`python -O` subprocess uses explicit checks to verify public rejection with
assertions disabled. The full suite also exercises existing actions, scripted
bots, ProcBot, environments, competition and web server startup.

The web smoke executes a valid action through Flask's test client and rejects an
invalid action through `api.step`. It does not establish HTTP request atomicity:
the inherited endpoint calls `api.get_game`, which refreshes human games before
validation and again after an exception. That known boundary is recorded in
[issue #22](https://github.com/murillo128/botbowl/issues/22#issuecomment-5566905595).

The timestamp xfail is the existing issue #16 defect; the Gym warnings and
adapter conformance remain issue #17's work. Validation uses the procedure's
already-published choices, does not regenerate rule decisions, and does not roll
back a valid action if subsequent procedure execution has an internal failure.
Raw install/build/test logs and JUnit XML stay outside Git.
