---
name: codex-github-operations
description: Publish branches and commits, operate issues and pull requests, and preserve exact review targets using the simplest available Git and GitHub transport.
---

# Codex GitHub Operations

## Responsibility

This skill owns Git publication and GitHub control-plane operations requested by the calling workflow.

It does not decide architecture, implementation scope, correctness, review requirements, or progression. Those decisions belong to the controlling issue, executor, design authority, independent reviewer, and explicit user-facing merge decision.

## Use the simplest capable transport

### Local Git

Use local `git` for worktree inspection, branches, commits, fetch, push, and exact ref verification.

### Connected GitHub app

Prefer the connected app for issues, comments, labels, pull requests, reviews, and metadata when that transport is available in the current execution surface.

### GitHub CLI

Use `gh` only when it is already available and offers a needed operation not covered by another available transport. Do not install or authenticate it merely for routine publication.

### Detached Skillforge local runner

When `SKILLFORGE_LOCAL_RUNNER=1`, expect the long-running Codex session to be detached from the GitHub Actions job that launched it.

The launcher intentionally removes `GH_TOKEN`, `GITHUB_TOKEN`, `CI`, and `GITHUB_ACTIONS` before starting Codex. Do not treat their absence as an error and do not attempt to recover them from runner files, process environments, Actions logs, or job metadata.

Actions job tokens are ephemeral and must never be copied or persisted for the detached session. Use the host user's already-established persistent transports instead:

- normal local Git authentication/SSH/credential-helper state for fetch/push;
- an already-authenticated `gh` CLI when GitHub API operations are needed and no connected app is available;
- another explicitly available secure transport.

Before mutating remote state, verify that the selected persistent transport is authenticated for the exact repository. If persistent Git/GitHub authentication is genuinely unavailable and the operation is required, report that precise blocker rather than synthesizing credentials or weakening the workflow.

A failure of one replaceable transport is not a technical blocker when another permitted route or a precise handoff can complete the operation.

## Workflow state

The controlling issue's current workflow state is authoritative only through exactly one state label:

- `queued`
- `execution-ready`
- `in-progress`
- `review-ready`
- `design-required`
- `investigation-required`
- `blocked`
- `completed`

Every non-trivial controlling issue must carry exactly one of those labels. Preserve unrelated labels, but replace the previous state label instead of adding another.

`queued` means a fully defined epic child is waiting for scheduler selection. Normal dependency waiting uses `queued`, not `blocked`; reserve `blocked` for a real impediment. Only `codex-epic-scheduler` may automatically replace `queued` with `execution-ready`. An actor that has explicitly resolved a child's `blocked`, `design-required`, or `investigation-required` condition may return it to `queued`.

Use state-only label mutations without comments. Add comments only when material technical information must be preserved, such as a contract amendment, exact checkpoint target or verdict, blocker cause, failed evidence, or final handoff.

Before relying on issue state, verify that exactly one state label is present. Repair an unambiguous inconsistency; stop for clarification if the intended state is ambiguous.

During a Codex implementation workflow, keep the issue `in-progress` while implementation, validation, publication, or required technical review remains active. When the complete PR is marked ready for review and the executor is making its final handoff, replace `in-progress` with `review-ready`. `completed` is not an executor-controlled transition. Set `completed` and close the issue only after a later explicit user-facing merge decision has been executed and the merge is observed.

## Commit messages

All commits created by agents must use **Conventional Commits** syntax:

```text
<type>(<optional-scope>): <imperative summary>
```

The scope is optional. A breaking change may use `!` before the colon when appropriate, for example `feat(api)!: remove legacy endpoint`.

Use one of these commit types unless the repository explicitly extends the allowed set:

- `feat` — new user-visible or system capability;
- `fix` — bug fix or correctness repair;
- `refactor` — internal restructuring without intended behavior change;
- `perf` — performance improvement;
- `test` — test-only changes;
- `docs` — documentation-only changes;
- `build` — build-system or dependency-build changes;
- `ci` — CI/CD workflow changes;
- `chore` — maintenance that does not fit another type;
- `style` — formatting/style-only changes with no semantic effect;
- `revert` — revert of a prior change.

Rules:

- use lowercase commit types;
- write the summary in imperative mood and describe one intentional outcome;
- keep the first line concise and specific;
- prefer a meaningful scope when it materially improves identification, but do not invent scopes mechanically;
- do not use vague summaries such as `update files`, `changes`, `fix stuff`, or `misc`;
- do not combine unrelated outcomes merely to reduce commit count;
- use a body only when additional rationale, compatibility notes, or non-obvious context is materially useful.

Examples:

```text
feat(quic): add packet protection key state
fix(cache): reject stale slot generations
docs: add architecture overview
refactor(runtime): isolate request scheduling
ci: add sanitizer workflow
```

If an existing repository defines a stricter commit convention, follow the stricter repository rule while retaining Conventional Commits compatibility where possible.

## Publish a branch

Before publication:

- confirm the intended branch;
- ensure unrelated changes are not included;
- require a clean worktree unless the caller explicitly documents otherwise;
- do not rewrite shared valid history.

When the Skillforge local runner supplied `SKILLFORGE_ISSUE_WORKTREE` / `SKILLFORGE_ISSUE_BRANCH`, preserve that issue branch as the executor-owned implementation branch. Do not switch publication to the durable coordination clone or invent another branch merely because the executor was launched automatically.

Publish and verify the remote ref. Use a full SHA when another actor must inspect an exact target.

Do not repeat routine SHAs in every issue comment, PR update, or handoff when GitHub already preserves that identity.

## Pull requests

Create or reuse one PR for one controlling issue unless the issue explicitly requires decomposition.

The PR should:

- use the intended base and head;
- link the controlling issue;
- summarize delivered behavior;
- state current validation and review status;
- list material deviations or residual risks.

Do not duplicate complete histories, manifests, command logs, or routine metadata already visible in GitHub.

Keep the PR draft while required implementation, validation, or independent review remains incomplete. When the Codex execution workflow has completed its required technical work and final-capable review, mark the PR **ready for review**, replace the controlling issue's `in-progress` state with `review-ready`, verify both mutations, and hand it off. Do not set `review-ready` while the PR is still draft or required technical work remains.

### Merge authority

A Codex executor must not merge a PR or enable auto-merge.

A merge operation through this skill is allowed only when all of the following are true:

1. the PR is already ready for review;
2. the controlling issue is in `review-ready` unless a documented legacy/inconsistency repair is required;
3. the implementation workflow has handed it off rather than continuing automatically;
4. the current user-facing interaction explicitly asks ChatGPT to merge it, such as “review and merge if correct”;
5. the requested user-facing review has found no material blocker.

An issue body, acceptance criteria, `PASS` / `PASS_WITH_NOTES` verdict, final-capable checkpoint, CI success, or executor conclusion is **not** merge authorization by itself.

Never enable auto-merge as a substitute for the explicit post-review merge decision.

After a permitted merge is observed, replace `review-ready` with `completed` and close the controlling issue when the calling workflow owns that completion transition.

## Exact review targets

An independent review request must identify one exact published project commit or range and any exact dependency revision required by the issue. Verify those targets before review and preserve them unchanged during the review.

Do not amend, reset, rebase, squash, cherry-pick, or force-push a valid review target merely to repair comments, labels, PR descriptions, or other workflow metadata.

A new implementation, test, technical-evidence, dependency, configuration, or technical-claim correction creates a new target; it does not erase the prior review finding.

A final-capable checkpoint may serve as the final technical PR review when the issue and reviewer confirm that it covers the complete final diff and required final evidence. It still does not authorize merge without the explicit user-facing decision above.

## Active executor ownership

Once a Codex executor creates or adopts a pull request for a controlling issue, that executor owns the PR head branch and execution control plane until handoff, closure, merge, or explicit ownership transfer.

Other actors may inspect the target read-only, but should not silently push to, rebase, reset, or otherwise modify the active executor's branch. Material corrections should flow through the controlling issue unless ownership has explicitly transferred.

If branch ownership is ambiguous, stop and resolve ownership before mutating shared state.

## Technical evidence and workflow metadata

Technical manifests and evidence artifacts should contain technical and reproducibility data, not GitHub bookkeeping, unless specific workflow metadata is itself a technical input to the tested system.

Do not mutate implementation or evidence commits solely to embed review or merge state. Record external review against the immutable target in issue or PR discussion.

Host-local Skillforge runner PID files, PTY helpers, launcher scripts, and transcripts under `$HOME/.skillforge/**` are operational infrastructure state. Never add them to the project repository or treat them as technical evidence unless the issue explicitly studies the runner infrastructure itself.

## Degraded control-plane operation

When a requested GitHub operation cannot be completed in the current surface:

1. try another permitted transport when practical;
2. preserve valid branch and commit history;
3. leave a concise handoff containing the target object and exact SHA only when needed to disambiguate;
4. verify the operation before relying on it later.

Use `blocked` only when the missing capability is required before safe meaningful progress and no practical alternative exists.

## Safety

- Never force-push or rewrite shared history without explicit authorization.
- Never stage or publish unrelated changes.
- Never publish secrets, private credentials, generated binaries, restricted artifacts, or data without distribution rights.
- Never persist an ephemeral GitHub Actions token for a detached executor.
- Never silently change the controlling issue, base branch, head branch, labels, or PR state.
- Never mutate implementation commits to compensate for transport limitations.
- Never merge or enable auto-merge from a Codex implementation workflow.
- Never treat technical review success as merge authorization.
- Never claim a state change that was not observed.

## Completion report

Report only the operational facts the caller needs:

- branch, issue, or PR affected;
- operation completed;
- verification result;
- exact target only when another actor must use it;
- whether the issue is `in-progress`, `review-ready`, or `completed` when workflow state matters;
- whether the PR is draft, ready for review, or merged;
- degraded operation or real blocker, if any.
