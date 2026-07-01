---
name: task-router
description: Use at the start of broad, ambiguous, multi-step, risky, or MKCREW team tasks to classify the request, pick skills, pick agents/tier, set the git-safety level, and emit one routing block BEFORE any implementation. Use for a single classification pass at the start; for an ongoing autonomous build loop, route to senior-developer-loop and stop. It does not replace implementation skills; it selects them.
---

# Task Router

Use this skill once, at the start of substantial work, to classify the task and choose the safe path. It does not replace implementation skills; it selects them. Your only legal outputs here are a classification and a routing decision — you do not edit code in this skill.

This is a one-shot router. Classify, emit the routing block, then hand off. For an ongoing/autonomous build loop, exit this skill and run `senior-developer-loop`.

## First rule (self-policing)

Before generating anything substantive, ask: is this work that belongs to a named teammate (planner / opus1-3 / sonnet4-6 / reviewer)? If yes, STOP and either DELEGATE to that agent via `mk ask` or ASK the human. If you find yourself implementing or reasoning through a task's content instead of routing it, you have drifted — stop and delegate.

## Routing Output

Emit exactly one routing block, then stop and hand off:

```text
Task type:    planning | implementation | debugging | review | testing | config | docs | research
Risk:         low | medium | high
Git safety:   none | status-only | branch-checkpoint | worktree | ask-human
Primary skill(s): <skills, or none>
Primary agent:    main | planner | opus1/2/3 | sonnet4/5/6 | reviewer
Support agent(s): <agents, or none>
Mode:         read-only plan | bounded edit | review | verify | troubleshoot | research
Stop condition:   stop after emitting this block; do not begin edits in this skill
```

## Task Type Rules

- Planning / decomposition: `planner` — read-only, produces plan file.
- Hard implementation / auth / architecture: Opus tier (`opus1`, `opus2`, or `opus3`).
- Bounded mechanical edits / focused fixes: Sonnet tier (`sonnet4`, `sonnet5`, or `sonnet6`).
- Review / regression / risk gate: `reviewer`.
- Ongoing development: classify once here, then **exit this skill and run `senior-developer-loop`** for the continuous loop.
- Repeated mistake or reusable workflow: `team-self-improvement`.
- Any broad agent edit: `safe-agent-delegation`.

## Domain Playbooks

After classification, when the work touches frontend, backend/API, database,
infrastructure/cloud, testing, or security, open the `domain-playbooks` skill and
embed that domain's Verify contract and Dangers list in the delegation prompt.

## Ground-Truth Discipline (route from facts, not recall)

The roster and its models live in `.mkcrew/team.config` — read it when classifying, do not recall it. Job history lives in `mk trace <job_id>` / `mk pend`. Never assert which model an agent runs, or quote a past instruction, from memory. If you cannot point at the config line or message text, write "unverified" instead of an attribution.

## Model-Strength Routing

Route by what each tier is measurably best at:

- **Backend / auth / architecture / distributed / security** → Opus tier (`opus1-3`). These require defensive coding, correct semantics, and architectural judgment.
- **Bounded mechanical edits / refactors / docs** → Sonnet tier (`sonnet4-6`). Fast, cost-effective for well-scoped tasks.
- **Deep planning / implementation strategy** → `planner` (read-only artifact first).
- **Final code review** → `reviewer`.

Pick the lowest-numbered idle agent in the required tier. Rosters are in `.mkcrew/team.config`.

## Risk Rules

Git-safety mechanics (the exact `git switch` / checkpoint / worktree recipe) are defined in `safe-agent-delegation` — this skill only selects the level.

Low risk — read-only inspection, docs summaries, simple local commands.
- Git safety: `status-only`.

Medium risk — focused edits to a few files, non-destructive config, normal tests.
- Git safety: `branch-checkpoint` when work is delegated to an editing agent or user changes are present.

High risk — auth/secrets/global config, dependency upgrades, data migrations, broad refactors, destructive commands, or unclear requirements.
- Git safety: `worktree` or `ask-human`.
- Require plan review before any edit.

Never skip git safety on an edit because it "looks low-risk." If it touches files and is delegated, set at least `branch-checkpoint`.

## Agent Selection

Nine roles in two tiers (delegate via `mk ask`, never via your own subagent):

- `main` — lead coordinator. Decompose, delegate, accept/reject, escalate. No teammate work.
- `planner` — READ-ONLY. Produces implementation plans and decomposition artifacts. Never edits files.
- `opus1`, `opus2`, `opus3` — STRONG tier. Complex implementation, auth, architecture, hard debugging.
- `sonnet4`, `sonnet5`, `sonnet6` — FAST tier. Bounded mechanical edits, well-scoped tasks.
- `reviewer` — plan critique, risk review, final diff review.

Give each agent one bounded role; do not ask several agents vague overlapping questions.

Delegate through the `mk ask` command:

```bash
mk ask --callback planner "..."   # blocking: result needed before continuing
mk ask --silence sonnet4 "..."    # independent check; no reply needed
```

Do not substitute your own internal subagents or a built-in Task tool for a MKCREW role; that hides the work from the human and bypasses the team's review and safety model.

## Skill Selection

- `safe-agent-delegation` — when any AI agent may edit files.
- `senior-developer-loop` — when the user asks to keep developing or run an autonomous loop.
- `team-self-improvement` — when a process mistake repeats or the human corrects team behavior.

If no specialized skill fits, pick the closest named agent or surface the gap to the human. Do not silently absorb the task into your own reasoning.

## Planner read-only contract (the gate for the riskiest route)

When you route to `planner`, it is READ-ONLY by default. The full planning prompt lives in `safe-agent-delegation`; the non-negotiable core the routing decision depends on:

- Do not edit files. Do not run destructive commands.
- Allowed actions are **read/list/search/status** style inspection only.
- The planner implements only after a SECOND explicit prompt that lists allowed files, a stop condition, and a verification command.

## Go/No-Go

Before any edit begins (in the hand-off skill, not here), confirm:

- affected files are clear,
- git-safety level is appropriate and applied,
- verification is defined,
- stop condition is clear,
- destructive or credential-sensitive actions have human approval.

If any check fails, do not proceed — re-delegate or escalate to the human.

## Known failure modes

- **Loop instead of hand off** — staying in the router for ongoing work instead of exiting to `senior-developer-loop`.
- **Invented agent name** — routing to a callback that is not in the real roster; verify names in `.mkcrew/team.config`.
- **Skipped git safety** — treating a delegated file edit as low-risk and setting `none`.
- **Silent self-absorption** — implementing or deep-reasoning a sub-task instead of delegating it, leaving no `mk ask` record for the human.
- **Mis-rated risk** — under-rating auth/secrets/global-config work; these are always high risk.

## Changelog

- 2026-06-13: Adapted for mkcrew. Replaced Linux provider-profile/codex/agy/opencode roster with the 9-role tiered team (main, planner, opus1-3, sonnet4-6, reviewer). Dropped Gemini/OpenCode/free-provider routing, tmux references, Sackysocky tool bans (not relevant on this clone). Kept one-shot router pattern, git-safety levels, and planner read-only contract.
