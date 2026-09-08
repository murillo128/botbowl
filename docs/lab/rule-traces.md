# Operational rule traces (EVAL-06)

`RuleTrace` is opt-in instrumentation of the implemented engine. It extends the
DATA-02 `EventV1` envelope with **payload version 2**, in a separate historical
channel. It never adds sports reports, advances an API-02 counter, draws a die,
or drives a procedure. DATA-02 recording without explanations still uses
payload version 1 and needs no trace.

```python
from botbowl.lab.rule_traces import RuleTrace, validate_rule_graph

# game must use external_control=True, before init/any reports.
trace = RuleTrace(game)  # Uses an existing Timeline, or attaches one.
game.init()
# Drive the existing game.advance / ActionControl / PolicyDriver interfaces.
validate_rule_graph(trace.events, rules=trace.rules)
print(trace.status)     # Always inspect complete/reason, even for zero rows.
```

For persistent recording, pass `rule_trace=True` to `EpisodeRecorder`, optionally
with `rule_trace_options={"max_events": 10000, "max_bytes": 16777216}`. It writes
`events/rules.jsonl` and one `events/rule-status.jsonl` row using DATA-02's existing
bounded JSONL writer, hashes, atomic confirmation and full reader. Both files
must be present together. The complete validator checks their contexts against
DATA-02 admission/phase boundaries, participants against the initial roster,
anchors against reports and accepted decisions, and the rule descriptor against
manifest provenance. Supported report/phase anchors must match the declared site,
mirrored outcome fields and participants; phase conditions must match as well.
This compares existing records without re-evaluating rules or equating reused
report dice with newly consumed rolls. A trace failure can accompany a successfully recorded game:
its status explicitly says incomplete. A writer failure still fails episode
confirmation; no action is retried.

## Envelope and payload

| Field | Meaning |
| --- | --- |
| `schema_version=1`, `payload_version=2` | Existing EventV1 envelope, explanatory payload version. Unknown versions fail. |
| `event_id=[episode, branch, "rule", sequence]` | Separate namespace; sequence starts at 1 and increments once per retained explanation. It is **not** API-02 `event_seq`. |
| `context` | API-02 counters at emission. It may be an event prefix with no sports report (including prefix zero). |
| `decision_seq`, `data.decision_id` | Nullable accepted coach decision causing this emission; ID is `[episode, branch, decision_seq]`. Null means unowned scenario/automatic work. |
| `kind` | `rule_decision` records one accepted choice; `rule` records an automatic evaluation/consequence. An automatic skill reroll is not a coach action. |
| `data.event_ref` | Exact DATA-02 sports/phase event `[episode, branch, event_seq]`, or null for a decision/explicit rule site with no report. |
| `data.parent_event_ids` | Earlier explanation IDs from the same episode and branch. Scheduling and procedure continuation supply the links. |
| `data.emitter`, `data.coverage` | Closed emitter registry and `instrumented` or `unsupported` for this emission. |
| `data.rule_id` | `<RulesDescriptor.ruleset_id>:<registered-rule-key>`. The status retains the complete #30 descriptor, including implementation version. This is no certification of an edition. |
| `data.participants` | Named roles using #33 roster slots (`home:0`, `away:0`, …) and seats (`home`, `away`), never engine UUIDs/objects. Unavailable roles are absent. |
| `data.conditions` | Only captured evaluated locals/context at the listed site. Absence means not captured, not false. No completed explanation is synthesized for an unsupported site. |
| `data.modifiers`, `data.threshold` | Evaluated roll's modifier total and target/comparison flags; null when this site has no tested roll. Table bands (e.g. injury 10/8) live in conditions. |
| `data.rolls` | Only newly consumed rolls at the site, copied immediately. Reuse of a roll has `[]`, while a re-evaluated target may still be present. |
| `data.outcome` | Realized report or explicit branch/choice: type, quantity/effect, position, skill. Null denotes an inapplicable field; zero stays zero. |

Unsupported payloads have **only** emitter, coverage, parent IDs, decision ID and
event reference. They contain no rule ID, participants, conditions, thresholds,
rolls or inferred outcome. Resolve an unsupported sports event through its
existing DATA-02 anchor if authorized. Unknown emitters default to unsupported;
no runtime discovery expands the instrumented allowlist.

All records are finite plain JSON, with DATA-02's byte/depth limits and detached
copies. Integers count logical events, dice pips, squares or rule modifiers;
there are no wall-clock or physical-frequency units. `coverage_registry()` returns
a fresh versioned inventory of every current procedure and the exact report,
decision and explicit-signal sites. The registry is closed and covered by a test
that detects newly added engine procedures. Most procedures are unsupported;
a partial procedure supports only its enumerated sites.

## Coverage and limits of explanations

| Family | Instrumented sites | Boundaries |
| --- | --- | --- |
| Movement | Move checks/displacement; dodge, GFI, pickup attempts and movement decisions | Dodge captures actual tackle-zone/tail/ignore inputs; weather and evaluated modifier totals are retained. Tentacles, Shadowing, Leap and other skill procedures remain unsupported. |
| Block | Block dice and selection, resolved face, push choices/displacement/stops, follow-up choices/staying/movement | Skill reports outside the allowlist remain unsupported. Die selection creates no new consumed die, even though the legacy report constructs a synthetic die. |
| Pass | Ball pass, interception candidate choice/attempt, catch | Bombs and thrown players remain unsupported. Safe Throw skill reports are unsupported; an initial interception is an attempt result, not a promise of final possession. |
| Injury | Knockdown, armor result, injury-table branch, casualty/decay, KO, Apothecary choices/re-roll/selection/application, regeneration | Armor captures its ordinary comparison and the evaluated Claws predicate/threshold; this is not a complete per-skill derivation. Injury captures the tested niggling total before the legacy report projection. Fixed casualty outcomes still disclose the D68 actually consumed by the engine. |
| Rerolls | Coach choice (including Pro decline), automatic resolution, Pro and Loner rolls | Failed attempt → optional choice → resolution → next attempt remains reconstructible. Automatic skill rerolls share the original decision. |
| Turn/drive | API-02 phase boundaries, turn choices, turnover and touchdown | Phase counters retain #52 semantics. Other kickoff/pregame/end-game reports are marked unsupported. |

Armor's `threshold` describes the direct ordinary `armor_total >= target`
comparison, with no automatic success/failure extremes. On non-fouls,
`claws_total`, `claws_comparison`, `claws_threshold` and `claws_threshold_met`
record the evaluated raw-roll comparison (`>= 8`); `claws` is the full
short-circuited predicate, including the inflictor/skill check when reached.
Those Claws fields are absent on fouls, where that predicate is not evaluated.
For Claws against AV9 with dice `[4,4]`, the ordinary target stays 10 while the
Claws predicate succeeds at 8. The historical roll remains unchanged, including
its legacy DiceRoll flags; those flags are not armor's evaluated semantics.

The graph describes **operational antecedents in the simulator**: report order
inside a procedure, scheduling of children, and continuation after children.
It is not a causal effect estimate, tactical explanation or counterfactual model.
Sibling procedures may be operationally ordered even when their sporting effects
are independent. A parent explains how a procedure was reached, not that every
parent was a necessary sufficient cause of its sporting outcome. Optional choices
remain separate nodes; they are not inevitable consequences of a preceding roll.
There is no natural-language provider or strategic/oracle annotation API here.

Dice are public **historical** data in the explicit event channel. The input-only
reader does not open either trace file; these are not added to #48 encoder
profiles. No engine object, RNG state, forced-roll queue, future, oracle label,
callback exception text or arbitrary attached field is serialized. Existing
`privileged` and `evaluation` channels retain their separate authorization.

`status.complete` means every emission/choice was captured through its stated
prefix, including unsupported markers; it does not mean all rules are explained
or the game ended naturally. Complete reading rejects missing/duplicate anchors,
missing/future/cyclic/cross-branch antecedents, incompatible versions, foreign
participants and a partial capture relabelled complete. It validates internal
references, not the authenticity of producer-supplied game facts.

Default limits are 10,000 trace rows and 16 MiB of encoded rows. Retention and
procedure/roll bookkeeping are bounded. Event/byte exhaustion or a failing
consumer stops capture without stopping/repeating the engine action. Status keeps
only a fixed failure code. A consumer gets a detached row once; it is trusted
local code, not a sandbox against a callback that independently owns/mutates Game.
Forks retain their historical prefix but start new ancestry with no cross-branch
links. Rewinding a timeline explicitly makes the attached trace incomplete;
the snapshot API explicitly rejects a live trace instead of silently dropping it.
Recording a restored run requires a fresh trace/recording. No snapshot codec or
cross-branch causal semantics are introduced here.

## Validation

`pytest tests/lab/test_rule_traces.py` checks deterministic sites, exact dice and
conditions, role IDs and graph edges; choices and automatic rerolls; composite
block-to-injury reconstruction; casualty/reward multiplicity; trace-on/off state,
RNG, reports, timeline and forward-model trajectory; persistence corruption with
truthful hashes; limits, consumer failure and selective-reader redaction.
The bounded overhead corpus covers sizes 1/3/5 with fixed seed 17 and scripted
one-round games; it records timings/counts/bytes without a noisy CI threshold.
It is a bounded diagnostic corpus, not a full-game performance guarantee.
