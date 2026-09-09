# External local consumers (OPS-06)

Install Bot Bowl's core wheel in the consumer's own virtual environment. The
reader and simulation need NumPy and untangle; they do not need NFL code, an ML
framework, an HTTP client, Flask, a renderer, weights or a GPU. The examples use
only public Bot Bowl APIs and can run from any working directory. Example scripts
are repository files: copy the two scripts below beside your own application;
they are not installed as a package or console entry point.

```sh
python -m venv /tmp/botbowl-consumer
/tmp/botbowl-consumer/bin/python -m pip install /path/to/botbowl.whl
cd /tmp
/tmp/botbowl-consumer/bin/python /path/to/examples/lab/external_control.py
/tmp/botbowl-consumer/bin/python /path/to/examples/lab/external_sequences.py generate /tmp/m1
/tmp/botbowl-consumer/bin/python /path/to/examples/lab/external_sequences.py read /tmp/m1/consumer.json --split train
```

Use the actual wheel filename and absolute example paths. Destinations must be
new. The HTTP SDK remains a separate API; local reads do not start a service.

## Minimal reader contract

```python
from botbowl.lab.dataset_client import DatasetReader

reader = DatasetReader('/tmp/m1/consumer.json', split='train')
window_count = reader.validate()
for batch in reader.iter_batches(batch_size=16):
    for sample in batch:
        encoder_inputs = sample['inputs']
        public_future = sample['targets']
        audit = (sample['origin'], sample['split'], sample['metadata'])
```

`iter_windows()` streams individual windows. `iter_batches()` holds at most the
requested batch plus DATA-03's bounded history/lookahead; it does not materialize
all episode rows. Exhaust or close a partially consumed iterator to close its
files. `validate()` exhausts the selected split's channel hashes, row schemas,
causal references, availability rules and membership checks, returning the number
of windows. It uses no Game construction or reconstruction. It is selective
validation, not a full audit of unselected event, oracle or privileged files.

Inputs are dictionaries of explicitly allowed typed feature paths and plain JSON
scalars/arrays. They are not a padded tensor, vocabulary or normalized numerical
encoding. History/target slot masks distinguish absent `null` slots; each
observation also preserves its own field Presence semantics. Future public
observations are targets, never added to inputs. A resolved terminal observation
is present; unavailable later targets remain absent. A zero-decision episode
produces zero windows. Action-conditioned mode adds exactly the semantic action
selected at the cutoff, before its consequences, as specified by
[causal windows](windows.md).

Feed only `inputs` to an encoder. Entity IDs, origin family, split, source
transition references, terminal status and availability audit data remain
separate. `reader.manifest` returns a detached policy/audit copy. No oracle,
snapshot, generic channel-selection method or privileged capability is exposed
by `DatasetReader`. Such access requires explicitly constructing a different
producer/audit object, for example `EpisodeReader`, or a snapshot reader.
The existing Python package may import engine implementation modules internally;
reading does not execute a simulation and consumers do not import engine internals.

## Frozen manifest

`consumer.json` is a strict `DatasetManifestV1` with exactly these fields:

| Field | V1 contract |
| --- | --- |
| `schema_version` | Exact integer 1 |
| `observation` | `{"schema": "ObservationV1", "schema_version": 1}` |
| `view` | `{"observer_team": "home", "coordinates": "absolute"}`, DATA-02's canonical recorded public view |
| `window` | Complete serialized `WindowSpecV1`, including observation allowlist/profile, versions, `decisions` time unit, history/target policy and passive/action-conditioned mode |
| `split_manifest` | Complete frozen DATA-04 `SplitManifestV1` |
| `episodes` | Exact inventory of `{"source_id": ..., "path": ...}`; paths relative to this manifest's directory |

Unknown/missing keys, unknown versions, alternate time conversions, malformed
profiles, unsupported views, duplicate/path-traversal entries and manipulated
split assignments fail visibly. `split` is a required constructor argument and
must name a frozen partition. Opening verifies **all** episode manifests against
the complete split inventory and content digests. Streaming opens only the
selected split's transitions, primary data and explicitly authorized aid/mask
channels. The profile must be a subset of each source's recorded authority.
The primary/derived/control leaf registry is an allowlist, not removal of known
label names from an arbitrary record. API-04 validation rejects extra nested
primary fields even when the selected profile would otherwise omit them.

For previously recorded episodes, first perform producer-level validation, then
freeze their origins and explicitly choose the window policy:

```python
import json
from pathlib import Path
from botbowl.lab.dataset_client import DatasetManifestV1
from botbowl.lab.recording import EpisodeReader
from botbowl.lab.splits import build_split_manifest, origin_from_episode
from botbowl.lab.windows import WindowSpecV1

root = Path('/tmp/recordings')
paths = {'episode-a': 'episode-a', 'episode-b': 'episode-b'}
sources = [origin_from_episode(EpisodeReader(root, path).manifest,
                               source_id=source_id)
           for source_id, path in paths.items()]
splits = build_split_manifest(sources, proportions={'train': .5, 'test': .5},
                              seed=17, split_version='experiment-v1')
manifest = DatasetManifestV1.from_sources(
    paths, window=WindowSpecV1(3, 2, mode='action_conditioned'), split_manifest=splits)
(root / 'consumer.json').write_text(json.dumps(manifest.to_json()), encoding='utf-8')
```

V1 accepts episode inventories generated by `origin_from_episode`; snapshot,
augmentation and custom-provenance inventories need their own consumer contract.
Do not modify or regenerate the frozen splits per batch or window. Files and
provenance must remain immutable while reading. Hashes detect content changes;
they do not authenticate a malicious producer that fabricates self-consistent
public values and provenance. This is an API boundary, not an OS security sandbox.

## Reproducible M1 and local control

[`external_sequences.py`](../../examples/lab/external_sequences.py) creates 100
short 3v3 match-start episodes with explicit engine/policy seed recipes, an
eight-decision budget and a 1,000-step automatic-resolution limit per decision.
It uses DATA-01 `build_plan`/`execute_plan`, fully verifies the generated dataset
through the separate producer validator, freezes DATA-04 origin families, and
writes the consumer manifest. Generation reads privileged audit data explicitly;
the separate `read` command does not. `plan.json`, `dataset.json`, episode files
and `consumer.json` stay in the destination outside Git.

The default seed 17 produces 100 truncated fragments, zero naturally completed
matches and 800 accepted-decision windows. The existing whole-family allocator
assigns 78/11/11 independent declared families to train/validation/test for the
requested .8/.1/.1 proportions. The example reports actual counts, per-episode
semantic hashes, window hashes, history/target sizes, masks and audit IDs. Its
persistence predictor copies the last available input observation; this is only
a data-consumption demonstration, with no trained model or success claim.

Running `generate` again in a different new directory with the same seed/budget
recreates the plan and semantic records under the same engine/dependency versions.
The installed subprocess regression checks all 100 hashes and all three split
window hashes across two runs. `--max-decisions` permits other bounded fragments;
end counts always distinguish natural completion from truncation.

[`external_control.py`](../../examples/lab/external_control.py) uses the API-01
`SimulationSession` factory façade and two independently seeded public reference
policies. On every step it queries the actual next actor and legal semantic
commands, and submits the matching state revision. It handles both changes of
actor and consecutive decisions by one team. Its default 12-decision run reports
budget truncation without claiming a match result. No web server or renderer is
started.

`tests/lab/test_external_client.py` builds a fresh wheel, installs it into a new
venv outside the checkout, copies the standalone examples and runs them with an
unrelated cwd and isolated Python (`-I`, no repository `PYTHONPATH`). Import guards
reject optional ML/NFL/web dependencies, and the read process fails if it tries
to construct a Game. Adversarial tests exercise frozen membership, changed bytes
and manifests, unauthorized oracle/future fields, empty/terminal windows and
future-suffix invariance. Final independent review belongs to the PR audit handoff.
