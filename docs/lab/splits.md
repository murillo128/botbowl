# SplitManifestV1: origin-family dataset partitions (DATA-04)

`botbowl.lab.splits` assigns complete provenance components before callers build
training windows, branches, or augmentations. A train/validation/test boundary
is therefore a boundary between known origin families, never merely between
rows. Split randomness is an explicit integer seed and never consumes a game,
policy, scenario, observation, or process RNG.

```python
from botbowl.lab.splits import build_split_manifest, validate_window_membership

split = build_split_manifest(
    sources,
    proportions={"train": 0.8, "validation": 0.1, "test": 0.1},
    seed=20260909,
    split_version="league-data-v1",
)

# Run immediately before a loader exposes samples.
validate_window_membership(split, windows)
```

The complete executable example is
[`examples/lab/splits.py`](../../examples/lab/splits.py).

## OriginSourceV1

Every source is a closed, data-only `OriginSourceV1` object with the following
fields. JSON objects accepted by `OriginSourceV1.from_json()` use the same
shape.

| Field | Contract |
| --- | --- |
| `schema_version` | Exact integer `1`; booleans are not integers. |
| `source_id` | Stable logical source ID, not a path or scheduling ordinal. |
| `source_version` | Version of the producing source format or recipe. |
| `kind` | `episode`, `snapshot`, `replay`, `augmentation`, or `variant`. |
| `episode_id` | Nullable DATA-02 episode identity. Equal IDs join a family. |
| `origin_family_id` | Nullable declared root family. DATA-02 `source_family` maps here. |
| `content_digest` | Required `sha256:<hex>` exact-content identity. |
| `semantic_origin_fingerprint` | Nullable `sha256:<hex>` exact semantic origin identity. |
| `variant_group_id` | Nullable explicit group for near variants that equality cannot discover. |
| `relationships` | Typed references to other `source_id` values. |
| `groups` | Explicit lists for `scenario` and `policy` holdout selection. |

Relationship kinds are `parent_episode`, `parent_snapshot`, `replay_source`,
`augmentation_source`, and `variant_source`. They all point from a derived
source to an ancestor. Missing targets and directed cycles are errors. Two
identical occurrences of a source ID collapse; the same ID or episode ID with
different episode content is an error.

`origin_from_episode()` validates and adapts an `EpisodeManifestV1`. It carries
the recorder's `episode_id`, `source_family`, scenario, home/away policy
identities, and a canonical digest into the origin record. Seed recipes and the
rules descriptor remain in the recorder manifest; they are not copied into
features. Snapshot producers should copy the `SnapshotFileV1.semantic_state_hash`
captured at the declared origin boundary into `semantic_origin_fingerprint` and
identify the snapshot format in `source_version`.

Semantic fingerprints are equality keys, not similarity detectors. They are
safe to compare only for compatible snapshot scope/schema/component versions
and the same declared origin boundary. The #39 hash proves equality of its
normalized executable projection; it does not prove that two different worlds,
policies, or generated situations are statistically independent or similar.
Near variants therefore join only through `variant_group_id` or an explicit
relationship. File paths and worker order are not represented and cannot affect
assignment.

Use `policy_group_id(id, version)` to name recorder policy holdouts. This
length-prefixed encoding distinguishes embedded colons and missing versions;
`None` and the literal version `unversioned` are different policies. Source
identifiers have at most 128 characters; composed group identifiers allow 384.
Group lists and relationship lists are normalized because their order has no
meaning. Input data is limited to 16 MiB per record/manifest, depth 40 and one
million JSON value nodes. These are bounded reference records, not a sharded
storage format.

## Component resolution and assignment

The version-1 grouping rule forms the undirected closure of:

- provenance relationships;
- equal `episode_id` or `origin_family_id` values;
- equal exact `content_digest` values;
- equal `semantic_origin_fingerprint` values; and
- equal declared `variant_group_id` values.

All descendants inherit the component's generated `family_id`. Different
declared family names joined by equality or explicit provenance become one
component, including near-variant groups spanning multiple families. The
original labels remain in the source inventory for inspection.

`stable-family-balance` version 1 validates positive finite proportions that
sum to one, computes whole-family target counts, then orders families with
SHA-256 over the independent split seed and stable family identity. Explicit
scenario/policy holdouts are placed first; remaining families fill the largest
current target deficit. Whole-family counts can only approximate requested
ratios. Target counts reserve one family per split, then apportion the remainder
by largest fractional remainder (split-name order breaks ties). Unforced
assignment reserves enough families to fill every requested split even when
holdouts exceed their target. If there are fewer independent families than non-empty splits,
`InsufficientOriginsError` reports both counts and requires more origins or a
different protocol. It never divides sibling windows to manufacture a ratio.
The same diagnostic applies when holdouts leave too few unforced families to
populate the remaining splits. In that case the diagnostic counts refer to
unforced families and splits still needing one.

An origin family matching holdout groups directed to different splits is an
error. The manifest retains the exact maps under `held_out_groups`, so an OOD
claim is inspectable rather than inferred from filenames.

## Frozen manifest and validation

`SplitManifestV1` stores a canonical defensive copy and returns fresh JSON
copies. It records:

- schema, split, grouping-rule, and assignment-algorithm versions;
- the split seed and requested proportions;
- scenario/policy holdout maps;
- the normalized source/version inventory and its digest;
- every source-to-family and family-to-split assignment; and
- source/family counts per split.

Construction and loading recompute the graph, assignments, inventory digest,
and counters. A corrupt/non-canonical manifest fails instead of being repaired.
`validate_split_manifest(manifest, current_sources)` additionally compares the
frozen inventory with current provenance. A newly added relationship that
bridges existing splits is reported as contamination. Added sources or any
other provenance change require a newly built manifest.

Pass `previous_manifest` while rebuilding. If its source set, seed,
proportions, grouping policy, or holdouts changed, reusing the same
`split_version` raises `SplitVersionRequiredError`; an explicit version bump
allows a complete new assignment. Training and evaluation should retain one
validated manifest unchanged rather than regenerating it as new data arrives.
There is no global registry: callers must supply the prior manifest to enforce
version continuity. The current source inventory and supplied digests are
trusted producer metadata. This utility does not load or authenticate source
files; use the recorder/snapshot readers to verify them before constructing
origin records, and retain their format/schema identities in `source_version`.

`validate_window_membership()` accepts future DATA-03-style sample mappings.
Each mapping has `split` and an exact `origin` object containing `source_id`,
DATA-02 `episode_id`, and DATA-02 `source_family`. Extra window payload fields
are ignored. Unknown origins, recorder-metadata disagreement, and a sample label
that differs from its family's frozen split are errors. Fit sampling,
normalization, and probes only after this validation, and fit them only from the
manifest's train membership.

This contract prevents leakage among known equal or declared-related origins.
It does not demonstrate statistical independence between similar worlds that
the protocol did not identify.
