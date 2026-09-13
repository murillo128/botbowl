# Performance investigation, issue #26

Source base: `81957843b03142c3913e93a5a1b2841b63b3d141`. The delivered changes
are instruments, isolated prototypes, tests and this investigation. No engine
representation, persistence format or web transport is changed. Optimization
implementation requires an accepted, bounded follow-up issue.

The [reproduction guide](../performance-investigation.md) defines the corpus,
commands, timing boundaries and limits. The compact JSON evidence accompanying
this report retains repetitions and dispersion; full reports, states, environment
records and logs live at `/tmp/botbowl-issue26-evidence/` on the investigation host.

Hardware: Intel i7-11700K, 8 cores/16 logical CPUs, 62.7 GiB reported RAM,
Linux x86_64, local ext4 storage. Versions: CPython 3.11.16, NumPy 1.26.4,
Cython 3.3.0, G++ 13.3.0, Flask 3.1.3 and Chromium 143.0.7499.4. This is shared
hardware without exclusive CPU reservation. Full versions, affinity, load,
memory information and source/input hashes are in the raw report.

## Final measured baseline

Values are **median ± sample standard deviation in milliseconds per 144-input
corpus**, with five repetitions. Counts are per repetition. The internal timers
overlap and must not be added together. Profiled/unprofiled differences include
cache and ordering effects; the uninstrumented decision timer is the baseline.

| Operation | Count | Python ms | Native ms |
| --- | ---: | ---: | ---: |
| Policy selection | 144 | 5.928 ± 0.095 | 6.387 ± 0.095 |
| Decision, uninstrumented | 144 | 460.342 ± 4.305 | 60.389 ± 0.333 |
| Decision, profiled | 144 | 429.316 ± 5.288 | 61.356 ± 0.985 |
| Internal `_one_step`, inclusive | 497 | 427.387 ± 5.103 | 59.226 ± 0.931 |
| Procedure `step`, inclusive | 574 | 8.287 ± 0.621 | 8.772 ± 0.220 |
| Legal-action regeneration | 497 | 415.778 ± 4.415 | 46.724 ± 0.594 |
| Cached legal-action read | 144 | 0.073 ± 0.009 | 0.075 ± 0.003 |
| Separate pathfinding query | 144 | 958.687 ± 11.055 | 41.506 ± 0.146 |
| Observation construction | 144 | 63.060 ± 1.952 | 64.350 ± 0.280 |
| Observation dataclass-to-dict | 144 | 178.027 ± 2.237 | 177.694 ± 0.258 |
| Whole-game deepcopy | 144 | 973.761 ± 28.103 | 983.202 ± 9.275 |
| Checkpoint capture | 144 | 12.150 ± 0.875 | 12.321 ± 0.070 |
| Checkpoint restore, one decision | 144 | 3.163 ± 0.216 | 3.192 ± 0.040 |
| Game-to-dict | 144 | 55.361 ± 2.447 | 56.810 ± 0.742 |
| JSON encoding | 144 | 84.451 ± 2.121 | 94.602 ± 2.068 |
| Replay state recording | 144 | 43.958 ± 1.380 | 46.620 ± 0.684 |
| Replay pickle encoding | 3 | 16.171 ± 0.448 | 18.139 ± 0.470 |
| Pre-encoded replay write/close | 3 | 0.794 ± 0.068 | 0.768 ± 0.012 |

The separate eight-decision memory sample peaks at 7,830,272 Python-allocated
bytes for Python and 8,044,340 for native. These include harness objects and
exclude untracked native allocations. Replay writes in this workload total
1,760,307 bytes across three files. They use buffered local writes without fsync.
Neither memory figure is a whole-match retained-size estimate.

## What is comparable

The main corpus has 144 decision inputs: 48 each from game seeds 7/17/27 and
separate policy seeds 10007/10017/10027. Python generates the input positions;
both backends receive fresh copies of those positions and the same actions.
Each backend reproduces its uninstrumented result under instrumentation and
checkpoint restoration. Warmup is one corpus pass; timings have five repetitions.
Decision, internal procedure and report/frame counts are distinct.

Cross-backend **complete output equality is false**. Six inputs expose 91
different routes and one different roll sequence. For those inputs, destination
sets and probabilities match exactly, and state/action/RNG results match after
excluding available-action payloads. Those observations do not prove that
executing arbitrary alternative routes consumes the same dice or has identical
gameplay. Timing comparisons therefore characterize these backend implementations;
they do not qualify a substitution as a semantics-preserving optimization.
The first differing complete output is retained in `baseline/headless.json`;
`compare-backends.py` and `fixture-path-differences.json` retain the wider diagnostic.

Clocks and start/end timestamps are excluded from semantic comparisons according
to the existing checkpoint boundary. Real serializers still include clock fields.
The fixture disables kickoff-table variation and covers opening play, not complete
matches, AI search trees or a population of trained policies. CPU times are not
physical game frequencies.

## A reproducible loader-growth finding

The ordinary rule loader appends into shared RuleSet constructor defaults.
Three cold-process loads produce 24/48/72 races, 71/142/213 stars and 8/16/24
inducements; the returned objects share their race list. This inflated copying
and allocation measurements during early runs despite unchanged public game
JSON. Those timings are invalid for comparative claims and are retained only
under the exploratory evidence directories.

The final harness follows the existing issue-20 fixture pattern: run the stock
parser in a private namespace with fresh constructor containers, then detach the
result. A regression challenges fixture independence after contaminating the
inherited defaults. No engine loader or shared default is changed. GC remains
enabled with collection between decision samples, outside the timers.

The inherited growth can be reproduced in a fresh process:

```python
import botbowl
for _ in range(3):
    rules = botbowl.load_rule_set('BB2016')
    print(len(rules.races), len(rules.star_players), len(rules.inducements))
```

## Representation constraints

The naive slots substitution fails the existing `immutable_after_init` decorator:
it writes an instance `__setattr__` attribute that slots do not permit. The adapted
slots prototype uses an explicit immutable setter and reconstruction hook.
The naive frozen dataclass includes `_out_of_bounds` in equality and has a
different hash; the adapted dataclass explicitly retains Square's coordinate-only
equality/hash. Neither syntactic transformation is safe by itself.

The isolated adapters preserve tested object aliasing across deepcopy, distinct
copied identities, JSON coordinates, hash lookups, Reversible registration,
checkpoint restoration and tested path probabilities. Exact-type checks require
replacing all imported Square aliases inside the experiment. Pickle class names
change to the experimental class; old save compatibility and a public migration
are not established. These remain implementation risks even if a microbenchmark
looks favorable.

The engine-level Square comparison uses a separate matched eight-input corpus
(seed 7), with five repetitions and one warmup for each representation/backend.
Its output signatures must match the baseline for that backend. It is deliberately
smaller than the main corpus and does not justify a whole-match speed claim.

Final isolated results, with one warmup and five repetitions:

| Probe | Square | Adapted slots | Adapted dataclass |
| --- | ---: | ---: | ---: |
| Construct 10,000 objects, ms | 7.670 ± 0.171 | 3.542 ± 0.062 | 3.527 ± 0.074 |
| Construction allocation peak, bytes | 1,126,024 | 645,640 | 1,045,640 |
| Deepcopy 10,000 objects, ms | 33.872 ± 0.487 | 18.509 ± 0.155 | 31.407 ± 0.436 |
| Copy one game after four seed-7 decisions, ms | 9.584 ± 0.106 | 8.878 ± 0.038 | 9.466 ± 0.117 |
| That game-copy allocation peak, bytes | 1,647,189 | 1,457,397 | 1,647,013 |
| Eight-input Python corpus deepcopy total, ms | 47.389 ± 0.093 | 43.484 ± 0.215 | 47.035 ± 1.221 |

Slots save about 43% of construction allocations in the object microbenchmark,
but about 12% in the isolated game-copy peak and 7–9% of copying time in these
small game probes. The eight-input Python decision totals are 18.591, 18.607 and
18.720 ms respectively: this does not establish a general decision-speed gain.
The dataclass offers little whole-game copying benefit here. The larger earlier
slowdowns were contaminated by loader growth and are **not** prototype evidence.

## Replay and web boundaries

Replay experiments include both public full JSON frames and the actual legacy
Replay container. The latter preserves shared report objects, per-frame report
counts, sparse frame/action IDs and episode boundaries. Checkpoint/delta envelopes
need an adapter to reconstruct the existing container; the current loader does
not accept the experimental envelope. Full decoding, every frame seek, pickle
round trips and output independence are checked. Engine checkpoints still exclude
I/O and policy state and are not a persistent replay format.

The web experiment loads the existing spectator UI in Chromium through a local
Flask host. It measures server conversion/encoding and GET/update POST costs,
observes scheduled requests, then measures JSON parse and the existing Angular
update/digest/forced-layout path over the same public frames. Frame-scheduling
wait is separate from CPU work and is not GPU paint duration. This is one local
client, not a WAN or concurrent deployment capacity test.

The actual legacy-container comparison gives the following medians for three
48-frame episodes. All operations have five repetitions; dispersion and raw
samples are in [the compact evidence](performance-issue-26.json).

| Storage experiment | Bytes | Build + pickle, ms | Load + expand, ms | Write/close, ms |
| --- | ---: | ---: | ---: | ---: |
| Existing Replay | 1,788,750 | 10.583 | 46.472 | 0.942 |
| Delta, checkpoint every 4 frames | 1,296,288 | 11.542 | 392.742 | 0.751 |
| Delta, every 16 frames | 1,175,131 | 11.424 | 442.398 | 0.705 |
| Delta, every 64 frames | 1,148,379 | 11.778 | 450.254 | 0.701 |

The storage reduction is 28–36%, with slightly more encoding CPU and roughly
8–10 times the load/reconstruction cost in this straightforward adapter. Buffered
write savings are fractions of a millisecond. The separate public-frame seek
sample (indices 0/18/36/54/72/90/108/126/143) costs 13.502 ms with full frames,
59.483 ms at interval 4, 345.213 ms at 16 and 1,221.648 ms at 64. Every frame seek
is also checked for equality outside timing. Public full-frame serialization
alone would overstate the saving against the real legacy report-sharing format.

Final web medians: game-to-dict 0.300 ms, Flask JSON encoding 0.348 ms, GET
1.172 ± 0.037 ms and idle update POST 1.152 ± 0.033 ms, with approximately 33 kB
responses. Observed update spacing was 2.51–2.52 s. Across 144 frames, browser
JSON parsing took 25.600 ± 0.863 ms and update/digest/forced-layout took
1,813.200 ± 19.786 ms: approximately 0.178 and 12.592 ms per frame respectively.
The separate frame-scheduling wait was 560.500 ms per corpus. All five runs
rendered 476 cells, with no page errors or failed requests in the final run.

## Prioritized benefit/risk decision

1. **Prioritize a bounded RuleSet initialization/loader child.** The deterministic
   growth and shared-list identity are established independently of noisy times.
   Its contract should cover independent constructor containers, repeated-load
   counts, intentional sharing, loader compatibility and existing regressions.
   This investigation only isolates its own fixture; it does not fix the loader.
2. **Prefer existing checkpoints for a caller that needs rollback.** In the
   native corpus, copying costs about 6.83 ms per input, versus approximately
   0.108 ms for capture plus one-decision restore. This is a per-operation
   comparison, not an end-to-end application speedup. A child must identify one
   real copy-heavy caller and account for policy state, RNG, clocks, I/O and
   ancestor validity. Observation-to-dict conversion is another measurable
   caller cost; profile its frequency before choosing a conversion optimization.
3. **Keep adapted slots as a conditional, bounded candidate; defer a general
   conversion and dataclass adoption.** The measured game-copy benefit is modest
   compared with the object microbenchmark. Require old-save migration/format
   compatibility, exact-type and hashing behavior, forward-model tests, and a
   representative consumer workload before implementation. Dataclass syntax
   alone provides insufficient benefit and defaults violate Square semantics.
4. **Defer replay-format replacement.** Deltas reduce bytes but make loading and
   seeking materially more expensive here. A later storage-bound workload could
   justify a child with explicit indexing, cache, compatibility and recovery
   requirements. These local buffered-write measurements do not establish that
   current replay writing is the bottleneck.
5. **Do not promote WebSockets from this evidence.** Local serialization is much
   smaller than per-update browser work, while polling cadence dominates waiting.
   A future transport proposal needs a concrete freshness/concurrency/network
   workload and measured unmet requirement. No second remote API is justified.

Existing native pathfinding is measurably faster on this input corpus, but the
reported path/roll differences prevent treating backend substitution as an
unqualified semantic-preserving optimization. All optimization implementation
decisions above require a separately accepted child contract.

## Validation and retained evidence

| Validation | Observed result |
| --- | --- |
| Full native suite | 2,094 passed, 1 inherited xfail; 102 inherited Gym warnings |
| Baseline identity/copy/forward-model/probability selection plus then-current harness | 800 passed |
| Same engine regression selection with adapted slots | 778 passed |
| Same engine regression selection with adapted dataclass | 778 passed |
| Final harness, including loader isolation and read-result identity | 25 passed |
| Repository error lint, compileall and pip check | Passed |

The full suite and 800-case selection preceded the final harness fixture/GC
isolation and three added harness cases. The final 25-case run validates those
changes; the engine and prototype substitution mechanism are unchanged. There
are no elapsed-time assertions or CI performance thresholds in the new tests.

The main corpus SHA-256 is
`3c463a1cd550d855896d880ec49652b204626a569c747dd7bbd26b0c82308b37`.
The compact JSON retains timings, counts, dispersion, fingerprints, validation
counts and raw-file hashes. The full evidence directory also retains the
environment/build logs, JUnit reports and explicit superseded runs. Small
engine/copy probes are reproduced by `engine-probes.py` and `copy-probes.py` in
that directory; both call the committed instruments with seed 7, five repetitions
and one warmup. The engine probe uses eight decisions; the isolated copy probe
advances four decisions before repeatedly copying the resulting game.
