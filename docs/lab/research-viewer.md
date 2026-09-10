# Local research viewer (EVAL-07)

`botbowl.web.research.create_app(gateway, store)` adds `/research/` alongside the
existing lab HTTP/SDK API. It is an explicit, separate research application; the
competitive web application and legacy pickle replay routes are unchanged.
The page uses the existing web package and original geometric `render_grid`
renderer from the NumPy views. No inherited artwork, GPU, learned model,
frontend framework, CDN, or training dependency is required.

## Run the reproducible demo

```sh
pip install -e '.[web]'
python -m examples.lab.research_viewer /tmp/botbowl-research-demo
```

Use a new destination. Choose a local access token at the hidden terminal
prompt, open `http://127.0.0.1:5001/research/`, enter that token, and connect.
Select `demo-factual`, then the two alternatives. They fork the same coin-toss
boundary with HEADS/TAILS, fixed independent seeds and horizons of one and two
accepted decisions. The artificial predictor emits constant data at decision 1;
it is neither trained nor calibrated. `factual-upload.json` can be selected in
the upload control; `prediction.json` illustrates the external-record input.
Object IDs are newly allocated each run; decisions, scenario seed, branch seed
recipes and artificial outputs are reproducible.

The demo explicitly grants its principal evaluator `snapshot` and `restore`
capabilities. An application can instead supply a `ResearchStore` with
`Access('spectator')` or `Access('player', team='away')` and its existing
`CommandGateway` authentication callback. Grants are trusted operator
configuration for **one shared research workspace**, not request parameters.
A role alone does not grant branching. Both evaluator capabilities are required.
The server never returns snapshots or RNG state, even to its evaluator browser.
The optional trusted Python `fork(..., seed=SeedSpec(...))` argument supports
reproducible experiments; it is not accepted from browser requests.

## Upload, navigation and comparison

`pack_replay(directory)` packages exactly one confirmed ReplayV1 directory as
`{"format":"ResearchReplayUploadV1","files":{"manifest.json":"...", ...}}`.
Save the result as JSON and select it in the page. The service copies its files
to a private temporary directory and verifies the entire executable replay,
including hashes, actions, events and checkpoints, before registration. No
arbitrary filesystem path, directory serving, archive extraction, symlink or
pickle input is accepted. A complete zero-decision replay is valid; empty,
truncated, incompatible and corrupt uploads are rejected atomically. A confirmed
nonterminal prefix retains its recorded truncation reason.

Default limits are 32 MiB per upload/request, 256 files, eight registered
trajectories, 128 decisions per replay/branch and 128 records per record category
per trajectory. Configure `ResearchLimits` explicitly for another bounded
workload. Combined prediction/annotation storage is also limited to 32 MiB per
trajectory. Four HTTP requests may be admitted concurrently; writes and reads use
a workspace lock. Close the store to remove its temporary checkpoint files.

Select an event by type or exact entity ID, or navigate by decision/turn. Events
use ReplayV1's verified event lookup. For an interior event the board displays
the **previous restorable decision**, with the selected event's own context and
next boundary separately visible. The branch controls stay disabled until an
explicit decision selection. Seeking uses existing indexes and checkpoints on
private clones; navigation, pause, reconnection and panel changes cannot modify
the recorded trajectory. Replay IDs survive reconnection within this process;
restarting the service requires loading the files again.

Alternatives created from the selected factual are available in two synchronized
panels. Selected alternatives must be distinct and share one divergence decision;
otherwise the comparison is rejected. Through divergence they display the shared factual prefix. After
that, the horizon is `requested_decision - divergence_decision`, measured in
accepted primitive decisions. Each panel displays its full actual logical
context; event counts and team turns need not agree. If a branch has ended, its
last state stays visible with an explicit **no data at requested decision**
message and end reason. This is not an extrapolated observation. A branch is
always labeled **Simulated continuation**, and retains origin family, parent,
action, policy/version, chance mode/naturalness, intervention and declared
horizon. Chance seeds/tapes remain server-side. Imported factuality is an
explicit source declaration, with known derived/fabricated replay origins
rejected; it is not independent attestation of the original match.

Player buttons include off-pitch entities and support keyboard activation.
Selection aligns across panels by episode-local player ID. Actual numeric zero
is displayed as `0`, absent position as missing data, and an unknown entity as
absent entity. Geometry is in absolute arena coordinates; observations use the
home perspective marker. The renderer is the original square-based RGB renderer
from `botbowl.lab.rendering`, not a tactical or probabilistic visualization.

## External predictions and human records

The prediction textarea accepts this wrapper:

```json
{"prediction": "<PredictionV1 object>", "target_decision": 1, "retrospective": false}
```

Replace the placeholder with the full [PredictionV1](branches.md) object.
Its model ID/version, declared emission context, visible history, horizon,
output and metadata are retained. The emission must be an exact replay decision
boundary in the same origin family. A prediction emitted after the target
comparison boundary is rejected unless explicitly imported with
`retrospective: true`, which is labeled **Retrospective analysis**. As with
BranchTree, imported emission/history declarations are not cryptographic proof
of when an external model ran or what it saw. `parent_snapshot_id` is the
producer's reference; only the matching logical emission context is resolved
locally. History references are inert identifiers and are never loaded.

Duplicate IDs reject; revisions may correct metadata only. Outputs cannot be
replaced when later observations arrive. Uncertainty is displayed exactly as
supplied; the viewer does not infer probabilities from latent distances or
retrain anything. Human annotations are separately labeled and attached to a
selected decision. All external strings use text-only DOM insertion.

## Security and validation

Both network peer and Host must be loopback; cross-origin requests reject.
Same-origin browser requests use the existing authenticator and an explicit
Bearer token retained only in page memory/input. Responses disable caching and
use a restrictive CSP. No public snapshot, seed or RNG endpoint is added.
The original `/api/v1` transport keeps its existing no-Origin policy.

```sh
pytest tests/lab/test_research_viewer.py
pip install playwright
PLAYWRIGHT_BROWSERS_PATH=/tmp/botbowl-browsers python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=/tmp/botbowl-browsers pytest tests/web/test_research_browser.py
```

The API tests are also part of the mandatory `lab-http` CI profile. The explicit
browser suite runs Chromium against a temporary loopback server, with actual
uploads, events, branch creation, keyboard selection, geometry/context/value
assertions, reconnection, navigation pause, permissions and HTML input. It uses
DOM and data assertions; screenshots are not the correctness oracle.

External representation layers, fitting provenance and synthetic image/fragment
exports use [AnnotationBundleV1](annotations.md). These remain separate from
engine state and the standard dataset reader.
