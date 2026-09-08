---
name: codex-pr-audit
description: Audit one pull request at an exact head through an independent technical review, record the result, and apply only verdict-derived workflow transitions.
---

# Codex Pull Request Audit

## Responsibility

Use this skill when a caller requests `Audit PR #N` or otherwise identifies one
concrete pull request for technical audit. It is invocation-agnostic: a manual
ChatGPT request, direct Codex request, or future automation must follow the same
protocol. No GitHub Actions environment variables or trigger details are required.

This skill owns unambiguous controlling-issue resolution, exact-target capture,
preparation of a final-capable review packet, durable result recording, idempotent
replay, and only the workflow mutations derived from the result.

Technical judgment is governed exclusively by
`../codex-independent-review/SKILL.md`. When this audit is already running in a
fresh context that is isolated from the executor and pinned to a dedicated exact-
head review worktree, the current Codex session **is** the independent reviewer:
load and apply `codex-independent-review` directly in this same session and do not
spawn, delegate to, or wait for another reviewer. When those independence
conditions are not established, invoke exactly one fresh isolated reviewer instead;
an executor/controller context must never self-certify merely by adopting this
skill.

Use `../codex-github-operations/SKILL.md` only after the technical verdict is fixed
to record the audit and reconcile GitHub state. The auditor must not implement
fixes, merge, enable auto-merge, or submit a formal GitHub `APPROVE` review.

## Inputs

Required:

- repository identity when it is not already unambiguous from the current context;
- pull request number or canonical pull request URL.

Optional:

- a caller-supplied controlling issue number or canonical issue URL;
- a concise reason or audit request identifier for operational traceability;
- an explicit caller/launcher assertion that this session is itself the fresh,
  isolated audit context, when that assertion is backed by a separate review
  thread/worktree rather than the executor context.

Resolve every other input from current GitHub and repository state. In particular,
do not require `GITHUB_EVENT_PATH`, `GITHUB_REF`, `GITHUB_SHA`, `GH_TOKEN`, or any
other GitHub Actions variable. Authentication and transport selection belong to
`codex-github-operations`.

## Preconditions

Before starting technical review:

1. Load `AGENTS.md`, this skill, `codex-independent-review`,
   `codex-github-operations`, the pull request, and the resolved controlling issue.
2. Verify that the pull request exists, is open, and belongs to the intended
   repository.
3. Resolve exactly one controlling issue using the rules below.
4. Verify that the issue is open and carries exactly one repository workflow-state
   label.
5. Verify that the issue/PR relationship and current state permit an audit without
   displacing a different active owner or workflow.
6. Capture the PR number, base ref name, exact observed base SHA, head ref name,
   and exact observed head SHA. The head SHA is the review target.
7. Establish the review mode before inspecting implementation:
   - **direct isolated review** only when the current session is fresh with respect
     to the executor, uses a dedicated review worktree pinned to `audit_head`, and
     has not authored the implementation under review;
   - otherwise **delegated review**, using exactly one fresh isolated reviewer.

A launcher-created fresh App Server thread plus a distinct detached review worktree
at the published PR head satisfies the direct isolated review condition. Merely
opening a new turn in the executor's thread or using the executor's worktree does
not.

An ordinary new audit starts from `in-progress` or re-audits a current
`review-ready` PR. `execution-ready` and `blocked` are valid only while replaying
an already-recorded audit whose mutations were interrupted. `design-required` and
`investigation-required` require their owning workflows and are never overwritten
by this controller. `completed` is terminal and must not be audited as active work.

If a precondition is not met, fail closed. Preserve a concise reason on the PR
when possible, but do not guess ownership or mutate a possibly unrelated issue.

## Resolve the controlling issue

Treat these as authoritative candidate sources:

1. an issue explicitly supplied by the caller;
2. the exact head-branch convention `codex/issue-N`;
3. an explicit GitHub issue relationship, such as the PR's structured closing
   issue references or an unambiguous `Controlling issue: #N` field in its body.

The caller-supplied issue is preferred, but preference is not permission to ignore
a contradiction. Verify every authoritative candidate source that is present:

- all candidates must identify the same open issue in the intended repository;
- when no issue was supplied, infer only if the observed authoritative candidates
  collapse to exactly one issue;
- a missing optional source is not a contradiction;
- milestone membership, numeric proximity, similar titles, labels shared by many
  issues, and unstructured suggestive prose are never ownership evidence.

If candidates disagree, identify multiple issues, identify another repository, or
produce no exact issue, return `BLOCKED` for the audit attempt and do not begin the
technical review. When no controlling issue can be established, record the reason
on the PR only; there is no issue whose state may safely be changed.

## Fix the exact target

Capture the PR head SHA once, before constructing the review packet. Name that
value `audit_head`. Capture the observed base SHA as `audit_base` so the reviewed
comparison is reproducible. The reviewer must inspect the complete PR diff from
`audit_base` through `audit_head`, not only the last commit or a locally checked-out
branch name.

Confirm that both commits are published and fetchable. Immediately before any
verdict-derived mutation, re-read the PR and compare its current head SHA with
`audit_head`.

- If they match, the verdict applies to the current PR head.
- If they differ, the completed review is stale for the PR. Record at most one
  concise stale-attempt note keyed by the old and new SHAs, apply no verdict-derived
  transition, and restart later with the new head in a fresh review context.

A verdict for any other SHA must never be reused as evidence for `audit_head`.
Changes to technical code, tests, evidence, dependencies, configuration, or claims
after the audit similarly require a new exact target. Workflow-only metadata does
not alter the reviewed commit.

## Prepare and execute the review

Build the minimum final-capable review packet required by
`codex-independent-review`:

- `AGENTS.md` and the independent-review skill;
- controlling issue number and its current technical contract;
- PR number, `audit_base`, `audit_head`, and the complete diff range;
- validation/evidence required by the issue and evidence already available for
  that exact target;
- the issue's acceptance criteria, material risks, and any unresolved material
  finding that remains relevant.

### Direct isolated review

When the preconditions established that this current Codex session is already the
fresh isolated audit context, **perform the technical review yourself in this
session**. Apply `codex-independent-review` exactly as written. Do not create a
subagent, nested reviewer, second Codex session, or other delegated review merely
to manufacture another layer of independence: the independent boundary is the
fresh audit thread/worktree that launched this session.

Remain read-only with respect to implementation. You may run proportional tests or
diagnostics in the dedicated audit worktree when allowed by the review contract,
but do not publish or implement a correction there.

### Delegated review fallback

If the current context does not meet the direct-isolation conditions, invoke
exactly one fresh, isolated, read-only reviewer. Its context must not inherit the
executor's hidden reasoning. Ask it to apply `codex-independent-review` and return
the same verdict packet described below. Do not fall back to self-review in an
executor-owned thread/worktree.

### Required result

Whether the review is direct or delegated, produce:

- exactly one of `PASS`, `PASS_WITH_NOTES`, `FAIL`, or `BLOCKED`;
- the exact reviewed head SHA;
- whether the review is final-capable;
- validation run or evidence inspected;
- material findings and non-blocking notes.

Do not duplicate the independent skill's inspection procedure, materiality rules,
or testing policy here. A `PASS` or `PASS_WITH_NOTES` is actionable only when the
review confirms both `audit_head` and `final-capable: yes`. A mismatched target or
incomplete positive review is not converted into success; fail closed and request
a valid fresh review.

The review phase is entirely read-only. Fixes discovered by the reviewer belong to
the normal executor in a later execution. The auditor stops after recording and
mapping the verdict.

## Canonical audit record

After fixing the verdict and re-verifying the head, record one concise PR comment
with a machine-detectable marker:

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

This is a technical audit, not merge authorization.
```

Do not submit a formal approval review. Keep logs and repeated workflow metadata
out of the comment.

Before posting, search existing PR comments for the exact marker. If a fresh
review of the same SHA returns the same verdict and the marker already exists, do
not post a materially equivalent comment. Reconcile only missing compatible state
mutations. A different SHA always needs a new review and record. A genuinely
different verdict for the same SHA is not equivalent: preserve the new result,
explain the changed evidence, and do not overwrite incompatible workflow state.

Perform recording before state transitions. If a later mutation fails, a retry can
detect the record and finish only the missing compatible operations.

## Verdict mapping

Re-fetch the PR head, PR draft state, and issue workflow label before applying this
table. Preserve all unrelated labels. Use `codex-github-operations` for every
mutation and verify the result after each operation.

| Verdict | PR mutation | Controlling issue mutation | Controller terminates with |
| --- | --- | --- | --- |
| `PASS` | Mark ready for review if draft | Replace `in-progress` with `review-ready` | Ready for user-facing review |
| `PASS_WITH_NOTES` | Mark ready for review if draft | Replace `in-progress` with `review-ready` | Ready for user-facing review with preserved notes |
| `FAIL` | Convert to draft if ready | Replace `in-progress` or `review-ready` with `execution-ready` | Findings handed back to the normal executor |
| `BLOCKED` | Do not change draft state | Replace the active technical state with `blocked` only when required capability/evidence is genuinely unavailable with no safe alternative | Exact blocker preserved |

For `PASS` and `PASS_WITH_NOTES`, an already-ready PR and an already
`review-ready` issue are no-ops. For `FAIL`, an already-draft PR and an already
`execution-ready` issue are no-ops. For `BLOCKED`, an already-`blocked` issue is a
no-op. These terminal-state no-ops are permitted only when reconciling the matching
canonical audit record for the current SHA.

Apply the PR readiness mutation before moving the issue to `review-ready`; never
advertise `review-ready` while the PR remains draft. For other transitions, record
the verdict first, mutate the PR when the table requires it, then replace the issue
state. Preserve findings for `FAIL`; do not implement them. Neither a positive
verdict nor any state transition grants merge authority.

If the observed state is outside the source or idempotent terminal state described
above, another workflow may own it. Stop without overwriting it. If a GitHub
operation partially succeeds, re-read state and resume only the still-missing,
compatible operations. A replaceable transport failure is retried through another
permitted route and is not reported as a technical `BLOCKED` verdict.

## Controlled acceptance examples

These examples define observable controller behavior without depending on a live
PR mutation test.

| Caller input and observed GitHub state | Expected result |
| --- | --- |
| Isolated GA audit of PR #47; fresh audit thread + detached exact-head worktree; branch `codex/issue-122` | Resolve #122 and perform `codex-independent-review` directly in the current audit session; no nested reviewer |
| Manual `Audit PR #47` from an executor-owned context; branch `codex/issue-122`; structured PR link to #122 | Resolve #122, capture PR #47's current head, and delegate exactly one fresh review |
| Explicit issue #122; branch and structured link also identify #122 | Resolve #122 |
| Explicit issue #122; branch identifies #123 | `BLOCKED`; comment on the PR, review nothing, mutate neither issue |
| No supplied issue, non-conventional branch, and no structured issue link | `BLOCKED`; do not infer from title, milestone, or nearby number |
| Review covered head `aaa`; PR now points to `bbb` | Treat the old verdict as stale, apply no mapping, and request a fresh review of `bbb` |

| Fresh result for unchanged head and compatible starting state | Expected final state |
| --- | --- |
| `PASS`, final-capable; draft PR; issue `in-progress` | PR ready, issue `review-ready`, no merge |
| `PASS_WITH_NOTES`, final-capable; ready PR; issue `in-progress` | PR remains ready, issue `review-ready`, notes preserved, no merge |
| `FAIL`; ready PR; issue `review-ready` | PR draft, issue `execution-ready`, findings preserved, no fix |
| `BLOCKED`; draft PR; issue `in-progress`; indispensable evidence unavailable | Draft state unchanged, issue `blocked`, reason preserved |

Running the controller again for the same unchanged head and same verdict performs
a fresh independent review, finds the canonical marker, creates no equivalent
second comment, and makes no already-satisfied state mutation. Running it after a
new head appears necessarily creates a new exact-target review; the earlier marker
cannot satisfy the new audit.

## Handoff

Report only the PR, controlling issue, reviewed head, verdict, final-capable status,
material evidence/findings, observed final PR/issue state, and the next bounded
action. For positive verdicts, that action is user-facing review and a separate
merge decision. For `FAIL`, it is normal executor correction. For `BLOCKED`, it is
restoration of the named capability/evidence or resolution of the ownership
ambiguity. Never merge or enable auto-merge as part of this workflow.
