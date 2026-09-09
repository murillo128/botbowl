# Headless dataset generation (DATA-01)

The installed core package provides `botbowl.lab.generate`: a sequential generator,
closed reference policies, validation and supplied-action replay. It needs only
core runtime dependencies, with no display, Flask, Gym, PyTorch or GPU. Generate
outside the installed package and outside Git:

```sh
python -m botbowl.lab.generate generate --output /tmp/botbowl-small \
  --episodes 2 --master-seed 17 --scenario pickup --side away \
  --home-policy scripted --away-policy random --max-decisions 8
python -m botbowl.lab.generate validate /tmp/botbowl-small
python -m botbowl.lab.generate replay /tmp/botbowl-small episode-000000 \
  --output /tmp/botbowl-replayed
```

Every destination must be new. There is no overwrite, automatic retry, resume or
parallel execution. Configuration validates before creating a destination or
executing any episode. The CLI accepts closed IDs and JSON data, never module
names, code, pickle or plugin discovery. Jobs support the six registered
[scenarios](scenarios.md) and `--scenario match`, using shipped `gym-SIZE` rules,
human rosters and primitive actions with pathfinding disabled. Sizes are
1/3/5/7/11 subject to recipe compatibility. Match jobs start at the normal pregame;
scenario starts are explicitly synthetic, with structural validity rather than
proven legal reachability. Scenario-only distance/reroll parameters retain their
recipe bounds. `--side` selects the scenario's initial actor, not hidden human
control of a rival. Every subsequent actor is scheduled through the session.

The job requires a positive episode count (up to 1,000), an explicit integer
master seed, an output directory, both policy identities, the `primary` input
profile and a decision budget. Defaults are shown by `generate --help`. Match
budgets allow 0–10,000 decisions and scenario budgets 0–256; per-decision automatic
steps allow 1–10,000. These bounds do not supersede the recorder's byte/row limits;
a large episode can fail recording rather than silently dropping data.

`--horizon N` declares a fragment beginning at the recorded match/scenario start,
ending after at most N accepted decisions. It must not exceed the decision budget.
Zero horizon or budget creates a valid empty episode with observations, manifest
and no fabricated transition. The generator does not support arbitrary hidden
midgame starts or warmup prefixes.

```python
from botbowl.lab.generate import JobConfig, build_plan, execute_plan

job = JobConfig(output='/tmp/my-job', episodes=2, master_seed=17,
                scenario='match', size=1, home_policy='scripted',
                away_policy='scripted', max_decisions=256)
plan = build_plan(job)  # Pure data; no episodes run and no files written.
result = execute_plan(plan, job.output)
```

## Plans and policy inputs

`plan.json` is versioned and materialized before execution. It retains each
`episode_id`, `origin_family_id`, start/horizon/budget, exact recipe/rules/backend,
policy IDs/versions, authorized input profile and every seed recipe. Reusing a
plan under a different destination revalidates it against the installed catalogue
and descriptor before running. Policy changes do not change scenario or engine
seeds, the other policy stream, or the origin family. Origin families identify
the same initial recipe and engine source across policy interventions; they are
not policy features.

All streams use [SIM-01](randomness.md). Scenario, engine, home policy, away policy
and observer have separate purpose/component recipes. For registered scenarios,
the plan also retains the exact second-level layout seed recipe used by that
constructor. The engine receives the planned engine SeedSpec explicitly. The
primary observer is deterministic and consumes no RNG, but its reserved stream
identity is retained. No global RNG is seeded or consumed.

Both policy IDs have version `1` and own a private RNG:

- `random` samples uniformly among the session's enumerated legal semantic
  actions. Accepted no-op actions such as incomplete `END_SETUP` remain possible;
  randomness makes no progress or tactical guarantee.
- `scripted` installs a shipped setup formation, ends setup, then ends turns,
  with deterministic pregame choices and first-offered fallbacks. It is a bounded
  lifecycle smoke script, not an objective-solving or competitive agent.

Policy calls receive projected primary features and copied legal action control.
Seeds, origin IDs, future events and outcomes never enter those features. Both
seats receive all their actual decisions, including interrupting choices. Invalid
policy results or exceptions fail the episode with a separate diagnostic; they
never switch policy, retry an action or reseed.

## Records, boundaries and failures

Each episode uses the unchanged [DATA-02 JSONL channels and manifest](recording.md).
`SimulationSession.start_recording` and `ScenarioSession.start_recording` transfer
the zero-coach-decision timeline to the recorder. The session retains its initial
home observation; scenario attachment imports the validated uncaused synthetic
phase prefix without resetting counters. Attachment after a coach decision or to
an already recorded timeline is rejected. The caller owns recorder finish/close;
sessions imported with `from_snapshot` lack the original reset observation and
are also rejected before creating a writer.
Reset during active recording is rejected, and recording cannot be rewound.
Existing direct `EpisodeRecorder(Game, ...)` behavior remains supported.

`dataset.json` contains the plan hash and each confirmed episode's manifest hash,
semantic hash and exact outcome flags. The semantic hash covers the canonical
manifest and all stored channel rows, including public events/actions/observations.
The hash streams those canonical UTF-8 bytes after DATA-02 validation, retaining
V1 hash values without treating the entire episode as a single 4 MiB record.
Individual records retain that limit; their aggregate retains the 128 MiB
episode bound. Hashes and the encodable summary are prepared from the validated
persisted rows before the episode is atomically confirmed. A preparation failure
therefore leaves a partial recording rather than a confirmed-but-failed episode.
No incidental audit fields are currently emitted or excluded from hashing.
A physically separate privileged generation row retains the start/horizon and
outcome for cross-checking; it is never read by `EpisodeReader.read_inputs()`.

Outcome precedence is natural `game_over`, scenario objective/turn end, session
execution failure/budget, decision budget, then fragment horizon. Match completion
sets `terminated`; budgets and fragment horizons set `truncated` without inventing
wins, losses or a game-over flag. Scenario completion sets `scenario_terminal`,
with an independent boolean `scenario_success`; neither match flag is set.
The DATA-02 V1 manifest has only terminal-match and truncated-match end kinds,
so a finished exercise is a match fragment with
`{kind: truncated, reason: scenario_terminal}` there. The dataset's explicit
scenario flags preserve the exercise outcome without claiming a completed match.

An engine automatic-step budget interruption preserves the admitted pending
transition, actual partial state and diagnostics, then confirms a truncated
episode. Unexpected engine errors, invalid policies and cancellation leave no
confirmed failed episode. Writer failures leave `.partial` storage and close all
handles. Separate `episode-ID.failure.json` diagnostics omit arbitrary exception
text and payloads. If the filesystem also rejects the diagnostic write, the
original error and partial directory remain the evidence. Previously confirmed
episodes remain valid; an interrupted job lacks `dataset.json` and is not a
complete dataset. There is no granular restart contract.

## Replay and validation

`validate` uses `EpisodeReader.read_episode()` and `read_inputs()` on every declared
episode, checks plan identities, channel integrity and outcome summaries, and
rejects missing episodes or changed hashes. It is integrity validation, not
cryptographic authentication or independent adjudication of scenario success.

Replay reconstructs the initial recipe and engine RNG semantics, then submits
exact `ActionV1` rows through the session, without constructing or invoking
policies. To supply a sequence explicitly, extract the `action` field from each
transition into one JSON object per line, then run:

```sh
python -m botbowl.lab.generate replay /tmp/botbowl-small episode-000000 \
  --actions /tmp/actions.jsonl --output /tmp/botbowl-supplied
```

Missing, extra, malformed, wrong-actor and illegal actions fail rather than
selecting a fallback. An alternative legal sequence may produce a different
outcome; compare returned episode semantic hashes to test exact reproduction.
A replay dataset explicitly names the selected original episode and keeps the
original plan. No images, snapshots or hidden agent state are needed. Pin the
engine implementation and dependency environment as well as the declared
versions; cross-version/backend equivalence is not promised.

`examples/lab/generate.py` demonstrates generation, full reading, extracting
supplied actions and comparing replay hashes in temporary directories.
