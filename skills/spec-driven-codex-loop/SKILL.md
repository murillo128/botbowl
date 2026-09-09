---
name: spec-driven-codex-loop
description: Execute an approved controlling issue through bounded implementation, repository-native validation, publication, and a review-ready handoff.
---

# Spec-Driven Codex Loop

## Responsibility

Use this skill for non-trivial implementation under an approved controlling issue. The issue is the complete task-specific contract; repository documents define durable architecture and project-wide constraints; branches and PRs preserve implementation; tests and technical evidence preserve observed behavior.

The executor owns implementation, validation, commits, publication, issue-declared intermediate review checkpoints, and handoff. Delegate GitHub mutations to `codex-github-operations` and only explicit intermediate checkpoints to `codex-independent-review`. The executor may not review its own work independently.

The executor's terminal delivery state is a PR that is **ready for review** and a controlling issue labeled `review-ready`, not merged. Do not invoke a duplicate final independent review merely to reach that state: `review-ready` hands the current PR to `codex-pr-audit`, which owns the single final independent review and verdict-derived merge/completion path.

## Context and authority

Load once:

1. `AGENTS.md` when present;
2. the controlling issue and its top-level comments needed to resolve execution context;
3. only the exact plan, decision, source, test, build, configuration, dependency, artifact, or external input needed by the active outcome;
4. only the workflow or utility skill that owns the current action.

Do not weaken the issue, reconstruct its intent from broad history, or choose between materially different implementations when the issue is silent. Return to design instead.

Do not silently promote exploratory notes, hypotheses, brainstorming, or provisional chat conclusions into requirements. Use them only when the controlling issue or an authoritative repository source explicitly adopts them.

On resume, verify branch, `HEAD`, worktree, the controlling issue's single authoritative state label, the canonical execution context when present, and new material issue or PR discussion since the last handoff. Reuse unchanged inspected context rather than replaying history.

## Canonical execution context

An epic scheduler may materialize the execution target into one top-level issue comment identified by the exact marker:

```text
<!-- codex-execution-context:v1 -->
```

Before editing, search the controlling issue's top-level comments for that exact marker.

### No canonical context

If there are zero matches, treat the issue as standalone for branch-target purposes:

- the repository default branch is the intended PR target;
- preserve the normal local-runner behavior for the issue branch;
- do not search epics or infer a parent merely from links, prose, labels, milestones, or issue numbers.

### One canonical context

If there is exactly one match, parse its fenced YAML. It must contain exactly one usable value for each of:

```yaml
epic_issue: 3
integration_branch: codex/epic-issue-3
base_sha: 0123456789abcdef0123456789abcdef01234567
```

Treat this execution context as authoritative **only for execution base and PR target**:

- `integration_branch` is the intended PR base branch;
- `base_sha` is the exact integration snapshot selected for this activation;
- stale branch/base references in the child issue body or older comments are non-authoritative;
- the child issue body remains authoritative for technical scope, acceptance, and invariants.

Validate before editing that:

- `base_sha` is a full 40-character hexadecimal commit in this repository;
- `integration_branch` exists in this repository;
- the current `integration_branch` history still contains `base_sha` as an ancestor or exact head;
- the declared `epic_issue` exists and is not the controlling child itself.

The integration branch may have advanced after scheduling; that does not invalidate the activation as long as it still contains the pinned `base_sha`. A rewritten branch that no longer contains `base_sha` invalidates the context. Fail closed without implementation edits and surface the control-plane inconsistency rather than guessing a replacement base.

If there are more than one canonical-marker comments, fail closed. Do not choose the newest comment or infer which duplicate is authoritative.

### Prepare the issue branch from the pinned base

The implementation branch remains the executor-owned `codex/issue-N` branch supplied by the launcher. The execution context changes its base, not its branch name.

Before the first implementation edit of an activation with canonical context:

1. fetch the exact `integration_branch` and verify `base_sha`;
2. require a clean worktree before any automatic base reconciliation; pre-existing uncommitted issue work must be inspected and preserved, never discarded;
3. if the issue branch has no issue-owned commits beyond its launch base and can fast-forward to `base_sha`, fast-forward it to `base_sha`;
4. if the issue branch already contains preserved issue work and `base_sha` is not already an ancestor of `HEAD`, merge the exact `base_sha` into the issue branch before new implementation edits; do not reset or rewrite the issue's valid published history merely to change bases;
5. resolve merge conflicts only within the controlling issue's authority. A semantic conflict requiring a new product/design decision returns to `design-required` rather than being guessed locally;
6. verify after reconciliation that `base_sha` is an ancestor of the issue branch `HEAD`.

A retry or reactivation may therefore adopt a newer scheduler-pinned base while preserving previous issue commits. Never reset an existing issue branch to `base_sha` when doing so would discard issue-owned work.

Use `codex-github-operations` for PR creation/reuse/retargeting. When canonical context exists, the PR must target its `integration_branch`; when none exists, it targets the repository default branch.

## Skillforge local-runner entry

When `SKILLFORGE_LOCAL_RUNNER=1`, the launcher has already established the executor's repository isolation before Codex starts.

Treat the supplied local-runner context as an execution lease:

- current working directory must resolve to `SKILLFORGE_ISSUE_WORKTREE`;
- current branch must equal `SKILLFORGE_ISSUE_BRANCH`, normally `codex/issue-N`;
- that worktree/branch belongs to the controlling issue for this execution;
- a retry may contain unfinished state from an earlier attempt and must inspect/adopt it deliberately rather than replacing it.

Before editing, fail closed if the working directory, branch, repository identity, or controlling issue does not match that lease.

Do **not** create another worktree, switch to the durable coordination clone, switch to the default branch, invent a second implementation branch, or reset/discard pre-existing issue work merely because the session was launched by automation. The persistent issue worktree is the correct workspace.

The local-runner GitHub Actions job ends shortly after launching this interactive Codex session. Therefore:

- Actions job success means only that the session was launched, not that the issue succeeded;
- do not depend on `GH_TOKEN`, `GITHUB_TOKEN`, `CI`, or `GITHUB_ACTIONS` being present;
- those Actions-specific variables are intentionally removed from the detached session;
- Git/GitHub operations must use the persistent host transports described by `codex-github-operations`;
- do not attempt to recover, persist, or reuse an Actions job token from runner state or logs.

This local-runner entry changes only workspace/control-plane mechanics. Scope, validation, review, PR, and merge rules remain exactly the normal executor rules below.

## Entry gate and workflow state

Before editing, confirm:

- exactly one state label exists;
- it is `execution-ready` or `in-progress`;
- branch and worktree are safe;
- canonical execution context is either absent or uniquely valid and its pinned base has been adopted as required above;
- scope, invariants, failure semantics, acceptance, and required inputs are clear;
- no competing branch or PR creates ambiguous ownership.

If the issue is already `review-ready`, the executor has handed implementation to `codex-pr-audit`. Do not resume or mutate implementation merely because a session was restarted; require an audit `FAIL` or another explicit correction/re-execution instruction that moves the issue back to an executable state.

Before the first implementation edit, use `codex-github-operations` to replace `execution-ready` with `in-progress`. Do not post a comment solely for this transition.

Use label replacements for execution-time returns:

- missing material design decision: `design-required`;
- evidence needed before design: `investigation-required`;
- genuinely unavailable external capability: `blocked`.

`review-ready` is the successful executor handoff state. Set it only when the complete implementation has passed required validation, any **explicitly issue-declared intermediate** review checkpoints are satisfied, the PR is marked ready for review, and the final handoff is being made. `completed` is post-merge and is owned by the positive `codex-pr-audit` path. The executor must not merge, set `completed`, or close the controlling issue.

By default, add comments only when a material reason, technical finding, contract amendment, exact checkpoint target/verdict, blocker capability, or final handoff must be preserved. The scheduler-owned canonical execution-context comment is the deliberate exception: it is control-plane input and must not be rewritten by the executor.

## Execution loop

### 1. Establish the bounded outcome

Confirm intended behavior, permitted subsystem, invariants, required validation/evidence, execution base/target, and next checkpoint. Do not combine unrelated work.

Do not invent project-wide roadmaps, phases, schemas, frameworks, or process machinery as a side effect of executing one issue. Create durable structure only when the controlling issue or an explicit repository decision requires it.

### 2. Implement the smallest coherent delta

- follow the issue and accepted architecture;
- preserve baseline behavior outside scope;
- add tests or evaluation coverage with implementation when required;
- use repository-native integration;
- avoid unrelated cleanup and formatting;
- stop when evidence invalidates the design or acceptance strategy.

Commits should represent reviewable outcomes. Mechanical substeps do not need separate commits.

### 3. Handle dependencies and external inputs deliberately

For submodules, vendored code, external repositories, packages, datasets, generated artifacts, or other versioned inputs:

- preserve the identities required by the issue;
- respect licensing, provenance, and redistribution requirements;
- update inputs only at coherent implementation or review boundaries when identity matters;
- publish any external target another actor must inspect;
- never present unavailable or ambiguous dependency state as a review target.

### 4. Validate honestly

Prefer repository-native build, test, lint, type-check, evaluation, and benchmark commands. Disposable diagnostics are acceptable during investigation; durable required validation should use the approved project path.

Run required and useful narrower checks. Record material deviations, environmental limits, and checks not run. Never claim an unrun check passed. A local implementation failure is corrected within scope; it is not an external blocker.

### 5. Retain evidence proportionally

Keep enough technical evidence to support the claim being made. This may include configuration, dependency identities, commands, results, metrics, artifacts, and limitations.

Do not add workflow bookkeeping to technical artifacts unless it is itself relevant to the tested system. Large generated artifacts, caches, binaries, datasets, traces, and bulky logs belong outside Git unless the repository explicitly defines otherwise.

### 6. Publish intentionally

Publish when remote preservation, collaboration, a checkpoint, or PR review requires it. Exact SHAs are useful for review targets and dependency pins, not routine progress prose.

When canonical execution context exists, verify before publication that the PR target equals its `integration_branch`. If an existing PR still targets a stale branch, retarget it through `codex-github-operations` before relying on its diff, CI, or review state.

Update durable repository documents only when the durable content they own changes. Do not edit architecture, plans, decision records, guidelines, or knowledge documents merely to mirror workflow state.

## Comments and progress observability

By default, comment only when:

- a checkpoint is ready;
- scope or acceptance changes;
- a material failure, blocker, design return, or investigation return needs its cause preserved;
- final handoff is ready.

Use:

```markdown
## <Checkpoint ready | Contract amendment | Design required | Investigation required | Blocked | Ready for review>

**Delivered or confirmed:** <one to three bullets>
**Validation:** <result or evidence>
**Material issue:** <none or concise finding>
**Next:** <one bounded action>
```

At a checkpoint, include the exact published target and any dependency revision needed for review.

When a calling workflow explicitly requests progress observability, concise progress comments are additionally allowed or required at the phase boundaries defined by that caller. Such comments must remain operational rather than evidentiary: they report what is running, what just finished, coarse progress when cheaply available, and what comes next.

Progress-observability comments:

- do not require a published review target;
- do not trigger independent review;
- do not change issue scope, acceptance, or workflow state;
- do not replace normal checkpoint, blocker, design/investigation-return, or final-handoff comments;
- should not reproduce logs or emit per-item/per-step chatter.

## Review checkpoints

Executor-side independent review exists only for a **material intermediate checkpoint explicitly required by the controlling issue**. Do not create a final checkpoint merely because implementation is complete; `codex-pr-audit` owns the final independent review after `review-ready`.

At an explicit intermediate checkpoint:

1. publish the exact target;
2. provide scope, material risks, acceptance criteria, and relevant evidence;
3. invoke one fresh independent review;
4. continue only after `PASS` or non-blocking `PASS_WITH_NOTES`.

Progression:

- `PASS` / `PASS_WITH_NOTES`: continue implementation if work remains; when implementation and required validation are complete, prepare the normal `review-ready` handoff without another duplicate review;
- `FAIL`: choose a bounded correction, `design-required`, or `investigation-required`;
- `BLOCKED`: set `blocked` only when required evidence/review capability has no safe alternative;
- transport failure: use another route or leave a precise handoff; it is not an implementation verdict.

Do not mechanically implement every reviewer suggestion.

## Repeated-review circuit breaker

After two consecutive failures in substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism, stop compensating patches and return to design authority before a third cycle unless the defect is materially different. This never waives a continuing technical defect.

## Pull request discipline

Use one PR per controlling issue unless the issue explicitly decomposes delivery. Keep it draft while required implementation or validation remains incomplete.

The intended PR base is resolved only from the canonical execution context when present, otherwise from the repository default branch. Do not let a stale target named in the child issue body override this rule.

When the complete final diff has passed the required validation and any issue-declared intermediate checkpoints, update the PR description with the final technical state, mark the PR **ready for review**, use `codex-github-operations` to replace the controlling issue's `in-progress` label with `review-ready`, and then stop execution and hand it to `codex-pr-audit`. The label transition and PR readiness are one logical handoff: do not advertise `review-ready` while the PR is still draft or required technical work remains.

The Codex executor must never:

- merge the PR;
- enable auto-merge;
- treat CI success, issue acceptance criteria, or an intermediate review verdict as permission to perform the audit controller's mutations;
- close the controlling issue or set it to `completed`.

## Handoff

Include only what the next actor cannot derive cheaply:

- controlling issue, now labeled `review-ready`, and current bounded outcome;
- ready-for-review PR;
- intended integration branch when canonical execution context exists;
- material evidence not cheaply reproducible from CI/current repository state;
- unresolved non-blocking note or finding.

The immediate next action is `codex-pr-audit`; do not repeat its final review inside the executor. Do not continue past handoff unless a later audit `FAIL` or explicit instruction returns the issue to execution.
