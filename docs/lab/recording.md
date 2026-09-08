# Versioned episode records (DATA-02)

`botbowl.lab.records` defines validated, data-only `EpisodeManifestV1`,
`TransitionV1`, and `EventV1`. Construct them with a plain JSON dictionary or
`from_json(dictionary)`; `to_json()` returns an independent mutable copy. The
records retain canonical UTF-8 JSON bytes internally and expose no engine
references. Constructors and readers reject missing/extra fields and unknown
schema versions. They never load pickle, resolve classes, discover plugins,
import names supplied by a file, or execute callbacks from data.

Run `python -m examples.lab.recording` for one short CPU-only episode and an
input-only read. Pass `--destination /tmp/my-recordings` to retain it. The output
subdirectory `example-17` must not already exist. Final destination names ending
in `.partial` are reserved and rejected before the writer creates any files.

## Capture and causation

Attach `EpisodeRecorder(game, destination, relative_path, ...)` before the first
action of an externally controlled game, with no existing timeline. Attachment
may precede `init()` or follow it at START_GAME. Supply public `episode_id`,
`source_family`, `scenario_id`, both policy identities, and the actual seed plan.
The recorder obtains the initial [rules descriptor](rules.md) from the loaded
inputs, binds initial roster IDs, and specializes the existing [timeline](timeline.md)
admission/settlement hooks. There are no engine/rules/RNG changes.

Use `recorder.advance(action_or_ActionRequestV1)` and
`recorder.execute_macro(macro)`. Rejections through these methods become separate
operational diagnostics, including stale semantic requests. Accepted advances
through Game, ActionControl macros or PolicyDriver also cross the capture hooks;
input rejected before those hooks is diagnosable only through the recorder's
submission methods. Reading never advances the game or chooses an action.

Before admission, the recorder copies the public observation at the *old*
logical decision context. On settlement it copies the post-observation at the
*new* context. It retains every decision, including consecutive choices by one
seat, rerolls, Pro declines with an empty event interval, and defender choices
inside the attacker's activation. No Trajectory/Replay index becomes a decision.
Both snapshots use the fixed `observer_team="home"`: ObservationV1 exposes the
same public facts to both seats, with absolute coordinates. `actor_id` and
`next_actor_id` identify the choosing seats; this canonical observer allows exact
pre/post continuity across seat changes without claiming side normalization.

Primary observations have contiguous, one-based `observation_id`s. Each row
contains `schema_version`, `observation_id`, the exact `TimelineContext`, and an
API-04 primary channel envelope. An identical consecutive context/observation
reuses its ID. The initial snapshot may be `unstarted`; the pre-action snapshot
then records the initialized prompt. Intermediate snapshots retained during a
budget interruption remain valid observations, even if no finalized transition
references them. A snapshot row is never an invented coach transition.

Each transition has exactly these fields:

| Fields | Meaning |
| --- | --- |
| `schema_version` | Integer literal 1. |
| `transition_id` | `[episode_id, branch_id, after.decision_seq]`. |
| `source_family`, `scenario_id` | Required public provenance identifiers, matching the manifest. |
| `before`, `after` | Accepted API-02 contexts. Decision counter increases exactly once. |
| `actor_id`, `action` | Choosing seat and exact accepted ActionV1, including option shapes. |
| `pre_observation`, `post_observation` | References into the physically separate primary file; contexts must equal before/after. |
| `event_start`, `event_stop` | Complete half-open event sequence range `[before.event_seq + 1, after.event_seq + 1)`. Equal endpoints mean no events. |
| `next_actor_id` | Next offered seat, or null when unavailable/terminal. For a pending operational boundary it follows API-02's current actor semantics. |
| `status` | `resolved` or `pending`, preserving timeline admission despite budget failure. |
| `end` | Null normally; `{kind: terminal, reason: game_over}` or `{kind: truncated, reason: <identifier>}` at the episode's last matching decision boundary. |
| `macro_id`, `primitive_order` | Null together for a direct action; parent ID and zero-based actual submitted primitive order for a macro. |

An EventV1 contains exactly `schema_version: 1`, `event_id` (episode, branch,
event sequence), `context`, `decision_seq` (causing action or null), `kind`,
`payload_version: 1`, and `data`. It preserves each timeline report/phase event
once, in emission order. Payloads retain the approved timeline fields, enum
names, rolls, quantities, and local player/team references. Reports, dice and
automatic skills are events, never agent commands. Casualties are neither
filtered nor duplicated. The current payload catalogue is closed; future
explanatory annotations need a supported payload/schema revision (EVAL-06).

Complete validation derives scope identities from the ordered API-02 phase
stream. Activation, team-turn and drive IDs start at null, increment once per
corresponding start event and retain their cumulative value after end events,
half changes and forks. End events must close an opened scope. Half starts
advance from null to 1 to 2 and set round to zero; round-start events increment
within the current half. Reports and other events cannot change these counters.
Every event, decision boundary, stored observation/diagnostic context, macro
boundary, branch parent and manifest boundary must match the scope identities
at its referenced event prefix. This checks the recorded counter contract;
it does not simulate rules, adjudicate phase/gameplay legality or authenticate
a fabricated but internally consistent trace.

Macro rows retain the API-02 parent proposal, before/after context, status,
ordered child decision sequences and interruption context/reason/next order.
Snapshots occur around each primitive, not merely around the entire macro.
If a budget stops a child midway, automatic `advance(None)` updates that same
transition identity using its original pre-observation. The interrupted parent
retains the original operational boundary, even if its pending child later
resolves. Confirmation and full reading require every interruption context to
equal its parent's historical `after`, within the same episode and branch bounds;
a resumed child's later `after` cannot replace it. A rejected action creates
neither a transition nor a gameplay event.
Diagnostics hold only a version, context, exception type name and stable code;
exception messages, tracebacks and the rejected arbitrary payload are omitted.

`finish()` requires natural game over; otherwise call
`finish(truncation_reason="decision_limit")` (or another public reason ID).
An unresolved accepted decision may be explicitly truncated with its actual
partial post-state; it is never represented as resolved. A zero-decision episode
retains initial/final observations, manifest and end reason, with an empty
transition file. Automatic/unowned work can emit events and end an episode after
the last coach boundary; that end belongs to the manifest/final observation and
does not rewrite the earlier transition's causation. Uncaused scenario reports
remain uncaused. Silent edits at an identical logical boundary that break a
transition's pre/post continuity fail complete validation.

Forward `timeline.fork(new_id)` preserves the recorded prefix and records the
new branch's parent context in the manifest. Full validation checks emission
lifetimes and branch-local causation. This reference writer records a single
forward history: persistent rewind/restore is rejected *before* engine checkpoint
restoration, rather than mixing old observations with a rewound timeline.
Portable snapshots, ancestor-prefix import and advanced resume are outside this
implementation; existing unrecorded Timeline/checkpoint behavior is unchanged.

## Storage and authorized input loading

An episode consists only of a manifest JSON and independently stored UTF-8 JSONL
files. `files` records each fixed relative path, schema version, row count, byte
count and SHA-256 of the exact bytes. Channel paths cannot alias one another.

| Channel | Relative path | Contents |
| --- | --- | --- |
| primary | `inputs/primary.jsonl` | Public ObservationV1 snapshots. |
| derived, optional | `inputs/derived.jsonl` | Explicit API-04 aids, keyed to observations. |
| control, optional | `control/inputs.jsonl` | API-04 controller masks/IDs/context. |
| transitions | `control/transitions.jsonl` | ActionV1, actors, logical identities and observation/event references. |
| macros | `control/macros.jsonl` | Parent proposals and actual primitive expansion. |
| events | `events/events.jsonl` | Historical reports and phase consequences. |
| diagnostics | `diagnostics/operations.jsonl` | Operational failures, separate from sporting results. |
| evaluation, optional | `evaluation/targets.jsonl` | API-04 labels/estimates with provenance. |
| privileged, optional | `privileged/audit.jsonl` | Explicit plain JSON audit data. No automatic Game/snapshot/RNG export. |

`append_channel(name, observation_id, data)` adds only the named side channel,
validating its existing API-04 schema. One row per channel/observation is allowed.
The recorder supplies no implicit derived aid, mask, label, oracle or privileged
state. An enriched/mask profile must have all required inputs explicitly supplied
at every stored observation before confirmation.

`EpisodeReader(root, relative_path).read_inputs()` reads the manifest and only
primary plus the derived/control fields authorized by its validated InputProfile.
The default profile selects primary alone. The result separates `features` from
profile/entity metadata and observation/context references. Feed only `features`
to an encoder. Input loading never opens transition, macro, event, diagnostic,
evaluation or privileged files. Historical events also stay outside inputs,
preventing accidental future labels or action results from entering pre-features.
`read_channels(["evaluation"])` loads targets separately; `read_episode()` loads
and validates the complete trace. Partial selection validates the selected bytes,
schemas and input alignment; only a full read claims cross-channel integrity.
All returned containers are copies. API-04's trusted-producer limitation still
applies: these data boundaries do not sandbox malicious Python with Game access.

The manifest has exactly schema version, episode/source/scenario IDs,
initial/final contexts and observation references, `end`, `provenance`, `profile`,
`schemas`, `branches` and `files`. Provenance contains the unchanged #30 `rules`
descriptor, `policies` (`home`/`away`, each with `id` and nullable `version`), and
`seed_plan` (`schema_version: 1`, public `algorithm` ID, nonempty `sources` map).
Sources are bounded plain JSON recipes; the caller owns their correctness.
For example, a legacy Game seeded with 17 records
`{schema_version: 1, algorithm: "legacy-numpy-seed", sources: {engine: 17}}`.
For the accepted lab stream derivation, record its algorithm and actual source
recipes from EpisodeContext's provenance. The reader never dispatches on an
algorithm ID or regenerates RNG. Descriptor/schema/profile/policy/seed identities
stay outside features; advanced RNG state and forced queues belong exclusively
to explicitly supplied privileged data.

## Types, bounds and confirmation

Required nullable fields must be present as JSON null. Missing required keys are
errors. Null means unavailable/not applicable; zero is an actual counter/value;
empty lists mean a known empty collection. ObservationV1's `Presence` masks
retain their exact existing consistency rule. Optional *channels* are absent
from `files` when not supplied, rather than encoded as null blobs.

Logical counters, references and indices are nonnegative signed-64-range JSON
integers (IDs/event endpoints start at 1); booleans and floating point values are
rejected in their place. There is no duration or Hz unit. Half/round and
activation/turn/drive counters retain [API-02 semantics](timeline.md).
Coordinates, score, player attributes and roll quantities retain their documented
[ObservationV1](observations.md), [ActionV1](actions.md) and engine units: arena
squares, touchdowns, movement squares, dice faces and modifiers. The inherited
report `n` field is a nullable scalar union: numeric quantity, boolean flag
(e.g. Dauntless), or a `CasualtyEffect` enum name for casualty/recovery reports.
It is not coerced into a numeric reward. Finite JSON
floating point values are binary64; no NaN/Infinity is accepted. General
provenance/side-channel integers are bounded to `-2**256 < n < 2**256` to retain
256-bit seed recipes. Public identifiers are 1–128 ASCII label characters;
#30 descriptor strings retain their hash/version punctuation.

A record is at most 4 MiB UTF-8, nesting depth 40, 100,000 elements per container,
and 1,000,000 visited JSON nodes. Each channel has at most 100,000 rows; channel
bytes together are at most 128 MiB. Limits apply to writes and reads. Duplicate
JSON keys, broken/trailing JSONL lines, non-regular files, symlinks and relative
paths with traversal, empty components, backslashes or absolute prefixes fail.
The authorized root is caller-selected; filesystem operations assume it is not
concurrently modified by an adversarial writer. Files are plain JSON/JSONL even
when stored values resemble module names or code strings.

The recorder retains bounded copied rows in memory until `finish`. The
`JsonlEpisodeWriter` also implements explicit `append((channel, row))`, `flush`,
`close`, and `confirm` for trusted producers. It creates `<relative_path>.partial`
with a visible operational `status.json`. It writes, flushes and fsyncs channel files,
validates their actual bytes and all cross-references, writes/fsyncs the manifest,
and atomically renames the directory to the final path on the same filesystem.
Existing destinations are never intentionally overwritten. Readers reject any
`.partial` directory, including one containing a manifest before the rename.
Atomic visibility is promised; power-loss durability of the parent-directory
rename and hostile concurrent path races are not claimed.

A writer failure propagates as `RecordingError`, leaves the recording visibly
partial, and prevents further use of that recorder. `close()` without `finish`
never invents a terminal/truncated result or confirms an episode. There is no
action retry on failure and no promise to roll back already executed engine work.
This is a bounded reference format, with no columnar layout, sharding, advanced
resume, mass generation, training, or executable snapshot codec (DATA-07).
