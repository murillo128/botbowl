# Rules descriptor evidence — issue #30

The accepted base is `1cf22c3801ce3dd81587c448d6f382cd6918bdf4`.
Final tested implementation is `440902fefc100f1d37290807731faa66ae515397`;
the subsequent report commit records evidence without changing that code.
The [API contract and field inventory](../lab/rules.md) describe the delivered
descriptor, catalogue, canonicalization and version policy. No `Game`, loader,
engine rule, dependency, CI workflow or asset changes are included.

## Material field findings and corrections

The inherited `RuleSet.__init__` uses shared mutable list/dict defaults. In a fresh
installed process, `load_rule_set("BB2016")` yielded 24 race records, 71 stars and
8 inducements. Loading it again grew those *same list objects* to 48/142/16.
`get_role("Lineman", "Human")` still returned the exact same first role object.
The descriptor remained equal before/after the duplicate load. Loading
`LRB5-Experimental` into that process then produced a typed
`IncoherentResourceError: Conflicting duplicate resource records`.

This justifies the bounded normalization: unique race/role lookup is by name;
identical repeated projections add no effective choice, while any differing
projection with the same name fails instead of being silently discarded. Star
and inducement records are retained as resource inventory, not executed pregame
support. Lists inside records (skills, injuries, etc.), formation ordering and
team/player ordering remain intact. The descriptor neither reloads resources
nor repairs the shared defaults. Tests restore fresh defaults only within their
own fixture so unrelated tests/owners retain their original objects.

An initial field audit missed **player jersey number**. The negative regression
against `adc0ea3af7ea4c2351c5795e97b08608daa9e25b` uses two home players, seed 17
and forced D6 rolls `[6, 6, 1, 6]`: two rolls select pitch invasion and two resolve
it. Swapping jersey numbers changes the stunned vector from `[False, True]` to
`[True, False]`, because `KickoffTable` sorts players by `nr`. The old descriptor
incorrectly stayed equal. The retained test fails solely at that digest
inequality (**1 failure, 74 deselected**), after both behavioral assertions pass.
The correction includes `nr`, removes its exclusion assertion and updates the
field inventory. This is a descriptor correction, with production kickoff
behavior unchanged; it does not take ownership of #20.

The audit also includes `debug_mode` because `_one_step` enables runtime assertions,
not just printing. Formation names select setup actions and must be included.
`kick_scatter_distance` and `dungeon` have no configuration consumers in the
current engine; `kick_scatter_dice` is the effective scatter input. Names of
teams/players and `config.name` have no rules consumers. UUIDs are excluded after
checking uniqueness; a duplicate ID is incoherent, not a second equivalent input.
The explicit field inventory is not a promise to identify arbitrary monkeypatched
code or bots that inspect cosmetic metadata.

An earlier fixture error assumed small boards supplied an Orc team. Initial
descriptor validation was **59 pass / 9 fail**, all nine failing at the existing
team loader before descriptor construction. The fixture now uses bundled human
teams for sizes 1/3/5/7 and Human/Orc at size 11. The resource inventory is unchanged.
Both this failed run and the jersey negative control retain their logs and JUnit.

## Validation

See [compact evidence](rules-issue-30.json) for exact source identities, counts,
durations and environment summaries. All local artifact/suite runs use Linux
x86_64 and CPython 3.11.16, under the accepted dependency constraints. The native
build loads a real CPython extension; Python wheels use the reference pathfinder.
Each suite profile exports committed source, installs its wheel in a new venv,
copies tests/examples to a directory without `botbowl`, and asserts site-packages
and the required backend before collection. No other owner's environment or
loaded extension is moved or modified.

The focused source run at the corrected implementation passes **140 tests and
1 inherited timestamp xfail** (77 descriptor tests plus 63 #4 fixture checks).
The installed Python wheel passes the explicit descriptor command: **77 passed**.
The installed native wheel passes descriptor plus #4 fixtures: **140 passed,
1 inherited xfail**. A separate subprocess comparison across all five sizes
confirms that the complete installed Python/native descriptors differ only in
`backend_id`, including equal resource/config digests. Game/resource pickle bytes,
global Python/NumPy RNG states and forced-roll queues remain unchanged by
descriptor construction in the side-effect regression.

The final artifact profile passes: fresh compiler-disabled Python wheel/sdist,
native wheel from that sdist, separate minimal installs, dependency checks and
headless smokes for all five sizes. Each smoke now constructs a descriptor and
continues through three existing game decisions. Optional web/Gym/Docker/render
packages and pytest are absent from the minimal installs; the smoke also checks
that optional modules and Tk are not imported. Artwork remains excluded.

Repository error lint (`E9,F63,F7,F82`) and `git diff --check` pass. The complete
installed Python-core and native/RL suite results are recorded in the compact
evidence:

| Final local profile | Result | Wall seconds (build/install/tests) |
| --- | --- | --- |
| Installed Python core | 1,098 passed, 288 capability skips, 1 inherited xfail | 257.18 |
| Installed native/RL | 1,404 passed, no skips, 1 inherited xfail | 434.06 |
| Fresh artifacts and minimal headless smokes | All build/install/check/smoke steps passed | 29.70 |

The native/RL count is the accepted 1,327-test baseline plus 77 descriptor tests.
Its 102 inherited Gym checker warnings remain observations, not API conformance.
The Python profile intentionally excludes the legacy Gym module and
records unavailable Gym/native cases as explicit capability skips; the native/RL
profile runs those capabilities. The inherited strict human end-time xfail stays
owned by #16; no expectation or skip is added to hide an engine failure.

The initial full profiles target `adc0ea3`, before the jersey correction, and
remain superseded evidence. They do not establish final-source acceptance.
Final-source local profiles target `440902f`; hosted CI must identify the exact
published report head. Fresh independent review of the full inclusion/exclusion
boundary and capability claims remains a separate parent-owned gate. No earlier
green run substitutes for either gate.

## Reproduction and raw evidence

From the issue worktree at the tested implementation, with an isolated Python
environment containing the constrained build tool:

```bash
python -m pytest tests/lab/test_rules_descriptor.py tests/game/test_baseline.py
python tools/ci/run_profile.py suite --backend python --output /tmp/botbowl-issue30-repro-python
python tools/ci/run_profile.py suite --rl --backend native --output /tmp/botbowl-issue30-repro-native
python tools/ci/run_profile.py artifacts --output /tmp/botbowl-issue30-repro-artifacts
```

Each output directory must be new. The repository helper uses `git archive HEAD`,
so uncommitted edits are deliberately not tested. It records `result.json`,
JUnit, build/install/test logs, package freeze, wheel and import identity.

Raw evidence is retained under `/tmp/botbowl-issue30-evidence/`; final profile
outputs and installed runtimes are `/tmp/botbowl-issue30-final-python-suite/`,
`/tmp/botbowl-issue30-final-native-rl-suite/` and
`/tmp/botbowl-issue30-final-artifacts/`. The tooling runtime is
`/tmp/botbowl-issue30-tools/`. `jersey-negative.log/.xml` preserves the failed
criterion, `loader-observation.log` preserves duplicate/mixed-edition behavior,
and `compare-backends.py/.log` plus `descriptors-python.json` and
`descriptors-native.json` preserve the cross-backend comparison. Superseded
profiles use `/tmp/botbowl-issue30-python-suite/` and
`/tmp/botbowl-issue30-native-rl-suite/`. No binaries or bulky logs are committed.

These local results establish the tested Linux/Python environment only. Full
rules-edition conformance, arbitrary custom-geometry/native-table behavior,
exhaustive skill interactions, and unresolved QuickSnap/forward-model work are
not claimed. The catalogue's evidence limitations remain part of its data.

Catalogue wording clarification: the baseline fixtures set `kick_off_table=False`
and disable pathfinding, but inherited `Kickoff.step` still schedules kickoff
events. The [independent observation at `09a470d`](https://github.com/murillo128/botbowl/pull/87#pullrequestreview-5131205904)
records seed-17 RIOT events for sizes 1/7/11 and PERFECT_DEFENSE for sizes 3/5;
its reproduction and output remain in
`/tmp/botbowl-review30-probes/catalogue_wording.py` and `catalogue-wording.log`.
This corrects the catalogue's disabled-kickoff claim without changing engine
behavior or reattributing the source-specific suite/artifact evidence above.
