# Issue #6 — hosted CI and dependency validation

This change is based on accepted integration
`23d8c09a5822157a510174c9f0c6bfd3501f74f0`, containing reviewed #4/#5/#7/#13.
It changes dependency metadata/constraints, the hosted Tests workflow, CI helpers,
documentation and one coordinated core/Gym test boundary. Production engine and
Skillforge launcher sources are unchanged. The transient validation PR is
[#82](https://github.com/murillo128/botbowl/pull/82); epic delivery remains #81.

## Scope and isolation

[Profile documentation](../ci.md) defines the matrix, installed-artifact isolation,
skip reasons and reproducibility commands. The workflow has read-only contents,
explicit GitHub-hosted OS labels, bounded timeouts, branch/PR cancellation and
immutable maintained action revisions. PR head checkout disables credential
persistence. Only `pull_request` and push-to-main run these jobs. The separate
issue-label launcher remains byte-identical to the accepted base; no PR execution
uses self-hosted/Codex or `pull_request_target`.

Native and Python jobs assert module identity before test collection. Local inverse
controls both exit 4: asking the native installation for Python, and the Python
installation for native, fails with the actual loaded module in the diagnostic.
The suite never imports checkout `botbowl`: the built wheel is installed in a new
venv and tests/examples are copied into a separate directory without the package.

The sole test-path adjustment parameterizes
`test_disabled_block_choice_and_gym_mask_wait_for_reroll` with core and Gym-mask
variants. All previous assertions survive. Both core cases execute without Gym;
only the two Gym variants skip when the capability is absent, explicitly linked
to #17. Local focused controls: core 2 pass / 2 capability skips; RL 4 pass.
Parent authorized this exact boundary in #6 coordination; #14 independently owns
the forced-dice injection calls inside the same function. Both edits must survive
integration, followed by fresh combined validation/review.

## Initial evidence and corrections

Initial implementation `e6d9cee0a3cfb8cc87e4a7c19bb1a554dca462c2` ran as
[34103857236](https://github.com/murillo128/botbowl/actions/runs/34103857236).
The missing Gym import in the mixed action-validation test produced two failures
in core profiles. This is retained failed evidence; the capability split above
corrects the test boundary without dropping the engine assertions. Local native
core validation at this initial head: unit 497 pass / 2 fail / 7 skip / 1 known
xfail (127.99 s); integration 492 pass (227.70 s).

Local Linux 3.11 artifact profile at the same head passed in 27.01 s: fresh source
export, compiler-unavailable Python wheel/sdist, native wheel built from that
sdist, three clean minimal artifact installs/smokes and `pip check`. It used
NumPy 2.4.6 with the new setuptools/Cython constraints. This evidence is attributed
to the exact initial source; earlier #5 report runs are not relabeled as later
Gym-registration or current-source runs.

## Hosted acceptance controls

The initial run completed all 23 jobs. Both legacy RL jobs passed 1,016 tests
plus the known #16 xfail (614 s and 639 s wall-clock). Lint, all six independent
extra smokes, all three platform artifact profiles and all three dependency
audits passed. Eight core jobs retained the two mixed Gym-import failures;
all their integration tests passed. This failed initial run is preserved.

The negative-control head is
`00f4aa1e8a7407f5001b149135275d2cf99cfda8`, run
[34104931293](https://github.com/murillo128/botbowl/actions/runs/34104931293).
It includes the coordinated capability correction and two deliberate errors in
separate temporary files. Hosted lint job 101687667157 fails with Ruff F821 for
`issue6_deliberately_undefined_name`, exit 1. The pytest control asserts
`False` with message `issue6 hosted pytest negative control`. Hosted Python 3.14 job 101687667794 fails only that assertion: unit 499 pass /
1 fail / 9 capability skips / 1 known xfail; integration 213 pass / 279 native
skips. Job duration and combined counts are retained in the compact evidence.

Both temporary files are removed by an additive restore; the negative commit
and red jobs remain in shared history. The restored source runs the complete
23-job matrix. The unfinished negative matrix jobs may be canceled by the restore
push after completed lint/pytest failures establish the controls; cancellation
is not a correctness result or a substitute for final matrix validation.

## Dependency audit

[The dated audit](dependency-audit-2026-09-07.md) records current primary-source
versions, immutable action revisions and explicit dispositions for all inherited
findings. Updated scans use strict pip-audit with no ignored advisory. These are
observed package identities at the audit date, not a universal claim for every
library range or future vulnerability database.

## Evidence locations and limits

Local runtimes and complete logs: `/tmp/botbowl-issue6-evidence/`,
`/tmp/botbowl-issue6-tools/venv/`, `/tmp/botbowl-issue6-test-adjustment/`.
Hosted results retain per-job source revision, interpreter/dependency versions,
step durations, JUnit and logs as 14-day Actions artifacts. Initial/control job counts and durations are retained in
[compact evidence](ci-issue-6-evidence.json). The final exact head, hosted run,
per-job counts/durations and gate verdict are recorded in the final-capable
handoff comment on PR #82, after the restore head completes CI. This report
does not infer a final green result from an earlier revision. Bulky raw logs
and built artifacts remain outside Git.

No coverage percentage is claimed. Full suites run on Linux; other platforms
have Python 3.11 artifact smokes only. Independent extra coverage is Linux 3.11
(web/competition/dev/render/RL) plus RL 3.12, not the Cartesian product. Competition
smoke imports helpers without running Docker containers; rendering uses Agg.
Native-only comparisons skip in reference jobs and execute in native jobs.
The inherited strict end-time xfail belongs to #16. Known inherited comparison/
forward-model defects remain under #20; this CI task adds no waiver, engine fix,
blanket skip, or expectation change for them.
