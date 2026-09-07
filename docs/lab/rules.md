# Rules and configuration identity

`botbowl.lab.rules` describes the initial inputs to an episode without creating,
serializing or modifying a `Game`, drawing randomness, or changing rules. It is
headless and needs only the core package. This is metadata for later consumers;
there is no recorder, snapshot format, rules engine or loader replacement here.

Use the existing loaders, apply deliberate overrides, then pass the exact loaded
resources that will be supplied to `Game`:

```python
import botbowl as bb
from botbowl.lab.rules import describe_rules, capability_catalogue

config = bb.load_config("gym-3")
ruleset = bb.load_rule_set(config.ruleset)
arena = bb.load_arena(config.arena)
home = bb.load_team_by_filename("human", ruleset, board_size=3)
away = bb.load_team_by_filename("human", ruleset, board_size=3)
descriptor = describe_rules(config, ruleset, arena, home, away)
record = descriptor.to_json()
catalogue = capability_catalogue(3)

game = bb.Game("episode-id", home, away,
               bb.Agent("home", human=True), bb.Agent("away", human=True),
               config, arena=arena, ruleset=ruleset, seed=17)
```

Take the descriptor before starting the episode. Supplying live team resources
does not describe their current score, positions, skill use or other match state.
The utility never calls `Game.to_json()` or the arena's caching serializer. Pass
an explicit arena/ruleset to `Game` as above to avoid implicitly loading different
resources after taking the descriptor.

## Fields and versions

`RulesDescriptor` is a frozen, data-only dataclass with seven string fields;
`to_json()` returns a fresh JSON-compatible dictionary.

| Field | Meaning |
| --- | --- |
| `ruleset_id` | Loaded `RuleSet.name`, required to equal `Configuration.ruleset`; normally `BB2016`. This is a reference, not certification of an edition. |
| `ruleset_version` | Bot Bowl's implementation version, initially `1.0.0`, plus `+sha256.<digest>` of the projected rules and engine rule tables. Not an official game-edition version. |
| `engine_version` | Installed `botbowl` distribution version from `importlib.metadata`; `unknown` if no distribution metadata is available. |
| `config_id` | `<ruleset_id>/sha256:<digest>` for both shipped and customized inputs. Display names and filenames are not identity. |
| `config_digest` | `sha256:<digest>` of canonical effective configuration, geometry, formations, rules/tables and both ordered initial rosters. |
| `backend_id` | `python` or `native`: the actual `Pathfinder` bound in `core.procedure`. Native additionally requires the loaded module to be an extension. This describes pathfinding; the main engine is Python. |
| `capabilities_version` | Version of the bounded evidence catalogue, initially `1.0.0`. |

The digest carries identity format version **1** in its preimage. Neither the
backend nor installed engine version is in `config_digest`: the same inputs have
the same digest across those environments, while the complete descriptor records
their different identities. Compare the whole descriptor when those differences
matter. SHA-256 is a content fingerprint, not an authenticity or correctness
attestation. A package release version does not identify an uncommitted source
tree or arbitrary monkeypatches. Preserve the source revision separately when
reproducing development experiments; no episode UUID or revision recorder is
introduced by this API.

Custom configurations always derive identity from their actual inputs and retain
the ruleset reference. Merely renaming `config.name` does not create a different
configuration. A custom ruleset reference is retained too, but the BB2016
catalogue does not establish support for it. Custom coherent geometry can be
described with matching formations; that does not extend the tested variant set.

## Canonical input boundary

The projection uses explicit field lists, not object `__dict__`, raw file bytes,
or generic serialization. It identifies **loaded effective values**: changing
unused XML text, JSON whitespace, resource paths or loader-ignored fields is not
a behavior change. This preserves current loader interpretation, including its
limitations, rather than silently fixing rule parsing.

| Included | Reason and ordering |
| --- | --- |
| `roster_size`, `pitch_max`, `pitch_min`, `scrimmage_min`, `wing_max`, `rounds` | Roster, setup and match limits. Size means `pitch_max`, one of 1/3/5/7/11. |
| `kick_off_table`, `kick_scatter_dice`, `throw_in_dice` | Kickoff and ball procedures, including the explicit small-board `d2` scatter case. |
| `fast_mode`, `debug_mode`, `competition_mode`, both pathfinding flags | Execution/decision modes; debug enables assertions as well as logging. An enabled flag does not prove native availability. |
| `time_limits.turn/secondary/init/end` | Configured durations, not timestamps. All four are retained conservatively; current core clocks consume turn/secondary. |
| Arena dimensions, every tile, home/away/scrimmage/wing/touchdown tile groups | Full loaded geometry, in row-major order; no mutable JSON cache. |
| Offensive and defensive formations | List order, action-selecting names and every row/selector are retained. `Wedge`, `Line`, `Spread`, `Zone` are the current setup action names. |
| Ruleset races/roles | Named lookup records: race reroll cost, apothecary/stakes; role name, races, MA/ST/AG/AV, skills, cost, feeder, normal/double categories and star flag. |
| Other loaded rule resources | Star records, inducement cost/limit/reduction, SPP actions/levels, improvements and spiralling expense parameters. Retained as resource identity even where progression is unimplemented. |
| `Rules` tables | Pass matrix/modifiers, casualty effects, agility targets, miss-next-game effects, immovable and pass-player action lists. No assumption that all rule behavior lives in these tables. |
| Home then away team | Race, treasury, apothecaries, rerolls, assistant coaches, cheerleaders, fan factor; ordered players with jersey number, full loaded role, extra characteristics/skills, injuries, MNG and SPP. Player order matters for formation tie selection; jersey number determines pitch-invasion roll assignment in `KickoffTable`. |

Mappings sort by key; named lookup record order is irrelevant. Identical duplicate
named records are collapsed. Conflicting duplicates fail explicitly rather than
guessing which resource was intended. This matters because the inherited
`RuleSet` constructor has shared mutable defaults: repeated loader calls append
records, and mixing editions can contaminate earlier objects. The descriptor
does not repair those objects or change constructor defaults. Load independently
in a fresh process when resource contamination is suspected. Descriptor tests
isolate and restore those legacy defaults in test setup only.

Lists/tuples preserve order and multiplicity, including skills and injuries;
formation rows and the home/away positions are never sorted. Enums encode their
qualified type and **member name**, not numeric ordinal. Enum-keyed maps use
sorted tagged key/value pairs. Compact ASCII-escaped JSON uses sorted keys,
no insignificant whitespace, UTF-8 and finite numbers. Sets, unsupported values
and non-finite numbers fail; there is no `repr()` or object-address fallback.

Excluded fields include team/player/episode UUIDs, clock readings and timestamps,
source/absolute paths, credentials, arbitrary attached metadata, display names,
positions, scores, runtime player/team state, RNG/forced-roll state,
trajectories, caches and agents. `config.arena` is a locator replaced by loaded
geometry. `config.ruleset` is a semantic reference and must be a simple name,
not a path. Formation/role/race names are functional selectors and are retained.
Team/player IDs are checked for uniqueness before exclusion: a collision is
incoherent input, while changing distinct generated IDs preserves the digest.
The unused inherited `kick_scatter_distance` and `dungeon` config fields are
excluded: the current two-player engine uses `kick_scatter_dice` and has no
configuration-driven dungeon mode. Any future behavioral use requires a field
boundary review. This descriptor does not identify arbitrary bots that choose to
act on otherwise cosmetic labels or credentials.

## Failures and evidence limits

All descriptor validation failures derive from `RulesDescriptorError`:

- `UnsupportedSizeError`: `pitch_max` (or catalogue size) is not an integer in
  1/3/5/7/11.
- `IncoherentResourceError`: malformed/mismatched reference, conflicting named
  resources, inconsistent limits, unsupported dice expression, malformed arena
  border/dimensions, incompatible formation shape/selectors, missing race/role,
  mismatched player ownership or empty/oversized roster.
- `UnsupportedBackendError`: an unrecognized procedure pathfinder, substituted
  class binding, or purported native module that is not a loaded extension.

Loader errors still belong to the existing loader call before descriptor
construction. The descriptor validates a bounded structural contract, not every
legal roster, arena layout, possible setup or skill combination. It does not
validate native support for arbitrary runtime table modifications. No acceptance
of a resource should be read as a rule-conformance verdict.

The small catalogue returns fresh dictionaries with implemented/partial/
unsupported claims, concrete evidence paths and limits. It describes the shipped
BB2016 `gym-N` inputs and cited tests, not every custom descriptor with the same
ruleset name. It distinguishes standard board size 11 from smaller Bot Bowl
variants; it makes no claim that those are an official rules edition.

| Size | Arena width × height (with border) | Roster | Pitch minimum / scrimmage minimum / wing maximum | Scatter / throw-in |
| --- | --- | --- | --- | --- |
| 1 | 6 × 5 | 4 | 1 / 1 / 1 | d2 / d3 |
| 3 | 14 × 7 | 4 | 1 / 1 / 1 | d3 / d6 |
| 5 | 18 × 11 | 7 | 2 / 2 / 1 | d3 / d6 |
| 7 | 22 × 11 | 9 | 2 / 3 / 2 | d6 / 2d6 |
| 11 | 28 × 17 | 16 | 3 / 3 / 2 | d6 / 2d6 |

All shipped gym variants configure eight rounds per half and enable the kickoff
table; their defaults disable pathfinding. The standard `bot-bowl` config enables
pathfinding and competition mode. Catalogue tests check the table against actual
loaded defaults. Small-size bundled team files are human only; size 11 has 13
team files. The baseline scenarios establish movement, block, pass, reroll, drive
and game-end observations for seeds 0/17. They disable pathfinding and set
`kick_off_table=False`, but inherited `Kickoff.step` still schedules kickoff
events (seed 17 reproduces them for all five sizes). The fixtures use
synthetic micropositions and include a size-1 pass with two teammates; that pass
is not a legal one-player setup. See [the baseline evidence](../reports/baseline-issue-4.md).

BB2016 support remains **partial**. `Pregame` does not schedule inducements and
`EndGame` does not implement league progression, regardless of XML entries.
Pathfinding evidence is the [bounded parity corpus](../reports/pathfinding-issue-13.md),
not exhaustive skill interaction coverage. Kickoff tests do not close the
QuickSnap/forward-model investigation owned by #20. The inherited end-time xfail
remains #16. Historical `docs/features.md` entries are not promoted into verified
claims; the catalogue cites source and current test evidence for each claim.

## Change policy

- **Correction:** restore the intended behavior of this implementation; add a
  regression and bump its appropriate semantic patch version. Release the engine
  under a new package patch version for engine fixes. Rules/resource corrections
  also change the resource digest and implementation version as appropriate.
  A fix to BB2016 behavior does **not** rename it to a different game edition.
- **Experimental variant:** preserve the reference edition, derive a different
  config identity from changed inputs, and document the difference and evidence
  boundary. Additive implementation capabilities use an appropriate minor version;
  custom inputs never inherit a blanket verified-support claim.
- **Compatibility break:** an incompatible public descriptor/identity convention
  or intentional reinterpretation needs a major version and migration notes.
  Increment the identity-format version if canonicalization or its inclusion
  boundary changes, even when adding a previously omitted field. Catalogue claim
  changes receive a corresponding catalogue semantic version and evidence update.

The initial implementation/catalogue versions establish this API's baseline;
they do not retroactively assign semantic versions to every historical rule fix.
Hashes detect content changes but do not decide whether those changes are a fix,
variant or break. That classification requires the controlling decision and
regression evidence. Semantic version build metadata is not ordered by SemVer:
compare the full `ruleset_version` string for content identity.

## Data and artwork inventory

At this delivery the packaged rules/data inventory is **5 arena text files,
11 config JSON files, 15 formation text files, 2 rule XML files
(`BB2016`, `LRB5-Experimental`) and 17 team JSON files**. These are the existing
package-data patterns; no resources are copied or edited by this delivery. The
presence of `LRB5-Experimental.xml` does not establish a separate verified engine
edition. Identity covers projected loaded contents rather than file inventory
counts. Engine tables remain in `core/table.py`; implementation behavior remains
in the engine and its release version.

Artwork is a separate inventory and rights boundary. No graphics are read,
hashed, modified or newly distributed here. The existing README artwork notice
and package exclusions remain authoritative; neither this document nor the
source-code license expands asset redistribution permissions.

## Validation

Run `python -m pytest tests/lab/test_rules_descriptor.py` and the #4 fixtures at
`tests/game/test_baseline.py`. Tests cover all five sizes, duplicate loads, mapping
and sequence order, enums, resource/config/geometry changes, excluded metadata,
custom identity, typed errors, true backend identity and unchanged Game/RNG/rules.
The existing `tests/packaging/smoke.py` now describes all five sizes too, so the
repository artifact checks exercise this API from installed wheels outside the
checkout, including minimal headless environments. Native/Python suite profiles
assert their actual backend before collection. Results and reproduction commands
are retained in [the delivery evidence](../reports/rules-issue-30.md).
