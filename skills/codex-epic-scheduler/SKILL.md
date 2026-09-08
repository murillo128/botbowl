---
name: codex-epic-scheduler
description: Select and promote dependency-ready queued children from one event-driven epic DAG, publishing an exact execution context before activation without dispatching, reviewing, or integrating work.
---

# Codex Epic Scheduler

## Responsibility

Use this skill only when the controlling parent issue declares `execution_mode: epic-dag` and a closed `children` graph. GitHub issues, labels, and the parent-declared integration branch are the persistent source of truth. Each invocation validates current state, promotes one deterministic wave, and terminates; it does not depend on memory from an earlier turn.

The scheduler owns only:

1. reading the parent contract and every declared child's current workflow-state label;
2. validating the closed DAG and integration branch before any mutation;
3. selecting eligible `queued` children within the declared worker and mutex limits;
4. resolving one exact `base_sha` from the current head of the parent's `integration_branch` for the selected wave;
5. creating or updating one canonical machine-readable execution-context comment on each selected child;
6. replacing `queued` with `execution-ready` on those children only after their execution context is durable;
7. terminating.

The execution-context publication is scheduling metadata, not worker dispatch or integration.

## Strict boundaries

The scheduler must not launch or wait for workers, implement or modify a child, invoke independent review, inspect or mutate pull requests, resolve exceptional workflow states, create or move branches, compose reviewed heads, resolve Git conflicts, perform integration, close or complete the parent, or move the default branch.

It may read the declared integration branch only to resolve its current exact head SHA before activating a wave.

In particular:

- `blocked`, `design-required`, and `investigation-required` are manual paths and are never promoted by the scheduler;
- ordinary dependency waiting is `queued`, not `blocked`;
- `review-ready` does not satisfy a dependency: only `completed` does;
- legacy fields such as `kind`, `ready_state`, `mandatory`, `initial_base`, and `final_integration` do not govern scheduling and may be ignored;
- stale branch/base references in child issue prose do not govern execution once a canonical execution-context comment exists;
- batch orchestration is unsupported.

## Entry and parent state

Load `AGENTS.md`, the parent issue, this skill, and `codex-github-operations`. Confirm that the repository and issue match the launch context. Parent-scoped launcher concurrency owns duplicate-session prevention; do not inspect or manage worker sessions here.

The parent must have exactly one workflow-state label:

- on its first invocation it may be `execution-ready`;
- on later invocations it must be `in-progress`.

Do not change the parent yet. Graph and integration-context validation precede every mutation. After successful validation, replace a first-run parent's `execution-ready` label with `in-progress`; otherwise leave its `in-progress` label unchanged.

## Read and validate the closed graph

Read the fenced YAML contract that contains `execution_mode: epic-dag`. The scheduler consumes at least:

```yaml
execution_mode: epic-dag
integration_branch: codex/epic-issue-3
max_parallel_workers: 2
children:
  - issue: 10
    depends_on: []
    mutex: [engine]
  - issue: 11
    depends_on: [10]
    mutex: []
```

Before changing the parent or any child, fail closed unless all of these are true:

- there is one unambiguous epic contract;
- `integration_branch` is one non-empty branch name in the same repository and that branch currently exists;
- `max_parallel_workers` is an integer of at least one;
- `children` is a closed list of unique positive issue numbers;
- every child issue exists in the same repository;
- every child has exactly one official workflow-state label;
- every `depends_on` value is a unique declared child, is not the child itself, and the resulting graph is acyclic;
- every `mutex` entry is a non-empty string and is unique within that child.

Do not infer children, dependencies, or integration targets from child issue prose, checklists, number order, milestones, links, pull requests, or existing implementation branches. A validation failure makes no state mutation: terminate and report the exact defect for manual design handling. It is not permission to repair the graph or change a workflow state.

## Observable state classification

For slot accounting, classify children only from their single current workflow-state label:

- **queued:** `queued`;
- **active and consuming one slot:** `execution-ready` or `in-progress`;
- **slot released and not schedulable:** `review-ready`, `completed`, `blocked`, `design-required`, or `investigation-required`.

No other signal consumes or releases a slot. Do not inspect worker sessions or PRs to refine this classification. If active children already meet or exceed `max_parallel_workers`, select nothing.

## Deterministic wave selection

A child is a candidate only when:

1. its current state is exactly `queued`; and
2. every issue in its `depends_on` list currently has state exactly `completed`.

Calculate `available_slots = max(0, max_parallel_workers - active_children)`. Collect mutex values held by active children. Then inspect candidates by ascending issue number and greedily select a child only when:

- a slot remains; and
- none of its mutex values is held by an active child or by a candidate already selected in this wave.

Continue in issue-number order until no slot remains or no further candidate fits. This ordering is the only tie-breaker; do not maximize cardinality by skipping an earlier compatible choice in favor of a different combination.

The bundled pure planner at `skills/codex-epic-scheduler/scripts/plan_wave.py` accepts a normalized JSON snapshot and prints the validation and selection result without GitHub mutations. Use it when practical to make the planned wave inspectable. The snapshot contains `parent_state`, `execution_mode`, `max_parallel_workers`, and each child's `issue`, `depends_on`, `mutex`, and observed `state`. `integration_branch` and `base_sha` are control-plane metadata and do not affect DAG ordering.

## Canonical execution context

The parent owns the integration target. Child issue bodies do not need to duplicate it.

Immediately before applying a non-empty wave:

1. re-read the parent and all child workflow-state labels and recompute the plan if any observed state changed;
2. re-read the parent's declared `integration_branch` and fail closed if it changed to an invalid or missing branch;
3. resolve that branch's current exact 40-character commit SHA **once**; call it `base_sha`;
4. use the same `integration_branch` and `base_sha` for every child selected in that wave, even if the branch advances while the wave is being activated.

Each selected child must have exactly one canonical execution-context comment identified by this exact marker:

```text
<!-- codex-execution-context:v1 -->
```

The canonical comment body is:

````markdown
<!-- codex-execution-context:v1 -->
## Execution context

```yaml
epic_issue: 3
integration_branch: codex/epic-issue-3
base_sha: 0123456789abcdef0123456789abcdef01234567
```

This context is authoritative for the next execution. The scheduler updates this same comment on later activations; stale branch or base references in issue prose are non-authoritative while this comment exists.
````

Use the actual parent issue number, integration branch, and resolved SHA. Do not include timestamps or incidental scheduler/session identifiers.

For each selected child, search its top-level issue comments for the exact marker:

- zero matches: create the canonical comment;
- exactly one match: update that comment in place with the new context;
- more than one match: fail closed for that child and do not activate it; duplicate authoritative contexts are ambiguous and must be repaired deliberately.

Never append a new context comment when a canonical one already exists. A child reactivated after returning to `queued` receives an updated comment, so the current activation context supersedes earlier branch/base information without editing the child body.

## Apply the selected transition

For each selected child in ascending order:

1. re-read that child's labels;
2. if its single workflow state is no longer exactly `queued`, skip it without changing its comment or labels;
3. create or update and then verify its canonical execution-context comment as described above;
4. only after that durable context is verified, use `codex-github-operations` to replace `queued` with `execution-ready` while preserving unrelated labels;
5. verify the resulting single workflow state is `execution-ready`.

The comment must be durable **before** `execution-ready` is observable, so the launcher can never legitimately start a newly scheduled child without an execution context.

If comment publication succeeds but the label transition fails or races, leave the child non-executable. A later scheduler invocation may safely update the same comment again and retry selection. Do not fill a newly available slot after the chosen wave begins; a later event-driven invocation will recompute from GitHub.

## Idempotency and termination

A promoted child is no longer `queued`, so the same observed state can never select it again. Repeated invocations recompute from current labels and update a canonical comment only for children actually selected for a new activation. Wake-up events may be duplicated or delayed; they do not broaden scheduler authority.

After the selected context publications and label replacements are verified, terminate the turn. If no candidates qualify, terminate successfully with the parent still `in-progress`. Even when every child is `completed`, do not close, complete, review, or integrate the epic.

Report only the validated child count, active count, available slots, selected issue numbers, the integration branch and wave `base_sha` when a wave was activated, skipped races if any, and confirmation that no other state was changed.
