---
name: design-github-issue
description: Define a self-contained execution-ready GitHub issue that resolves material decisions and gives a fresh executor the facts needed to implement safely, then explain the designed mechanism separately to the user in chat.
---

# Design a GitHub Execution Issue

## Responsibility

Use this skill before non-trivial implementation starts, or when execution returns because a material design or validation decision is unresolved.

The design authority owns:

- the observable outcome;
- material architectural and validation decisions required for the bounded task;
- the task-specific context needed to execute safely;
- scope, invariants, exclusions, failure semantics, and acceptance criteria;
- risk-based review checkpoints when useful;
- the issue's initial readiness and any design-authority state transition;
- a separate concise explanation to the user of how the designed mechanism works when that helps them understand the project.

It does not implement code, operate branches, publish commits, perform independent review, or grant the executor merge authority.

## Assume a fresh executor

Design the issue for an executor that:

- has no access to the design session's hidden reasoning;
- should not need to reconstruct material facts from prior chats, issues, or PR history;
- must be able to distinguish required behavior from examples, observations, alternatives, hypotheses, and future work.

The issue must contain every task-specific fact, decision, constraint, and acceptance rule required for correct implementation. Links are supporting references, not substitutes for material instructions.

A self-contained issue is not an archive. Include the current contract in full; omit chronological narration and generic workflow already owned elsewhere.

## Keep user teaching outside the issue

The GitHub issue is an execution contract for Codex or another implementation agent. Do not add tutorial-style or pedagogical sections solely to teach the user.

After designing or publishing the issue, explain the relevant non-obvious mechanism separately in the assistant's chat response. Focus on end-to-end behavior, define specialized terms when needed, and keep the explanation proportional.

The issue itself may still describe technical data flow, semantics, or component interaction when the executor needs that information to implement correctly. Keep such text contractual and implementation-oriented.

## Design-session traceability

When a ChatGPT design session publishes a new issue:

- if the current private conversation URL is available, add `Design session: [ChatGPT](<private-conversation-url>)` to the initial issue body;
- never create or use a public/shared ChatGPT link for this provenance; if the private conversation URL is unavailable, omit the link rather than blocking issue publication;
- after GitHub assigns the issue number, if the environment exposes a supported conversation-title action, rename the current ChatGPT conversation to `#<issue-number> — <issue-title>`;
- do not add a repository prefix to the conversation title, and do not fail or block the workflow when conversation renaming is unavailable.

This traceability applies only to the design session. Do not add Codex implementation-session identifiers or general session bookkeeping to the issue.

## Load material design context

Start with:

1. `AGENTS.md` when present;
2. the user request, roadmap item, or existing controlling issue.

Then inspect only what is needed to settle the task:

- exact plans, decisions, specifications, or design notes relevant to the change;
- relevant source seams, APIs, ownership boundaries, state, and tests;
- baseline behavior or prior evidence that constrains the work;
- required hardware, dependencies, external repositories, datasets, artifacts, or environment inputs;
- overlapping current work and superseded attempts when their findings materially constrain the design.

Prefer authoritative current outcomes over complete historical traversal.

Do not silently promote exploratory notes, hypotheses, brainstorming, or provisional chat conclusions into requirements. They become contractual only when the task explicitly adopts them or the repository's authoritative sources already establish them.

## The issue is the executor's complete contract

Depending on the task, include:

- current limitation and observable goal;
- accepted baseline behavior and defaults that must remain unchanged;
- relevant dependencies, artifacts, datasets, or external inputs when they affect the result;
- inspected implementation seams and data shapes;
- resolved API or configuration semantics and invalid combinations;
- ordering, ownership, lifetime, concurrency, failure behavior, and resource constraints where relevant;
- permitted implementation scope and explicit exclusions;
- commands, targets, fixtures, environments, and artifacts needed for validation;
- objective acceptance criteria and material review risks;
- prior negative evidence when it prohibits repeating a known-invalid mechanism.

Use precise names, paths, values, examples, and equations where they remove ambiguity.

Do not copy generic Git, publication, review, label, merge, or reporting procedure already owned by skills. Do not duplicate chronological histories, complete logs, routine GitHub metadata, or user-oriented teaching content.

An issue may state observable post-merge completion conditions, but it must not authorize the Codex executor to merge or enable auto-merge. The repository workflow owns that boundary: execution delivers a ready-for-review PR and a controlling issue in `review-ready`; a later explicit user-facing review decides whether to merge.

## Readiness

Use exactly one workflow state label:

- `execution-ready`
- `design-required`
- `investigation-required`
- `blocked`
- `in-progress`
- `review-ready`
- `completed`

At issue publication, set exactly one state label through `codex-github-operations`. The issue body may record **Initial state** for historical context, but the label is authoritative for current state.

## Design method

### 1. Define the observable outcome

State what must become true, why it matters, the current limitation, and the boundary of the requested change.

### 2. Resolve material unknowns

Resolve questions that can change behavior, compatibility, architecture, data handling, correctness, failure handling, validation, licensing, security, performance, or deployment strategy.

Use these classifications only when useful:

- `OBSERVED`
- `ACCEPTED`
- `OPEN`
- `SPECULATIVE`
- `REJECTED`
- `BLOCKED`

Do not turn `OPEN` or `SPECULATIVE` items into implementation requirements. Record durable cross-task architecture in the repository location that owns durable decisions; keep task-local choices in the issue.

Do not invent project-wide roadmaps, phases, schemas, frameworks, ontologies, or process machinery merely because they might be useful later. Introduce persistent structure only when the current task or an explicit repository decision requires it.

### 3. Bound implementation without under-specifying it

Define the smallest coherent outcome, permitted subsystem or files, explicit exclusions, and invariants. Include exact files or seams when an executor could otherwise modify the wrong layer.

### 4. Define validation that proves the outcome

Specify material validation concretely:

- repository-native build, test, lint, type-check, evaluation, or benchmark targets;
- correctness, repeated-run, failure-path, numerical, data-integrity, concurrency, security, or performance checks when relevant;
- required environment and external artifacts;
- objective pass/fail criteria;
- technical evidence artifacts when useful.

Use exact commands when arguments or environment are part of what is being proven; otherwise identify the target and required result without freezing replaceable invocation syntax.

### 5. Keep evidence proportional

Capture enough technical evidence to support the decision or comparison being made. This may include dependency identity, configuration, commands, results, metrics, artifacts, and limitations.

Do not require elaborate provenance, immutable archives, hashes, or machine-readable manifests unless the task specifically needs them.

### 6. Add review checkpoints when they reduce risk

Add independent checkpoints only for distinct material risks such as architecture, ownership/lifetime, data integrity, numerical behavior, concurrency, security, backend execution, broad refactoring, or decision-driving performance evidence.

A checkpoint defines:

- the covered outcome and target semantics;
- material risks and acceptance criteria;
- evidence to inspect or reproduce;
- what would make progression unsafe.

When the last checkpoint can inspect the complete final diff and all remaining acceptance criteria, it can be declared **final-capable**. A final-capable verdict is a technical gate to the ready-for-review handoff, not merge authorization.

### 7. Define dependency and publication boundaries

When work depends on external repositories, submodules, packages, generated artifacts, models, datasets, or other versioned inputs, specify the identity and update boundary needed for the current task. Do not require bookkeeping commits that add no technical value.

### 8. Define restart semantics

Distinguish:

- local implementation defect: correct a bounded delta;
- design defect: return to `design-required`;
- evidence gap: return to `investigation-required`;
- replaceable tool failure: use another transport or leave a precise handoff;
- real blocker: no safe practical continuation exists.

Two consecutive review failures for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism should trigger design review before a third corrective cycle. This never waives a continuing material defect.

### 9. Check overlap

Inspect only plausibly overlapping open issues, PRs, branches, and recent attempts. Link superseded work and summarize its material constraint instead of copying its history.

### 10. Explain the mechanism to the user in chat

After the issue contract is complete, give the user a separate concise explanation of what will be built and how its important pieces interact when that explanation is useful.

Do not repeat the issue field by field. Focus on concepts needed to understand the project and the decisions just made.

## Execution-ready check

Before marking the issue `execution-ready`, confirm:

- a fresh executor can implement without design-session reasoning;
- the observable outcome and terminology are unambiguous;
- all material facts and decisions are present;
- linked sources supplement rather than replace the contract;
- scope, invariants, failure behavior, and acceptance are clear;
- required inputs and validation capabilities are identified;
- review checkpoints, if any, match distinct risks;
- dependency and external-evidence boundaries are explicit when applicable;
- exploratory material was not silently promoted to a settled requirement;
- no unnecessary project-wide machinery was invented;
- no user-oriented tutorial content was added merely for pedagogy;
- no issue text grants the executor merge or auto-merge authority;
- `execution-ready` is the issue's only state label.

## Issue structure

```markdown
# <Outcome-oriented title>

## Readiness
**Initial state:** execution-ready | design-required | investigation-required | blocked

## Goal and current limitation
<Observable outcome, why it matters, and current behavior.>

## Baseline and inputs
<Material baseline facts, dependencies, artifacts, and defaults.>

## Resolved technical contract
<APIs, data/control flow, ownership, failure semantics, bounds, and concrete seams.>

## Scope
### In scope
### Out of scope
### Invariants

## Validation and evidence
<Required targets, cases, environment, artifacts, and objective gates.>

## Checkpoints
<Only distinct material-risk checkpoints; mark the last one final-capable when applicable.>

## Delivery
<PR shape, dependency/publication boundaries, ready-for-review handoff, and observable post-merge completion. Do not authorize executor merge.>
```

Add or split sections when technical completeness requires it.
