---
name: safe-agent-delegation
description: Use before delegating coding or planning to MKCREW agents when git backup, worktree isolation, detailed prompts, allowed files, stop conditions, and verification are needed. Canonical source of truth for the planner read-only prompt, git-checkpoint commands, reply discipline, and delivery watchdog.
---

# Safe Agent Delegation

Use this skill before asking another MKCREW agent to plan, edit, refactor, review, or run broad checks. This is the canonical source of truth for the read-only planner prompt and the git-checkpoint commands; other skills (e.g. `senior-developer-loop`) point here instead of re-embedding them.

## Delegation Discipline

Delegate role work to named MKCREW agents (planner, opus1-3, sonnet4-6, reviewer) through the `mk ask` command. Do not use your own internal subagent or built-in Task tool in place of a MKCREW teammate; MKCREW agents are visible to the human and go through team review and safety, internal subagents do not.

Decompose work yourself; sub-agents may not spawn further sub-agents (recursive delegation hides the work tree). If no roster agent covers a sub-task, surface that gap to the human instead of doing it silently.

Roles: `main` (lead), `planner` (read-only plans), `opus1/2/3` (STRONG tier — complex/auth/architecture), `sonnet4/5/6` (FAST tier — bounded mechanical edits), `reviewer`. See `task-router` for agent selection.

## Git Safety

Start with repo state:

```bash
git status --short
git branch --show-current
```

For normal AI coding, create a branch and checkpoint:

```bash
git switch -c ai/<task-name>
git add -A
git commit -m "checkpoint: before ai task"
```

If user changes should not be committed yet, use a worktree or ask first:

```bash
git worktree add ../<project>-ai-<task-name> -b ai/<task-name>
```

Do not discard, reset, or overwrite user changes without explicit human approval.

## Planner Read-Only Contract

<!-- PLANNER-READONLY-CONTRACT -->
Use this for `planner` first. The planner is READ-ONLY by default:

```
mk ask --callback planner <<'PLAN'
Read-only implementation planning task.

Goal:
<goal>

Context:
<what the project does, relevant constraints, current failure, user preference>

Scope:
- Inspect only these files/areas first: <paths>
- Do not edit files.
- Do not run destructive commands.
- Do not call browser, preview, design, dev-server, file-write, patch, dependency, auth, or "declare done" tools.
- Allowed actions are read/list/search/status style inspection only.
- Do not change dependencies, config, auth, generated files, or unrelated code unless explicitly listed.

Required output:
1. Current understanding
2. Files likely to change
3. Step-by-step implementation plan
4. Edge cases and risks
5. Rollback/checkpoint advice
6. Verification commands
7. Questions or blockers

Stop after the plan.
PLAN
```

The planner is read-only by default. Promote it to editing ONLY via a SEPARATE, explicit second prompt that lists allowed files, a stop condition, and a verification command. Never let a planning session start editing in the same prompt.

**Named tool bans for planner (prohibited — name these explicitly in every planner prompt):**
The planner MUST NOT use: Edit, Write, MultiEdit, NotebookEdit (file edits/writes); Bash builds or dev-server starts (`npm run`, `python -m`, `uvicorn`, `flask run`); destructive ops (`git reset --hard`, `git clean`, `rm -rf`, `DROP`, `TRUNCATE`); any tool that declares work done or pushes to remote. Violation of this contract requires an immediate re-delegation with an explicit STOP instruction.

## Bounded Implementation Prompt

Only after accepting a plan, and (for the planner) only as the SEPARATE second prompt described above:

```
mk ask --callback <agent> <<'IMPL'
Bounded implementation task.

Goal:
<goal>

Allowed files:
- <path>
- <path>

Do not modify:
- dependencies unless explicitly listed
- auth/secrets/global config
- unrelated files
- generated artifacts unless requested

Implementation steps:
<numbered steps from accepted plan>

Stop condition:
Stop after <phase/behavior> and report back. Do not continue into extra improvements or unrelated refactors.

accept_when:
<verification command> exits 0 and introduces no new failures.

Verification:
Run: <commands>
If verification fails, report the failure and the smallest proposed fix. Do not expand scope to fix it.

When done, complete this delegation by running:
  mk-done <job_id> "<one-line summary of what was done>"

Return:
- changed files
- summary
- verification results
- risks/blockers
IMPL
```

Use `mk ask --callback` when you need the result before continuing. Use `mk ask --silence` for independent low-risk checks where success needs no reply.

## Review Prompt

```
mk ask --callback reviewer <<'REVIEW'
Review this plan or diff. Return blocking issues first.

Check:
- correctness
- regressions
- missing tests
- unsafe file scope
- unnecessary dependency/config changes
- simpler alternatives

accept_when:
no blocking issue remains and the change stays inside the agreed file scope.

Context:
<plan or diff summary>

When done: mk-done <job_id> "<one-line verdict>"
REVIEW
```

## Task State and Handling Failures

Track each delegated task through four states: AWAITING_ACK -> IN_PROGRESS -> REVIEW_PENDING -> DONE (or ABORTED). Do not advance a task to DONE on assumption; advance only on a real returned result.

If a delegate returns PARTIAL or FAIL, your only legal moves are:

- re-issue a bounded prompt to the SAME agent,
- reassign to ANOTHER named agent, or
- escalate to the human.

Do not finish the task inline yourself. Retry budget: at most 2 retries before you must escalate to the human. On abort, emit a snapshot: last good checkpoint/worktree, failure reason, and recommended remediation. Never abort silently.

Keep session state compact: log milestone + outcome per delegation (e.g. "planner: plan accepted", "worker: FAIL -> reassigned"), drop raw tool output once summarized.

## Delivery Watchdog (self-healing, with one cheap manual check)

Reply capture is event-driven: a Stop hook reports the reply within seconds of the agent's turn ending. The daemon self-heals: a delivery with no evidence after ~5 minutes, or a delivered job whose observer heartbeat freezes for ~10 minutes, is cancelled and retried automatically (2 retries, then an explicit INCOMPLETE reply to the asker — you are never left waiting silently). Daemon restarts reconcile in-flight deliveries on startup.

Keep these habits:

1. A job in `delivering` state while the teammate is VISIBLY WORKING is HEALTHY. Age alone is never evidence of failure. Do not cancel a job because it is N minutes old.
2. Before suspecting transport failure, gather evidence: look at the pane (actively working? leave it alone) and `mk pend`. A job whose snapshot shows progress is being captured — cancelling it destroys a healthy delivery.
3. Manual intervention is justified only when the teammate has VISIBLY FINISHED and the reply has not landed after ~2 minutes. Even then: `mk trace <job_id>` first, then cancel + re-issue. Otherwise trust the daemon's watchdog.
4. `mk repair resubmit <job_id>` requires terminal attempts — the active attempt should be cancelled first. If captures fail repeatedly, inspect daemon logs.
5. Design every delegation to be idempotent on re-paste (verify on-disk state, re-run verification, reply) — watchdog retries then cost seconds, not a re-implementation.

## Parallel Workers (tiered teams)

The lead parallelizes ONLY independent milestones: allowed-file sets must be DISJOINT — two agents must never touch the same file; overlapping tasks run sequentially. Route by tier first (Opus = complex/risky/auth; Sonnet = bounded mechanical), then pick the lowest-numbered idle agent of that tier. Each parallel delegation carries its own complete contract. Collect all replies before integrating; review the combined result before the next wave.

## Reply Discipline

<!-- REPLY-DISCIPLINE -->
- Every delegation completes exactly once: the receiving agent MUST run `mk-done <job_id> "<one-line summary>"` when finished. If a teammate does not complete via `mk-done`, the job stays open; the sender should check `mk trace <job_id>` before re-asking to avoid duplicate deliveries.
- Sender: include this line in every result-needed delegation: "Complete this task by running `mk-done <job_id> \"<one-line summary>\"`. Quote this job ID verbatim."
- Receiver: never report a delegation as "no result" until you have searched jobs for its ID — replies can arrive under a different request ID. Run `mk pend` to check open jobs.
- Ground truth over recall: team facts (which agent runs which model) come from `.mkcrew/team.config` and `mk trace` output, NEVER from memory. Before attributing a result to a model or quoting a past instruction, re-read the actual source; if you cannot find the text, say "I cannot verify this" instead of quoting from recall.
- Escape valve: every delegation completes exactly once — with the result, a workspace file path plus summary when the result is oversized, or an explicit failure reply. Never silence.

## Inspection Commands

- `mk pend` — list all open/in-flight jobs (status table).
- `mk trace <job_id>` — full detail + event log for a single job.
- `mk repair resubmit <job_id>` — force redelivery of a wedged job.

## Completion Rule

Do not merge or continue broad work until:

- checkpoint/worktree exists for risky edits,
- scope is explicit,
- changed files are reviewed,
- verification is run or the reason it cannot run is documented.

## Failure Modes

- Letting the planner edit in the same session — requires the SEPARATE second prompt above.
- Substituting an internal subagent/Task tool for a MKCREW teammate — hides work from the human.
- Finishing a delegate's PARTIAL/FAIL inline instead of re-delegating or escalating.
- Skipping the checkpoint/worktree before risky or delegated edits.
- Forgetting `mk-done` in the delegation prompt — leaves jobs permanently open.
- Re-asking before checking `mk trace <job_id>` — risks duplicate delivery.

## Changelog

- 2026-06-13: Adapted for mkcrew. Replaced codex/agy/OpenCode/Gemini roster with 9-role tiered team. Replaced Linux `command ask` syntax with `mk ask --callback|--silence <role>`. Added `mk-done <job_id>` reply discipline as the canonical completion mechanism. Added named tool bans for planner. Kept git-safety mechanics, reply discipline, delivery watchdog, and parallel-worker contract intact.
