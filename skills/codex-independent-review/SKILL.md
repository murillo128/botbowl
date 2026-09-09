---
name: codex-independent-review
description: Independently review an exact published target against the controlling issue's material risks and acceptance criteria, returning a concise risk-calibrated verdict.
---

# Codex Independent Review

## Responsibility

Use this skill for declared checkpoints and final technical review when a separate review is required.

The reviewer owns independent exact-target inspection, proportional validation, materiality, and the technical verdict. It does not implement fixes, redesign the issue, mutate workflow state, publish commits, continue execution, or authorize/perform merge while acting as reviewer.

A `PASS` or `PASS_WITH_NOTES` means the reviewed target is technically safe to progress according to the controlling workflow. The verdict itself has no GitHub mutation authority. When this review is invoked by `codex-pr-audit`, a positive final-capable verdict may be consumed by that controller under its standing automatic-completion authority after the reviewer role has ended. Outside that controller path, the verdict alone never merges, labels, or closes anything.

## Trust the issue as the technical contract

Judge the implementation against the controlling issue's explicit contract. Do not replace settled decisions with a new design merely because another approach is possible.

Repository notes, exploratory documents, and chat history provide context but are not acceptance criteria unless the controlling issue or an authoritative repository source explicitly adopts them.

Open linked sources only to inspect the exact implementation, durable decision, dependency, or evidence identified by the issue. Do not require the executor to reconstruct intent from unrelated project history.

## Minimal review packet

A fresh reviewer needs:

1. `AGENTS.md` when present;
2. this skill;
3. the controlling issue or exact checkpoint contract;
4. the exact published target or range;
5. the technical evidence required by that checkpoint.

Load prior comments or reviews only when an unresolved material finding or circuit breaker depends on them.

## Independence

The reviewer must:

- use a fresh context that does not inherit the executor's hidden reasoning;
- inspect exactly the requested target;
- remain read-only;
- judge evidence rather than intent;
- not implement corrections or advance later work.

Independence does not mean maximal hostility. Test declared risks and plausible normal use, not every imaginable malformed input or representational variant unless the issue requires that boundary.

## Materiality

Return `FAIL` only when a finding:

- violates an explicit invariant or acceptance criterion;
- exposes a plausible normal-path defect;
- makes required technical evidence materially false, incomplete, ambiguous, or misleading;
- introduces unapproved scope, architecture, dependency, format, data handling, or behavior;
- makes progression from the reviewed target unsafe.

Use `PASS_WITH_NOTES` for editorial wording, bookkeeping, optional hardening, stale non-authoritative prose, or robustness outside the declared boundary when the technical outcome remains trustworthy.

For every `FAIL`, state:

1. the exact criterion violated;
2. the material consequence;
3. the smallest corrective delta, or why design must reopen.

When those cannot be stated concretely, do not fail the checkpoint.

## Review procedure

### 1. Establish risk and authority

Identify the checkpoint outcome, scope, invariants, acceptance criteria, evidence, exact target, and explicit failure boundary.

### 2. Inspect the exact target

Check:

- diff and scope compliance;
- implementation and integration;
- credible required evidence;
- plausible correctness, ownership/lifetime, numerical, data, concurrency, security, backend, or performance failures covered by the checkpoint;
- unexpected dependencies, secrets, restricted artifacts, or behavior;
- safety to proceed.

Do not replay accepted earlier ranges without a concrete unresolved risk.

### 3. Test proportionally

Prefer checks capable of falsifying the claimed outcome.

Exact-head green CI, retained executor evidence, and deterministic artifacts are valid evidence when their target and environment are clear. Inspect and reuse them rather than mechanically rerunning the same broad suite. Run focused or additional tests when they materially increase independence, target a plausible risk, close an evidence gap, or are explicitly required by the controlling issue. Do not rerun the full repository suite merely to duplicate already trustworthy exact-head CI/evidence.

Distinguish commands personally run from committed or external evidence inspected. Never claim an unrun check passed.

### 4. Determine whether the review is final

A checkpoint can serve as final technical review when the issue declares it final-capable and the exact target includes:

- the complete final PR diff;
- the final dependency revisions required by the issue;
- required final technical evidence;
- all remaining acceptance criteria and unresolved material findings.

A later change to code, tests, technical evidence, dependencies, configuration, or technical claims invalidates that verdict for the changed target. Changes only to issue/PR prose, labels, roadmap state, merge metadata, or other derived workflow state do not.

Final technical review completion returns control to the calling workflow. It does not allow the reviewer role itself to merge or mutate GitHub state. If the caller is `codex-pr-audit`, that controller may then apply its verdict mapping after leaving the reviewer role.

### 5. Report briefly

Record only:

- exact target;
- verdict and safety to progress to the next workflow boundary;
- whether the review serves as final technical review;
- material findings;
- validation run or evidence inspected;
- smallest required delta or non-blocking notes.

Do not state or imply that the reviewer role itself performed or authorized a merge.

## Reviewer transport

Use any fresh isolated read-only reviewer capable of inspecting the exact target.

If one transport fails, preserve the target and try another permitted route. Do not amend implementation commits, guess a different target, or fall back to executor self-review.

Return `BLOCKED` only when required evidence or independent-review capability remains unavailable after practical alternatives are exhausted.

## Repeated-review circuit breaker

When two consecutive reviews fail for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism:

- stop open-ended searches for representational variants;
- use `PASS_WITH_NOTES` when progression is technically safe and the remaining concern is non-material;
- request design review when the validation strategy itself prevents a trustworthy decision;
- require explicit design-authority direction before a third corrective review of the same mechanism.

The circuit breaker never waives a continuing material defect.

## Verdicts

Return exactly one:

- `PASS`
- `PASS_WITH_NOTES`
- `FAIL`
- `BLOCKED`

Transport failure is an attempt result, not a verdict.

## Concise review request

```text
Act as a fresh independent read-only reviewer for <checkpoint> of issue #<issue>.

Review exact target <sha-or-range> against the issue's technical contract,
material risks, acceptance criteria, and relevant evidence. Inspect only the
context needed to determine whether progression is technically safe.

This checkpoint is <final-capable | not final-capable>. If final-capable, confirm
whether the target contains the complete final diff and all remaining evidence.

Return FAIL only for a concrete material violation, plausible normal-path defect,
misleading required evidence, unapproved scope, or unsafe progression. Treat
workflow metadata, editorial, bookkeeping, and optional hardening concerns as
PASS_WITH_NOTES.

Do not implement fixes or mutate repository/GitHub state while acting as reviewer.
Return exactly PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
```
