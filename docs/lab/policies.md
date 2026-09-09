# Reference policies and experience coverage (DATA-06)

`botbowl.lab.policies` extends the two DATA-01 policies through a closed registry
of trusted factories. `PolicySpecV1` records name/version, normalized configuration
and SHA-256 digest, `ActionV1-1`, the exact input profile, a materialized private
`SeedSpec`, and the supported state protocol. `create_policy(spec)` validates all
fields against the installed catalog before constructing anything. It never
imports modules, loads pickles, or discovers plugins from manifest strings.
Specs return defensive JSON copies. Seeds and captured state are privileged
provenance, not policy inputs. Pin engine/dependencies as well as these versions;
cross-version/backend autonomous reproduction is not promised.

```python
from botbowl.lab.policies import PolicySpecV1, create_policy, policy_inputs
from botbowl.lab.randomness import SeedSpec

spec = PolicySpecV1('possession',
    SeedSpec(72, 'episode-1', 'policy-home', 'possession-v1'),
    {'error_rate': 0.1})
policy = create_policy(spec)
features, control = policy_inputs(session.observe().primary)
action = policy.act(features, session.legal_actions(), control)
state = policy.capture_state()  # Data-only PolicyStateV1; store outside features.
restored = create_policy(spec)
restored.restore_state(state)
```

Each episode and seat needs its own instance. Closing prevents further decisions.
`capture_state()` includes the exact spec, closed flag, last action type and the
complete private NumPy MT19937 state (including Gaussian cache). Restoration
validates into a candidate before changing the instance and rejects another
seed/configuration/version. To resume autonomously, the caller must separately
restore the matching simulation state and action sequence boundary; restoring a
policy does not rewind a session. The generator continues to support supplied
external-action replay without constructing policies.

## Catalog version 1

| ID | Fixed behavior and bias |
| --- | --- |
| `random` | Uniform over the **N complete semantic actions** in `LegalActionsV1.actions`: each has probability 1/N. A type offering k complete actions has probability k/N. Macros are excluded. Legal no-ops remain possible. |
| `scripted` | Unchanged DATA-01 lifecycle smoke policy: setup formation, deterministic pregame, end setup/turn, first-offered fallback. |
| `possession` | Prefer starting the carrier, otherwise a player nearest the visible ball; move the carrier toward the opponent endzone, or move toward the ball. |
| `cautious` | Same possession heuristics, with lower priority for block/blitz/pass/leap/foul action types and preference for spending an offered reroll. |
| `risk_taking` | Same possession heuristics, with higher priority for those action types and preference for declining a reroll. |

All styles prefer a shipped setup formation before ending setup, stand up and
favorable block faces before unfavorable ones, and finishing player/team turns
when higher-ranked actions are unavailable. Ties choose the first offered action.
Home advances toward x=1 and away toward width-2 in shipped arenas. Distance to
the ball is Manhattan distance, capped in the ranking; unavailable position or
carrier information falls back to action-type priorities. These are small
heuristics, not strong bots, search, calibrated risk estimates or a human model.
In particular, `cautious` does not calculate dodge/GFI probabilities or guarantee
a safer result. It does not require derived tactical planes.

Only the three new styles accept `error_rate`, a finite number in [0,1], default
0. On that fraction of decisions a private RNG chooses uniformly among the same
legal complete actions, possibly selecting the heuristic's own favorite. Thus
`error_rate` is an exploration probability, not a measured error frequency.
No style synthesizes an illegal action to simulate mistakes. The two baseline
policies retain empty configuration and their prior decisions/seed streams.

The standard call receives projected `PRIMARY_PROFILE` features, detached
`LegalActionsV1` control, and four declared identity control fields: player IDs,
player teams, ball carriers and the active player. These fields connect legal
player choices to public observations and are not encoder features. No `Game`,
RNG, snapshot, evaluation target, future event or origin metadata enters the
standard call. `policy_inputs()` uses the API-04 allowlist projector. Missing
optional tactical data has deterministic fallback behavior; unknown feature or
control keys are rejected. As with API-04, this is an API boundary for trusted
code, not a Python sandbox.

`LegacyRandomAdapter` is explicitly separate from the manifest-loadable catalog.
It wraps the existing `RandomBot` through `act_game(game, action_control)` and is
labeled `privileged-game-copy` and `external-actions-only`. The bot receives a
detached Game with a private dice source; chosen engine actions are mapped back
to current legal semantic actions. Neither live engine RNG nor live players are
passed to the bot. The legacy type-first distribution (and exclusion of player
placement) differs from uniform complete-action sampling. No equivalent input
access or state-restorable autonomy is claimed. The adapter rejects a decision
with no supported legacy choice; it does not retry or silently substitute a bot.

## Generation, evidence and coverage

The existing generator accepts all five IDs, JSON `--home-policy-config` and
`--away-policy-config`, and an optional `--episode-prefix` for unique identities
when combining jobs. New plan/dataset format 2 adds per-seat `policy_specs`;
format-1 baseline plans remain validatable, executable and replayable. The
unchanged DATA-02 manifest retains its closed id/version provenance. Full specs
and per-decision option counts live in the separate privileged generation row;
the dataset summary includes scenario, both specs and episode coverage. These
metadata never enter recorded primary inputs. Policy changes leave the engine,
scenario and other seat's seed recipes unchanged.

```sh
python -m botbowl.lab.generate generate --output /tmp/policy-sample \
  --episodes 2 --scenario pickup --home-policy possession \
  --home-policy-config '{"error_rate":0.1}' --max-decisions 8
python -m botbowl.lab.generate validate /tmp/policy-sample
python -m examples.lab.policies
```

`option_counts()` counts every enumerated semantic action by type before each
admitted decision, its actor, and its selected type. `episode_coverage()` starts
fresh for every episode, with decision/horizon/automatic-step budgets and, for
each side:

- decisions D, total available complete actions A and mean A/D;
- per-type complete actions available, decisions offering the type O, and
  selections S;
- availability O/D, selection S/D, and selection given opportunity S/O.

Empty denominators are `None`, never fabricated zero success rates. Types absent
from all decisions are omitted. Accepted pending transitions count once. Public
event occurrences come from the validated recorded event rows, including the
synthetic initial phase prefix. `validate_dataset()` recomputes coverage, matches
selected types/actors against transitions, and verifies persisted hashes/specs.
Available-option counts are trusted collector instrumentation; validating them
against engine legality independently requires replaying the same engine.

`coverage_report(episode_summaries)` adds sample episode/decision counts, all
budgets, and groups by scenario, side, policy name/version/config digest. Event
keys distinguish lifecycle events and `report:OUTCOME_TYPE`. The report separates
**supported** instrumented events, **reached** events and **unexercised** events.
Support describes the timeline/report vocabulary, not reachability in every
scenario or complete rules-engine support. Each event reports occurrences,
episodes containing it, and that episode count divided by *all* sample episodes,
including zero-decision ones. A missing event in a small sample is not evidence
of impossibility. Callers must exclude duplicate/replayed samples before
aggregation; the report does not establish sample independence or human
representativeness.

[`examples/lab/policies.py`](../../examples/lab/policies.py) fixes four scenario/style
pairs and scenario/style test holdouts before collecting eight episodes, each
with at most eight decisions and 1,000 automatic steps per decision. It uses
DATA-04 `origin_from_episode`, `build_split_manifest`, and validation so whole
origin families stay together and the touchdown scenario/risk-taking style are
held out in test. Test membership is not used to tune policies. This is a bounded
comparison demonstration, not a fitted benchmark or a claim that eight episodes
are a representative distribution. Collection and split data remain outside Git.
