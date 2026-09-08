# AGENTS.md

Repository-wide instructions for ChatGPT, Codex, and other development agents.

## Mission and scope

Bot Bowl is a Python framework for playing Blood Bowl and developing/evaluating AI bots. Preserve that project scope unless an authoritative repository decision or controlling issue explicitly changes it.

The durable product/domain description belongs in `README.md` and the repository documentation that explicitly owns it. Do not invent adjacent project goals or turn exploratory discussion into settled scope.

This file owns repository-wide agent invariants and routes work to reusable skills. Skills define reusable procedure; issues define bounded task contracts; repository documentation defines durable project knowledge.

## Load context progressively

For non-trivial work, start with:

1. `AGENTS.md`;
2. the controlling GitHub issue.

Then load only the context needed for the current role and task: the exact accepted specification or decision referenced by the issue, relevant source/tests/build/configuration/evidence, and the one workflow skill that owns the current action.

Do not preload every repository document, skill, issue/PR history, generated result, or derived wiki. On resume, verify branch, `HEAD`, worktree state, the issue's current state label, and new material discussion since the last handoff.

## Source-of-truth hierarchy

Unless a project-specific document defines a stricter hierarchy, use this order:

1. Tests, formal checks, evaluation outputs, and captured evidence establish observed behavior.
2. Accepted specifications, decisions, architecture documents, `docs/**`, and other explicitly normative repository documents establish durable intended behavior within their scope.
3. The controlling GitHub issue establishes the bounded execution contract for the active task.
4. Pull requests, checks, reviews, commits, and Git history preserve implementation and reproducible evidence.
5. Planning documents establish planning/dependency status only to the extent they explicitly claim authority.
6. Exploratory notes and drafts remain provisional unless explicitly adopted.
7. `wiki/**` is agent-generated, derived, non-normative project knowledge and never overrides stronger sources.
8. Chat discussion is provisional until intentionally recorded in an authoritative repository or GitHub source.

When authoritative sources materially conflict, do not silently choose one. Surface the conflict and return to the appropriate design/decision authority.

## Skill-driven workflow

Load skills lazily by role:

- optional local Codex runner provisioning/repair: `skills/codex-local-runner/SKILL.md`;
- design authority: `skills/design-github-issue/SKILL.md`;
- main executor: `skills/spec-driven-codex-loop/SKILL.md`;
- pull-request audit controller: `skills/codex-pr-audit/SKILL.md`;
- Git and GitHub mutation/publication: `skills/codex-github-operations/SKILL.md`;
- independent checkpoint/final technical review: `skills/codex-independent-review/SKILL.md`;
- event-driven epic-DAG scheduling: `skills/codex-epic-scheduler/SKILL.md`;
- derived repository wiki curation: `skills/repository-wiki-curation/SKILL.md`.

Do not read a role skill merely because it exists. Keep repository-wide invariants here, reusable procedure in skills, and task-specific scope/inputs/commands/gates in the controlling issue.

## Workflow state

For non-trivial controlling issues, use exactly one current workflow-state label:

- `queued`
- `execution-ready`
- `in-progress`
- `review-ready`
- `design-required`
- `investigation-required`
- `blocked`
- `completed`

The label is authoritative for current workflow state. State-only transitions should not produce comments whose sole purpose is to announce the transition.

`review-ready` is the executor's successful terminal state: implementation, validation, and required final technical review are complete and the PR is ready for user-facing review. `completed` is post-acceptance/post-merge. Executors and independent reviewers must not merge or enable auto-merge on their own authority.

`queued` is the normal waiting state for a fully defined child of an active or planned epic. It is not a blocker. Only `codex-epic-scheduler` may automatically move a child from `queued` to `execution-ready`; an actor that explicitly resolves `blocked`, `design-required`, or `investigation-required` may return that child to `queued`.

The launcher for an `execution-ready` issue is always the same. After launch, the agent reads the controlling issue: when it declares `execution_mode: epic-dag`, route to `codex-epic-scheduler`; otherwise route to `spec-driven-codex-loop`. Keep this routing decision in agent instructions rather than duplicating it in the launcher Action.

## Bot Bowl repository invariants

- Primary implementation lives under `botbowl/**`; tests live under `tests/**`; examples and documentation live under `examples/**` and `docs/**`.
- The documented baseline is Python 3.8 or newer. Do not intentionally reduce supported runtime compatibility unless the controlling issue explicitly approves it.
- Repository-native development setup is `python setup.py build` followed by `pip install -e .` when a local editable installation is needed. The build may compile the optional Cython/C++ pathfinding implementation when a suitable compiler is present; compiler availability is not a universal correctness requirement unless a task specifically targets that path.
- The repository-native test command is `pytest` from the repository root. Behavior changes should add or update focused tests when appropriate, and non-trivial implementation should run the relevant tests plus the full suite when practical and risk-appropriate.
- Preserve behavior outside the controlling issue's scope. Avoid broad modernization, dependency churn, formatting sweeps, or upstream cleanup as incidental work.
- This is a fork of upstream Bot Bowl. Do not overwrite upstream attribution, project history, or compatibility merely to make the fork look standalone. Deliberate divergence should be issue-driven and documented where it materially changes project behavior.
- The source-code license does not automatically cover all graphics/assets. `README.md` explicitly notes separate copyrighted artwork and permissions. Do not redistribute, replace, relicense, or make rights claims about those assets without verifying the applicable permissions.

## Design and execution discipline

For non-trivial changes, design the controlling issue so a fresh executor can work without hidden chat reasoning; implement the smallest coherent outcome; preserve behavior outside scope; validate with repository-native checks proportional to risk; retain enough evidence for the claims being made; and use independent review at issue-declared material checkpoints.

Do not invent project-wide roadmaps, schemas, frameworks, ontologies, or process machinery merely because they might be useful later. Do not mechanically implement every reviewer suggestion; judge findings against the controlling contract, materiality, and repository invariants.

## Durable docs and derived wiki

Keep deliberate normative documentation and generated memory distinct. `docs/**` and other explicitly normative project documents are maintained through the normal design/execution workflow. `wiki/**` is generated, derived, non-normative memory maintained by `repository-wiki-curation`. GitHub issues own actionable discrepancies, unresolved decisions, suspected drift/defects, and bounded work contracts.

The wiki curator has standing ownership only of `wiki/**` and may publish there directly to the default branch only after its mandatory adversarial and write-boundary gates pass. It must never modify or publish a non-wiki path.

## Evidence and artifacts

Keep evidence proportional to the claim. Commit source, tests, configuration, small deterministic fixtures, concise reports, and compact evidence that materially helps inspection or reproduction. Large generated outputs, binaries, model weights, datasets, caches, traces, or bulky logs should stay outside Git unless explicitly required and redistribution is permitted.

Never publish secrets, credentials, private data, or artifacts without the necessary rights.

## Git and GitHub behavior

- Keep changes scoped; avoid unrelated cleanup or formatting.
- Use explicit paths when staging or publishing changes.
- Agent-created commits follow the convention owned by `skills/codex-github-operations/SKILL.md`.
- Do not force-push or rewrite shared valid history without explicit user authorization.
- Direct commits to the default branch require explicit user instruction except for the narrowly authorized `repository-wiki-curation` workflow.
- A Codex implementation workflow ends with a ready-for-review pull request, the controlling issue transitioned from `in-progress` to `review-ready`, and a handoff.
- Before that `review-ready` transition, the executor must complete every remaining GitHub mutation required for the handoff: final push/publication, PR metadata and ready-for-review state, final technical/handoff comments, and any remote verification that could require a corrective mutation. Replacing `in-progress` with `review-ready` must be the executor's **final GitHub mutation**. After that transition the executor may perform only local teardown, local bookkeeping, response composition, and normal turn/session completion; it must not push, comment, edit PR/issue metadata, change labels, or otherwise mutate GitHub.
- Merge into the default branch requires a later explicit user-facing instruction after review finds no material blocker.
