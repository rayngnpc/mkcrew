# tests/test_prompts.py
"""Tests for P2-2: role bootstrap prompts (TDD — written before prompts.py)."""
import importlib
import re


# ---------------------------------------------------------------------------
# String properties
# ---------------------------------------------------------------------------

def test_lead_prompt_is_single_line():
    """LEAD_PROMPT must be a single-line string (no newlines)."""
    from mkcrew import prompts
    assert "\n" not in prompts.LEAD_PROMPT, "LEAD_PROMPT must not contain newlines"


def test_planner_prompt_is_single_line():
    """PLANNER_PROMPT must be a single-line string (no newlines)."""
    from mkcrew import prompts
    assert "\n" not in prompts.PLANNER_PROMPT, "PLANNER_PROMPT must not contain newlines"


def test_lead_prompt_contains_ask_role():
    """LEAD_PROMPT must contain 'ask <role>' phrasing."""
    from mkcrew import prompts
    # Must contain the literal phrase 'ask <role>' (could be ask followed by angle-bracket word)
    assert re.search(r"ask\s+<\w+>", prompts.LEAD_PROMPT), (
        "LEAD_PROMPT must contain 'ask <role>' delegation syntax"
    )


def test_planner_prompt_contains_readonly():
    """PLANNER_PROMPT must contain 'READ-ONLY' (case-sensitive)."""
    from mkcrew import prompts
    assert "READ-ONLY" in prompts.PLANNER_PROMPT, (
        "PLANNER_PROMPT must contain 'READ-ONLY'"
    )


def test_lead_prompt_mentions_task_router_skill():
    """LEAD_PROMPT must reference the task-router skill."""
    from mkcrew import prompts
    assert "task-router" in prompts.LEAD_PROMPT, (
        "LEAD_PROMPT must mention task-router skill"
    )


def test_lead_prompt_mentions_senior_developer_loop():
    """LEAD_PROMPT must reference the senior-developer-loop skill."""
    from mkcrew import prompts
    assert "senior-developer-loop" in prompts.LEAD_PROMPT, (
        "LEAD_PROMPT must mention senior-developer-loop skill"
    )


def test_lead_prompt_mentions_role_tiers():
    """LEAD_PROMPT must mention opus and sonnet tiers."""
    from mkcrew import prompts
    text = prompts.LEAD_PROMPT.lower()
    assert "worker" in text, "LEAD_PROMPT must mention the universal worker roles"
    assert "reviewer" in text and "planner" in text


def test_lead_prompt_mentions_planner_role():
    """LEAD_PROMPT must mention the planner role."""
    from mkcrew import prompts
    assert "planner" in prompts.LEAD_PROMPT, "LEAD_PROMPT must mention planner"


def test_lead_prompt_says_do_not_do_teammate_work():
    """LEAD_PROMPT must tell the lead not to do teammate work."""
    from mkcrew import prompts
    text = prompts.LEAD_PROMPT.lower()
    # The instruction says "Do not do teammate work yourself"
    assert "do not" in text, "LEAD_PROMPT must instruct the lead not to do teammate work"
    assert "teammate" in text, "LEAD_PROMPT must mention 'teammate'"


def test_planner_prompt_says_never_edit():
    """PLANNER_PROMPT must instruct the planner to NEVER edit files."""
    from mkcrew import prompts
    text = prompts.PLANNER_PROMPT.lower()
    # Must prohibit edits and writes
    assert "never" in text, "PLANNER_PROMPT must say NEVER"
    assert "edit" in text or "write" in text, (
        "PLANNER_PROMPT must mention prohibited edit/write actions"
    )


def test_planner_prompt_mentions_mk_done():
    """PLANNER_PROMPT must reference mk-done as completion mechanism."""
    from mkcrew import prompts
    assert "mk-done" in prompts.PLANNER_PROMPT, (
        "PLANNER_PROMPT must mention mk-done"
    )


# ---------------------------------------------------------------------------
# cmd_start references prompts module
# ---------------------------------------------------------------------------

def test_cli_imports_prompts():
    """cli must import from the prompts module."""
    from mkcrew import cli
    # Verify that cli has a reference to prompts (directly or via attribute)
    import mkcrew.prompts as prompts_mod
    # Either cli has prompts as an attribute or we can confirm it references the constants
    assert hasattr(cli, "prompts") or (
        hasattr(cli, "LEAD_PROMPT") or "prompts" in dir(cli)
    ), "cli must reference the prompts module"


def test_lead_prompt_referenced_in_cmd_start():
    """cmd_start must build the lead prompt via prompts.lead_prompt(...) and still
    reference PLANNER_PROMPT for the planner."""
    from mkcrew import cli
    import inspect
    src = inspect.getsource(cli)
    assert "lead_prompt" in src, "cmd_start must reference the lead_prompt(...) builder"
    assert "PLANNER_PROMPT" in src, "cmd_start must reference PLANNER_PROMPT"


def test_lead_prompt_lists_actual_team_with_providers():
    """lead_prompt(mk, team) lists the REAL teammates (not main) with their provider, so the
    lead knows who exists and which CLI each is — and never invents agents not on the team."""
    from mkcrew import prompts
    team = [
        {"role": "main", "provider": "claude"},
        {"role": "opus1", "provider": "claude"},
        {"role": "planner", "provider": "codex"},
    ]
    p = prompts.lead_prompt("C:/x/mk.exe", team)
    assert "opus1 (claude" in p          # teammate + its CLI
    assert "planner (codex" in p         # teammate + its CLI
    assert "main (" not in p             # the lead does not list itself as a teammate
    assert "opus2" not in p              # never invents agents that aren't on this team
    assert "\n" not in p                 # single line for send_line()
    assert "C:/x/mk.exe ask" in p        # delegation command with the full mk path


def test_lead_prompt_tells_lead_the_cockpit_is_live():
    """The lead prompt asserts the cockpit is already running, so the lead delegates
    immediately instead of burning turns verifying processes/ports/panes or hunting commands."""
    from mkcrew import prompts
    p = prompts.lead_prompt("C:/x/mk.exe").lower()
    assert "already" in p                                   # cockpit is ALREADY running
    assert "verify" in p                                    # ...do NOT verify the infra
    assert "\n" not in prompts.lead_prompt("C:/x/mk.exe")   # still single line


def test_lead_prompt_fast_mode_drops_gates():
    """mode='fast' appends the gate-skipping clause; 'standard' (default) adds nothing. Both
    stay single-line for send_line()."""
    from mkcrew import prompts
    fast = prompts.lead_prompt("C:/x/mk.exe", mode="fast")
    std = prompts.lead_prompt("C:/x/mk.exe", mode="standard")
    assert "FAST MODE" in fast and "skip" in fast and "\n" not in fast
    assert "FAST MODE" not in std                           # standard is the bare default posture
    assert prompts.lead_prompt("C:/x/mk.exe") == std        # default mode is standard


def test_lead_prompt_claude_keeps_skill_sentence():
    """A claude lead (default) is told to use the claude-only task-router / senior-developer-loop skills."""
    from mkcrew import prompts
    p = prompts.lead_prompt("C:/x/mk.exe", provider="claude")
    assert "task-router" in p and "senior-developer-loop" in p
    assert prompts.lead_prompt("C:/x/mk.exe") == p          # provider defaults to 'claude'


def test_lead_prompt_non_claude_omits_skill_sentence_but_keeps_delegate_core():
    """A codex/agy/opencode lead drops the claude-only skills line but keeps the `mk ask` delegate
    core (full mk path + ask <role>), still single-line for send_line()."""
    from mkcrew import prompts
    p = prompts.lead_prompt("C:/x/mk.exe", provider="codex")
    assert "task-router" not in p and "senior-developer-loop" not in p
    assert "C:/x/mk.exe ask <role>" in p                    # delegation core preserved
    assert "delegate" in p.lower()
    assert "\n" not in p


def test_lead_prompt_includes_workspace_name_when_set():
    """FIX #4: a named workspace gives the lead a stable identity — the prompt reads 'the "<name>"
    workspace'; an unset name falls back cleanly to the generic 'a MKCREW team' wording. Both stay
    single-line for send_line() and never list the lead as its own teammate."""
    from mkcrew import prompts
    named = prompts.lead_prompt("C:/x/mk.exe", name="Testing")
    assert 'the "Testing" workspace' in named                # the workspace identity
    assert "a MKCREW team" not in named                      # replaced the generic wording
    assert "\n" not in named and "main (" not in named

    plain = prompts.lead_prompt("C:/x/mk.exe")               # name unset -> today's wording
    assert "a MKCREW team" in plain
    assert "workspace" not in plain.lower()
    assert prompts.lead_prompt("C:/x/mk.exe", name=None) == plain   # None == unset
    assert prompts.lead_prompt("C:/x/mk.exe", name="") == plain     # blank == unset


def test_team_update_prompt_names_the_changes_and_current_roster():
    """A resumed lead's update prompt states the changes + the current teammates (with CLIs),
    single-line, and never lists the lead itself."""
    from mkcrew import prompts
    team = [{"role": "main", "provider": "claude"},
            {"role": "worker1", "provider": "codex"},
            {"role": "worker2", "provider": "opencode"}]
    p = prompts.team_update_prompt("C:/x/mk.exe", team, ["worker1 is now codex (was claude)"])
    assert "worker1 is now codex" in p              # the change
    assert "worker1 (codex)" in p and "worker2 (opencode)" in p   # current roster + CLIs
    assert "main (" not in p                        # lead is not a teammate
    assert "\n" not in p                            # single line for send_line()
    assert "C:/x/mk.exe ask" in p


def test_thorough_and_plan_first_mode_clauses():
    """The two new postures inject their clauses; 'standard' output is BYTE-IDENTICAL to a prompt
    with no mode at all (regression guard: adding modes must not change existing cockpits)."""
    from mkcrew import prompts
    base = prompts.lead_prompt("C:/x/mk.exe")
    assert prompts.lead_prompt("C:/x/mk.exe", mode="standard") == base    # untouched default
    thorough = prompts.lead_prompt("C:/x/mk.exe", mode="thorough")
    assert "THOROUGH MODE" in thorough and "review gate" in thorough
    assert "loosened assertion" in thorough        # anti-verification-gaming (Devin + SWE-agent)
    plan = prompts.lead_prompt("C:/x/mk.exe", mode="plan-first")
    assert "PLAN-FIRST MODE" in plan and "WAIT" in plan
    assert "THOROUGH MODE" not in base and "PLAN-FIRST MODE" not in base


def test_architect_mode_clause():
    """architect v2 = flagship-as-architect: knowledge TRANSFER through blueprints, not just
    judging. The clause must carry every research-backed discipline: hands-off, one-sentence
    slices, definition of done, blueprints with decisions made + interfaces stated identically,
    tier calibration, positive constraint economy, independent re-run verification, re-decompose
    on failure, spot audits, final assembly check, batching, no mid-task counter-asks.
    'standard' stays byte-identical (regression)."""
    from mkcrew import prompts
    base = prompts.lead_prompt("C:/x/mk.exe")
    arch = prompts.lead_prompt("C:/x/mk.exe", mode="architect")
    assert "ARCHITECT MODE" in arch and "\n" not in arch
    for marker in ("Never read source files", "one sentence", "DEFINITION OF DONE",
                   "BLUEPRINT", "architectural choice", "IDENTICALLY", "acceptance criteria",
                   "phrased positively", "CALIBRATE", "DIFFERENT worker", "re-RUNS",
                   "RE-DECOMPOSED", "Spot-audit", "assembly check",
                   "never by asking you mid-task",
                   "knows NOTHING",                          # zero-shared-context maxim (crewAI)
                   "RULED OUT"):                             # dead-end field (context-handoff)
        assert marker in arch, f"clause lost its '{marker}' discipline"
    # Recency pinning: the verification mandate is the CLOSING beat of the clause (mid-paragraph
    # is the attention slot models skip under pressure), after the finish/economy sections.
    assert arch.index("assembly check") < arch.index("re-RUNS")
    assert arch.index("mk pend") < arch.index("re-RUNS")
    assert "ARCHITECT MODE" not in base
    live = prompts.mode_update_prompt("architect")          # `mk mode architect` live switch
    assert "ARCHITECT MODE" in live and "\n" not in live


def test_lead_crew_handling_notes_per_provider_present():
    """Provider handling notes appear ONLY for the CLI families actually on the crew (deduped),
    and an all-claude crew's bootstrap is byte-identical to before (no-damage guarantee)."""
    from mkcrew import prompts
    mixed = [{"role": "main", "provider": "claude"},
             {"role": "worker1", "provider": "codex"},
             {"role": "worker2", "provider": "codex"},          # duplicate provider -> ONE note
             {"role": "worker3", "provider": "opencode"}]
    p = prompts.lead_prompt("C:/x/mk.exe", team=mixed)
    assert "CREW HANDLING:" in p and "\n" not in p
    assert p.count("STRAIGHT to work") == 1                     # codex note, deduped
    assert "mk-done requirement inside the ask" in p            # opencode note
    assert "gemini" not in p                                    # antigravity NOT on this crew
    claude_only = [{"role": "main", "provider": "claude"},
                   {"role": "worker1", "provider": "claude"}]
    q = prompts.lead_prompt("C:/x/mk.exe", team=claude_only)
    assert "CREW HANDLING" not in q                             # all-claude crew: nothing injected


def test_lead_roster_names_each_workers_model():
    """Tier calibration needs tiers: the roster shows each worker's MODEL next to its CLI when
    configured (small models get step-by-step blueprints, strong ones get goals -- the lead can
    only route/calibrate if it knows who is which). Model-less agents render exactly as before."""
    from mkcrew import prompts
    team = [{"role": "main", "provider": "claude", "model": "claude-fable-5"},
            {"role": "worker1", "provider": "claude", "model": "claude-haiku-4-5"},
            {"role": "worker2", "provider": "codex", "model": "gpt-5.5"},
            {"role": "worker3", "provider": "opencode"}]                    # no model set
    p = prompts.lead_prompt("C:/x/mk.exe", team=team)
    assert "worker1 (claude claude-haiku-4-5:" in p
    assert "worker2 (codex gpt-5.5:" in p
    assert "worker3 (opencode:" in p                       # blank model -> provider only (as before)
    assert "\n" not in p


def test_mode_update_prompt_live_switch_line():
    """`mk mode <m>` sends a single-line posture update: names the mode, carries its clause; an
    unknown/standard mode falls back to the balanced-default wording. Single-line (send_line)."""
    from mkcrew import prompts
    up = prompts.mode_update_prompt("thorough")
    assert "\n" not in up and "'thorough'" in up and "THOROUGH MODE" in up
    back = prompts.mode_update_prompt("standard")
    assert "balanced default" in back


def test_warroom_mode_clause():
    """warroom = multi-CLI plan panel: the planner DRAFTS, every other teammate ATTACKS (adversarial,
    capped, one round -- additive review bloats plans), the lead SYNTHESIZES one final plan holding
    the pen, and the user GATES before any implementation. Single-line (send_line delivery)."""
    from mkcrew import prompts
    war = prompts.lead_prompt("/x/mk", mode="warroom")
    assert "\n" not in war                                        # single-line invariant
    assert "WARROOM MODE" in war
    for step in ("DRAFT", "ATTACK", "SYNTHESIZE", "GATE"):        # the 4-step relay, in order
        assert step in war
    assert war.index("DRAFT") < war.index("ATTACK") < war.index("SYNTHESIZE") < war.index("GATE")
    assert "attack the plan, do not extend it" in war             # adversarial, not additive
    assert "AT MOST 5 objections" in war                          # capped critique
    assert "you hold the pen" in war                              # one synthesizer
    assert "WAIT for explicit OK" in war                          # user gate before implementation
    assert "draft it yourself" in war                             # no-planner fallback
    assert "one round each" in war                                # no critique loops
    live = prompts.mode_update_prompt("warroom")                  # `mk mode warroom` live switch
    assert "\n" not in live and "WARROOM MODE" in live


def test_chief_mode_clause():
    """chief = architect with a drafting office: the lead DECIDES (decision-complete directives --
    the planner decides NOTHING), the planner ELABORATES the blueprint, the lead CHECKS the draft
    mechanically against its own decisions (anchoring guard: 'length is not quality'), and EXECUTES
    with architect discipline. Single-line (send_line delivery)."""
    from mkcrew import prompts
    chief = prompts.lead_prompt("/x/mk", mode="chief")
    assert "\n" not in chief                                       # single-line invariant
    assert "CHIEF-ARCHITECT MODE" in chief
    # Step markers in relay order (the intro sentence "you DECIDE, the planner ELABORATES, workers
    # EXECUTE" front-runs the bare words, so anchor on the step-opening phrases).
    steps = ("DECIDE: plan the deliverable", "ELABORATE: send each directive",
             "CHECK each draft", "EXECUTE with architect")
    for s in steps:
        assert s in chief, s
    assert chief.index(steps[0]) < chief.index(steps[1]) < chief.index(steps[2]) < chief.index(steps[3])
    assert "the planner decides NOTHING" in chief                  # decision-drift guard
    assert "length is not quality" in chief                        # detail-masquerade guard
    assert "never forward a draft unreviewed" in chief             # lead owns the blueprint
    assert "write the blueprints yourself" in chief                # no-planner fallback (= plain architect)
    assert "DIFFERENT worker re-RUNS" in chief                     # architect execution rigor retained
    assert "RE-DECOMPOSED" in chief
    live = prompts.mode_update_prompt("chief")                     # `mk mode chief` live switch
    assert "\n" not in live and "CHIEF-ARCHITECT MODE" in live


def test_venture_mode_clause():
    """venture = business inception (BMAD-inspired, elicitation INVERTED): intake ingests provided
    material, the planner drafts a claim-labeled brief (no invented numbers), at most 5 derived
    questions, research routed to the cheapest research-capable seat (never the lead), one red-team
    pass, one decision gate with a falsification plan, evidence-preserving handoff -- and it never
    implements. Single-line (send_line delivery)."""
    from mkcrew import prompts
    v = prompts.lead_prompt("/x/mk", mode="venture")
    assert "\n" not in v                                          # single-line invariant
    assert "VENTURE MODE" in v
    steps = ("INTAKE: read everything", "DRAFT: send the planner", "ASK ONCE: derive",
             "VERIFY: route fact-checking", "RED-TEAM: a DIFFERENT worker",
             "GATE: present ONE decision review", "HANDOFF: write the approved brief")
    for s in steps:
        assert s in v, s
    idx = [v.index(s) for s in steps]
    assert idx == sorted(idx)                                     # pipeline order preserved
    assert "FACT (with source), HYPOTHESIS (with confidence), or UNKNOWN" in v   # claim labeling
    assert "never invent statistics" in v                         # no-invented-numbers rule
    assert "at most 5 questions" in v and "at most 3 in total" in v   # capped ask + follow-ups
    assert "never ask what the provided material already answers" in v  # input reduces questions
    assert "LOWEST-COST worker with live web search" in v         # token-conservation routing
    assert "claims stay UNVERIFIED" in v                          # no-research-capable-crew fallback
    assert "resolve a material source conflict by inference" in v # inference ban on conflicts
    assert "KILL the hypothesis" in v                             # falsification tests carry kill conditions
    assert "CRITICAL unknowns named as such" in v                 # informed consent, not a hard blocker
    assert "NEVER hand off unapproved" in v                       # approval-withheld behavior
    assert "never research yourself" in v                         # the lead never researches
    assert "never desirability" in v                              # desk-research boundary
    assert "FALSIFICATION PLAN" in v and "WAIT for explicit approval" in v
    assert "ONE re-draft" in v                                    # fork-correction loop, bounded
    assert "draft it yourself" in v                               # no-planner fallback
    assert "venture never implements" in v                        # hard mode boundary
    live = prompts.mode_update_prompt("venture")                  # `mk mode venture` live switch
    assert "\n" not in live and "VENTURE MODE" in live


def test_thorough_mode_adds_independent_rerun_and_escalation():
    """thorough borrows architect's two strongest verification levers: the verifying re-run goes
    to a DIFFERENT agent (kills self-verification bias -- the implementer's own pasted output is
    never the only proof), and a twice-failed result is RE-DECOMPOSED for a different worker
    instead of re-asked verbatim (the known retry anti-pattern)."""
    from mkcrew import prompts
    thorough = prompts.lead_prompt("/x/mk", mode="thorough")
    assert "DIFFERENT agent" in thorough
    assert "implementer's own pasted output" in thorough
    assert "RE-DECOMPOSED" in thorough and "never re-asked verbatim" in thorough


def test_enforcement_series_clause_upgrades():
    """The enforcement series, clause side: VERIFY: marker + per-slice ledger + two-strike tier
    escalation + worked-example rule + qualitative-review routing + first-class BLOCKED in
    architect/chief; the CHECKED stamp in chief; the VERIFY: marker in thorough; fast sanity-checks
    instead of blind-accepting. All still single-line."""
    from mkcrew import prompts
    arch  = prompts.lead_prompt("/x/mk", mode="architect")
    chief = prompts.lead_prompt("/x/mk", mode="chief")
    thor  = prompts.lead_prompt("/x/mk", mode="thorough")
    fast  = prompts.lead_prompt("/x/mk", mode="fast")
    for lead in (arch, chief):
        assert "STARTS with the word VERIFY:" in lead                 # audited marker
        assert "BUILT by X, VERIFIED by Y" in lead                    # per-slice ledger
        assert "TWICE moves UP to the strongest worker" in lead       # two-strike escalation
        assert "WORKED EXAMPLE" in lead                               # mimicry rule for small models
        assert "STRONGEST non-lead seat" in lead                      # qualitative-review routing
        assert "BLOCKED reply is a first-class move" in lead
        assert "stands alone even if its session reset" in lead      # codex fold: self-contained re-ask
    assert "CHECKED: <your ruling>" in chief and "daemon audits the stamp" in chief
    assert "starting the ask with the word VERIFY:" in thor
    assert "BLOCKED reply is a first-class move" in thor
    assert "sanity-check the reply" in fast
    for lead in (arch, chief, thor, fast):
        assert "\n" not in lead



def test_planner_protocol_injected_when_planner_seated():
    """The plan-review loop (live report: an agy/Opus-thinking planner has a SMALL budget): ONE
    planning ask -> lead reviews -> deficiencies-only re-ask to the SAME planner -> approve -> only
    then delegate to workers. Injected only when a planner is actually seated, and only for modes
    that don't already carry their own planner contract (chief/warroom/venture do; fast skips the
    planner entirely)."""
    from mkcrew import prompts
    team = [{"role": "main", "provider": "claude"},
            {"role": "worker1", "provider": "claude"},
            {"role": "planner", "provider": "antigravity"}]
    for mode in ("standard", "thorough", "plan-first", "architect"):
        p = prompts.lead_prompt("C:/x/mk", team=team, mode=mode)
        assert "PLANNER PROTOCOL" in p, mode
        assert "ONLY the deficiencies" in p and "SAME planner" in p
        assert chr(10) not in p
    for mode in ("fast", "chief", "warroom", "venture"):
        assert "PLANNER PROTOCOL" not in prompts.lead_prompt("C:/x/mk", team=team, mode=mode), mode
    no_planner = [{"role": "main", "provider": "claude"}, {"role": "worker1", "provider": "claude"}]
    assert "PLANNER PROTOCOL" not in prompts.lead_prompt("C:/x/mk", team=no_planner)


def test_non_claude_lead_gets_distilled_lead_loop():
    """A codex/agy/opencode main cannot load the claude-only task-router/senior-developer-loop
    skills -- the two files that carry the whole lead doctrine -- so it gets the loop DISTILLED
    inline (live report: a non-claude main 'did not act like claude': one delegation, no review).
    A claude main keeps the skills reference and does NOT get the inline copy."""
    from mkcrew import prompts
    for prov in ("codex", "antigravity", "opencode"):
        p = prompts.lead_prompt("C:/x/mk", provider=prov)
        assert "run the lead loop" in p, prov
        assert "review every returned result" in p and "task-router" not in p
        assert chr(10) not in p
    c = prompts.lead_prompt("C:/x/mk", provider="claude")
    assert "task-router" in c and "run the lead loop" not in c


def test_planner_prompt_token_economy_and_revision():
    """PLANNER_PROMPT v2: plans only, budget-frugal reading, tight numbered output, and the
    incremental-revision contract (fix ONLY the flagged points; never rebuild from scratch)."""
    from mkcrew import prompts
    pp = prompts.PLANNER_PROMPT
    assert "READ-ONLY" in pp and "NEVER edit/write" in pp
    assert "token budget" in pp and "read just what the plan requires" in pp
    assert "per-step acceptance command" in pp
    assert "ONLY the flagged points" in pp and "never rebuild the plan from scratch" in pp
    assert chr(10) not in pp
