---
name: codex-pr-audit
description: Audit one pull request at an exact head through the independent-review procedure, record the verdict, and apply the verdict-derived merge or correction workflow.
---

# Codex Pull Request Audit

## Responsibility

Use this skill when one concrete pull request must be audited after the executor has handed it off with the controlling issue in `review-ready`.

This skill is the audit **controller**. It owns:

- unambiguous controlling-issue resolution;
- exact base/head capture;
- running the technical review according to `../codex-independent-review/SKILL.md`;
- recording one canonical audit result;
- and the GitHub mutations that follow from that result.

`codex-independent-review` remains the technical-review authority and remains read-only. It does not merge, change labels, close issues, or implement fixes. A positive technical verdict is an input to this controller; the controller, not the reviewer role, owns automatic integration and completion.

When the audit launcher has already created a fresh App Server thread isolated from the executor and a dedicated detached worktree pinned to the exact PR head, the current Codex session may apply `codex-independent-review` directly in that same session. Do not create a nested reviewer merely to manufacture independence that the launcher already established. During the review phase, behave exactly as the independent-review skill requires: read-only, exact-target, evidence-based, and no GitHub state mutations. After the verdict is fixed, leave the reviewer role and resume this audit-controller procedure.

If the current context is not demonstrably fresh and isolated from the executor, invoke exactly one fresh isolated reviewer instead. An executor context may never self-certify its own implementation.

Use `../codex-github-operations/SKILL.md` for Git/GitHub mechanics after the verdict is fixed. This skill grants standing workflow authority for the positive audit path described below; no separate user-facing merge decision is required for that automatic audit completion path.

The audit controller must never implement a technical fix. A failed review or stale integration target returns the work to the normal executor.

## Inputs

Required:

- repository identity when not already unambiguous;
- pull request number or canonical PR URL.

Optional:

- an explicitly supplied controlling issue;
- a caller/launcher assertion that this session is the fresh isolated audit context, when backed by a distinct review thread/worktree.

Resolve all other state from current GitHub and the repository. Do not depend on ephemeral GitHub Actions tokens or event files.

## Preconditions

Before technical review:

1. Load `AGENTS.md`, this skill, `codex-independent-review`, `codex-github-operations`, the PR, and the controlling issue.
2. Resolve exactly one controlling issue from authoritative evidence: explicit caller input, the `codex/issue-N` head convention, and/or an explicit structured PR-to-issue relationship. Any contradictory authoritative source fails closed.
3. Require an open PR in the intended repository and an open controlling issue whose sole workflow-state label is `review-ready` for a fresh audit. A matching already-recorded audit may be replayed only to finish interrupted compatible mutations.
4. Capture PR number, head ref, `audit_head`, base ref, and `audit_base`. Both SHAs must be exact published 40-character commits.
5. Require the PR head branch to be owned by the controlling issue and the PR to be ready rather than draft for a fresh audit.
6. Establish review mode: direct-in-this-session only when this session is fresh relative to the executor and uses a dedicated detached worktree pinned to `audit_head`; otherwise use one fresh isolated reviewer.

Fail closed on ambiguous ownership, contradictory issue references, wrong workflow state, missing published commits, or a moving target.

## Exact target invariants

A fresh audit always targets the pull request's **current head at audit start**. `audit_head` and `audit_base` are snapshots captured only to detect races and prevent a moving PR from being reviewed or merged accidentally; they are not caller-selectable historical review targets. If the PR has advanced, start a fresh audit of its new current head instead of intentionally reviewing an older commit.

The technical review covers the complete PR diff `audit_base..audit_head`, not merely the last commit.

Immediately before recording a verdict and again immediately before any verdict-derived mutation, re-read the PR and require the current head SHA to equal `audit_head`, the base ref to remain the expected base ref, and the PR to still belong to the expected repository and controlling issue.

If the **head** moved, the verdict is stale. Record at most one concise stale-attempt note, perform no verdict-derived mutation, and require a fresh audit of the new head.

If the head is unchanged but the **base branch tip** moved from `audit_base`, do not review or merge against the historical base. Fetch the current base tip and distinguish:

- forward integration drift: the current base tip is a descendant of `audit_base` on the same base ref;
- rewritten/ambiguous base: ancestry cannot establish a normal forward advance, the base ref changed, or repository ownership changed.

Forward integration drift is normal executable reconciliation work, not a technical `FAIL` and not `BLOCKED`. End any reviewer role, mark the PR draft if it is currently ready, replace the controlling issue's `review-ready` state with `execution-ready`, leave the issue open, and stop. That label transition intentionally relaunches the executor so it can rebase onto the new integration tip, republish, and obtain fresh CI before another audit.

For a rewritten or ambiguous base, fail closed and preserve a concise control-plane finding instead of guessing or rewriting the implementation branch.

## Review phase

Apply `codex-independent-review` exactly as written. Build the minimum packet it requires:

- `AGENTS.md`;
- controlling issue and its technical contract;
- PR number and exact `audit_base..audit_head`;
- required validation/evidence for that target;
- unresolved material findings that still apply.

The review phase must return:

- exactly one of `PASS`, `PASS_WITH_NOTES`, `FAIL`, or `BLOCKED`;
- exact reviewed head SHA;
- whether it is final-capable;
- validation/evidence run or inspected;
- material findings and non-blocking notes.

A positive result is actionable only when the reviewed head exactly equals `audit_head` and `final-capable: yes`.

Do not edit implementation, switch branches, merge, label, close, or otherwise mutate GitHub while acting in the reviewer role. Once the verdict is fixed, resume the audit-controller role below.

## Canonical audit record

Before changing PR or issue state, write one concise PR comment with this exact marker:

```markdown
<!-- codex-pr-audit:v1 head=<full-head-sha> verdict=<VERDICT> -->
## Codex PR audit

**Controlling issue:** #<issue>
**Reviewed head:** `<full-head-sha>`
**Observed base:** `<full-base-sha>`
**Verdict:** `<VERDICT>`
**Final-capable:** `<yes-or-no>`
**Validation/evidence:** <concise summary>
**Material findings:** <none or concise findings>
**Non-blocking notes:** <none or concise notes>
```

Do not submit a formal GitHub `APPROVE` review. If the exact marker already exists for the same head and verdict, do not duplicate it; use it only to reconcile missing compatible controller mutations.

A different head always requires a new audit. A genuinely different verdict for the same head is a new audit record and must preserve the changed evidence rather than overwrite history.

Integration drift detected before a valid verdict is fixed does not invent a reviewer verdict or canonical audit record. If a positive record was already written and the base advances before merge, preserve that record as historical evidence, return the issue to execution as described above, and require a fresh audit after the executor publishes a new head/base pair.

## Verdict mapping

Re-fetch the exact PR and controlling issue before applying any row.

| Verdict | PR action | Issue action | Terminal result |
| --- | --- | --- | --- |
| `PASS` | Automatically merge the exact audited head | After merge is observed, replace `review-ready` with `completed`, then close the issue | Integrated and complete |
| `PASS_WITH_NOTES` | Same automatic merge; preserve notes in the audit record | After merge is observed, replace `review-ready` with `completed`, then close the issue | Integrated and complete with non-blocking notes |
| `FAIL` | Mark PR draft if currently ready | Replace `review-ready` with `execution-ready`; leave issue open | Returned to executor |
| `BLOCKED` | Do not change PR readiness | Replace `review-ready` with `blocked` only for a genuine unavailable capability/evidence condition | Blocked with exact cause |

Integration-base drift and a `dirty`/conflicting PR that requires base reconciliation are **control-plane returns**, not extra reviewer verdicts. When the implementation head is still owned by the controlling issue, mark the PR draft, replace `review-ready` with `execution-ready`, and leave the issue open so the normal executor can reconcile it.

### Positive automatic merge path

For `PASS` or `PASS_WITH_NOTES`:

1. Require `final-capable: yes` and exact head/base equality.
2. Require the PR still open and ready.
3. Merge **that exact `audit_head`** through `codex-github-operations`, using an expected-head guard so a moved PR cannot be integrated accidentally. Use the repository's normal permitted merge method; do not rewrite the reviewed head first.
4. Re-read the PR and verify GitHub reports it merged. A merge request or queued auto-merge is not enough to complete the issue.
5. Re-read the controlling issue. If it is still open with sole workflow state `review-ready`, replace that state with `completed` while preserving unrelated labels.
6. Verify `completed` is observable. This label transition intentionally wakes the event-driven epic scheduler when the issue is a DAG child.
7. Close the controlling issue with completed resolution and verify it is closed with the `completed` label still present.
8. Stop. Do not perform implementation edits or additional PR mutations after completion.

The independent reviewer did not merge anything: the review ended before step 3. Automatic integration is a separate controller action authorized by this audit workflow.

If the exact-head merge cannot be completed, classify the reason before choosing state:

- if the PR/base/head moved or GitHub reports the PR `dirty`/conflicting because the integration target advanced, use the integration-drift/control-plane return above (`draft` + `execution-ready`);
- if required checks or mergeability are merely pending/temporarily unresolved while head and base remain exact, leave the issue `review-ready` and the PR open for later controller reconciliation;
- use `BLOCKED` only for a genuine unavailable required capability with no safe alternative, not ordinary pending checks or integration drift.

Never falsely mark `completed` until GitHub reports the exact PR actually merged.
