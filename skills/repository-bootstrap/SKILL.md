---
name: repository-bootstrap
description: Initialize a repository created from the Skillforge template exactly once, establish required GitHub labels and project-specific top-level instructions, then remove this bootstrap skill and all of its template-only references; optionally hand off to local Codex runner provisioning after the repository bootstrap is complete.
---

# Repository Bootstrap

## Responsibility

Use this skill exactly once when initializing a new repository created from the Skillforge template.

This is a **one-shot bootstrap capability**, not a permanent project workflow.

Its successful repository terminal state is that:

- the repository has a project-specific `README.md`;
- `AGENTS.md` contains the repository-specific invariants that are already known and genuinely required;
- the required Skillforge GitHub labels exist;
- `skills/repository-bootstrap/` no longer exists;
- `AGENTS.md` and `README.md` no longer mention or route to `repository-bootstrap`.

The optional local Codex runner is deliberately **not** a repository-bootstrap acceptance criterion. Runner provisioning is a separate host/GitHub control-plane capability owned by the persistent `skills/codex-local-runner/SKILL.md`.

The presence of `skills/repository-bootstrap/SKILL.md` in a repository created from the template means bootstrap has not yet completed.

**Never execute this skill in the canonical `murillo128/skillforge` template repository itself.** The template must retain the bootstrap skill so newly created repositories receive it.

## One-time authority

Explicit invocation of this skill grants authority to perform the repository bootstrap directly on the repository's default branch without creating an issue or pull request.

That authority is deliberately narrow.

### Permitted file mutations

The bootstrap may only:

- create or replace `README.md`;
- update `AGENTS.md`;
- delete `skills/repository-bootstrap/**` as its final self-removal step.

It must not modify any other repository file or directory.

In particular it must not modify code, tests, `docs/**`, `wiki/**`, other skills, build files, CI, configuration, fixtures, generated artifacts, or project data.

### Permitted GitHub metadata mutations

The bootstrap may create or normalize the required repository labels defined below.

It must not create project issues, PRs, milestones, releases, projects, branches, or unrelated repository configuration merely as part of bootstrap.

### Optional runner authority remains separate

An explicit request to configure the local Codex runner during bootstrap is only a recorded opt-in for a **post-bootstrap handoff**.

It does not widen this skill's file or metadata mutation authority. The runner setup must not begin until the repository bootstrap commit has been published and verified. At that point, invoke `skills/codex-local-runner/SKILL.md`, whose own authority and safety gates govern host/service and runner-registration changes.

If runner provisioning later fails, the already verified repository bootstrap remains complete; report the infrastructure failure separately and leave the persistent runner skill available for retry.

## Required labels

Before bootstrap can complete, verify that these labels exist at repository scope:

- `execution-ready`
- `in-progress`
- `review-ready`
- `design-required`
- `investigation-required`
- `blocked`
- `completed`
- `curator-detected`

Create any missing labels using the simplest available GitHub transport.

The label **names** are part of the Skillforge workflow contract. Descriptions may be concise and useful; colors are presentation only and are not normative.

Do not delete unrelated labels. Do not rename an existing unrelated label merely to reuse its color or description.

If the required labels cannot be created or verified, bootstrap is incomplete and the skill must remain installed.

## Optional local Codex runner

Repositories created from the template inherit `.github/workflows/codex-execute-ready.yml` and `skills/codex-local-runner/SKILL.md`.

The local executor is opt-in:

- do not install or register a runner merely because the workflow exists;
- absence of a matching runner does not make bootstrap incomplete;
- if the user explicitly requests local runner provisioning as part of initialization, record that intent before publication and invoke `codex-local-runner` only after repository bootstrap publication and verification;
- if the user does not explicitly opt in, perform no host-side runner changes;
- never provision a repository runner for the canonical `murillo128/skillforge` template itself.

The inherited workflow is expected to make its execution job eligible only when the newly applied issue label is exactly `execution-ready` and to target `[self-hosted, codex]`. Runner setup and verification belong entirely to `codex-local-runner`.

## Project-specific README

`README.md` must stop being the Skillforge template README and become the durable project-level entry point.

Use project information explicitly supplied by the user or already established in authoritative repository context. At minimum it should make the actual project mission and scope clear.

Add setup, usage, architecture, status, or other sections only when they are already meaningful for the project. Do not fabricate architecture, commands, roadmap, features, or maturity merely to fill a template.

Do not preserve Skillforge setup instructions just because they came from the template. A brief attribution to Skillforge is allowed if the project wants one, but the README must describe the actual project rather than the template machinery.

If there is not enough authoritative context to write a truthful project README, stop and report what project-level information is missing. Do **not** self-remove.

## Project-specific AGENTS additions

Review `AGENTS.md` and add only repository-wide invariants that are already established and materially useful, such as:

- language/runtime and coding constraints;
- ownership, lifetime, concurrency, or security invariants;
- required build/test/evaluation paths;
- dependency or licensing constraints;
- hardware/runtime requirements;
- project-specific correctness or performance hard failures;
- explicit planning authority when the project actually uses one.

Do not invent process, architecture, phases, roadmaps, schemas, or speculative project rules during bootstrap.

Keep reusable procedure in skills and task-specific detail in issues. Bootstrap should leave `AGENTS.md` compact.

## Idempotent bootstrap procedure

Bootstrap must remain safely retryable until final publication.

### 1. Verify repository identity

Confirm the repository is the intended newly created project.

If it is `murillo128/skillforge`, stop immediately without mutation.

### 2. Load minimal initialization context

Read:

- this skill;
- current `README.md`;
- current `AGENTS.md`;
- repository metadata needed to identify the default branch;
- existing repository labels;
- only the additional project context needed to write truthful project-specific top-level documentation.

Also record whether the user has **explicitly opted in** to local Codex runner provisioning for this initialization. Unspecified means disabled; do not interrupt or block repository bootstrap merely to obtain a runner preference.

Do not inspect the entire history merely because bootstrap is running.

### 3. Ensure required labels

Create missing required labels and verify all eight required names exist.

Label creation is intentionally completed before self-removal. If a run stops later, repeating this step must be harmless.

### 4. Prepare the project README

Rewrite or validate `README.md` as the actual project's README.

Do not proceed to self-removal while the README still describes Skillforge rather than the new project.

### 5. Adapt AGENTS.md

Add only established project-specific repository invariants.

Preserve the generic Skillforge workflow unless the project has made an explicit stricter decision.

Preserve the routing entry for the persistent `skills/codex-local-runner/SKILL.md`; only bootstrap-specific routing and instructions are removed.

### 6. Prepare mandatory self-removal

A successful bootstrap commit must also:

- delete `skills/repository-bootstrap/SKILL.md` and therefore the now-empty `skills/repository-bootstrap/` directory;
- remove the `repository-bootstrap` routing entry from `AGENTS.md`;
- remove the template-only one-time bootstrap instructions from `AGENTS.md`;
- remove bootstrap instructions/references from `README.md` as part of making it project-specific;
- leave no stale `repository-bootstrap` reference in either `AGENTS.md` or `README.md`.

Do not leave the bootstrap skill installed "for later" after a successful run. Its deletion is part of the acceptance criteria.

## Mandatory publication hard gate

Before creating the final bootstrap commit, inspect the **complete candidate change**, independent of transport.

The candidate file-path set must be a subset of exactly:

```text
README.md
AGENTS.md
skills/repository-bootstrap/SKILL.md
```

The skill path may only be deleted, not replaced with a persistent bootstrap implementation.

If any candidate path falls outside this set, **abort publication**. Do not stage, commit, push, move a Git ref, or otherwise publish the candidate.

Also verify before publication that:

- all required labels exist;
- `README.md` is project-specific and truthful;
- `AGENTS.md` contains no `repository-bootstrap` routing or bootstrap-only section;
- `AGENTS.md` still routes optional local runner setup to `skills/codex-local-runner/SKILL.md`;
- the resulting tree does not contain `skills/repository-bootstrap/SKILL.md`;
- no project content outside the permitted bootstrap files is being changed.

This is a fail-closed gate.

## Final commit

Publish the completed bootstrap as **one coherent commit directly to the default branch**.

Use the repository commit convention, normally:

```text
chore: bootstrap repository
```

Prefer local Git or Git data/tree APIs when needed to make the README update, AGENTS update, and skill deletion atomic in one commit.

If the available transport cannot safely publish the bootstrap as one coherent commit, use another permitted transport or stop. Do not intentionally leave the default branch in a partially bootstrapped state.

Do not open a PR for this one-time bootstrap unless the user explicitly overrides this skill's direct-publication behavior.

## Post-publication verification

After publication, verify the observed default-branch state:

- the bootstrap commit is the intended published target;
- its changed file paths were limited to the permitted bootstrap set;
- `README.md` is project-specific;
- `AGENTS.md` has no bootstrap routing/instructions;
- `AGENTS.md` still routes the persistent local runner skill;
- `skills/repository-bootstrap/` is absent;
- all required labels exist.

Do not report repository bootstrap complete until these conditions are observed.

## Optional post-bootstrap local runner handoff

Only after the repository bootstrap is published and verified:

- if the user explicitly opted in, load `skills/codex-local-runner/SKILL.md` and execute it against this newly initialized repository;
- if the user did not opt in, perform no runner installation or registration;
- do not modify repository files as part of this handoff;
- if runner setup fails, report the runner-specific blocker separately without reverting or misreporting the already completed repository bootstrap.

The runner skill remains installed after bootstrap, so this optional setup can always be invoked or retried later.

## Failure behavior

Bootstrap is intentionally fail-safe:

- if required project context is missing, stop without self-removing;
- if a required label cannot be established, stop without self-removing;
- if a candidate write escapes the permitted path set, abort publication;
- if atomic publication is unavailable, stop rather than leave a partial bootstrap;
- if verification fails after publication, report the exact mismatch and do not pretend initialization succeeded.

A partial run may leave already-created required labels behind. That is safe and should make the next run simpler.

A failure in the separately invoked optional local runner setup does not retroactively invalidate a successfully published and verified repository bootstrap.
