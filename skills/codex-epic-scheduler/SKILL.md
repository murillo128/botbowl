---
name: codex-epic-scheduler
description: Select and promote dependency-ready queued children from one event-driven epic DAG without dispatching, reviewing, integrating, or retaining session state.
---

# Codex Epic Scheduler

## Responsibility

Use this skill only when the controlling parent issue declares `execution_mode: epic-dag` and a closed `children` graph. GitHub issues and labels are the complete source of truth. Each invocation validates current state, promotes one deterministic wave, and terminates; it does not depend on memory from an earlier turn.

The scheduler owns only:

1. reading the parent contract and every declared child's current workflow-state label;
2. validating the closed DAG before any mutation;
3. selecting eligible `queued` children within the declared worker and mutex limits;
4. replacing `queued` with `execution-ready` on the selected children;
5. terminating.

## Strict boundaries

The scheduler must not launch or wait for workers, implement or modify a child, invoke independent review, inspect or mutate pull requests, resolve exceptional workflow states, create branches or integration bases, compose reviewed heads, resolve Git conflicts, perform final integration, close or complete the parent, or move the default branch.

In particular:

- `blocked`, `design-required`, and `investigation-required` are manual paths and are never promoted by the scheduler;
- ordinary dependency waiting is `queued`, not `blocked`;
- `review-ready` does not satisfy a dependency: only `completed` does;
- legacy fields such as `kind`, `ready_state`, `mandatory`, `initial_base`, and `final_integration` do not govern scheduling and may be ignored;
- batch orchestration is unsupported.

## Entry and parent state

Load `AGENTS.md`, the parent issue, this skill, and `codex-github-operations`. Confirm that the repository and issue match the launch context. Parent-scoped launcher concurrency owns duplicate-session prevention; do not inspect or manage worker sessions here.

The parent must have exactly one workflow-state label:

- on its first invocation it may be `execution-ready`;
- on later invocations it must be `in-progress`.

Do not change the parent yet. Graph validation precedes every mutation. After successful validation, replace a first-run parent's `execution-ready` label with `in-progress`; otherwise leave its `in-progress` label unchanged.

## Read and validate the closed graph

Read the fenced YAML contract that contains `execution_mode: epic-dag`. The scheduler consumes at least:

```yaml
execution_mode: epic-dag
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

- there is one unambiguous epic contract and `max_parallel_workers` is an integer of at least one;
- `children` is a closed list of unique positive issue numbers;
- every child issue exists in the same repository;
- every child has exactly one official workflow-state label;
- every `depends_on` value is a unique declared child, is not the child itself, and the resulting graph is acyclic;
- every `mutex` entry is a non-empty string and is unique within that child.

Do not infer children or dependencies from issue prose, checklists, number order, milestones, links, branches, or pull requests. A validation failure makes no state mutation: terminate and report the exact defect for manual design handling. It is not permission to repair the graph or change a workflow state.

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

The bundled pure planner at `skills/codex-epic-scheduler/scripts/plan_wave.py` accepts a normalized JSON snapshot and prints the validation and selection result without GitHub mutations. Use it when practical to make the planned wave inspectable. The snapshot contains `parent_state`, `execution_mode`, `max_parallel_workers`, and each child's `issue`, `depends_on`, `mutex`, and observed `state`.

## Apply only the selected transition

Immediately before applying a wave, re-read the parent and all child workflow-state labels and recompute the plan if any observed state changed. For each selected child in ascending order:

1. re-read that child's labels;
2. if its single workflow state is no longer exactly `queued`, skip it without changing any label;
3. otherwise use `codex-github-operations` to replace `queued` with `execution-ready` in one label-edit operation while preserving unrelated labels;
4. verify the resulting single workflow state is `execution-ready`.

Do not post comments for these state-only transitions. Do not fill a newly available slot after the chosen wave begins; a later event-driven invocation will recompute from GitHub.

## Idempotency and termination

A promoted child is no longer `queued`, so the same observed state can never select it again. Repeated invocations recompute from current labels and may safely select nothing. Wake-up events may be duplicated or delayed; they do not broaden scheduler authority.

After the selected label replacements are verified, terminate the turn. If no candidates qualify, terminate successfully with the parent still `in-progress`. Even when every child is `completed`, do not close, complete, review, or integrate the epic.

Report only the validated child count, active count, available slots, selected issue numbers, skipped races if any, and confirmation that no other state was changed.
