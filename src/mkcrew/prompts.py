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
    "thorough": "THOROUGH MODE: correctness over speed. Every worker result must pass the review "
                "gate before you accept it, and verify claims by RUNNING the result (build/tests/"
                "the app) -- never accept a completion summary on faith, and never accept a pass "
                "obtained by weakening the check itself (a green run on a loosened assertion is a "
                "failure, not a fix). Workers may take long turns; that is expected here. ",
    "plan-first": "PLAN-FIRST MODE: before your FIRST delegation, present the full task breakdown "
                  "(which teammate does what, in what order, which files each task owns) and WAIT "
                  "for the user's explicit OK. After approval, proceed without re-asking per task. ",
    "architect": "ARCHITECT MODE: you are the flagship lead -- the crew's quality ceiling is YOUR "
                 "intelligence transmitted through task briefs. A weaker model executing a "
                 "complete blueprint performs a tier above itself, and most multi-agent failures "
                 "are underspecified plans, so the brief is where your effort goes. Never read "
                 "source files, write code, or run builds/tests yourself; delegate ALL hands-on "
                 "work, including investigation. PLAN once, up front: decompose into slices each "
                 "statable in one sentence with one verifiable outcome; parallelize only slices "
                 "with non-overlapping files; define the deliverable's DEFINITION OF DONE as "
                 "mechanically checkable facts (builds, runs, tests pass, every feature wired "
                 "end to end). BLUEPRINT every ask, specific to THIS task -- the worker knows "
                 "NOTHING you do not write down, so never reference ('as discussed', 'like the "
                 "auth slice'), always explain: the exact files/functions/names to create or "
                 "change; the approach with the key decisions already made by you -- a worker "
                 "must never face an architectural choice; every shared interface (signatures, "
                 "data shapes, routes) stated IDENTICALLY in each ask that touches it; a pasted "
                 "skeleton or example when the pattern is fiddly; what you already RULED OUT and "
                 "why, so no worker re-explores a dead end; acceptance criteria each provable by "
                 "an exact command; and only the 2-3 constraints this worker is actually likely "
                 "to violate, phrased positively (touch only X, use only Y) -- long rule lists "
                 "reduce compliance. CALIBRATE to tier (the roster names each worker's CLI and "
                 "model): small/fast models get step-by-step blueprints and smaller slices; "
                 "strong reasoning models get goal, interfaces, and criteria with freedom on the "
                 "how. Spot-audit ~1 in 5 completed tasks: the worker pastes the FULL diff and "
                 "you review it properly. FINISH: nothing is done until the final assembly check "
                 "-- one worker runs the WHOLE deliverable against the definition of done and "
                 "pastes the outputs. Economy: plan in ONE opening turn, fire the asks, stay "
                 "idle while workers run, judge in batches (mk pend). Hand-code only what you "
                 "could not specify, and announce it. Workers escalate inside their reply, never "
                 "by asking you mid-task. Above all, VERIFY -- the rule most easily skipped "
                 "under time pressure: a DIFFERENT worker re-RUNS every result against its "
                 "criteria; never accept the implementer's own pasted output as the only proof; "
                 "a failed slice gets RE-DECOMPOSED into smaller slices, not re-asked verbatim. ",
}


def mode_update_prompt(mode: str) -> str:
    """Sent to a RUNNING lead when the user switches core mode (`mk mode <m>`), so the posture
    changes live without a cockpit restart. Single-line (delivered via send_line)."""
    clause = _MODE_CLAUSE.get(
        mode, "Return to the balanced default: delegate -> do -> review/verify as the task warrants. ")
    return (f"Core-mode update: the user switched your working posture to '{mode}'. {clause}"
            "Acknowledge in one short line and apply it from your next action onward.")


# Per-provider HANDLING notes injected into the lead bootstrap for the CLIs actually on the crew:
# operational facts about each provider's measured failure modes -- NOT vendor "magic phrasing",
# which expires with every model generation (DeepSeek's own guidance reversed twice in 18 months).
# codex is OFFICIAL (OpenAI's codex prompting guide: asking for narrated plans/status makes it stop
# before finishing -- and its harness can end the turn on a status message before the promised
# action, openai/codex#27352). gemini/opencode are convergent practitioner findings. claude needs
# no note: the lead speaks its own dialect natively. One sentence each -- rule lists reduce
# compliance, so these stay terse and operational.
_PROVIDER_NOTES = {
    "codex": "codex workers go STRAIGHT to work -- never ask codex to present a plan or post "
             "status updates first (that reliably makes it stop before the work is done); give "
             "it one focused objective per ask",
    "antigravity": "antigravity/gemini workers need every constraint and expectation stated "
                   "explicitly in the ask (gemini under-infers unstated rules) and a reply-length "
                   "cap or they run long",
    "opencode": "opencode workers vary by model route and may run custom personas that fight the "
                "crew protocol -- restate the goal AFTER any pasted context block and restate "
                "the mk-done requirement inside the ask itself",
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
        # CLI + MODEL per teammate: the lead calibrates task-brief detail to each worker's tier
        # (small/fast models need step-by-step blueprints, strong reasoners need goals -- measured
        # both directions), which it can only do if the roster names the actual models.
        def _cli(a):
            prov, model = a.get("provider", "claude"), (a.get("model") or "").strip()
            return f"{prov} {model}" if model else prov
        roster = "; ".join(
            f'{a["role"]} ({_cli(a)}: {_role_function(a["role"])})'
            for a in mates
        )
        roster_clause = f"Your ONLY teammates are: {roster}. Delegate to them by their exact role name. "
        # Handling notes only for the provider families actually PRESENT (deduped, roster order):
        # an all-claude crew gets nothing -- bootstrap byte-identical to before.
        notes = "; ".join(_PROVIDER_NOTES[p] for p in
                          dict.fromkeys(a.get("provider", "claude") for a in mates)
                          if p in _PROVIDER_NOTES)
        if notes:
            roster_clause += f"CREW HANDLING: {notes}. "
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
