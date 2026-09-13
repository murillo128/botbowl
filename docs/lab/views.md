# NumPy views V1

`botbowl.lab.views` consumes only an [ObservationV1](observations.md) or its
plain `to_json()` dictionary. Each constructor validates and copies the complete
primary schema through [API-04 channels](channels.md); unknown fields, versions,
inconsistent presence, geometry and entity references fail with `ValueError`.
Pass `channels["primary"]["data"]` explicitly when consuming channel exports.
Channel bundles, evaluation, privileged data, derived helpers, control masks and
Game objects are not view inputs. The input profile is `PRIMARY_PROFILE`, recorded
in metadata. These views are deliberately smaller numeric encodings of that
profile; they do not opt into the enriched RL profile or change the Gym adapters.

```python
from botbowl.lab.views import entity_view, grid_view, graph_view, align_targets

table = entity_view(observation, team="away", pad_to=32, permutation_seed=7)
grid = grid_view(observation, team="away")
graph = graph_view(observation, team="away", pad_to=32, permutation_seed=7)
targets = align_targets(table, {"away:0": [1.0, 0.0]})  # separate output
```

All view metadata contains `schema_version=1`, a `view_id`, the input profile,
ordered channel names, corresponding units, dtypes, and a versioned coordinate
frame. Changes to channel order, units, identity, aggregation, masks or coordinate
meaning require a new view version. Arrays are caller-owned copies; the frozen
containers do not make their arrays immutable. No view advances play, calls a
feature provider, uses engine randomness or modifies the observation.

## Coordinates and actions

The default `team=None` is absolute arena geometry: origin upper left, x to the
right, y downward, including crowd-border cells. `team="home"` or `"away"`
explicitly selects a relative frame with the selected team attacking toward
**decreasing x**. This choice is independent of `observer_team`, the actor and
the active team, so it also works at boundaries without an actor. The two
touchdown tile sets determine direction; custom arenas without horizontally
separated endzones reject team-relative views. Absolute views remain available.

Horizontal reflection is exactly `x' = width - 1 - x`, `y' = y`, using actual
arena dimensions, before padding. Coordinates are never clamped, even outside
the arena. Tiles move with their cells: `HOME_TOUCHDOWN` remains the home endzone,
and player teams, actor IDs and target IDs retain their meaning. No home/away
identity swap occurs. On the supplied arenas home uses the absolute orientation
and away is reflected. Padding is added on the right and bottom after this map.

`coordinate_frame(observation, team)` returns a `CoordinateFrame` with schema
version 1 and convention `arena-xy-v1`. Its `position(PositionV1)` and
`inverse_position` are exact integer maps. `action(ActionV1)` and
`inverse_action` make strict copies through the accepted [semantic action
schema](actions.md), transforming the position and every path option step.
Actions without positions retain their payload. The same reflection is its own
inverse; inverse-map a proposed action before submitting its existing control
envelope. Views do not assert legality or issue a new decision/revision token.
Relative coordinates are never relabeled as an absolute ObservationV1.

## Entities, context and targets

`entity_view` returns `EntityView` with `float32 features[N,C]`, boolean
`present[N,C]`, boolean `row_mask[N]`, `entity_ids[N]`, and aligned `entities[N]`
metadata. The default order is observation players followed by balls. Player
IDs are unchanged. `ball:<index>` denotes only the current observation's ball
list index; it is not a persistent ball identity across snapshots.

Channel order is published as `ENTITY_CHANNELS`, with `ENTITY_UNITS`:

| Channels, in order | Meaning |
| --- | --- |
| `x`, `y` | Recorded position in the selected frame, arena cells |
| `is_player`, `is_ball` | Entity-kind indicators, both present on real rows |
| `attributes.{ma,st,ag,av}`, `role_attributes.{ma,st,ag,av}`, `extra_attributes.{ma,st,ag,av}` | Current, role and extra raw attribute points |
| `mng` | Raw miss-next-game flag |
| `status.<flag>` | All sixteen boolean PlayerStatus flags, in schema order |
| `status.moves`, `status.spp_earned` | Raw movement counter and SPP |
| `on_ground`, `is_carried` | Raw ball flags, independently preserved |

Player-only channels are absent for balls and vice versa. An off-pitch player
is a real row with absent position, not padding. `pad_to` adds rows with no ID,
no entity metadata, false row/value masks and zero storage. A missing value uses
zero storage with `present=False`; zero with `present=True` is actual data.
Float32 is a model-oriented representation: integer values are exact through
2**24, with NumPy rounding above that range. The arrays are not a lossless wire
serialization for arbitrary integer magnitudes.

IDs, team membership, locations, role, skills, used skills, injuries and carrier
references remain in aligned metadata, never numeric columns. Slot indices,
episode IDs, source hashes and provenance are not numeric features. The row
order itself can identify roster slots; it is not an anonymity guarantee.

Entity and grid views (including `graph.nodes`) have the same separate numeric
`context` and boolean `context_present` arrays. `context_channels`/`context_units` enumerate match
half, round, unavailable drive, game-over, decision pending and transformed
target x/y, followed by both teams' score, turn and all public resources.
`context_team_ids` supplies team alignment outside those arrays. The phase and
actor/active/subject/target references are metadata. `logical_time` copies half,
round, drive presence and both team turns. These fields are **not** a unique
decision tick or state identity; ObservationV1 has neither. Comparing views
from different reads requires caller-owned timeline/control identity.

`permutation_seed` uses a fresh local NumPy `Generator(PCG64(seed))` to permute
all rows, including padding. Features, presence, row masks, IDs and metadata
move together. The external seed and algorithm are metadata, not features.
The same observation/options yield the same table and graph node ordering.
No global NumPy generator or engine RNG is read or changed.

`align_targets(table, targets_by_id)` constructs an independent `AlignedTargets`
output in that table's current row order. Numeric scalars or equal-shaped
arrays become float32 with an equally shaped presence mask. Missing labels and
padding are absent, unknown IDs/nonfinite targets/mismatched shapes reject.
Align again by ID after changing the permutation; positional target arrays are
not accepted. Target data never becomes part of a view or its metadata.

## Raw grid and geometric graph

`grid_view` returns `features[C,H,W]`, `present[C,H,W]`, `arena_mask[H,W]`,
`playable[H,W]`, and `cell_entity_ids[y][x]` tuples. `pad_to=(height,width)` must
cover the actual arena. Real crowd cells have arena=true, playable=false;
padding has both false. The first twelve channels are `tile.<name>` indicators
in the explicit `TILES` order, all present at every arena cell. They do not use
engine enum ordinals. Their presence is false in padding.

The remaining channels are `ENTITY_CHANNELS` suffixed `.sum`: sums of the
present values of **all** entities recorded at that cell, including x/y sums.
Presence means at least one contributor for that channel. Empty cells therefore
have zero entity storage with false presence. `is_player.sum` and `is_ball.sum`
are counts. Co-located players/balls are not overwritten; all their IDs appear
in the cell tuple. Aggregation loses individual numeric values at a shared cell;
use the table/graph to retain them. `off_grid_ids` includes absent-position and
exterior-position entities. They contribute no grid values, even if an exterior
coordinate falls in allocated padding. Crowd-border entities remain visible.

`graph_view` returns an `EntityView` as `nodes`, `int64 edge_index[2,E]` and
`float32 edge_features[E,5]`. There is one directed edge per ordered pair of
distinct positioned entities, including exterior or crowd positions; unplaced
and padded rows have none. Edges follow node order. Channels are destination
minus source `dx,dy`, Manhattan distance, Chebyshev distance and `adjacent_8`
(Chebyshev exactly one). Co-located entities have distance zero and are not
adjacent. These are explicit geometric relationships, not reachability, legal
moves, tackle zones, line of sight, path distances or probability estimates.

The encodings omit numeric categorical expansion, movement histories, weather,
race, turn-context flags and prompt options. Graphs omit absent-position edges;
grids additionally aggregate collisions and omit off-grid numeric entities.
The observation remains the richer public source. None of these views claims
Markov sufficiency, tactical equivalence to legacy RL, or full reconstruction.

## Optional synthetic rendering

Explicitly import `botbowl.lab.rendering.render_grid(grid, cell_size=12)` for a
`uint8 RGB[H*s,W*s,3]` image. It uses only original flat colours and squares:
pitch/endzone/crowd backgrounds, a large player square, a smaller ball square
above it, and an outline at the public target. Cell `(x,y)` occupies exactly
`image[y*s:(y+1)*s, x*s:(x+1)*s]`; padding is black. It omits identities, counts
and off-grid entities. Geometry tests verify these regions directly.

No artwork, icons, fonts, assets, GUI, display library or GPU is loaded. Core,
lab and view imports do not import this optional module. Rendering consumes no
randomness and does not inspect or change an action. The executable
[`examples/lab/views.py`](../../examples/lab/views.py) produces two views from a
single public observation using public APIs only.
