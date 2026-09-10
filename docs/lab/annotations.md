# External annotations (EVAL-08)

`botbowl.lab.annotations.AnnotationBundleV1` is an immutable, data-only exchange
format. The opt-in [research viewer](research-viewer.md) imports these bundles
without changing a Game, its RNG, original predictions or standard dataset inputs.
There is no provider SDK, model, fitting implementation or text-to-tool execution.

## Reproduce and view

```sh
python -m examples.lab.annotations /tmp/botbowl-annotations-demo
python -m examples.lab.research_viewer /tmp/botbowl-annotations-viewer
```

Use new destinations. The first command writes a seeded factual replay upload,
`annotations.json`, and `synthetic-export/` containing PNG images, `fragment.json`
and `manifest.json`. It supplies all five kinds using artificial row indices,
including deliberately reversed player order and an HTML-looking text note.
Game IDs are freshly allocated per run; seeds, row values and decision positions
are reproducible. No transformation is trained and no evaluation claim is made.

The second command starts the local viewer with a token chosen at its hidden
prompt. Connect, upload the **first** demo's `factual-upload.json`, and select that
replay in **Annotation / export replay** before uploading its `annotations.json`.
Navigate to decision 1. Each bundle has an external-layer checkbox. Numeric rows
join to the replay by stable player ID; the projection plot is accompanied by an
accessible entity/coordinate table. Notes, source strings and metadata render as
text. Moving away from a target hides its decision layers. Interior event records
appear with their event detail and explicitly refer to the preceding board.
Choose **Event replay** before searching events to inspect the factual replay or
one of its branches, independently of the comparison panels and import selector.
The selected event shows that replay's exact context, preceding recorded board
and external layers; equal event counters in other branches do not share layers.
At a branch's initial boundary, layers use that branch's recorded context even
while its board displays the shared factual prefix. They remain isolated from
the factual panel and other branches.

## Wire format

The top-level fields are exactly `format: "AnnotationBundleV1"`, integer
`schema_version: 1`, `bundle_id`, and a nonempty `items` list. Each item requires:

| Field | Meaning |
| --- | --- |
| `annotation_id` | Unique within the bundle; bundle IDs cannot be reimported into the same replay. |
| `episode_id`, `origin_family_id`, `branch_id` | Exact replay identities, not a library upload ID. |
| `target_kind`, `target` | `decision` or `event`, plus the complete recorded TimelineContext. Decision/event IDs are the tuple `(episode_id, branch_id, decision_seq/event_seq)`. |
| `entity_ids` | Nonempty, unique, ordered stable player/team IDs, including off-pitch players. Balls have no stable ObservationV1 ID and cannot be annotation targets. |
| `kind` | `embedding`, `probe_output`, `projection_2d`, `entity_score`, or `human_note`. |
| `method` | Data-only `method_id` and `version` identifiers. |
| `issued_at` | Complete context of an accepted decision boundary in this replay. |
| `horizon` | Nonnegative absolute distance between emission and target decision counters; event order is additionally checked. |
| `retrospective` | Boolean. Late emission is accepted only when true and displayed as retrospective. |
| `provenance` | `source` text, `access` (`public` or `privileged`), and `fitting`. |
| `data` | Numeric matrix declaration or a nonempty text note of at most 4096 characters. |

An emission or target must match **every** recorded context field. Event targets
must exist in ReplayV1 and use their recorded context; their board is the preceding
restorable boundary, not a fabricated event-state snapshot. A prior/contemporaneous
item cannot have a later decision counter **or** event counter than its target.
Emission times and provenance remain external declarations, not cryptographic
proof that a model actually ran before the outcome. Retrospective does not waive
identity, membership, shape, fitting or bounds checks.

Numeric `data` fields are exactly:

```json
{"format":"numeric_json","shape":[2,1],"dtype":"float64","nbytes":16,"values":[[20],[10]]}
```

Rows correspond to `entity_ids` in the supplied order. `bundle.aligned(id, ids)`
returns a new NumPy array joined by ID in the requested order. Dtypes are limited
to `float32`, `float64`, `int32`, and `int64`; `nbytes` is the declared decoded
matrix size, not JSON file length. All scalars must be finite and representable
in that dtype; integer dtypes require JSON integers. Float32 conversion may round.
JSON integer scalars are additionally limited to ±(2^53−1) so the browser's JSON
transport cannot silently round an imported or exported integer.
Projections require two columns and entity scores one. Embeddings/probes accept
one or more columns. Empty, ragged, oversized, object and boolean matrices fail.

V1 limits: 1 MiB per canonical bundle/file, 128 items per bundle, 16384 elements
per matrix, and the viewer's existing aggregate byte/record limits per replay.
The loader reads at most 1 MiB plus one byte. It rejects duplicate JSON keys,
unknown fields/versions, truncated files and non-finite JSON. **Only numeric JSON
is supported.** NPZ (including pickle-requiring arrays), pickle, compression,
module loading, scripts, active URLs and archive extraction are not supported.
Strings that resemble module/script names are still inert text.

## Fitting and access boundaries

Projection/probe items must declare `fitting`; other kinds may use null. A fitting
object has exactly `split_manifest_id`, `split_version`, `fit_partitions` (unique
partition names, possibly empty for an unfitted method), `evaluation_partition`,
and boolean `held_out`. Names/versions reference the producer's DATA-04 split
manifest. If `held_out` is true, neither `test` nor the declared evaluation
partition may occur in `fit_partitions`. Test-fitted exploratory transforms may
be imported with `held_out: false`; their fitting data remains visible. This checks
metadata consistency, not the truth or completeness of an external fit declaration;
it does not independently authenticate the referenced split manifest.

All research routes retain the existing token, loopback, Host and Origin guards.
Anyone with a research grant may import public layers. Only evaluators may import
or read privileged layers; mixed bundles are filtered per item for player and
spectator responses. Ordinary `store.summary(id)` reads are public by default.
Privileged imports stay in the research store: no channel, standard reader feature,
observation field or executable replay file is added. Operator grants still define
one shared research workspace, as in the existing viewer.

## Import and export API

- `POST /research/replays/{id}/annotation-bundles`: validate and atomically retain
  one AnnotationBundleV1. Invalid items reject the whole bundle.
- `GET /research/replays/{id}/annotation-bundles`: role-filtered bundles.
- `POST /research/replays/{id}/export` with `{"decisions":[1,2]}`: one to sixteen
  sorted, unique recorded decisions. The UI exports its current decision.

Exports are bounded JSON envelopes (`ResearchExportUploadV1`, `encoding: base64`)
with fixed generated filenames in `files`. The manifest (`ResearchExportV1`)
links each synthetic PNG to its exact frame index/context/entity IDs and SHA-256,
and enumerates the included event IDs and their exact contexts.
It links the fragment by hash and records replay ID, origin family, source
provenance and the canonical source manifest hash. The fragment (`ResearchFragmentV1`)
contains public observations at selected boundaries, events whose recorded
decision counter is selected, and visible annotations targeting those frames or
events. Each annotation retains its original entity order, target, emission,
horizon, method, access and fitting provenance. Manifest annotation entries link
bundle/item IDs to those same contexts and provenance.

These fragments are inspectable view data, **not executable ReplayV1**. There are
no checkpoints, RNG values or snapshots. PNGs use only the original geometric
renderer; external layers are preserved separately in the fragment and are not
painted into the engine image. No legacy artwork is loaded or redistributed.
Evaluator exports can include privileged annotations; spectator/player exports
cannot. External scores, attention and projection coordinates do not establish
causal structure or model understanding.

## External natural-language clients

A separately operated client may translate a user's question into read-only
structured calls to the existing authenticated research frame, event and bundle
routes. It must use its own granted token, honor the public/privileged distinction,
and cite replay/origin/branch, exact context and annotation provenance in answers.
The server accepts no natural-language command endpoint and does not interpret
notes as instructions, dereference their URLs, execute their text, provision keys,
or grant additional permissions. Model/provider selection, credentials, prompts
and any client-side processing belong outside Bot Bowl. A text request confers
no authority to fork, restore or execute a game command.
