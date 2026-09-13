# Channels and InputProfile V1

`botbowl.lab.channels` separates encoder inputs, controller data and targets.
It consumes plain JSON data, using the accepted [ObservationV1](observations.md)
as `primary`. It never receives a Game, calls a feature provider, advances play,
refreshes actions or queries randomness. The engine and legacy RL environment
are unchanged.

| Channel | V1 data | Consumer |
| --- | --- | --- |
| `primary` | Complete `ObservationV1.to_json()` with exact nested fields/types, literal domains and consistent Presence values | Default model profile |
| `derived` | Optional numeric planes `own_tackle_zones`, `opp_tackle_zones`, `roll_probabilities`, `block_dice` | Explicitly selected model features |
| `control` | Optional `action_ids: list[str]`, `action_mask: list[bool]`, `context: DecisionContext` | Controller; only the mask can be opted into the model |
| `evaluation` | Required JSON objects `labels`, `estimates`, `provenance` | Separate training/evaluation target consumer |
| `privileged` | JSON object for snapshots, RNG, forced queues, future state and internal audit | Explicit storage/audit consumer only |

Create each channel explicitly with `make_channel(name, data, metadata=None)`.
It returns fresh containers with `descriptor`, `data`, and `metadata` keys.
Descriptors contain the channel name, schema name and integer schema version 1.
`channel_schema(name)` returns a copy of its structural schema; `x-presence`
requires `present` to equal `(value is not None)`. Unknown object properties are
rejected throughout primary, derived and control. No arbitrary object subtree
can be selected as a feature. Evaluation and privileged payloads remain
extensible JSON and can never be authorized through an InputProfile.

Channel metadata admits only optional string fields `episode_id`, `source_id`,
`content_hash`, and `provenance`. It cannot contain nested payloads or labels.
Provenance here is a descriptive string; detailed target provenance belongs in
evaluation. A content hash is caller-supplied metadata, not verified integrity.
All inputs must be plain dictionaries with string keys, lists and finite JSON
scalars; Python objects, tuples, NumPy arrays/scalars and NaN/Infinity fail.
Producers convert arrays with `.tolist()` before crossing this boundary.

## Encoder selection

```python
from botbowl.lab.channels import InputProfile, input_profile, project_inputs

minimal = project_inputs(channels)  # PRIMARY_PROFILE
with_mask = project_inputs(channels, input_profile(include_action_mask=True))
one_help = InputProfile("custom", (
    ("primary.players[].attributes.ma", "integer"),
    ("derived.own_tackle_zones[][]", "number"),
))
selected = project_inputs(channels, one_help)
encoder_features = selected["features"]
```

The result has two separate keys: `features` and `metadata`. Feed **only
features** to an encoder. Features map exact leaf paths to JSON scalar/array
values; this API does not define padding, a vocabulary, normalization or a
numeric tensor. `[]` traverses one schema-declared array dimension, keeping
order and nested/ragged arrays. It is not an unrestricted wildcard: `*`, `[*]`,
indices, parent-object selection and unknown paths are rejected. Leaf types
are `integer`, `boolean`, `string`, or `number`; nullable ancestors yield null,
and their separate Presence bits retain the distinction between absence and
zero/false. Empty collections remain empty arrays.

`PRIMARY_PROFILE.fields` is the complete ordered V1 primary feature allowlist.
`InputProfile.to_json()` expands every permitted path/type; `from_json()` checks
it, including the name, version, duplicates and the mask option. The fixed
names `primary` and `enriched` must match their canonical lists. `custom` permits
a subset of the same closed registry, in the caller's declared order. Unknown
profile names/versions and incompatible field types fail. Extending that
registry requires a deliberate API/schema revision, not a path supplied by
an experiment. No categorical-ID exception is defined in V1.

The default contains only primary features. All IDs, roster slots, side
references, observation version and sufficiency declaration are in
`metadata.channels.primary.fields`, aligned to the original entity arrays.
Channel descriptors, source metadata (including hashes/provenance) and the
complete selected profile also stay under metadata. Metadata can identify an
episode and must never be concatenated into the tensor. Public categorical
facts such as race, role, weather, skills and phase remain feature strings.
Presence bits, geometry playability and cached public prompt facts from #33
remain primary data; they are not the optional controller action mask.

The controller reads `select_channels(channels, ["control"])` independently.
Adding `control.action_mask[]` to a model requires both that exact boolean path
and `include_action_mask=True`. Both appear in the serialized profile and
projection metadata. An option without the path, or a path without the option,
fails. Action IDs and controller context cannot be selected as features.

Validation covers the complete schema of each selected channel, even when a
custom profile selects one leaf. Thus an `evaluation` field injected inside a
primary entity is rejected. Missing channels, required schema fields and
selected optional paths are errors. Unselected channel payloads are not
accessed or validated by the projector. This allows primary reads without
loading evaluation/privileged data; it does not certify an unread blob.

## Explicit enriched baseline

`input_profile("enriched")` adds exactly the four named derived planes to all
default primary features. The mask remains a separate opt-in. Planes contain
finite numbers and are supplied by trusted feature producers: tackle-zone
counts, success probabilities, and signed block-dice counts, respectively.
V1 validates structural types, not tactical correctness, bounds, plane sizes
or correspondence to a particular arena/action ordering. Record producer
semantics and ordering in metadata/provenance. It does not calculate these aids.

The existing `botbowl.ai.env.EnvConf` / `BotBowlEnv.get_state()` remains the
explicit **legacy enriched RL baseline**: available-position layers, tackle
zones, roll probabilities, block dice, attributes and other layers are stacked;
non-spatial features and a flattened action mask are returned alongside them.
The API-04 enriched profile is an explicit comparison configuration, not a
byte-compatible adapter or a claim of equivalent information, tensors, action
spaces, learning performance or experimental conditions. It uses entity
observations plus four declared aids, with no implicit side flip. The partial
context limits of ObservationV1 still apply; no Markov/sufficiency claim is made.

## Independent export and target loading

`export_channels(channels, names, profile=PRIMARY_PROFILE)` requires an explicit
list of channels to export and returns `(manifest, blobs)`. The JSON manifest
has `format_version: 1`, a `channels` descriptor map and the expanded input
profile. Each blob is a separate JSON string containing one channel envelope.
Metadata lives in that channel's blob, never as a payload in the manifest.
Store the manifest and blobs independently; no dataset, filesystem layout or
recorder implementation is required. Exporting primary cannot implicitly export
other channels. To retain audit/target data, name those channels explicitly.

`import_channels(manifest, load_blob, names)` validates all manifest descriptors
and the profile before requesting any payload, then calls `load_blob(name)`
only for explicitly selected names. It validates the loaded envelope against
its declared channel/schema and rejects duplicate JSON keys. It returns copied
channels and the recorded profile. The recorded profile describes the intended
encoder configuration; a partial target-only read need not contain its inputs.
Projection with that profile still requires all its selected paths/channels.
The loader is a storage concern and never enters the pure projector.

```python
manifest, blobs = export_channels(channels, ["primary", "evaluation", "privileged"])
public, profile = import_channels(manifest, blobs.__getitem__, ["primary"])
inputs = project_inputs(public, profile)["features"]
targets, _ = import_channels(manifest, blobs.__getitem__, ["evaluation"])
labels = targets["evaluation"]["data"]["labels"]
```

Here neither input loading nor target loading requests the privileged blob.
Inputs are constructed from an allowlist, not by loading the entire record
and deleting labels afterward. Mutating a channel, projection, schema copy,
profile serialization or decoded result does not change its source containers.
Run `python -m examples.lab.channels` for a complete CPU-only example with a
synthetic target and a controller mask indexing cached choices (not Gym actions).

This is data/API isolation, **not a security sandbox**. A Python process with
Game access can read private state, bypass these functions or put a secret in
an otherwise valid numeric/public string field. Types cannot prove provenance
or honest labeling. Trusted producers must respect channel semantics; the
boundary prevents undeclared paths, nested object injection and automatic
cross-channel inclusion, not malicious code or covert encoding.
