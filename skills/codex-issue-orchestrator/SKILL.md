---
name: codex-issue-orchestrator
description: Orchestrate explicit issue batches and executable epic DAGs through fresh role-specific Codex workers while preserving each child issue's own contract, review boundary, and workflow state.
---

# Codex Issue Orchestrator

## Responsibility

Use this skill when one parent Codex session must coordinate multiple controlling GitHub issues. It supports two modes:

- **batch mode** for the original explicit parallel/sequential issue lists;
- **epic-dag mode** for one executable epic whose children form a directed acyclic dependency graph.

The orchestrator owns discovery from the declared contract, dependency scheduling, conflict-group serialization, worker dispatch, base-ref construction, progress observability, resume semantics, integration-branch composition in epic mode, and the final orchestration handoff.

It does **not** silently redesign child scope, decide product behavior for a child, weaken validation, self-review implementation, or merge the epic result into the default branch. Each child remains its own controlling contract and must use a fresh role-appropriate worker.

## Common invariants

- Load `AGENTS.md`, this skill, and the parent/batch contract first.
- Treat issue labels as authoritative current workflow state.
- Do not infer undeclared children from neighboring issues, milestones, labels, or repository history.
- Do not launch two implementation workers that own the same child issue.
- Do not silently promote observations from one child into another child's requirements. Cross-child contract changes must be written into the affected issue by the appropriate design authority.
- Preserve exact reviewed targets. Never rewrite a reviewed child head merely to make orchestration easier.
- Child technical review is not authorization to merge to the default branch.
- The parent orchestrator must remain coordination logic; it must not become a fallback implementation worker.

## Mode selection

### Batch mode

Use batch mode when the caller supplies an explicit ordered issue list plus:

- `schedule`: `parallel` or `sequential`;
- `dependency`: `independent` or `dependent`;
- initial base branch or exact base ref.

Preserve the historical semantics described later in this skill.

### Epic DAG mode

Use epic mode only when the parent issue contains an explicit **Epic execution contract** with `execution_mode: epic-dag` and a closed child set.

The contract must define at least:

- `initial_base`;
- `max_parallel_workers`;
- one entry for every child issue;
- each child's `kind`;
- each child's `depends_on` list, including an explicit empty list for roots;
- optional `mutex` / conflict groups;
- whether the child is mandatory for epic closure.

A child may additionally declare `ready_state`. When omitted, use these defaults:

- `implementation` -> `execution-ready`;
- `investigation` -> `investigation-required`;
- `design` -> `design-required`;
- `optional-design` -> `design-required`.

`ready_state` exists for children such as an implementation issue that must pass a design gate after its DAG prerequisites become available.

The machine-readable epic contract is authoritative for orchestration topology. The child issue remains authoritative for its technical scope and acceptance criteria. If those two sources materially disagree about a dependency, kind, ready state, or closure condition, stop that node and return the mismatch to design rather than guessing.

Before starting, validate that:

1. every referenced child exists and references the epic or is otherwise explicitly adopted by it;
2. every dependency references another declared child;
3. the graph is acyclic;
4. no child has more than one workflow-state label;
5. every explicit `ready_state` is one of `execution-ready`, `investigation-required`, or `design-required` and is coherent with the child contract;
6. `max_parallel_workers >= 1`;
7. no active child has ambiguous branch/PR ownership;
8. the initial base exists and is fetchable;
9. the parent is `execution-ready` or `in-progress`.

Move the parent from `execution-ready` to `in-progress` immediately before orchestration work begins. Do not use the parent label as a substitute for child labels.

## DAG-blocked children

Epic planning commonly leaves a child labeled `blocked` solely because its declared predecessors have not finished. In epic mode this is a **dormant DAG state**, not automatically an external failure.

For a declared child with non-empty `depends_on`:

- while one or more declared prerequisites are unsatisfied, `blocked` is a valid dormant state;
- once all declared prerequisites are satisfied, inspect the child body/comments for any material blocker independent of those prerequisites;
- if no independent blocker exists, replace `blocked` with the child's declared/default `ready_state` without adding a state-only comment;
- if an independent external/technical blocker is recorded, preserve `blocked` and do not auto-unblock it;
- if it is ambiguous whether `blocked` means DAG wait or a real blocker, stop that node and resolve the ambiguity from authoritative issue evidence before mutation.

A root node (`depends_on: []`) labeled `blocked` is never auto-unblocked merely because it is a root; it needs an explicit blocker resolution.

This automatic transition is limited to the parent-declared DAG. It does not grant a generic orchestrator authority to clear arbitrary `blocked` labels.

## Epic child kinds

The epic contract may use these kinds.

### `implementation`

A child that ultimately delivers code/docs/configuration/tests through `spec-driven-codex-loop`.

- `execution-ready` -> dispatch a fresh implementation worker using `spec-driven-codex-loop`.
- `in-progress` -> resume the existing owner rather than creating a competing worker.
- `design-required` -> dispatch design authority first; if the resulting contract becomes `execution-ready`, the same epic may later schedule an implementation worker.
- `investigation-required` -> dispatch an investigation worker first; schedule implementation only if investigation produces an execution-ready contract.
- `blocked` -> apply the DAG-blocked rule above; preserve a genuine blocker.
- `review-ready` or `completed` -> reuse the observed successful result when its reviewed target is still valid for this epic.

### `investigation`

A child whose declared deliverable is evidence, diagnosis, benchmark, reproduction, or a falsifiable conclusion rather than necessarily a code feature.

Use a fresh investigation worker with only `AGENTS.md`, the child issue, relevant evidence/source, and the minimum required utility skills. The worker must preserve reproducible evidence, may add tests/instruments/reports when the issue explicitly requires them, and must not smuggle an unapproved fix into the investigation.

A completed investigation must end in one of these explicit outcomes:

- `completed` when the investigation itself is the accepted deliverable and no implementation follows;
- `execution-ready` when it has produced a fully resolved implementation contract;
- `design-required` when a material choice remains;
- `blocked` when a required external capability is genuinely unavailable.

A dependency-blocked investigation transitions to its ready state (`investigation-required` by default) only when its declared prerequisites are satisfied.

If an investigation discovers a separate defect or feature outside its contract, create/update a bounded child issue rather than implementing it incidentally. Adding a new child to an already executing epic requires an explicit parent-contract amendment and DAG revalidation.

### `design`

A child whose current deliverable is a resolved technical decision. Use `design-github-issue` in a fresh context.

A successful design pass must either:

- leave the issue `execution-ready` with a complete implementation contract; or
- mark/close it according to an explicit design-only terminal decision when no implementation is required.

A dependency-blocked design transitions to `design-required` only after its declared prerequisites are satisfied. Do not treat prose discussion as a completed design while the authoritative label remains `design-required`.

### `optional-design`

Like `design`, but epic closure may accept an explicit, reviewed defer/reject decision. Optional does not mean silently skipped: the issue must record the decision and the parent summary must identify it as delivered, deferred, or rejected.

## Dependency satisfaction

A child is **schedulable** only when every declared predecessor has produced an outcome that satisfies the edge.

By default an implementation dependency is satisfied only by a technically accepted exact target (`review-ready` or already `completed`) that can be composed into the child's base. A design/investigation dependency is satisfied by the terminal outcome required by its child contract; if it produced a successor implementation contract, that successor state must also match the parent DAG before downstream work starts.

An unresolved external `blocked`, failed work, unresolved `design-required`, or unresolved `investigation-required` does not satisfy downstream dependencies. A `blocked` label caused only by still-unsatisfied DAG prerequisites is expected dormant state and must not be reported as an independent failure.

When a mandatory node cannot progress, mark only its descendants as `not-run-dependency-failure`; independent parts of the DAG may continue. The whole epic becomes blocked only when no schedulable mandatory work remains and at least one mandatory closure path is unsatisfied by a genuine failure/blocker/unresolved gate.

## Conflict groups and parallelism

The epic may declare zero or more `mutex` values per child. Two active workers whose mutex sets intersect must not execute concurrently, even if there is no graph edge between them.

This is a scheduling constraint, not a dependency: after one worker terminates, the other may use the newest valid composed base according to the DAG contract.

Never exceed `max_parallel_workers`. Prefer useful parallelism between genuinely independent subsystems; do not maximize concurrency merely because slots are available.

## Base refs and epic integration branches

### Why epic mode needs composition

A DAG can have joins: a child may depend on several reviewed but not-yet-default-merged predecessors. Therefore a single predecessor branch is not always a sufficient base.

Epic mode may maintain **ephemeral non-default integration branches** under:

`codex/epic-<parent>/integration/**`

These branches are coordination artifacts, not acceptance into the default branch.

### Single predecessor

When a child has one implementation predecessor, use that predecessor's exact reviewed head as the base if it already contains all transitive required ancestors.

### Multiple predecessors

When a child has several implementation predecessors, build a temporary integration base from the declared initial base and combine the exact reviewed predecessor heads without modifying them.

Composition rules:

- never force-push a shared reviewed head;
- never resolve a semantic merge conflict by guesswork;
- if exact heads combine cleanly, publish the resulting non-default integration ref and use it as the child's base;
- if composition conflicts, stop the affected node and route the conflict to the parent/design authority with the exact heads and paths involved;
- run cheap integration validation after a non-trivial join when the child contract or risk justifies it;
- integration commits must contain only the mechanical combination of already reviewed heads, not new implementation fixes.

Creating or fast-forwarding these non-default integration refs is authorized by `execution_mode: epic-dag`. **This authority never includes moving the default branch or merging the final epic PR.**

### Child PRs

Every implementation child still gets its own branch and PR according to `spec-driven-codex-loop`. Its PR base should be the exact epic base supplied to that worker, so the child diff represents only its bounded delta.

The orchestrator may compose a child's exact reviewed head into later ephemeral integration bases after the child reaches `review-ready`; this is not default-branch acceptance and does not waive the later user-facing merge decision.

## Worker contract

For every dispatched child:

1. start a fresh role-specific worker unless resuming an unambiguously existing owner;
2. give it exactly one controlling child issue plus the exact base ref selected by the orchestrator;
3. require it to load `AGENTS.md`, the child issue, and only the skill(s) required by its role;
4. let the worker own its technical work, validation/evidence, publication and role-specific handoff;
5. wait for a terminal or handoff state before treating its result as dependency evidence;
6. record the exact reviewed/published target when downstream composition needs it.

Do not carry hidden implementation reasoning between workers. Only authoritative issue amendments, published commits, PRs, tests, reports, and other preserved evidence may constrain a later child.

## Parent and child state handling

The parent and children have independent state machines.

Parent:

- `execution-ready` -> ready to start epic orchestration;
- `in-progress` -> epic is actively being orchestrated or can be resumed;
- `blocked` -> no remaining schedulable mandatory work and at least one mandatory path has a genuine blocker/unresolved failure;
- `review-ready` -> all mandatory epic outcomes have been integrated into one final non-default epic target, required final integration validation/review has passed, and the final epic PR is ready for user-facing review;
- `completed` -> only after an explicit user-facing merge/acceptance is observed.

Children keep the normal repository workflow. Never mark all children `execution-ready` merely because the parent started. Promote each child only when its own design/investigation gate and DAG dependencies are actually satisfied. Dependency-blocked children are awakened one at a time to their declared/default `ready_state` as prerequisites become valid.

State-only transitions should remain comments-free. Use comments for material dependency amendments, conflict/blocker evidence, investigation/design outcomes, exact integration targets, or final handoff.

## Epic execution loop

### 1. Reconcile current state

Read the parent contract and every declared child metadata/state. Reuse `completed` and valid `review-ready` work. Resume unambiguous `in-progress` work. Detect stale parent assumptions, superseded child contracts, closed/renumbered children, changed dependencies, and active competing ownership.

Classify every `blocked` child as either dormant-on-DAG or genuinely blocked from authoritative issue evidence.

### 2. Build and validate the DAG

Construct the exact graph from the parent contract, calculate transitive prerequisites, verify acyclicity, and identify roots, ready nodes, dormant blocked descendants, genuine blockers and mutex conflicts.

Do not derive dependencies merely from issue-number order or priority.

### 3. Wake newly unblocked children

For every dormant child whose declared prerequisites are now satisfied, verify no independent blocker appeared and replace `blocked` with its explicit/default `ready_state`. Re-read the issue after mutation before scheduling it.

### 4. Select the next wave

Choose up to `max_parallel_workers` schedulable nodes whose mutex sets do not intersect. Favor mandatory nodes and shorter prerequisite paths before optional work unless the parent explicitly defines another priority.

### 5. Prepare exact bases

For each chosen implementation node, construct or reuse the exact base containing all satisfied implementation predecessors. Verify ancestry and remote publication before worker launch.

Design/investigation nodes normally use the current declared initial/integration context needed to inspect evidence, but must not accidentally inherit unrelated sibling implementation as technical truth.

### 6. Dispatch fresh workers

Dispatch by child kind and current label. Preserve each child's own acceptance criteria and tests. Do not convert an investigation into implementation or design into code merely to keep the epic moving.

### 7. Reconcile outcomes

After each wave, refresh child labels, PRs, exact heads, comments/contract amendments and any newly discovered blockers. Rebuild the schedulable set and wake any newly satisfied dormant nodes. Do not rely on the worker's prose summary when GitHub/repository state says otherwise.

### 8. Repeat until closure boundary

Continue while schedulable work exists. Independent branches of the graph continue even when another branch genuinely blocks.

When no work is schedulable:

- if all mandatory closure conditions are satisfied, build the final integration target;
- if mandatory paths are genuinely unresolved, set/report the parent `blocked` with the minimal blocker set;
- if only optional work was explicitly deferred/rejected according to contract, continue to final integration.

## Final epic integration and review

When every mandatory child is satisfied:

1. construct one final `codex/epic-<parent>/integration` target from the declared initial base and all mandatory accepted implementation heads, plus accepted optional implementation heads that the parent contract includes;
2. verify the complete changed-path set and ancestry;
3. run the parent-declared final integration tests/checks;
4. invoke a fresh final-capable independent review of the complete integrated target against the epic closure contract;
5. create or update one final epic PR from the integration branch to the default branch;
6. only after final review passes, mark that PR ready for review and move the parent from `in-progress` to `review-ready`;
7. stop.

The orchestrator must **not merge** the final epic PR, enable auto-merge, move the default branch, close the parent, or mark it `completed`. Those actions require a later explicit user-facing review/merge instruction.

Child PRs remain traceability artifacts for their bounded deltas. The final epic PR is the user-facing integration boundary for the complete DAG.

## Resume and idempotency

Epic orchestration is expected to span multiple sessions.

On resume:

- trust observed issue labels, PR state, exact published heads and integration refs over old session memory;
- reuse valid integration refs only after verifying their ancestry matches the currently declared dependency heads;
- reuse `completed` children;
- reuse `review-ready` children only if their exact reviewed target is still the one required by the parent DAG;
- resume an `in-progress` child only with unambiguous ownership;
- reclassify `blocked` children from current issue evidence rather than preserving an old session's assumption;
- never create a second worker merely because the original session is no longer visible;
- recompute schedulability from current state every time.

If the parent contract changes while execution is active, stop new dispatch, validate the amendment, recompute the DAG and record the material change before continuing. Never silently drop already delivered work.

## Progress observability

For long epics, post concise parent progress only at useful wave/phase boundaries, for example:

- orchestration started and graph validated;
- a wave completed with newly unblocked nodes;
- a mandatory path became genuinely blocked;
- an epic contract amendment changed topology;
- final integration/review started;
- final ready-for-review handoff.

Do not mirror every child comment on the parent. Child technical detail belongs to the child issue/PR.

Useful parent progress includes counts such as `completed/review-ready`, `active`, `dormant-on-DAG`, `genuinely blocked`, and the next schedulable wave, plus exact refs only when needed for recovery.

## Epic failure policy

- Child implementation failure belongs to that child; do not repair it in the parent context.
- A failed mandatory child blocks only its descendants until no other mandatory work is schedulable.
- A dependency-blocked child is not a failure and should automatically wake when its prerequisites are satisfied.
- A failed optional child may be deferred only if the parent contract explicitly permits that closure outcome.
- Merge conflicts between reviewed predecessor heads are integration blockers, not permission for the orchestrator to invent a resolution.
- Worker/process/transport failure is not a technical verdict; retry/resume through another permitted transport when ownership remains safe.
- If the DAG contract itself is contradictory, cyclic, incomplete, or ambiguous, return the parent to `design-required` rather than improvising topology.

## Batch mode compatibility

Batch mode preserves the original simpler semantics.

Valid combinations:

| Schedule | Dependency | Meaning |
| --- | --- | --- |
| `parallel` | `independent` | launch independent workers concurrently from one common base |
| `sequential` | `independent` | run independent workers one at a time from the declared base |
| `sequential` | `dependent` | run a linear stacked chain, each successful reviewed head becoming the next base |

`parallel + dependent` remains invalid in batch mode.

For independent batch issues, one failure does not cancel unrelated workers. For a dependent linear chain, a predecessor that cannot provide a valid `review-ready` published head stops downstream execution. Batch mode does not create an epic integration PR and does not infer a DAG.

## Completion report

For batch mode, retain the compact issue/outcome/PR-or-branch/result table.

For epic mode, report at least:

- parent issue and state;
- final integration branch/PR when created;
- mandatory children satisfied versus total;
- optional children delivered/deferred/rejected;
- dormant dependency-blocked children;
- genuinely blocked/failed children and affected descendants;
- exact next schedulable wave when not complete;
- final integration validation/review result when complete.

Do not duplicate complete child histories or logs. GitHub issues, PRs and preserved evidence remain the detailed source of truth.