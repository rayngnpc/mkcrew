---
name: team-self-improvement
description: "Use after a MKCREW task when experience should become durable: the same mistake recurs, reviewer flags a preventable issue, a worker misunderstands a delegation, a check is missing/flaky/too broad, the human corrects team behavior, or a workflow succeeds repeatedly and should be reusable. Converts experience into memory, skills, scripts, tests, or lessons. Not for implementing features or routing a fresh task."
---

# Team Self-Improvement

Use this skill to turn one concrete experience into one durable improvement for the whole MKCREW team. Trigger it when a mistake repeats, a review/check/human-correction reveals a preventable gap, or a workflow proves reusable — and only then. It does not implement features or route a fresh task; for routing use `task-router`, for ongoing development use `senior-developer-loop`.

Keep memory compact and factual; put procedures in skills or scripts. Do not bloat always-loaded memory with long workflows.

## Source of Truth & Paths

All ledger paths below are **relative to the current project root** and exist only where the MKCREW self-improvement scaffold is installed (`mk init` creates it). Gate on existence: if `.mkcrew/` or `.mkcrew-self-improvement/` is absent, ask the human where durable lessons should live. Never create stray `.mkcrew-self-improvement/` trees in unrelated repos.

## Improvement Targets

Choose the smallest durable target that makes the fix stick:

- `.mkcrew/memory.md` — compact team facts/rules every agent should always see.
- `.mkcrew/agents/<role>/memory.md` — compact role-specific behavior.
- `skills/<name>/SKILL.md` — reusable procedure loaded on demand.
- `.mkcrew-self-improvement/lessons.md` — accepted lessons (append-only).
- `.mkcrew-self-improvement/proposals.md` — proposed changes awaiting critique.
- scripts / tests / docs — executable or human-readable checks. Prefer these when behavior can be verified mechanically.

## Trigger Conditions

Start an improvement cycle when:

- The same mistake appears twice.
- `reviewer` finds a preventable issue.
- A worker misunderstands a delegation.
- A check is missing, flaky, or too broad.
- A workflow succeeds repeatedly and should become reusable.
- The human corrects team behavior.

If none of these hold, do not run the cycle — route or implement instead.

## Cycle

1. Observe: state what happened, in one or two sentences.
2. Diagnose: identify the root cause and whether it belongs in memory, a skill, a script, a test, or docs.
3. Propose: draft the smallest durable update. For broad team-rule changes, write it to `.mkcrew-self-improvement/proposals.md` first.
4. Ask `reviewer` to critique it.
5. Ask a Sonnet-tier agent to verify it is executable.
6. Apply the chosen durable update to that one artifact only.
7. Verify: validate syntax and run the relevant check/test when one exists. If a planner-rule, init, or shared-guardrail file changed, run `mk-verify-team`.
8. Append the accepted lesson to `.mkcrew-self-improvement/lessons.md` using the Lesson Format below.

Delegate the step-4 critique and step-5 executability check via `mk ask` to the named teammate; do not use your own internal subagent or Task tool in place of a MKCREW role.

## Delegation

Use the real `mk ask` command. `--callback` blocks for the teammate's reply; `--silence` runs it without surfacing chatter.

```bash
mk ask --callback reviewer "Critique this proposed team self-improvement. Find overreach, ambiguity, weakened safety/review/approval boundaries, over-generalization, and simpler alternatives.

<proposal>"
```

```bash
mk ask --callback sonnet4 "Read this proposed instruction as the implementer. Is it clear, executable, too broad, or missing a concrete stop condition / verification command?

<proposal>"
```

Live roster check: verify that your delegation target exists in `.mkcrew/team.config`. If a required role is not configured, surface the gap to the human; do not self-perform the role.

## Lesson Format

Append accepted lessons to `.mkcrew-self-improvement/lessons.md` in exactly this shape (keep every field; do not drop `Durable update`):

```md
## Lesson: <short title>

- Trigger: <what happened>
- Rule: <what the team should do next time>
- Applies to: <main|opus1-3|sonnet4-6|reviewer|all>
- Durable update: <file/script/test changed>
- Verification: <how the rule was checked>
- Date: <YYYY-MM-DD>
```

## Guardrails

- Do not weaken safety, review, or approval boundaries. Self-improvement may strengthen guardrails, never relax them.
- Never make self-improvement a hidden behavior change — every change is proposed, reviewed, applied to a named artifact, and recorded.
- Prefer scripts/tests over vague reminders when the behavior can be checked.
- Keep always-loaded memory short; move long procedures into skills.
- Never edit credentials, auth files, or global shell config as self-improvement.
- Before applying a durable update to shared files (memory, skills, lessons), ensure a git checkpoint or worktree exists — see `safe-agent-delegation`. Preserve user changes; never overwrite them and run no destructive ops without human approval.

## Preserved Team Invariants

These load-bearing rules must be kept verbatim in meaning and keyword; a lesson that weakens any of them must be rejected at step 4.

**Planner read-only:** The planner is read-only by default.

- Do not edit files.
- Do not run destructive commands.
- Allowed actions are read/list/search/status style inspection only.
- The planner implements only after a SECOND explicit prompt that names the allowed files, a stop condition, and a verification command.

**Delegation discipline:** The lead (`main`) delegates role work to MKCREW teammates via `mk ask` (planner, opus1-3, sonnet4-6, reviewer) and does not use its own built-in subagent or internal Task tool for a teammate's work — that hides review and safety from the human.

**Reply discipline:** Every delegation completes exactly once via `mk-done <job_id> "<summary>"`. If unsure whether a teammate finished, run `mk trace <job_id>` before re-asking — never assume silence means failure without checking.

**Git safety:** Checkpoint or worktree before risky/delegated edits; no destructive ops without human approval (`safe-agent-delegation`).

## Self-Check (done)

The cycle is complete only when ALL hold:

- [ ] Exactly one durable artifact was changed (smallest viable target).
- [ ] `reviewer` critiqued it and a Sonnet-tier agent confirmed it is executable, both via `mk ask` (not an internal subagent).
- [ ] No safety/review/approval boundary was weakened; preserved invariants above are intact.
- [ ] A git checkpoint/worktree existed before editing shared files; no user changes were overwritten.
- [ ] Verification ran where a check exists (incl. `mk-verify-team` for guardrail/init/planner-rule changes).
- [ ] A lesson was appended to `.mkcrew-self-improvement/lessons.md` in the Lesson Format.

## Changelog

- 2026-06-13: Adapted for mkcrew. Replaced codex/worker/agy roster with 9-role tiered team. Changed lesson destination from `.a2a-self-improvement/` to `.mkcrew-self-improvement/` (matches `mk init` scaffold). Replaced the old sync-project-skills command with `mk-verify-team`. Replaced Linux `mk ask --callback worker` with `mk ask --callback sonnet4`. Added `mk-done` reply discipline to Preserved Team Invariants.
