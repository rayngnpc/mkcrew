import json, sys
from pathlib import Path
from mkcrew import agent, config


def test_ensure_project_hook_writes_stop_hook(tmp_path, monkeypatch):
    """ensure_project_hook writes a Stop hook whose command == sys.executable and args == ['-m', 'mkcrew.finish_hook']."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    sp = agent.ensure_project_hook(project_dir)
    data = json.loads(sp.read_text(encoding="utf-8"))
    stop_list = data["hooks"]["Stop"]
    assert len(stop_list) == 1
    hook_cmd = stop_list[0]["hooks"][0]
    assert hook_cmd["command"] == sys.executable
    assert hook_cmd["args"] == ["-m", "mkcrew.finish_hook"]


def test_ensure_project_hook_is_idempotent(tmp_path, monkeypatch):
    """Calling ensure_project_hook twice keeps Stop list length at 1."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    agent.ensure_project_hook(project_dir)
    agent.ensure_project_hook(project_dir)
    sp = project_dir / ".claude" / "settings.json"
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert len(data["hooks"]["Stop"]) == 1


def test_ensure_project_hook_replaces_stale_renamed_hook(tmp_path, monkeypatch):
    """A stale finish-hook from an old package name (oldpkg.finish_hook) is REPLACED, not
    stacked, so a target project self-heals to mkcrew.finish_hook on the next start (one entry)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    dot = project_dir / ".claude"
    dot.mkdir(parents=True)
    stale = {"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "py", "args": ["-m", "oldpkg.finish_hook"], "timeout": 30}]}]}}
    (dot / "settings.json").write_text(json.dumps(stale), encoding="utf-8")
    agent.ensure_project_hook(project_dir)
    stop = json.loads((dot / "settings.json").read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert len(stop) == 1                                            # replaced, not stacked
    assert stop[0]["hooks"][0]["args"] == ["-m", "mkcrew.finish_hook"]


def test_ensure_project_hook_preserves_existing_keys(tmp_path, monkeypatch):
    """ensure_project_hook preserves pre-existing keys (e.g. permissions block)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    dot = project_dir / ".claude"
    dot.mkdir(parents=True)
    existing = {"permissions": {"allow": ["Bash"]}, "someOtherKey": "value"}
    (dot / "settings.json").write_text(json.dumps(existing), encoding="utf-8")
    agent.ensure_project_hook(project_dir)
    data = json.loads((dot / "settings.json").read_text(encoding="utf-8"))
    assert data["permissions"] == {"allow": ["Bash"]}
    assert data["someOtherKey"] == "value"
    assert "hooks" in data


def test_launch_command_returns_cmd_k_file(tmp_path, monkeypatch):
    """launch_command returns ['cmd', '/k', <file>] (/k keeps the pane open if the CLI exits)
    and the .cmd file contains expected content."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("worker", "claude-sonnet-5", project_dir)
    assert cmd[0] == "cmd"
    assert cmd[1] == "/k"
    cmd_file = Path(cmd[2])
    assert cmd_file.exists()
    content = cmd_file.read_text(encoding="utf-8")
    assert "cd /d" in content
    assert 'set "MK_ACTOR=worker"' in content
    assert "claude --permission-mode bypassPermissions --model claude-sonnet-5" in content


def test_codex_hook_resolves_role_from_per_pane_env_not_baked_overwrite():
    """Two codex agents in ONE project share the single .codex/hooks.json, so the Stop hook must
    resolve MK_ACTOR from the PER-PANE launch env (launch.cmd `set MK_ACTOR=<role>`, which codex's
    hook inherits) and fall back to the baked role only when absent — otherwise both codex panes use
    the last-baked role and the daemon can't route tasks per role."""
    import base64
    inner = base64.b64decode(
        agent._codex_hook_command("worker1").split("-EncodedCommand", 1)[1].strip()
    ).decode("utf-16le")
    assert "if (-not $env:MK_ACTOR)" in inner                 # env-first: keep the inherited per-pane value
    assert "$env:MK_ACTOR='worker1'" in inner                 # baked role stays as the single-codex fallback
    assert "SilentlyContinue'; $env:MK_ACTOR=" not in inner   # NOT an unconditional overwrite of the env


def test_launch_command_mode_plan(tmp_path, monkeypatch):
    """launch_command with mode='plan' writes --permission-mode plan in the .cmd."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("planner", "claude-opus-4-8", project_dir, mode="plan")
    cmd_file = Path(cmd[2])
    content = cmd_file.read_text(encoding="utf-8")
    assert "--permission-mode plan" in content
    assert "--permission-mode bypassPermissions" not in content


def test_launch_command_default_mode_is_bypassPermissions(tmp_path, monkeypatch):
    """launch_command default mode is bypassPermissions."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("main", "claude-opus-4-8", project_dir)
    cmd_file = Path(cmd[2])
    content = cmd_file.read_text(encoding="utf-8")
    assert "--permission-mode bypassPermissions" in content


def test_launch_command_with_effort(tmp_path, monkeypatch):
    """launch_command with effort='max' includes --effort max in the .cmd."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("main", "claude-opus-4-8", project_dir, effort="max")
    cmd_file = Path(cmd[2])
    content = cmd_file.read_text(encoding="utf-8")
    assert "--effort max" in content


def test_launch_command_without_effort_omits_flag(tmp_path, monkeypatch):
    """launch_command with effort=None omits --effort from the .cmd."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("worker", "claude-sonnet-5", project_dir, effort=None)
    cmd_file = Path(cmd[2])
    content = cmd_file.read_text(encoding="utf-8")
    assert "--effort" not in content


def test_claude_command_uses_session_id_when_fresh():
    cmd = agent._agent_command_line("claude", "claude-opus-4-8", "bypassPermissions",
                                    None, "opus1", "P", session_id="abc", resume=False)
    assert "--session-id abc" in cmd
    assert "--resume" not in cmd


def test_claude_command_uses_resume_when_resuming():
    cmd = agent._agent_command_line("claude", "claude-opus-4-8", "bypassPermissions",
                                    None, "opus1", "P", session_id="abc", resume=True)
    assert "--resume abc" in cmd
    assert "--session-id" not in cmd


def test_claude_command_no_session_flag_when_none():
    cmd = agent._agent_command_line("claude", "m", "bypassPermissions", None, "r", "P")
    assert "--session-id" not in cmd and "--resume" not in cmd


def test_gemini_command_presets_session_id_when_fresh():
    """gemini PRE-SETS the MKCREW per-role uuid on a fresh launch (`--session-id <id>`, like claude's
    settable id) and stays interactive + auto-approve."""
    cmd = agent._agent_command_line("gemini", "gemini-2.5-pro", "bypassPermissions",
                                    None, "g", "P", session_id="abc", resume=False)
    assert "--session-id abc" in cmd
    assert "--resume" not in cmd
    assert "-y" in cmd.split()                 # yolo preserved
    assert "-p" not in cmd.split()             # interactive, never headless


def test_gemini_command_resumes_session_id_when_resuming():
    """On restart gemini resumes THAT id (`--resume <id>`), not --session-id, keeping its flags."""
    cmd = agent._agent_command_line("gemini", "gemini-2.5-pro", "bypassPermissions",
                                    None, "g", "P", session_id="abc", resume=True)
    assert "--resume abc" in cmd
    assert "--session-id" not in cmd
    assert "-y" in cmd.split() and "--skip-trust" in cmd   # auto-approve + trust preserved on resume
    assert "-p" not in cmd.split()


def test_gemini_command_no_session_flag_when_none():
    """No MKCREW id -> no --session-id/--resume (e.g. a direct call without the sessions store)."""
    cmd = agent._agent_command_line("gemini", "gemini-2.5-pro", "bypassPermissions", None, "g", "P")
    assert "--session-id" not in cmd and "--resume" not in cmd


def test_codex_command_appends_resume_subcommand_when_resuming():
    """codex resume is the `resume --last` SUBCOMMAND (interactive), appended AFTER the global flags;
    fresh launches omit it. The bypass + model/effort flags are preserved on resume; never `exec`."""
    fresh = agent._agent_command_line("codex", "gpt-5-codex", "bypassPermissions",
                                      None, "main", "P", resume=False)
    res = agent._agent_command_line("codex", "gpt-5-codex", "bypassPermissions",
                                    None, "main", "P", resume=True)
    assert "resume --last" not in fresh
    assert res.rstrip().endswith("resume --last")          # subcommand last, after the global flags
    assert "exec" not in res.split()                       # interactive TUI, not headless exec
    assert "--dangerously-bypass-approvals-and-sandbox" in res and "-m gpt-5-codex" in res


def test_opencode_command_uses_continue_when_resuming():
    """opencode resumes the last session with `--continue` (interactive TUI); fresh omits it."""
    fresh = agent._agent_command_line("opencode", "anthropic/x", "bypassPermissions",
                                      None, "oc", "P", resume=False)
    res = agent._agent_command_line("opencode", "anthropic/x", "bypassPermissions",
                                    None, "oc", "P", resume=True)
    assert "--continue" not in fresh
    assert "--continue" in res
    assert "run" not in res.split()                        # still the interactive TUI, not `opencode run`
    assert "-m anthropic/x" in res


def test_antigravity_command_uses_continue_when_resuming():
    """agy continues the most recent conversation with `--continue`; fresh omits it, stays interactive."""
    fresh = agent._agent_command_line("antigravity", "gemini-3-pro", "bypassPermissions",
                                      None, "ag", "P", resume=False)
    res = agent._agent_command_line("antigravity", "gemini-3-pro", "bypassPermissions",
                                    None, "ag", "P", resume=True)
    assert "--continue" not in fresh
    assert "--continue" in res
    assert "--dangerously-skip-permissions" in res
    assert "-p" not in res.split() and "--print" not in res
    assert "--model gemini-3-pro" in res


def test_custom_provider_returns_verbatim_command():
    cmd = agent._agent_command_line("custom", "ignored", "bypassPermissions", None, "r", "P",
                                    command="codex --full-auto")
    assert cmd == "codex --full-auto"


def test_launch_cmd_file_contains_custom_command(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = agent.write_launch_cmd("r", "m", tmp_path, provider="custom", command="codex --yolo")
    assert "codex --yolo" in p.read_text(encoding="utf-8")


def test_codex_provider_builds_interactive_command():
    """codex launches its DEFAULT interactive TUI (never the headless 'exec' subcommand),
    with the model and the auto-approve/no-sandbox flag (codex's analog of bypassPermissions)."""
    cmd = agent._agent_command_line("codex", "gpt-5-codex", "bypassPermissions",
                                    None, "main", "P")
    assert cmd.startswith("codex ")
    assert "-m gpt-5-codex" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--dangerously-bypass-hook-trust" in cmd
    assert "exec" not in cmd.split()          # MUST stay interactive — no headless mode


def test_codex_launch_disables_lazycodex_auto_update(tmp_path, monkeypatch):
    """codex's launch.cmd disables the LazyCodex/omo session-start auto-update (env-scoped) so the
    agent doesn't self-update + exit mid-cockpit, dropping the pane to a bare shell. Codex-only —
    other providers don't get the flag, and the user's global codex keeps auto-updating."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cx = agent.write_launch_cmd("r", "gpt-5.5", tmp_path, provider="codex").read_text(encoding="utf-8")
    assert 'set "LAZYCODEX_AUTO_UPDATE_DISABLED=1"' in cx
    assert 'set "OMO_CODEX_AUTO_UPDATE_DISABLED=1"' in cx
    cl = agent.write_launch_cmd("r", "m", tmp_path, provider="claude").read_text(encoding="utf-8")
    assert "AUTO_UPDATE_DISABLED" not in cl          # codex-only; doesn't leak to other agents


def test_ensure_project_claude_md_writes_operating_section(tmp_path):
    """ensure_project_claude_md writes a CLAUDE.md documenting the MKCREW operating model,
    so every agent wakes up knowing the crew + commands without discovery."""
    p = agent.ensure_project_claude_md(tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "MKCREW" in text
    assert "mk ask <role>" in text
    assert "mk-done" in text


def test_ensure_project_claude_md_preserves_existing_and_is_idempotent(tmp_path):
    """It preserves the user's existing CLAUDE.md content and adds exactly ONE MKCREW block."""
    cm = tmp_path / "CLAUDE.md"
    cm.write_text("# My project\nKeep this line.\n", encoding="utf-8")
    agent.ensure_project_claude_md(tmp_path)
    agent.ensure_project_claude_md(tmp_path)            # run twice
    text = cm.read_text(encoding="utf-8")
    assert "Keep this line." in text                   # user content preserved
    assert text.count("<!-- MKCREW:start -->") == 1    # idempotent — one block only


def test_ensure_project_agents_md_writes_operating_section(tmp_path):
    """ensure_project_agents_md writes an AGENTS.md (codex/agy/opencode auto-read it) with the same
    MKCREW operating section claude gets from CLAUDE.md, so a non-claude main wakes up briefed."""
    p = agent.ensure_project_agents_md(tmp_path)
    assert p.name == "AGENTS.md"
    text = p.read_text(encoding="utf-8")
    assert "MKCREW" in text
    assert "mk ask <role>" in text
    assert "mk-done" in text


def test_ensure_project_agents_md_preserves_existing_and_is_idempotent(tmp_path):
    """It preserves the user's existing AGENTS.md content and adds exactly ONE MKCREW block."""
    am = tmp_path / "AGENTS.md"
    am.write_text("# My agents\nKeep this line.\n", encoding="utf-8")
    agent.ensure_project_agents_md(tmp_path)
    agent.ensure_project_agents_md(tmp_path)            # run twice
    text = am.read_text(encoding="utf-8")
    assert "Keep this line." in text                   # user content preserved
    assert text.count("<!-- MKCREW:start -->") == 1    # idempotent — one block only


def test_antigravity_provider_builds_interactive_agy_command():
    """provider='antigravity' launches the `agy` CLI interactively (never --print/-p), auto-approve on."""
    cmd = agent._agent_command_line("antigravity", "gemini-3-pro", "bypassPermissions", None, "w", "P")
    assert cmd.startswith("agy ")
    assert "--dangerously-skip-permissions" in cmd
    assert "--model gemini-3-pro" in cmd
    assert "-p" not in cmd.split() and "--print" not in cmd      # never the headless mode


def test_non_claude_provider_drops_a_claude_model():
    """A claude-* model is NOT passed to a non-claude CLI (it would reject it) -- the CLI uses its
    own default instead; a provider-appropriate model IS passed."""
    cmd = agent._agent_command_line("codex", "claude-opus-4-8", "bypassPermissions", None, "w", "P")
    assert "claude-opus-4-8" not in cmd and "-m" not in cmd       # claude model dropped, no flag
    cmd2 = agent._agent_command_line("codex", "gpt-5-codex", "bypassPermissions", None, "w", "P")
    assert "-m gpt-5-codex" in cmd2


def test_codex_command_includes_xhigh_effort():
    """BUG-5a: codex accepts 'xhigh' — it must map to -c model_reasoning_effort="xhigh" (not dropped)."""
    cmd = agent._agent_command_line("codex", "gpt-5.5", "bypassPermissions", "xhigh", "w", "P")
    assert 'model_reasoning_effort="xhigh"' in cmd


def test_antigravity_command_omits_effort_flag():
    """BUG-5b: agy exposes no launch-time effort/thinking flag, so none is emitted (the thinking level
    rides IN the --model variant name); a model that already carries the "(High)" variant passes
    through verbatim (no double suffix), and the command stays interactive."""
    cmd = agent._agent_command_line("antigravity", "Gemini 3.5 Flash (High)", "bypassPermissions",
                                    "high", "w", "P")
    assert "model_reasoning_effort" not in cmd and "--effort" not in cmd
    assert '--model "Gemini 3.5 Flash (High)"' in cmd


def test_antigravity_command_folds_effort_into_model():
    """agy effort gap resolved (wired): agy has no effort flag, so the picked thinking level is FOLDED
    into the --model variant name (base model + effort -> one "(Level)" value) — the chosen effort
    actually reaches the launch instead of being silently dropped; no separate effort/-c flag."""
    cmd = agent._agent_command_line("antigravity", "Gemini 3.5 Flash", "bypassPermissions",
                                    "high", "w", "P")
    assert '--model "Gemini 3.5 Flash (High)"' in cmd          # effort folded into the model name
    assert "model_reasoning_effort" not in cmd and "--effort" not in cmd
    # medium/low map to their capitalized variant suffixes too
    assert '--model "Gemini 3.1 Pro (Low)"' in agent._agent_command_line(
        "antigravity", "Gemini 3.1 Pro", "bypassPermissions", "low", "w", "P")
    # a model that ALREADY carries a "(...)" variant (the fixed Thinking one) is not doubled
    assert '--model "Claude Opus 4.6 (Thinking)"' in agent._agent_command_line(
        "antigravity", "Claude Opus 4.6 (Thinking)", "bypassPermissions", "", "w", "P")
    # an effort agy has no variant for ('max') -> no suffix, model passes through (not a bogus "(Max)")
    nomax = agent._agent_command_line("antigravity", "Gemini 3.5 Flash", "bypassPermissions",
                                      "max", "w", "P")
    assert '--model "Gemini 3.5 Flash"' in nomax and "(Max)" not in nomax


def test_agy_model_with_thinking_helper():
    """Unit: the agy thinking-fold helper maps low/medium/high to the capitalized "(Level)" suffix and
    leaves blank/claude-*/already-suffixed/unknown-effort models untouched (no double suffix, no drop)."""
    assert agent._agy_model_with_thinking("Gemini 3.5 Flash", "high") == "Gemini 3.5 Flash (High)"
    assert agent._agy_model_with_thinking("Gemini 3.5 Flash", "medium") == "Gemini 3.5 Flash (Medium)"
    assert agent._agy_model_with_thinking("Gemini 3.1 Pro", "low") == "Gemini 3.1 Pro (Low)"
    assert agent._agy_model_with_thinking("Gemini 3.5 Flash (High)", "high") == "Gemini 3.5 Flash (High)"
    assert agent._agy_model_with_thinking("Claude Opus 4.6 (Thinking)", "") == "Claude Opus 4.6 (Thinking)"
    assert agent._agy_model_with_thinking("Gemini 3.5 Flash", "max") == "Gemini 3.5 Flash"   # no variant
    assert agent._agy_model_with_thinking("Gemini 3.5 Flash", None) == "Gemini 3.5 Flash"
    assert agent._agy_model_with_thinking("", "high") == ""


def test_write_launch_resolves_bare_provider_to_default_account(tmp_path, monkeypatch):
    """ROOT fix for account drift (Windows flavor): a BARE built-in provider resolves to the
    default account wrapper from accounts.json -- and a .cmd wrapper is invoked with `call` so
    the relaunch loop survives. No accounts.json -> stays bare (single-account users unchanged)."""
    import json as _json
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    wrapper = tmp_path / "claudew.cmd"
    wrapper.write_text("@echo off\r\nclaude %*\r\n", encoding="utf-8")
    (tmp_path / "mkcrew").mkdir(parents=True, exist_ok=True)
    (tmp_path / "mkcrew" / "accounts.json").write_text(_json.dumps(
        [{"label": "work", "provider": "claude", "bin": str(wrapper), "default": True}]),
        encoding="utf-8")
    p = agent.write_launch_cmd("acctest", "claude-opus-4-8", tmp_path, provider="claude")
    text = p.read_text(encoding="utf-8")
    assert f"call {wrapper}" in text                     # resolved AND call-prefixed (.cmd wrapper)
    # no accounts.json -> bare provider unchanged
    (tmp_path / "mkcrew" / "accounts.json").unlink()
    p2 = agent.write_launch_cmd("acctest2", "claude-opus-4-8", tmp_path, provider="claude")
    t2 = p2.read_text(encoding="utf-8")
    assert "claudew" not in t2 and "\nclaude --permission-mode" in t2.replace("\r\n", "\n")



def test_crew_briefing_covers_current_operations(tmp_path):
    """The shared briefing (CLAUDE.md + AGENTS.md merge) must know the CURRENT cockpit -- it was a
    15-line fossil predating the modes, mk stats/trace, the BLOCKED protocol and evidence packs.
    It stays background mechanics: the envelope-wins rule is stated explicitly so it can never
    collide with a mode clause or task contract."""
    p = agent.ensure_project_claude_md(tmp_path)
    text = p.read_text(encoding="utf-8")
    for marker in ("mk stats", "mk trace", "mk pend", "mk mode", "BLOCKED", "self-audit",
                   "envelope always wins", "core mode", "late-work", "only completion signal"):
        assert marker in text, f"briefing missing '{marker}'"
    a = agent.ensure_project_agents_md(tmp_path)
    assert a.read_text(encoding="utf-8") .count("mk stats") >= 1     # non-claude CLIs get the same


def test_worker_skill_installed_alongside_lead_skills(tmp_path, monkeypatch):
    """mkcrew-worker: the worker counterpart to the five lead-centric skills -- installed to
    .claude/skills like the rest, carrying the lifecycle + BLOCKED protocol + envelope-wins rule."""
    from mkcrew import cli
    paths = cli.install_skills(tmp_path)
    ws = [p for p in paths if "mkcrew-worker" in str(p)]
    assert len(ws) == 1 and ws[0].exists()
    text = ws[0].read_text(encoding="utf-8")
    assert "mk-done" in text and "BLOCKED" in text and "envelope wins" in text
    assert "never weaken a check" in text.lower() or "Never weaken a check" in text
