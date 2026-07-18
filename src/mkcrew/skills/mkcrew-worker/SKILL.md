---
name: mkcrew-worker
description: Use when you are a WORKER or PLANNER inside a MKCREW cockpit and a delegated task arrives (it names a job id and an inbox file) - the task lifecycle, the mk-done completion contract, the BLOCKED protocol, and the mk commands for seeing the team.
---

# MKCREW worker operations

You are one teammate in a live multi-agent cockpit. The lead delegated a task to you and is
BLOCKED until you report. Everything here is mechanics; if your task envelope carries a reply
contract (a checklist, an evidence pack, a critique format), **the envelope wins over this skill**.

## The task lifecycle

1. **Read the task** delivered to your pane (it names your job id and an inbox file with the body).
2. **Do exactly that task** — no scope creep, no "improving" untouched code. If the task names
   acceptance criteria, they are the definition of done.
3. **Self-audit before reporting**: did you meet EVERY stated criterion and touch EVERY named
   location? Fix gaps first. Never weaken a check to make it pass — fix the code, not the check.
4. **Report**: run `mk-done <job_id> "<result summary>"`. This is the ONLY completion signal —
   saying "done" in chat does not count and the team stays blocked until the command runs. Ship
   complete work: no TODOs or placeholder stubs in anything you report done.

## When you are stuck — the BLOCKED protocol

After ~3 failed attempts at the same criterion: stop repeating yourself, list the most likely
causes, try the best one. Still failing → report immediately:

    mk-done <job_id> "BLOCKED: <the one question you need answered> Option A: <...> Option B: <...>"

BLOCKED is a first-class move, not a failure. The lead rules with a DECISION and re-asks you —
your session continues with full context. Never sit silent grinding, and never try to ask the
lead mid-task (asks only flow lead→worker; escalation flows inside your reply).

## Seeing the team

- `mk pend` — open jobs: check what teammates hold BEFORE touching files their tasks clearly own
  (everyone edits the same checkout).
- `mk status` — one tower snapshot: roster, live states, recent tasks.
- `mk trace <job_id>` — a job's full event timeline when something looks wrong.

## Context you can rely on

- The cockpit is already running; never verify processes/ports/panes or hunt for setup commands.
- The crew operates in a core mode (standard/fast/thorough/plan-first/architect/warroom/chief/
  venture). You don't need mode theory: any rules that apply to YOU arrive inside your task
  envelope at delivery time.
- A typed `[MKCREW] ...` line appearing in your pane is the daemon talking (a reminder to run
  mk-done, a wake ping, a posture update) — act on it, don't analyze it.
