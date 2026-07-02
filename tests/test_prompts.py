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
    plan = prompts.lead_prompt("C:/x/mk.exe", mode="plan-first")
    assert "PLAN-FIRST MODE" in plan and "WAIT" in plan
    assert "THOROUGH MODE" not in base and "PLAN-FIRST MODE" not in base


def test_mode_update_prompt_live_switch_line():
    """`mk mode <m>` sends a single-line posture update: names the mode, carries its clause; an
    unknown/standard mode falls back to the balanced-default wording. Single-line (send_line)."""
    from mkcrew import prompts
    up = prompts.mode_update_prompt("thorough")
    assert "\n" not in up and "'thorough'" in up and "THOROUGH MODE" in up
    back = prompts.mode_update_prompt("standard")
    assert "balanced default" in back
