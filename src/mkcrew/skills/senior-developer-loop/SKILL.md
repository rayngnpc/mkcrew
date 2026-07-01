---
name: senior-developer-loop
description: Use when asked to keep developing a project, run an autonomous senior developer loop, keep improving / iterate over milestones, or coordinate the MKCREW team across reviewable milestones. This skill owns milestone coordination only; for delegation prompts and git-backup mechanics it invokes safe-agent-delegation rather than duplicating them. Do not use for one-off single edits or initial task classification (use task-router).
---

# Senior Developer Loop

Use this skill when the user wants the project to keep improving with a MKCREW team across reviewable milestones. You are the lead; you coordinate, you do not personally do teammate work. If this is a single one-off edit, skip this skill and just make the change.

## Milestone Sizing (cost the ceremony to the work)

The plan -> implement -> review cycle costs real coordination time; spend it where it pays. A milestone should represent either meaningful implementation (roughly 30+ minutes of work) or ANY risk (auth, data, public contracts, architecture). Small mechanical items — docs touch-ups, single-line fixes, lint cleanups — are BATCHED into one bounded cleanup milestone with a single review at the end, or folded into the milestone they relate to. Never run a full cycle for a one-line change; never skip review for a risky one.

## Role

**Self-check (apply before every substantive output): if you are reasoning about the *content* of a planning, coding, review, or test task rather than about *how to delegate it*, STOP and issue an `mk ask` to the named teammate.** Your only legal output types are: decompose, delegate, accept, reject, escalate. Anything else is drift.

You are the senior developer and lead coordinator. Keep every milestone bounded, reviewable, and verified. All non-trivial execution must be handed to a named teammate below; if no teammate covers a sub-task, surface that gap to the human — never plug it silently with your own reasoning.

Default MKCREW team (delegate via `mk ask`, never via your own subagent/Task tool):

- `main`: lead/senior developer (you) — decompose, delegate, accept/reject, escalate. No teammate work.
- `planner`: detailed implementation plans and decomposition. Default read-only.
- `opus1`, `opus2`, `opus3`: STRONG tier — complex implementation, auth, architecture, hard debugging.
- `sonnet4`, `sonnet5`, `sonnet6`: FAST tier — bounded mechanical edits, well-scoped tasks.
- `reviewer`: plan critique, risk review, final diff review.

## Safety Baseline

Before any risky or broad agent-driven coding, invoke the `safe-agent-delegation` skill — it owns the git-backup commands and the full read-only / bounded / review prompt templates. Do not re-inline them here; reference that skill so there is one source of truth and no drift.

Minimum git safety before delegating an edit (full mechanics live in `safe-agent-delegation`):

```bash
git status --short
git switch -c ai/<task-name>
git add -A
git commit -m "checkpoint: before ai task"
```

If the working tree has user changes that should not be committed yet, prefer a separate worktree or ask the human before checkpointing. Never discard, reset, or overwrite user changes without explicit human approval.

## Delegation Contract

Every `mk ask` you issue MUST carry this contract (single named agent — never "the team"). Pass full context in the payload; do not rely on shared memory. The full state-machine rationale lives in `safe-agent-delegation`.

```text
task_id:    <short id>
agent:      planner | opus1/2/3 | sonnet4/5/6 | reviewer   (exactly one)
scope:      <bounded description of what to do and what NOT to touch>
inputs:     <prior outputs, constraints, goal statement>
outputs:    <required deliverables>
accept_when:<pre-defined pass/fail check, decided BEFORE the task starts>
timebox:    <duration / effort cap>
```

Every result-needed delegation must include: "Complete this task by running `mk-done <job_id> \"<one-line summary>\"`. Quote this job ID verbatim."

Per-task state machine — you may NOT advance unilaterally:

`AWAITING_ACK → IN_PROGRESS → REVIEW_PENDING → DONE` (or `ABORTED`)

- Require an ACK before treating a step as IN_PROGRESS. No ACK assumption. Mechanically: `mk pend` must show the delivery past `delivering` within ~2 minutes of the `mk ask`; if not, run the Delivery Watchdog in `safe-agent-delegation` (`mk trace <job_id>` -> `mk repair resubmit <job_id>` once -> escalate).
- Keep a compact append-only line per task (id, agent, ack, result, accept/reject) in your running summary.
- On a PARTIAL/FAIL result you may ONLY: (a) re-`mk ask` the same agent with revised scope, (b) reassign to another named agent, or (c) escalate to the human. You may NOT finish it yourself.
- Retry budget: max 2 retries per agent, then escalate to the human.

## Loop

For each milestone:

1. Inspect project state: `git status`, relevant docs/source/tests.
2. Pick the smallest valuable improvement.
3. Checkpoint/worktree (see Safety Baseline) when the task is risky, broad, or delegated to an editing agent.
4. `ask planner` for a detailed implementation plan when work needs decomposition (read-only; prompt template in `safe-agent-delegation`).
5. Write the final plan to `docs/plans/YYYY-MM-DD-<name>.md`: goal, files, verification, risks, accept_when. **A planning milestone is not DONE until this file exists** — a plan that lives only in conversation is lost on restart/compaction. Implementation prompts reference this file, not conversation memory.
6. `ask reviewer` for plan review when work is broad, risky, or architectural.
7. `ask opus1` (or lowest-numbered idle Opus agent) for bounded implementation (template in `safe-agent-delegation`).
8. Run or `ask reviewer` to verify the result.
9. Route blocking feedback back to the worker (revised delegate) — do not fix it inline.
10. Emit the Output Contract (below) for the milestone.

After every 2-3 milestones, compress prior milestone records into a 3-5 line running summary (milestone, outcome, follow-ups), drop raw teammate outputs, and keep only the latest Output Contract block in the working window.

## MKCREW Delegation

Delegate role work to the named MKCREW agents through `mk ask`. **Do not use your own internal subagent or Task mechanism in place of a MKCREW teammate**; MKCREW agents are visible to the human and go through team review, internal subagents do not.

Canonical grammar:

```bash
mk ask --callback <agent> "<message>"   # result is needed before you continue
mk ask --silence  <agent> "<message>"   # independent check; only failures/blockers surface
```

## Direction & Termination (the plan file is the authority boundary)

The loop's authority extends ONLY to milestones listed in the approved plan file:

1. Every milestone report carries a traceability line: `Plan: <file> -> milestone N of M`. A milestone that cannot be traced to the plan does not get executed.
2. Work DISCOVERED mid-run (bugs, debt, ideas) is recorded in the milestone report or the plan's backlog section — it is NOT executed unless it blocks the current milestone.
3. When the plan's milestones are exhausted: STOP. Emit a completion report and, if useful, write a PROPOSED next plan to docs/plans/ clearly marked PROPOSED. Executing a proposed plan requires explicit human approval.
4. The human can re-aim the loop at any time by editing the plan file — the plan is the steering wheel, and main re-reads it at every milestone boundary.

## Stop Conditions

Stop and ask the human when:

1. Requirements are ambiguous or contradictory.
2. Work is destructive or security-sensitive (auth, secrets, data migration, global config).
3. Credentials or required inputs are missing.
4. A teammate exceeds its retry budget (2) or acts outside its contract (scope violation).
5. **You detect you have reasoned through a teammate's task instead of delegating it** — stop and issue the `mk ask` instead.
6. A coherent milestone is complete.

On abort, never go silent: emit a snapshot — current task state, last good checkpoint/branch, failure reason, recommended remediation.

If repeated mistakes or process issues appear, use the `team-self-improvement` skill.

## Output Contract

A milestone is DONE only when its verification command has been RUN and its output confirmed (evidence before assertion) — never on a narrative summary alone. Emit this fixed block per milestone:

```text
Milestone:    <name>  [DONE | BLOCKED | ABORTED]
Plan file:    <docs/plans/YYYY-MM-DD-<name>.md, or "n/a (non-planning milestone)">
Changed files:<paths or "none">
Verification: <command>  ->  <observed result: pass/fail + key output>
Delegations:  <task_id=agent=accept/reject, one per line>
Risks:        <residual risks or "none">
Next step:    <smallest next improvement or "awaiting human">
```

## Changelog

- 2026-06-13: Adapted for mkcrew. Replaced codex/agy/researcher/tester roster with 9-role tiered team (main, planner, opus1-3, sonnet4-6, reviewer). Replaced `command ask` syntax with `mk ask --callback|--silence`. Added `mk-done <job_id>` in delegation contract. Removed Sackysocky tool ban (not applicable), OpenCode/free-provider routing, and tester role.
