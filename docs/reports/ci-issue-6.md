# Issue #6 — hosted CI and dependency validation

This change is based on accepted integration
`23d8c09a5822157a510174c9f0c6bfd3501f74f0`, containing reviewed #4/#5/#7/#13.
It changes dependency metadata/constraints, the hosted Tests workflow, CI helpers,
documentation, one coordinated core/Gym test boundary, and the existing sdist
verifier’s installer bootstrap/freeze. Production engine and Skillforge launcher
sources are unchanged. The transient validation PR is
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

The first restored head `eb3dd12c3fb93a3114b4acb4c117f64342d14ef6`
started run [34105429666](https://github.com/murillo128/botbowl/actions/runs/34105429666).
Inventory inspection then found ensurepip-seeded setuptools 65.5.0/79.0.1 outside
the maintained pins. Those versions have explicit audit findings. A narrow
bootstrap correction upgrades pip/setuptools under the existing constraints in
both CI venv creation and the sdist verifier; the fresh-source/native assertions
are preserved. The superseded run is retained as partial evidence, and a new
exact-head matrix is required after the correction.

## Dependency audit

[The dated audit](dependency-audit-2026-09-07.md) records current primary-source
versions, immutable action revisions and explicit dispositions for all inherited
findings. Updated scans use strict pip-audit with no ignored advisory. These are
observed package identities at the audit date, not a universal claim for every
library range or future vulnerability database.

## Final tested source

Source head `51360cce6fa1dd0c53e9089cec44ed19e7ddd25c` passed all **23 jobs**
in [run 34105952754](https://github.com/murillo128/botbowl/actions/runs/34105952754).
The downloaded artifacts identify that exact source in 22 main/nested reports;
all 25 installed inventories match the selected constraints, including maintained
pip/setuptools bootstrap tools. Three strict scans cover 61 RL 3.11, 58 core 3.11
and 57 core 3.14 third-party identities, all without advisories or skipped identities.

Each Python-reference cell passes 712 tests with 288 explicit skips and one #16
xfail; each native core cell passes 991 with nine Gym skips and one #16 xfail.
Each legacy RL cell passes 1,018 with no skips and one #16 xfail. The extra two
passing core variants account for the increase from the inherited 1,016-test RL
baseline. All required core and RL assertions execute in their supported profiles.

| Profile | Final job seconds (including setup/upload) |
| --- | --- |
| artifacts / macos-15 | 57 |
| artifacts / ubuntu-24.04 | 56 |
| artifacts / windows-2025 | 128 |
| core / 3.11 / native | 571 |
| core / 3.11 / python | 336 |
| core / 3.12 / native | 703 |
| core / 3.12 / python | 388 |
| core / 3.13 / native | 539 |
| core / 3.13 / python | 349 |
| core / 3.14 / native | 404 |
| core / 3.14 / python | 268 |
| dependency audit / 3.11 / core | 30 |
| dependency audit / 3.11 / rl | 28 |
| dependency audit / 3.14 / core | 28 |
| extra / competition / 3.11 | 23 |
| extra / dev / 3.11 | 25 |
| extra / render / 3.11 | 34 |
| extra / rl / 3.11 | 22 |
| extra / rl / 3.12 | 21 |
| extra / web / 3.11 | 27 |
| legacy RL / 3.11 | 622 |
| legacy RL / 3.12 | 638 |
| lint | 11 |

Exact patch interpreters are retained per profile in the compact evidence:
3.11.16, 3.11.9, 3.12.14, 3.13.15 and 3.14.7. Linux full-suite capability cells
use the corresponding modern 3.11–3.14 builds; platform packaging claims only
the actual Python 3.11 build each hosted platform supplied. The package audit is
a PyPI dependency-identity audit, not an OS/interpreter vulnerability audit.

The final documentation-only descendant records these observations without
changing code/configuration. Its exact SHA and pending/completed CI status are
recorded in the PR handoff. Parent explicitly permits independent review while
that report-only-head CI runs, and requires the exact-head gate before integration.
No earlier source run is relabeled as a later-source run. Parent has accepted #14
separately but explicitly instructed this owner to preserve the current immutable
base and let reviewed integration resolve the coordinated test-function boundary.

## Evidence locations and limits

Local runtimes and complete logs: `/tmp/botbowl-issue6-evidence/`,
`/tmp/botbowl-issue6-tools/venv/`, `/tmp/botbowl-issue6-test-adjustment/`.
Hosted results retain per-job source revision, interpreter/dependency versions,
step durations, JUnit and logs as 14-day Actions artifacts. Initial/control job counts and durations are retained in
[compact evidence](ci-issue-6-evidence.json). The final report head, hosted status and review gate are recorded in the
final-capable handoff comment on PR #82; the complete tested-source results are
retained here and in the compact evidence. This report
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
