# Headless performance investigation

Issue #26 adds opt-in instruments under `botbowl.benchmarks`. It does not change
engine representations, replay formats, or web transport. Run from the repository
root. Timing results are evidence for a later bounded decision, never CI gates.

## Reproduce the corpus

Use the project's build and editable-install workflow. Native comparison is
explicit and fails if the extension is absent; installing the Python backend
alone remains supported. The recorded environment uses CPython 3.11.16 and the
repository's `requirements/rl.txt` constraints (NumPy 1.26.4). The existing project
metadata, not this instrument, defines supported interpreter versions.

```bash
python -m pip install -c requirements/rl.txt setuptools Cython wheel
BOTBOWL_BUILD_NATIVE=1 python setup.py build
BOTBOWL_BUILD_NATIVE=1 python -m pip install -c requirements/rl.txt -e '.[dev,web,rl,competition]'
python -m pip check
python -m botbowl.benchmarks.headless --output /tmp/botbowl-issue26-evidence/baseline
```

The default workload has three seeds (7, 17, 27), 48 decision inputs per seed,
one discarded warmup and five measured repetitions per backend. The input game
is `gym-11`, human versus orc, with zone/wedge setup, kickoff table disabled,
pathfinding and fast mode enabled, and human agents to stop at each decision.
Roster and agent IDs are fixed. The policy sorts available choices and candidate
players/positions before sampling with a separate `random.Random(seed + 10000)`;
it excludes manual placement and rejects illegal choices. This is a synthetic
opening-game corpus, not a full-match or trained-agent throughput estimate.

The fixture runs the stock rule parser with private RuleSet constructor inputs
and detaches the result. The ordinary loader otherwise accumulates definitions
through inherited mutable defaults. This follows the existing issue-20 fixture
pattern, changes no engine loader, and fixes the corpus at 24 races, 71 stars,
8 inducements and 5/7/6 SPP-action/SPP-level/improvement entries. A harness test
challenges it after contaminating the inherited defaults. Timings from before
this isolation are invalid for comparative claims and retained as exploratory.

Both backends receive fresh clones of the **same Python-generated positions and
actions**. Their outputs never drive the next input. A backend must reproduce
its own uninstrumented state, action and game RNG trace exactly. Cross-backend
output differences are reported separately, with the first differing result
retained in full. A different route or roll sequence is not accepted as an
optimization simply because it is faster. Probability regressions also run
outside the benchmark.

Headless semantic comparisons exclude start/end timestamps and state clocks:
these are external to the trajectory checkpoint contract. Wall-clock reads are
fixed at zero during sampling; `perf_counter_ns` remains real. Serialization
timers call the actual unnormalized serializers, so they include clock fields.
The arena serialization cache is populated during fixture construction.

## Measurement boundaries

| Metric | Operation and interpretation |
| --- | --- |
| `policy` | Choose a legal input, using the separate policy RNG |
| `decision_step_uninstrumented` | One `Game.step(action)` on a prepared clone |
| `decision_step` | The same boundary with nested instrumentation enabled |
| `internal_one_step` | Inclusive `_one_step`, including procedure lifecycle and action generation |
| `procedure_step` | Procedure `step` calls; nested calls are inclusive |
| `legal_actions` | Regenerate available actions inside the decision |
| `legal_actions_read` | Read cached actions, without generation |
| `pathfinding_query` | `get_all_paths` for the first on-pitch player in stable ID order |
| `observation` / `observation_to_json` | Public lab `ObservationV1` construction / dataclass conversion |
| `deepcopy` | Copy the input game, before enabling its forward model |
| `checkpoint_capture` / `checkpoint_restore` | Capture trajectory and RNG / undo one decision and restore RNG |
| `state_to_json` / `json_encode` | Game-to-dict / standard sorted JSON encoding |
| `replay_record_step` | Existing full state recorder at the selected decision boundaries |
| `replay_pickle` / `replay_write` | Existing Replay encoding / pre-encoded bytes written and closed |

These timers overlap. Do not add an inclusive internal timer to decision time.
A decision, a procedure call, an emitted report and a replay frame are different
counts; none is a physical tick rate. The uninstrumented timer is the decision
baseline. Comparison with the profiled timer also includes cache/order effects,
so it is not a pure observer-overhead estimate. Input cloning, fixture construction, correctness checks and Python
corpus advancement are outside decision timers. GC stays enabled, with explicit
collection of unreachable clone graphs between decisions outside timers, and
between repetitions of isolated prototypes. Backends run
in a fixed Python-then-native order; load and scheduler effects on this shared
host remain limitations. Raw totals, counts, all repetitions, median, min/max
and sample standard deviation are recorded, not only a speedup ratio.

Memory uses a separate `tracemalloc` run of the first seed and first eight
decisions (or the requested smaller corpus). Its scope includes retained
benchmark frames, clones and correctness work; it is not game-only retained
size. It measures Python allocations, excluding untracked native/browser
allocations. Prototype operations have separate allocation peaks. No timing
sample runs under `tracemalloc`. Hardware, affinity, load, memory information,
package/compiler versions, module identities and input file hashes are recorded.
Writes use local temporary storage, buffered write plus close, without `fsync`;
they do not measure crash durability or remote storage. Temporary recorder files
are removed on both success and exception.

## Isolated prototypes

```bash
python -m botbowl.benchmarks.prototypes \
  --frames /tmp/botbowl-issue26-evidence/baseline/frames.json \
  --legacy-replays /tmp/botbowl-issue26-evidence/baseline/legacy-replays.pkl \
  --output /tmp/botbowl-issue26-evidence/prototypes.json
python -m botbowl.benchmarks.prototypes --engine slots \
  --output /tmp/botbowl-issue26-evidence/engine-slots.json
python -m botbowl.benchmarks.prototypes --engine dataclass \
  --output /tmp/botbowl-issue26-evidence/engine-dataclass.json
```

Only read `legacy-replays.pkl` created locally by this harness; it is a trusted
pickle, not an exchange format. Do not load supplied or remote pickles.

The Square experiments preserve coordinate equality/hash, JSON, copying and
Reversible immutability through explicit adapters. The slots version needs an
explicit reconstruction hook; the dataclass disables generated equality.
Naive versions are evaluated as negative controls. The process-local substitution
context restores imported aliases and the immutable registry. It is intentionally
not thread-safe. Construct games inside it; never let their objects escape into
an application. The experiment changes Python pickle class names and does not
establish old-save compatibility or justify changing the public Square type.

Replay experiments replace dictionary fields and treat lists atomically, with
checkpoint intervals 1/4/16/64 for public JSON frames. They verify full playback,
every random seek, JSON/pickle round trips and output independence. Seek timings
use the reported evenly spaced index sample; exhaustive seek checks are untimed.
A separate
comparison retains the **actual legacy report objects, report counts, actions,
sparse recording IDs and per-game boundaries**, measuring legacy files against
checkpoint/delta envelopes at intervals 4/16/64. Envelopes require an explicit
adapter to reconstruct Replay objects. Existing loaders do not read them. This
is a storage experiment, not event-sourced engine replay or a persistent RNG
checkpoint. No schema is promoted to a public format.

Run identity, copy, forward-model and probability regressions for each Square
prototype in fresh processes:

```bash
python -m pytest -q tests/performance/test_investigation.py
python -m botbowl.benchmarks.prototypes --regressions slots \
  tests/framework/test_rng_isolation.py tests/framework/test_forward_model.py \
  tests/framework/test_quick_snap_forward_model.py tests/ai/test_pathfinding.py \
  tests/game/test_probability_queries.py -q --require-pathfinding=native
# Repeat the preceding command with --regressions dataclass.
python -m pytest -q --require-pathfinding=native
```

The replay prototype's format/copy/identity assertions run alongside the harness;
it changes no forward model or probability implementation. The same engine
regression selection establishes that surrounding baseline separately.

## Existing web client

Install Playwright separately from project runtime dependencies and point at a
local Chromium executable:

```bash
python -m pip install playwright==1.62.0
python -m botbowl.benchmarks.web \
  --frames /tmp/botbowl-issue26-evidence/baseline/frames.json \
  --browser /path/to/chromium \
  --output /tmp/botbowl-issue26-evidence/web.json
```

The instrument serves the existing Flask app on loopback with an isolated host
and temporary storage. It times GET and idle update POST requests, actual JSON
conversion/encoding, and observes the spectator's scheduled requests for 5.6 s.
Browser resource entries include round-trip duration and payload size. It then
feeds the same corpus through the existing Angular controller, digest and DOM,
measuring JSON parse and update plus forced layout separately. Refresh/clock
timers are neutralized during this phase. `requestAnimationFrame` wait is reported
separately; it is not GPU paint duration. CDP reports layout/style/script totals
and JS heap usage. Warmup is excluded from per-run timings but included in CDP
totals. All 476 board cells must render and page errors fail the run.

This establishes local server/client costs and polling cadence. It does not
measure WAN latency, concurrent clients, a hidden tab, GPU paint, or deployment
capacity. Any later transport proposal must identify a measured bottleneck and
define a bounded contract. No WebSocket or second remote API is added here.
