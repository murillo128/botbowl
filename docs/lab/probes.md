# Probe tasks and the CPU reference protocol (EVAL-02)

`botbowl.lab.evaluation.probes` exports bounded, inert tasks from the existing
[EVAL-01 catalogue](evaluation.md), [DATA-03 causal windows](windows.md), and
[frozen DATA-04 family splits](splits.md). It does not import a training library,
load an encoder, change engine behavior, or define universal tactical labels.
The NumPy reference runner lives separately in `examples/lab/probe_reference.py`.

## Export a task

```python
from botbowl.lab.evaluation.probes import ProbeSpec, export_probe_task

# windows: exhaust iter_windows(reader, window_spec, split_manifest=splits).
# records: EvaluationRecord.from_json(...) from explicitly authorized EVAL-01
# sidecars, or EvaluationOracle().evaluate(...) captured at matching boundaries.
task = export_probe_task(
    windows, records, ProbeSpec('team.score', 'home', target_offset=1),
    split_manifest=splits,
    raw_features=[{'field': 'primary.teams[].score', 'path': [0]}],
)
# task.to_json() returns detached JSON suitable for a caller-owned file.
```

A task targets one entity (and an explicitly ordered second entity for spatial
or block relations), one catalogue label/version, and one time offset. Offset
zero is the cutoff observation; offset N is future slot N−1, exactly N primitive
decisions later. The offset must fit the declared window horizon. Position
regression requires `component='x'` or `'y'`. Boolean, location and carrier targets
use classification; numeric facts, coordinates and exact block quantities use
regression. A loose carrier is the categorical string `null`, distinct from an
unavailable label. External estimates, human annotations and heuristic labels
are outside this reference protocol.

The export retains the complete label definition, unit, target kind and metrics,
window specification (including profile, history and passive/action-conditioned
modality), raw selectors, full frozen split manifest, family membership, cutoff,
target context and presence/reason for every sample. Missing future slots and
unavailable oracle values remain masked and null. A requested record missing
from a present target slot is an error, rather than an invented negative label.
Duplicate windows/label records, unknown labels, foreign entities/contexts,
inconsistent masks/dimensions and out-of-horizon futures fail closed.

`raw_features` is an explicit ordered list of numeric/boolean scalar selectors.
Each `field` must already belong to the window's authorized profile. `path`
traverses only JSON list indices/object keys inside that projected field. For
example, `[0]` selects the home score; `[0, 'value', 'x']` can select a projected
player's present position coordinate. History is flattened oldest first, then
selector order. Each scalar contributes `[value, presence]`; absent history or
nullable values contribute `[0, 0]`. Zero with presence one is an observed zero.
Unknown paths and nonnumeric leaves are rejected. Selectors and dimensions are
specified before fitting; no vocabulary/schema is inferred from test data.

`literal_in_observation` discloses whether the label itself has a literal source
in ObservationV1. Possession requires a team-membership computation and is not
marked literal merely because carrier IDs are observable. Geometry and block
probabilities are derived quantities. `literal_in_input_profile` additionally
requires the target to be at offset zero and its source path to be selected by
the window profile. These flags describe source observation/profile exposure,
not proof of what an arbitrary external encoder view retains. The caller must
record that view and inspect its transformation. A future label is not declared
present in current inputs just because the same field exists at the cutoff.

## Frozen external embeddings

The reference interface accepts plain JSON numeric arrays, not executable Python
objects, pickles, modules, loader names or dataset-provided callbacks:

```json
{
  "schema_version": 1,
  "encoder_id": "external-encoder-v1",
  "encoder_revision": "sha256:<64 lowercase hexadecimal digits>",
  "observation_view": "describe the actual exported input view and version",
  "window_spec": "<replace with the exact task window_spec JSON object>",
  "rows": [
    {"id": {"episode_id": "episode-17", "entity_id": "home",
            "decision_id": ["episode-17", "root", 3]},
     "values": [0.25, -0.5]}
  ]
}
```

`sample_id(window, entity_id)` produces this ID. The decision ID includes the
branch and selected primitive sequence (cutoff decision sequence plus one).
`align_embeddings(task, bundle)` requires one consistent positive dimension and
an exact one-to-one row set, including masked samples. It joins by full IDs,
never array position, and returns defensive copies. Duplicate/missing/extra IDs,
nonfinite data, unexpected fields, conflicting input specs and inconsistent
dimensions are rejected. Samples cannot be relabelled from test to train because
membership is checked against the frozen origin manifest.

The encoder ID, artifact SHA-256 and view are inert producer declarations. The
runner has no API to read an encoder artifact, instantiate a model, backpropagate
through it, or optimize it. The caller must authenticate the encoder bytes and
ensure the external export used only `window['inputs']`, with exactly the declared
view and history. An exporter that fabricates consistent IDs/provenance or
encodes test labels cannot be detected from numeric arrays alone. Similarly,
source authentication and complete trajectory validation belong to DATA-02 and
`iter_windows`; this export rechecks membership and causal references but does
not reconstruct a trajectory from arbitrary window dictionaries.

## Matched reference controls

```console
python -m examples.lab.probe_reference --seed 17 --budget 8
python -m examples.lab.probe_reference --task /tmp/task.json \
  --embeddings /tmp/embeddings.json --seed 17 --budget 8
```

With no files the example creates a tiny, inert **synthetic** fixture. Its raw
signal and affine arrays have a known linear target; they are not a simulated
trajectory or evidence about a trained model. Its fixed seed repeats exactly in
the same numerical environment. CLI output is a JSON report on stdout; no weights
or experimental results are committed or written to the engine package.

The Python API separates `fit_reference(...)` from `evaluate_final(...)`:

1. Fix task, raw selectors, encoder/view, split manifest, seed, budgets, projection
   dimension and ridge grid before fitting. The API accepts an exact positive
   example count for each of train, validation and test; the CLI uses one count
   for all three. Insufficient present examples are an error. Masked rows are
   excluded once for all controls. SHA-256 of seed and ID selects examples without
   using label values. Every control uses the same IDs in each split.
2. Compare external embeddings, selected raw features, a fixed Gaussian projection
   of those raw features, and a constant train baseline. The projection uses only
   an independent NumPy generator and its declared seed; no labels affect it.
   Default width is 8; changing it is an explicit protocol choice. Controls match
   example/split budgets, not input dimensions or information content. In
   particular raw selectors may omit other encoder-view fields and selected
   action information from action-conditioned windows; do not claim equal views.
3. Give each origin family equal total weight; selected windows within that family
   share its weight. Fit weighted feature means and population standard deviations
   on train only, separately for each nonconstant control. Scales below 1e-12 are
   replaced by one. Projection precedes this train-only standardization.
4. Fit a linear ridge head with unpenalized intercept by NumPy least squares.
   Regression has d+1 coefficients; classification has C(d+1) coefficients for
   train-only one-hot classes, predicting the largest linear score. This is a
   least-squares classifier, not calibrated probabilities. Sorted train classes
   break score ties. Weighted squared error plus alpha times squared slopes is
   the objective; there is no hidden layer or encoder update. The constant uses
   the weighted train mean or majority class. Reports disclose parameter counts.
5. Select alpha from the declared grid (default 0, 0.01, 1) using family-weighted
   validation RMSE or accuracy only; exact ties prefer the smaller alpha. Do not
   refit on validation. `fit_reference` returns train-fitted normalization,
   coefficients, projection and selected IDs without test labels or test metrics.
6. Freeze every choice and call `evaluate_final` to open test. Do not select a
   control, seed, view or new hyperparameter after inspecting test and still claim
   a held-out result. The separation is an auditable API protocol, not a lock
   preventing an authorized Python caller from invoking evaluation repeatedly.

## Metrics and scope of conclusions

Every split/control report includes row and origin-family counts and per-family
metrics. Aggregate MAE is the mean of family MAEs; aggregate RMSE is the square
root of the mean of family MSEs. Accuracy is the mean of family accuracies.
Classification precision/recall/F1 use the same equal-family mass confusion
counts, with raw support counts alongside weighted support. An undefined ratio
is JSON null, not a fabricated perfect or zero score. Test-only classes are
reported as unseen classes and never added to the fitted head. Regression also
reports the target unit, weighted train mean/std and train min/max as its scale.
The constant control is reported alongside every other control.

Overlapping windows are not independent experimental replicates. No row-based
confidence interval or significance claim is supplied. Split families are known
provenance components, not a proof that all unrecorded similarities are absent.
A successful probe demonstrates predictability under this protocol. Recovering
an exposed fact does not discover an unknown variable; prediction alone does not
establish causality, general understanding, tactical competence or JEPA success.
