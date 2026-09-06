---
name: spec-driven-codex-loop
description: Execute an approved controlling issue through bounded implementation, repository-native validation, publication, independent review, and a review-ready handoff.
---

# Spec-Driven Codex Loop

## Responsibility

Use this skill for non-trivial implementation under an approved controlling issue. The issue is the complete task-specific contract; repository documents define durable architecture and project-wide constraints; branches and PRs preserve implementation; tests and technical evidence preserve observed behavior.

The executor owns implementation, validation, commits, progression through technical review, and handoff. Delegate GitHub mutations to `codex-github-operations` and checkpoint/final review to `codex-independent-review`. The executor may not review its own work independently.

The executor's terminal delivery state is a PR that is **ready for review** and a controlling issue labeled `review-ready`, not merged. It must not merge the PR, enable auto-merge, or treat a technical review verdict as merge authorization. Merge acceptance belongs to a later explicit user-facing review-and-merge interaction.

## Context and authority

Load once:

1. `AGENTS.md` when present;
2. the controlling issue;
3. only the exact plan, decision, source, test, build, configuration, dependency, artifact, or external input needed by the active outcome;
4. only the workflow or utility skill that owns the current action.

Do not weaken the issue, reconstruct its intent from broad history, or choose between materially different implementations when the issue is silent. Return to design instead.

Do not silently promote exploratory notes, hypotheses, brainstorming, or provisional chat conclusions into requirements. Use them only when the controlling issue or an authoritative repository source explicitly adopts them.

On resume, verify branch, `HEAD`, worktree, the controlling issue's single authoritative state label, and new material issue or PR discussion since the last handoff. Reuse unchanged inspected context rather than replaying history.

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
- scope, invariants, failure semantics, acceptance, and required inputs are clear;
- no competing branch or PR creates ambiguous ownership.

If the issue is already `review-ready`, the previous executor has handed implementation off for user-facing review. Do not resume or mutate implementation merely because a session was restarted; require an explicit correction/re-execution instruction that moves the issue back to an executable state.

Before the first implementation edit, use `codex-github-operations` to replace `execution-ready` with `in-progress`. Do not post a comment solely for this transition.

Use label replacements for execution-time returns:

- missing material design decision: `design-required`;
- evidence needed before design: `investigation-required`;
- genuinely unavailable external capability: `blocked`.

`review-ready` is the successful executor handoff state. Set it only when the complete implementation has passed required validation and final-capable independent review, the PR is marked ready for review, and the final handoff is being made. `completed` is a post-merge state. The Codex executor must not set `completed` or close the controlling issue as part of implementation delivery. After an explicit user-facing review accepts and merges the ready PR, the merge workflow may replace `review-ready` with `completed` and close the issue after observing the merge.

By default, add comments only when a material reason, technical finding, contract amendment, exact checkpoint target/verdict, blocker capability, or final handoff must be preserved. A calling workflow may explicitly request additional progress-observability comments; when it does, follow that narrow reporting policy without treating progress comments as checkpoints or technical evidence.

## Execution loop

### 1. Establish the bounded outcome

Confirm intended behavior, permitted subsystem, invariants, required validation/evidence, and next checkpoint. Do not combine unrelated work.

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

At a declared checkpoint:

1. publish the exact target;
2. provide scope, material risks, acceptance criteria, and relevant evidence;
3. invoke one fresh independent review;
4. continue only after `PASS` or non-blocking `PASS_WITH_NOTES`.

A checkpoint may serve as final technical review when it covers the complete final diff and all remaining acceptance criteria. Any later technical change invalidates that verdict; workflow-only changes do not. A final technical verdict never authorizes merge by itself.

Progression:

- `PASS`: continue with `in-progress`; if this was the final-capable review and all implementation work is complete, prepare the PR and final issue handoff for user-facing review;
- `PASS_WITH_NOTES`: continue unless a note violates an exit gate; if final-capable and non-blocking, prepare the PR and final issue handoff for user-facing review;
- `FAIL`: choose a bounded correction, `design-required`, or `investigation-required`;
- `BLOCKED`: set `blocked` only when required evidence/review capability has no safe alternative;
- transport failure: use another route or leave a precise handoff; it is not an implementation verdict.

Do not mechanically implement every reviewer suggestion.

## Repeated-review circuit breaker

After two consecutive failures in substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism, stop compensating patches and return to design authority before a third cycle unless the defect is materially different. This never waives a continuing technical defect.

## Pull request discipline

Use one PR per controlling issue unless the issue explicitly decomposes delivery. Keep it draft while required implementation, validation, or independent technical review remains incomplete.

When the complete final diff has passed the required validation and final-capable independent review, update the PR description with the final technical state, mark the PR **ready for review**, use `codex-github-operations` to replace the controlling issue's `in-progress` label with `review-ready`, and then stop execution and hand it off. The label transition and PR readiness are one logical handoff: do not advertise `review-ready` while the PR is still draft or required technical work remains.

The Codex executor must never:

- merge the PR;
- enable auto-merge;
- interpret `PASS` / `PASS_WITH_NOTES`, issue acceptance criteria, CI success, or a final-capable checkpoint as merge authorization;
- close the controlling issue or set it to `completed` before an explicit user-facing review accepts and merges the PR.

## Handoff

Include only what the next actor cannot derive cheaply:

- controlling issue, now labeled `review-ready`, and current bounded outcome;
- ready-for-review PR and exact final reviewed target when useful;
- last accepted checkpoint;
- material evidence;
- unresolved non-blocking note or finding;
- immediate next action: user-facing review and merge decision.

Do not continue past this handoff unless a later explicit instruction requests review, correction, or merge.
