# src/mkcrew/prompts.py
"""Bootstrap prompts injected into panes at launch (single-line, sent via send_line)."""

LEAD_PROMPT = "You are the lead (main) of a MKCREW team. When given work, use the task-router and senior-developer-loop skills, and delegate role work to teammates with `mk ask <role> \"...\"` — worker1..worker6 for implementation work, reviewer for the review gate, planner for read-only planning. Do not do teammate work yourself. Wait for the first instruction before acting."

PLANNER_PROMPT = "You are the READ-ONLY planner. Produce implementation plans only. NEVER edit/write files, run builds or dev-servers, or use any action/destructive tool. When a task is delegated to you, read it, produce a plan, and report the plan by running mk-done with your job id."

# Invariant: prompts must be single-line so send_line() delivers them atomically.
assert "\n" not in LEAD_PROMPT, "LEAD_PROMPT must not contain newlines"
assert "\n" not in PLANNER_PROMPT, "PLANNER_PROMPT must not contain newlines"


def _role_function(role: str) -> str:
    """A one-phrase hint of what a role is for, derived from its name."""
    if "rev" in role:             return "the review gate"
    if "plan" in role:            return "read-only planning"
    if role.startswith("worker"): return "implementation work"
    return "delegated work"


# Core-mode posture appended to the lead bootstrap. 'standard' is the default (no clause); 'fast'
# tells the lead to drop the gates. One entry per non-default mode -- add a mode = add a line here.
_MODE_CLAUSE = {
    "fast": "FAST MODE: skip the planner and the review/verify gates -- delegate the work, accept "
            "it, and ship directly without the plan->review->verify ceremony. ",
}


def lead_prompt(mk: str, team=None, mode: str = "standard", provider: str = "claude",
                name: str | None = None) -> str:
    """Lead bootstrap prompt: the FULL `mk` path PLUS the ACTUAL team roster (each teammate's
    role + provider/CLI + function), built from team.config — so the lead knows exactly who
    exists and which CLI each one is, and never delegates to an agent that isn't on the team.
    `mode` appends the core-mode posture ('fast' drops the gates; 'standard' adds nothing).

    `provider` is the MAIN agent's own CLI. The task-router / senior-developer-loop skills ship only
    for claude (src/mkcrew/skills), so that instruction is included ONLY when provider == 'claude';
    a codex / agy / opencode lead gets the same core briefing minus the claude-only skill sentence.

    `name` (FIX #4: name-as-identity) is the workspace's stable human name: when set, the lead is told
    it leads the "<name>" workspace (so two cockpits read as e.g. Testing vs Prod, not two anonymous
    mains); when unset it falls back to the generic 'a MKCREW team' wording.

    `mk` is a venv console-script NOT on the agent shell's PATH, so a bare `mk` fails; we inject
    the absolute exe path. Single-line, like the static prompts (delivered via send_line).
    """
    mates = [a for a in (team or []) if a.get("role") != "main"]
    if mates:
        roster = "; ".join(
            f'{a["role"]} ({a.get("provider", "claude")}: {_role_function(a["role"])})'
            for a in mates
        )
        roster_clause = f"Your ONLY teammates are: {roster}. Delegate to them by their exact role name. "
    else:
        roster_clause = (
            "Delegate to your workers (worker1, worker2, ...) for implementation, reviewer for "
            "the review gate, planner for read-only planning. "
        )
    # Claude-only skills (src/mkcrew/skills): name them only for a claude lead, else they're noise.
    skills_clause = ("use the task-router and senior-developer-loop skills, and "
                     if provider == "claude" else "")
    identity = (f'You are the lead (main) of the "{name}" workspace'
                if name else "You are the lead (main) of a MKCREW team")
    return (
        f"{identity}. Your cockpit is ALREADY running -- the "
        "coordination daemon is up and your teammates are live in their panes, ready for work; "
        "do NOT verify processes, ports, panes, or the project setup, and do NOT hunt for "
        f"commands -- just delegate. When given work, {skills_clause}"
        f'delegate role work by running this EXACT command (full path -- a bare `mk` is '
        f'NOT on your PATH): {mk} ask <role> "<task>". {roster_clause}{_MODE_CLAUSE.get(mode, "")}'
        "Do not do teammate work yourself. Wait for the first instruction before acting."
    )


def team_update_prompt(mk: str, team=None, changes=None) -> str:
    """Sent to a RESUMED lead when the team changed since its last run, so it acknowledges the new
    CLIs/roles instead of re-receiving the whole bootstrap. Single-line (delivered via send_line)."""
    mates = [a for a in (team or []) if a.get("role") != "main"]
    roster = "; ".join(f'{a["role"]} ({a.get("command") or a.get("provider", "claude")})' for a in mates)
    chg = "; ".join(changes or []) or "your team configuration"
    return (
        f"Team update since your last run: {chg}. Your teammates are now: {roster}. Keep "
        f'delegating with {mk} ask <role> "<task>" -- no need to re-verify anything.'
    )
