# Laboratory release proposal and compatibility

This consolidates the laboratory into the existing **2.0.0a1 proposed** fork
release. It is a changelog/review proposal, not a PyPI upload, tag, GitHub release,
container publication or account change. `botbowl/_version.py` remains the sole
package-version source; use [existing artifact preparation](../releasing.md).
The four [installed quickstarts](quickstarts.md) and their wheel acceptance tests
supply bounded laboratory evidence. They do not announce P2 functionality.

| Surface | Accepted version/runtime | Adapter or migration boundary |
| --- | --- | --- |
| Package | 2.0.0a1; CPython >=3.11; core NumPy >=1.26.4,<3 | No Python 3.8–3.10 support in this packaging line; see [migration](../migration.md). This proposal does not change runtime requirements. |
| ObservationV1, channel descriptors, ActionV1, EventV1 | Schema 1, current closed validators | No general converter from legacy `Game.to_json()` or Gym tensors; regenerate records via the owning API. |
| DatasetManifestV1 / WindowSpecV1 / recorded episodes | V1 reader with frozen, validated origin-family inventory | No automatic conversion of legacy arbitrary JSON/CSV, custom provenance or old split assignments. Validate producer records and explicitly freeze supported V1 inputs. [Consumer](external-client.md) |
| SnapshotFileV1 | Format/version 1; graph/snapshot 1; MT19937-v1; exact engine/NumPy/backend/schema identities | No legacy pickle, earlier schema-digest or cross-backend migration. Named trusted component adapters accept their declared V1 payloads only; unavailable names fail. [Snapshot codec](snapshot-files.md) |
| ReplayV1 | Current V1 executable snapshot/transition manifest | `open_legacy_replay(..., trusted=True)` accepts the inherited local `.rep` pickle representation only for visual navigation (`executable=False`). That unversioned legacy representation has no stable cross-release compatibility promise and no automatic executable conversion. [Replay](replays.md) |
| BranchTreeV1 | Schema 1 with explicit origin, policy, horizon and chance provenance | Prediction records do not become snapshots or observed outcomes. Forced or alternative trajectories do not automatically identify causal effects. [Branches](branches.md) |
| HTTP SDK / CommandGateway | `/api/v1`, schema 1; SDK standard library; server Flask >=3.1.3,<4 | No compatibility shim for the inherited UI or pickle competition socket; callers must use semantic commands, credentials and revision/request IDs. [HTTP](http.md) |
| Gym legacy | Gym 0.26.2, environment v4; CPython 3.11/3.12, NumPy <2 | Retained separate `rl` extra, not an automatic migration to v5. |
| Gymnasium | Environment v5; Gymnasium >=1.3,<2 | v4 weights, action indices and observation heads require explicit adaptation/retraining and validation. [Gymnasium](../gymnasium.md) |
| PettingZoo AEC | PettingZoo 1.25.0 / Gymnasium >=1.3,<2, `multiagent` extra | Current semantic session wrapper; no old Gym policy conversion and no parallel simultaneous-action promise. [AEC](pettingzoo.md) |

Expected release evidence is deliberately finite:

- Dataset: six 3v3 origins at seed 17, four accepted decisions each; repeated
  semantic hashes, 24 windows, disjoint partitions and no privileged input read.
- HTTP: eight decisions at seed 17 through two player credentials, compared with
  local public session output using the same selection policy. Ephemeral loopback
  transport is tested; production TLS/proxy deployment is not implied.
- Snapshot: a natural pregame episode saved after one decision, resumed in a new
  interpreter, with matching continuation events, budget state and semantic hash.
- Branches: two independent alternatives, two decisions each, one traced origin,
  unchanged factual/sibling state and executable replay verification of all events
  and hashes. One sample per branch is no model evaluation or causal estimate.

The tests also check isolated wheel imports, runtime package identity, help and
invalid arguments, and internal documentation links. Hosted CI owns the installed
quickstart gate. Local/CI results and any container limitation are recorded in the
PR; this document defines claims to verify, not a timeless assertion that every
future revision or runtime passed. Broader owner tests retain their own evidence
and coverage limits. The existing non-root containers run the same wheel modules;
there is no second packaging or installation route.

Preserve upstream Bot Bowl attribution and the separate artwork permissions in
[third-party notices](../../THIRD_PARTY_NOTICES.md) and the [README](../../README.md).
The source license does not grant distribution rights to excluded graphics/fonts.
