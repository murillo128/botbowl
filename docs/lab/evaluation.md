# Evaluation oracle V1

`botbowl.lab.evaluation` provides explicit evaluation over a detached
`EvaluationContext`, a closed `LABEL_SPECS` catalogue, and validated
`EvaluationRecord` sidecars. Import and invoke it explicitly in trusted control
code. It is never registered with a simulation, observer, recorder, policy,
encoder or default feature profile. There is no sampler, model training, reward
change, plugin registration, dataset-directed import or callback execution.

## Capture and query

Create the [rules descriptor](rules.md) from effective **initial** inputs and
retain it for the episode. Reuse its existing `ObservationControl` binding and
[TimelineContext](timeline.md); do not construct a second timeline or substitute
a wall clock. When the Game has a timeline, capture rejects a foreign or stale
context. Without a timeline the controller supplies the episode/branch and
logical boundary; the oracle cannot authenticate that external identity.

```python
from botbowl.lab.evaluation import (
    EvaluationContext, EvaluationOracle, LabelRequest, evaluation_channel,
)

# game, observation_control, timeline and initial_rules belong to the controller.
context = EvaluationContext.capture(
    game, observation_control, timeline.context,
    [LabelRequest("team.score", "home"),
     LabelRequest("player.position", "home:0"),
     LabelRequest("block.attacker_down", "home:0", "away:0")],
    rules=initial_rules,
)
records = EvaluationOracle().evaluate(context)
for record in records:
    if record.unavailability is None:
        print(record.spec.name, record.value, record.spec.unit)
    else:
        print(record.unavailability.code, record.unavailability.reason)

# Optional explicit sidecar attachment to the matching recorded observation:
recorder.append_channel("evaluation", observation_id, evaluation_channel(records))
```

`EvaluationOracle` structurally implements the existing `Evaluator` protocol.
Capture performs the observation read and, only for requested block pairs, the
validated engine helper. It copies those results into bounded canonical JSON
bytes. The context retains no Game, player, Square, live RNG, procedure, policy
or mutable dictionary. `evaluate` reads only those bytes and returns an immutable
tuple of canonical records. Capture and evaluation do not advance, refresh,
consume randomness/forced dice, mutate trajectory or consult/change clocks.
Unexpected internal helper errors propagate; they are not fabricated labels.
Re-querying an old context deliberately returns the old values. Re-capture after
advancing the game. Neither operation attaches itself to Game.

Record `to_json()` and `value` return fresh containers; nested mutation cannot
change the record or engine. `spec`, `context` and `unavailability` provide typed,
immutable metadata. An unavailable value is `None` with an `Unavailability`
code/reason; it is never numerical zero. Unknown label names and malformed
requests raise `UnknownLabelError`/`RecordError`. Valid requests for missing
entities return `unknown_entity`. Unsupported geometry, rules or states return
`off_pitch`, `invalid_pair`, `terminal` or `unsupported_state`. External labels
queried without external data return `external_required`.

## Closed catalogue and meaning

Every label has version **1**, category, value type, unit, entity domain, origin,
logical availability, visibility, observation path (or null) and conditions.
`label_spec(name).to_json()` is the complete machine-readable definition. The
mapping is immutable and has no extension/registration mechanism. Changing a
label's meaning requires a new code-owned version and validation.

All labels have `visibility=evaluation_only`: this describes this API's delivery
channel, **not secrecy**. State facts already present in `ObservationV1` retain
their `observation_path`. Geometry is deterministic arithmetic over its public
coordinates and is also available in the existing graph view. Recovering these
facts is not discovery of hidden information. This catalogue does not claim to
cover every observable field or every engine rule.

| Label(s) | Value and unit | Entity/domain |
| --- | --- | --- |
| `match.terminal` | bool / boolean | `match`; stored flag, including unstarted and terminal |
| `team.score` | int / touchdown | `home` or `away`; stored score |
| `team.possession` | bool / boolean | Side; exactly one coherent ball, true only for its current carrier's side |
| `team.rerolls`, `team.apothecaries`, `team.bribes` | nonnegative int / reroll, apothecary, bribe | Side; stored remaining resource, not action eligibility |
| `team.reroll_used`, `team.wizard_available` | bool / boolean | Side; stored flags |
| `player.position`, `ball.position` | `{x: int, y: int}` / arena_cell | Player or ball on playable pitch; absolute coordinates, upper-left origin |
| `player.location` | str / compartment | pitch, reserves, ko, casualties, dungeon, unplaced |
| `player.up`, `player.stunned`, `player.used` | bool / boolean | Player; stored status, including off-pitch/terminal, not eligibility |
| `ball.carrier` | player ID or null / entity_id | Exactly one coherent ball; null explicitly means loose |
| `geometry.manhattan` | int / arena_cell | Two on-pitch players/balls; `abs(dx) + abs(dy)` |
| `geometry.chebyshev` | int / arena_cell | Same domain; `max(abs(dx), abs(dy))` |
| `geometry.adjacent` | bool / boolean | Same domain; Chebyshev distance exactly 1; same square is false |
| `block.attacker_down`, `block.defender_down` | number / probability | Opposing player pair; exact single-block experiment below |
| `block.attacker_ball_loss`, `block.defender_ball_loss` | number / probability | Same experiment; release by that current carrier before continuation |
| `estimated.win_probability` | number in [0,1] / probability | Side; external estimate of strictly greater **terminal** score (draw is not win) |
| `heuristic.positional_advantage` | number in [-1,1] / heuristic_score | Side; external method-defined score |
| `human.tactical_annotation` | nonempty text, at most 4096 characters / annotation | Player; attributed human interpretation |

The first 18 labels are `state_fact`, four block labels are
`exact_rule_quantity`, and the last three have their named external categories.
Numeric values reject bool, NaN and infinity. Entity IDs reuse initial
home/away roster slots. Ball IDs `ball:0`, `ball:1`, etc. reference the captured
observation list, not a promise of persistent multi-ball identity. Pair order is
retained in keys; block order means attacker then defender.

Possession uses the observation's `is_carried` flag and board carrier. A loose
ball under a player is not possession. A carried ball must coincide with a
registered, grounded player on playable pitch, and its engine `on_ground` flag
must be true. In this engine that flag denotes ground level and can coexist
with `is_carried=True`; it does **not** mean loose. No ball, multiple balls, or
inconsistent/airborne carrying are unavailable. Loose balls need not have a
known playable position for possession to be false. Geometry needs playable
positions; it ignores obstacles, movement allowance, tackle zones and path risk.

## Exact block experiment

The oracle calls `Game.get_block_outcome_probs(..., reroll_policy="never",
already_rerolled=False)` from the accepted [probability-query contract](../probability-queries.md).
It does not sample, inspect forced draws or implement a second block engine.
V1 deliberately accepts a smaller domain:

- Consistent BB2016 configuration/ruleset and unchanged shipped rule tables.
- Nonterminal, registered opposing players at adjacent, playable board positions;
  both standing, unstunned, grounded and unrooted.
- On-pitch skills limited to Block, Dodge, Tackle, Strip Ball and Sure Hands.
- Every neighbour of the defender except the attacker is empty and playable.
  This excludes crowd removal, chain pushes, assists adjacent to the defender,
  Side Step, Stand Firm, Grab and other optional skill policies from V1.
- Zero or one coherent ball. No blitz, reroll or predicted continuation.

This is conditional on reaching one ordinary block roll, not the chance that a
legal activation/path reaches it. Stored action availability, attacker `used`,
turn ownership and activation restrictions do not certify block legality.
Custom rules/table monkeypatches are not certified by a descriptor string;
the initial descriptor records provenance, not proof of unchanged engine code.

The physical die has skull, both-down, push, push, stumble and pow. Skull downs
the attacker; both-down downs each participant without Block; stumble downs the
defender unless Dodge without opposing Tackle; pow downs the defender. On a
push-capable result, Strip Ball without Sure Hands releases a defender's ball.
Release from knockdown and Strip Ball is a **union**. Selection uses
`issue8_local_v1`: chooser avoids own down, seeks opposing down, avoids own ball
loss, then seeks opposing ball loss; ties prefer pow, stumble, push, both, skull.
The dice sign selects attacker or defender as chooser. Each marginal is the
number of `6**abs(signed_dice)` tuples whose selected face causes that event,
divided by the total. The four marginals overlap and must not be summed or
renormalized. They exclude armour, injury, follow-up, catches, bounce, future
possession, touchdown and global tactical quality. Runtime output is float,
validated against independent finite counts to absolute tolerance `1e-12`.

The committed enumeration covers 1,920 configurations: both sides, reachable
signed dice `-3,-2,1,2,3`, all 64 assignments of the six relevant participant
skill flags, and three carrier states. There are 195,840 enumerated physical
roll tuples and 7,680 marginal comparisons. Equal strength yields positive one;
negative one is not a reachable engine dice count in this interface.

## External data and availability

`EvaluationRecord.external(context, request, value, available_at=...,
provenance=...)` is the sole convenience constructor for external claims. It
rejects simulator-fact/exact label requests, unknown captured entities, unknown
fields, forged catalogue metadata and invalid values. `from_json` applies the
same structural validation. This validates data and declared provenance, not
the truth/calibration/quality of an external method or the author's identity.

All three external categories require nonblank `policy` and `method` strings,
a logical `horizon={"kind": "terminal", "decisions": null}` or
`{"kind": "decisions", "decisions": N}` (nonnegative integer), and uncertainty.
Use an explicit `not_applicable` policy for a heuristic/human method without a
policy. Win estimates require the terminal horizon and an interval:

```json
{
  "policy": "fixed-baseline-v1",
  "horizon": {"kind": "terminal", "decisions": null},
  "method": "external-rollouts-v1",
  "uncertainty": {
    "kind": "interval", "lower": 0.2, "upper": 0.8,
    "coverage": 0.95, "method": "external-Wilson"
  }
}
```

The interval must contain the estimate and lie in [0,1], with coverage in (0,1].
Heuristics/human annotations instead require
`{"kind": "not_quantified", "reason": "..."}`. They cannot claim exactness.
Method names remain inert strings. No modules, code or models are loaded.
Pressure, positional advantage, spectacularity and tactical quality are not
simulator ground truth; V1 implements no exact labels for those concepts.

`context` identifies the labelled boundary; `available_at` identifies when the
label could logically be used. Internal facts/quantities use the same captured
boundary. External availability is explicit, in the same episode/branch, with
both event and decision sequence at least those of the target. Counters are
controller declarations, not a validated future trajectory. Consumers must
verify those declarations and any attachment to a recorded observation.

## Sidecars and leakage boundary

A record key is `(episode_id, branch_id, event_seq, decision_seq, entity_id,
related_entity_id, label_name, label_version)`. `evaluation_channel(records)`
rejects duplicate keys and returns the existing evaluation channel shape, with
records under `labels.records`. It does not add fields to observations or
manifests. DATA-02 stores this as `evaluation/targets.jsonl`; standard
`EpisodeReader.read_inputs()` never opens evaluation or privileged files.
Explicit `read_channels(["evaluation"])` grants target access; parse each row
with `EvaluationRecord.from_json` before treating it as a catalogue record.
Existing free-form evaluation JSON is not automatically certified by this API.

`project_inputs`, primary/enriched profiles, and entity/graph/grid views retain
their existing closed input schemas. Nested oracle/RNG/future data is forbidden
there. The optional privileged JSON in a context is controller-owned audit data;
evaluating it exports none of that field. The **whole context**, explicit target
channel, or whole-episode privileged reader is never a restricted-policy input.
These are API/data boundaries, not a sandbox for code already holding Game or
permission to inspect private Python objects. Public facts can still appear in
features through their already-authorized observation paths.
