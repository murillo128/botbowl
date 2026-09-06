---
name: codex-local-runner
description: Install, repair, and verify the optional repository-scoped GitHub Actions bridge that launches Skillforge issue turns through the Codex App Server already shared with Desktop Remote Control.
---

# Codex Local Runner

## Responsibility

Use this skill to provision or repair Skillforge's optional host-side execution bridge.

The template workflow is `.github/workflows/codex-execute-ready.yml`. Its contract is deliberately narrow:

- react to GitHub `issues:labeled`;
- make the execution job eligible only when the newly applied label is exactly `execution-ready`;
- target a repository-scoped runner carrying `self-hosted` and `codex`;
- never use `actions/checkout` or the runner `_work` checkout as project state;
- expose the durable local clone through `SKILLFORGE_REPO_ROOT`;
- create or reuse one persistent worktree and branch `codex/issue-N` per controlling issue;
- connect to the **existing Codex App Server control socket used by Desktop Remote Control**;
- create or resume one durable Codex thread for the issue and start the implementation turn in the prepared worktree;
- leave implementation procedure to `AGENTS.md`, the controlling issue, and the normal Skillforge skills.

GitHub Actions is only the authorization and launch trigger. The actual Codex turn is owned by the shared App Server and continues after the Actions job exits.

This skill owns runner installation, registration, service configuration, durable-repository environment wiring, App Server prerequisites, repair, optional scaling, and verification. It does not implement issues or modify project repository files.

Never execute this skill against the canonical `murillo128/skillforge` template repository. The template carries the capability for descendants but must not itself acquire a repository runner.

## Invocation and authority

Runner provisioning is opt-in.

Explicit invocation of this skill, or an explicit local-runner opt-in passed through `repository-bootstrap`, grants authority only for the host and GitHub control-plane changes required to install, register, repair, start, scale, or verify the target runner installation.

That authority does **not** grant permission to:

- modify repository files or workflows;
- create an OpenAI API key or replace existing Codex authentication;
- expose inbound ports, webhooks, SSH, or public listeners;
- run the Actions service as root or an unrelated account;
- remove unrelated runners or services;
- trigger an actual `execution-ready` issue merely as a test.

If the expected workflow is missing or materially incompatible, stop and report the mismatch. Repository repair requires separate authority.

## Execution topology

Treat the runner application's `_work` directory as disposable runner state, never as the Codex project workspace.

Each repository-scoped runner must expose:

`SKILLFORGE_REPO_ROOT=<absolute durable local clone>`

It may also expose:

`SKILLFORGE_WORKTREE_ROOT=<absolute persistent worktree parent>`
`SKILLFORGE_LOG_ROOT=<absolute persistent local event-log parent>`
`SKILLFORGE_CODEX_APP_SERVER_SOCKET=<optional explicit App Server control socket>`

Defaults are:

- worktrees: `$HOME/.skillforge/worktrees`;
- logs: `$HOME/.skillforge/logs`;
- App Server socket: `${CODEX_HOME:-$HOME/.codex}/app-server-control/app-server-control.sock`.

For repository `<owner>/<repo>` and issue `N`, the workflow prepares a stable issue worktree and branch `codex/issue-N`. A retry validates and adopts that same worktree rather than discarding unfinished state or creating a competing branch.

The durable clone at `SKILLFORGE_REPO_ROOT` is a **shared coordination clone**. Implementation occurs in issue worktrees. Never point `SKILLFORGE_REPO_ROOT` at an Actions `_work` directory.

## Shared Codex App Server

For an SSH project, Codex Desktop starts the remote Codex App Server through SSH. The Skillforge launcher must join that same process through its Unix control socket rather than starting a TUI, running `codex exec`, or launching a competing App Server process.

Current workflow semantics:

1. create/resume the issue thread through the shared App Server;
2. create new threads with the durable repository root as the thread `cwd`;
3. give a new thread the user-facing name `#N — <issue title>`;
4. start the issue turn with the local execution environment's `cwd` set to the persistent issue worktree;
5. use `on-request`, `auto_review`, workspace-write, and network access for the implementation turn;
6. keep a lightweight background WebSocket client subscribed until the matching `turn/completed` event;
7. let the GitHub Actions job exit as soon as `threadId` and `turnId` are confirmed.

The root/worktree split is intentional. The durable repo root remains the conversation/project identity, while command execution is isolated in the issue worktree. Do not simplify this by making the worktree the initial thread cwd merely because it is the execution directory.

The App Server `environments` override used for the turn is an experimental protocol capability. Runner verification must therefore confirm that the installed Codex/App Server version accepts the current workflow contract. A version/protocol mismatch is an infrastructure blocker; do not silently fall back to a different execution topology.

### Keep the initiating connection alive

Do not disconnect the launcher client immediately after `turn/start`.

The background client deliberately remains connected until `turn/completed`. This preserves a stable subscriber/connection for long shell operations and avoids relying on disconnect behavior while a turn is in flight.

The background client is not another model executor. It is only a WebSocket control-plane subscriber/event sink. It does not answer approvals automatically. Approval or user-input requests remain App Server requests and may be handled by another subscribed UI/client, including Desktop, according to normal Codex behavior.

The client must survive GitHub runner process cleanup. Preserve the workflow's use of an empty `RUNNER_TRACKING_ID` for the detached client process.

### Do not create a competing App Server

If the expected Unix control socket is absent or unreachable, fail closed and report it.

Do **not** automatically start another `codex app-server`, `codex exec`, or interactive TUI as a fallback. The purpose of this topology is for Desktop and Skillforge to share one App Server writer/owner. A second App Server defeats that property and can recreate thread ownership and visibility problems.

Reconnect/enable the intended Desktop SSH project or otherwise restore the already-selected shared App Server before retrying the issue transition.

### Desktop project grouping is not authoritative

Generic App Server thread creation and Desktop's saved-project catalog are not currently the same identity system. A thread created through the shared App Server may appear under Desktop **Recents** rather than inside the saved project even when its repository root is correct.

Do not fabricate a second project, mutate Desktop-private databases, or use the issue worktree basename as a project identity in an attempt to repair presentation. Repository identity, Git worktree identity, and exact thread ID are the operational authorities.

This limitation is cosmetic/organizational only if the same thread is live, readable, steerable, and operating in the correct worktree.

## Issue execution state

Host-local state lives under:

`$HOME/.skillforge/run/<owner-repo>/issue-N/`

It may include:

- App Server client PID;
- durable App Server thread ID;
- current turn ID;
- launch-ready/completed/error markers;
- generated WebSocket client helper.

Logs live under the configured/default log root.

These files are infrastructure state, not project artifacts. Never commit them.

The thread ID is intentionally stable across retries. Re-executing the same issue should resume the known idle thread rather than create another conversation. If that stored thread is already active, fail closed rather than using `turn/start` to steer an existing turn accidentally.

PID files are only liveness guards. Validate the actual process before treating a PID file as proof of an active launcher.

## Parallel execution

Because the Actions job only prepares a worktree, launches the App Server client/turn, confirms IDs, and exits, **one self-hosted runner is sufficient for normal concurrent Codex execution**.

A single runner can launch issue A, become free, then launch issue B while App Server turns for A and B continue concurrently in separate worktrees. The workflow's per-issue concurrency plus issue-local PID/thread guards prevent duplicate simultaneous launches of the same issue.

Do not provision multiple runner instances merely to obtain multiple concurrent Codex sessions.

Additional runner instances are optional only when explicitly requested for burst launch throughput, runner redundancy, or another operational reason.

## Security boundary

A self-hosted runner executes repository workflow code on the local machine. Treat it as privileged infrastructure.

- Prefer a **repository-scoped** runner.
- Run the service as the same non-root OS user whose Codex Desktop/CLI remote state and persistent Git/GitHub authentication are intended for this repository.
- Never copy Codex credentials to another account and never create an OpenAI API key merely for the runner.
- Registration/removal tokens are temporary secrets: obtain them only when needed and never print, log, commit, or persist them.
- Do not expose the App Server socket or WebSocket transport to a public/shared network. The launcher reaches the local Unix socket only.
- Do not weaken host security or make runner/worktree/log/state directories writable by unrelated users.
- Before enabling this on a public repository, inspect workflows and stop if untrusted fork/PR-controlled workflow code can target `self-hosted`, `codex`, or an equivalent selector.
- Externally authored issue content remains untrusted input. Execution starts only through the authorized `execution-ready` label transition.

## Preconditions

Before changing the host, establish:

1. exact target repository and default branch;
2. target is not canonical `murillo128/skillforge`;
3. `.github/workflows/codex-execute-ready.yml` exists and:
   - gates on the freshly applied `execution-ready` label;
   - targets `self-hosted` plus `codex`;
   - contains no `actions/checkout`;
   - requires `SKILLFORGE_REPO_ROOT`;
   - prepares/reuses `codex/issue-N` in a persistent worktree;
   - connects to the shared Codex App Server rather than launching a standalone Codex executor;
4. repository visibility and public-repository safety;
5. absolute durable checkout intended for `SKILLFORGE_REPO_ROOT`, with exact matching `origin` and outside any runner `_work` tree;
6. OS, architecture, hostname, current user, service manager, and disk space;
7. availability of Git, Python 3, `setsid` on Linux, and any required persistent GitHub transport;
8. Codex Remote Control/SSH App Server is running under the same intended user and its Unix socket is reachable;
9. the installed App Server supports the workflow's thread/turn protocol and local environment override;
10. persistent Git/GitHub authentication is usable by the App Server execution environment;
11. existing runner directories/services and repository runner registrations.

For runner registration, prefer an already-authenticated `gh` CLI when available. Registration tokens expire quickly and must never be persisted.

## Idempotent provisioning procedure

### 1. Identify the durable repository and intended runner

Canonicalize `SKILLFORGE_REPO_ROOT`, verify its `origin`, and verify it is not under an Actions `_work` path.

Use a stable runner name derived from host and repository and one dedicated runner application directory outside the repository. Reuse a healthy existing durable clone and runner when possible.

### 2. Reuse before replacing

Inspect local services and GitHub registrations first. Repair a correct runner rather than creating duplicates. Remove stale registrations only when ownership by the local installation is unambiguous.

### 3. Obtain and register the official runner securely

Do not hard-code a runner version. Resolve the current official GitHub Actions runner for the detected OS/architecture and use official assets/instructions.

Obtain a temporary repository registration token through the authenticated GitHub control plane at registration time. Never echo, persist, or log it.

Configure the runner for the exact repository with normal self-hosted labels plus custom `codex`, persistent/non-ephemeral operation, and its normal internal `_work` directory.

### 4. Configure persistent runner environment

Expose `SKILLFORGE_REPO_ROOT`, plus optional worktree/log/socket overrides when required. Prefer the runner's supported `.env` file or a narrow service-manager environment override. Preserve unrelated entries and restart after environment changes.

### 5. Install as a persistent service

Use the official runner service mechanism. On Linux/systemd use the configured runner's `svc.sh` where applicable.

The runner service must run as the intended non-root user, be active now, be enabled at boot, and reconnect without an interactive shell.

### 6. Verify without launching a model request

Verify:

- service active, enabled at boot, and running as the intended user;
- GitHub lists the runner online for the exact repository with `self-hosted` and `codex`;
- service environment has the correct durable repo/worktree/log/socket configuration;
- persistent repo origin is exact and a harmless `git fetch` works;
- Python 3 and `setsid` exist on Linux;
- the shared App Server socket exists, is a Unix socket, and is owned/reachable by the intended user;
- workflow has no checkout step and uses the persistent issue worktree plus shared-App-Server topology.

Do not add/remove `execution-ready`, create a dummy issue, or launch a model request merely to verify installation. The first real issue is the end-to-end test.

## Repair behavior

Prefer repair over replacement.

- Runner executes from `_work`: repair `SKILLFORGE_REPO_ROOT`; never fall back to `GITHUB_WORKSPACE`.
- App Server socket missing: restore/reconnect the intended Desktop SSH App Server; do not launch a competing executor as fallback.
- WebSocket/initialize/thread protocol rejected: verify Codex version/protocol compatibility and repair the version mismatch.
- Thread created but Desktop groups it under Recents: treat this as the known project-catalog presentation limitation unless thread/worktree identity is actually wrong.
- Thread active with no matching launcher client: fail closed; do not send a new `turn/start` because that may steer the active turn.
- Background client dies when the Actions job ends: verify `RUNNER_TRACKING_ID` detachment.
- Codex can run but cannot mutate GitHub: verify persistent user Git/GitHub authentication; never persist an Actions job token.
- Offline but correctly registered runner: inspect service/network/environment and repair/restart.
- Codex/App Server authentication unavailable: report the exact blocker; do not synthesize credentials.

Never interrupt a live App Server issue turn merely to repair the runner service unless explicitly authorized.

## Bootstrap integration

`repository-bootstrap` may offer this capability only as an **optional post-bootstrap handoff**. Repository bootstrap remains valid without a runner and its file-write hard gate is not widened. Runner setup failure after successful bootstrap is a separate infrastructure failure and may be retried through this persistent skill.

## Completion report

Report only:

- target repository and durable repo root;
- runner name/install directory;
- service identity, active state, and boot enablement;
- worktree/log roots and App Server socket path;
- GitHub runner online/offline state and labels;
- shared App Server reachable/unreachable state;
- whether setup reused, repaired, created, or optionally scaled the runner installation;
- any real authentication, permission, workflow, App Server protocol, or host blocker.

Never include registration tokens, authentication files, secret values, or verbose logs.
