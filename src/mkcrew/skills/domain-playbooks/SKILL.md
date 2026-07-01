---
name: domain-playbooks
description: Use after task-router classification when work touches a concrete engineering domain (frontend, backend/API, database, infrastructure/cloud, testing, security) — supplies that domain's delegation routing, verification contract, and danger list to embed in MKCREW delegation prompts.
---

# Domain Playbooks

The models already know the domains; what they skip under pressure is domain-specific verification and danger handling. After `task-router` classifies the work, open the matching playbook(s) and copy the **Verify** contract and **Dangers** list into the delegation prompt (`safe-agent-delegation` owns the prompt mechanics — allowed files, stop condition, job-ID echo). Playbooks compose: a full-stack feature uses several, but each milestone's delegation carries only ITS domain's contract.

Roster facts (who runs what) live in `.mkcrew/team.config` — verify there, never recall.

## Frontend / UI

- Route: spec by `planner` (read-only artifact; planner never writes code); implement by an Opus-tier agent; visual verification by `reviewer` or via browser tooling.
- Verify: build passes; lint on touched files only; responsive sweep at the project's key widths; accessibility basics on touched surfaces (labels matched to control ids, contrast, focus order); zero new console errors on the changed flows.
- Dangers: never claim visual correctness from code reading alone — runtime evidence required. Pre-existing lint/contrast debt gets NOTED, not silently fixed (scope).

## Backend / API

- Route: Opus-tier agent implements. Auth, payments, and distributed-system logic always go to the strongest implementation tier — never to a fast-tier sonnet agent for complex work.
- Verify: tests for the changed behavior (failing test first when fixing a bug); the project's full test command after; exercise changed endpoints once for real (test client or curl) including at least one error path; flag any breaking change to a public contract explicitly in the reply.
- Dangers: never assert API/SDK facts from memory — read the installed version or docs in-repo; orchestrator recall of API details is unreliable.

## Database / Migrations

- Route: Opus-tier agent, with the migration plan stated in the delegation; destructive schema changes get a reviewer pass BEFORE apply.
- Verify: migration applies cleanly on a dev/copy database first; a rollback path exists and was executed once; post-migration data spot-check; the app boots and its smoke flow works against the migrated schema.
- Dangers (HARD STOPS — require explicit human approval inside the delegation text): DROP/TRUNCATE/destructive ALTER on data anyone cares about; mass UPDATE/DELETE without a reviewed WHERE clause and an expected-row-count preview; pointing any migration at a database not clearly identified as dev. Dump/backup before every irreversible step and say where the backup landed.

## Infrastructure / Cloud

- Route: plan written by `planner` or `main`; execution by an Opus-tier agent with this danger list pasted into the delegation verbatim.
- Verify: dry-run FIRST, always (`terraform plan`, `kubectl diff`, `--dry-run`, provider equivalents); apply only what the dry-run showed; post-apply smoke check; call out anything cost-relevant (instance sizes, regions, autoscaling, egress) in the reply even when unasked.
- Dangers (HARD STOPS): deleting stateful resources; DNS changes; IAM/permission widening; production deploys; anything billable beyond trivial. No `--force` or `--auto-approve` flags in delegated commands. Credentials never appear in prompts, replies, or logs — reference their location instead.

## Testing / QA

- Route: `reviewer` or an Opus-tier agent runs sweeps and reproduces bugs; a Sonnet-tier agent writes missing tests for well-scoped cases.
- Verify: bug fixes start from a failing reproduction; full suite green after; flaky tests get quarantined and reported, never deleted to go green.
- Dangers: editing a test's assertions to make it pass is a reviewer-visible event, never a silent change.

## Security-sensitive changes

- Route: Opus-tier agent implements; the reviewer pass MUST include an explicit security dimension (injection, authz on new surfaces, secrets in code/logs).
- Verify: input validation on every new external surface; authz checked on new endpoints (not just authn); no secrets in diffs or output.
- Dangers: auth, payments, crypto, and PII handling are never bundled into unrelated milestones and never "quick fixes" — they get their own bounded milestone with review, every time.

## Changelog

- 2026-06-13: Adapted for mkcrew. Replaced worker/tester roster references with Opus-tier/Sonnet-tier/reviewer. Removed Sackysocky tool references (not present on this clone). Routing and verification contracts are otherwise faithful to the Linux original.
