# tests/test_providers.py
"""Tests for multi-provider support (Phase 4)."""
import json
from pathlib import Path
from mkcrew import agent, teamconfig


# ---------------------------------------------------------------------------
# teamconfig: provider field defaults
# ---------------------------------------------------------------------------

def test_default_team_has_no_explicit_provider():
    """default_team() agents do NOT include a 'provider' key (it's opt-in via team.config)."""
    team = teamconfig.default_team()
    for a in team:
        # provider is absent OR defaults to "claude" — never another value
        assert a.get("provider", "claude") == "claude"


def test_load_team_tolerates_missing_provider(tmp_path, monkeypatch):
    """load_team works when config agents have no 'provider' key; get() defaults to 'claude'."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    config_without_provider = {
        "entry_window": "main",
        "agents": [
            {"role": "main", "model": "claude-opus-4-8", "effort": "max",
             "window": "main", "mode": "bypassPermissions"},
        ],
    }
    (mk_dir / "team.config").write_text(json.dumps(config_without_provider), encoding="utf-8")
    team = teamconfig.load_team(project_dir)
    assert team[0].get("provider", "claude") == "claude"


def test_load_team_round_trips_provider_field(tmp_path, monkeypatch):
    """load_team preserves 'provider' values written in team.config."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    config_with_providers = {
        "entry_window": "main",
        "agents": [
            {"role": "main",   "model": "claude-opus-4-8",      "effort": "max",  "window": "main",   "mode": "bypassPermissions", "provider": "claude"},
            {"role": "gemini1","model": "gemini-2.5-pro",       "effort": None,   "window": "gem1",   "mode": "bypassPermissions", "provider": "gemini"},
            {"role": "oc1",    "model": "anthropic/claude-opus-4-8", "effort": None, "window": "oc1", "mode": "bypassPermissions", "provider": "opencode"},
        ],
    }
    (mk_dir / "team.config").write_text(json.dumps(config_with_providers), encoding="utf-8")
    team = teamconfig.load_team(project_dir)
    assert team[0]["provider"] == "claude"
    assert team[1]["provider"] == "gemini"
    assert team[2]["provider"] == "opencode"


# ---------------------------------------------------------------------------
# agent.launch_command: provider="claude" unchanged
# ---------------------------------------------------------------------------

def test_launch_command_claude_provider_default(tmp_path, monkeypatch):
    """provider='claude' (default) still writes claude --permission-mode ... --model ..."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("worker", "claude-sonnet-4-6", project_dir,
                               mode="bypassPermissions", effort=None, provider="claude")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "claude --permission-mode bypassPermissions --model claude-sonnet-4-6" in content
    assert "gemini" not in content
    assert "opencode" not in content


def test_launch_command_claude_provider_with_effort(tmp_path, monkeypatch):
    """provider='claude' with effort still writes --effort flag."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("main", "claude-opus-4-8", project_dir,
                               mode="bypassPermissions", effort="max", provider="claude")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "--permission-mode bypassPermissions" in content
    assert "--effort max" in content


def test_launch_command_omits_provider_arg_defaults_to_claude(tmp_path, monkeypatch):
    """Calling launch_command without provider= still produces a claude command."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("worker", "claude-sonnet-4-6", project_dir)
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "claude --permission-mode bypassPermissions" in content


# ---------------------------------------------------------------------------
# agent.launch_command: provider="gemini"
# ---------------------------------------------------------------------------

def test_launch_command_gemini_uses_yolo_flag(tmp_path, monkeypatch):
    """provider='gemini' writes 'gemini -y' (--yolo / auto-approve all tools)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("gemini1", "gemini-2.5-pro", project_dir,
                               provider="gemini")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "gemini" in content
    assert "-y" in content or "--yolo" in content
    assert "--skip-trust" in content   # bypass the trusted-folder gate for unattended runs


def test_launch_command_gemini_includes_model_flag(tmp_path, monkeypatch):
    """provider='gemini' writes '-m <model>'."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("gemini1", "gemini-2.5-pro", project_dir,
                               provider="gemini")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "-m gemini-2.5-pro" in content or "--model gemini-2.5-pro" in content


def test_launch_command_gemini_is_interactive_no_baked_prompt(tmp_path, monkeypatch):
    """provider='gemini' launches an INTERACTIVE session (no -p baked task).
    Tasks arrive later via send-keys from the daemon."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("gemini1", "gemini-2.5-pro", project_dir,
                               provider="gemini")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    # Must NOT use -p (that's headless/non-interactive mode)
    assert "-p " not in content
    # Must NOT contain a baked inbox path or task instruction
    assert "inbox" not in content
    assert "mk-done" not in content


def test_launch_command_gemini_sets_mk_actor(tmp_path, monkeypatch):
    """provider='gemini' cmd still sets MK_ACTOR and cd to project."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("gemini1", "gemini-2.5-pro", project_dir,
                               provider="gemini")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert 'set "MK_ACTOR=gemini1"' in content
    assert "cd /d" in content


def test_launch_command_gemini_no_permission_mode_flag(tmp_path, monkeypatch):
    """provider='gemini' does NOT write --permission-mode (that's claude-specific)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("gemini1", "gemini-2.5-pro", project_dir,
                               provider="gemini")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "--permission-mode" not in content


# ---------------------------------------------------------------------------
# agent.launch_command: provider="opencode"
# ---------------------------------------------------------------------------

def test_launch_command_opencode_uses_interactive_tui(tmp_path, monkeypatch):
    """provider='opencode' launches the interactive TUI (no 'run' one-shot subcommand).
    Tasks arrive later via send-keys from the daemon.
    NOTE: opencode interactive TUI has no auto-approve flag; it will prompt on tool use."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("oc1", "anthropic/claude-opus-4-8", project_dir,
                               provider="opencode")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    # Must NOT use 'opencode run' (that's one-shot/headless)
    assert "opencode run" not in content
    # Must be the interactive TUI: just 'opencode' with optional flags
    assert "opencode" in content
    # Must NOT contain a baked inbox path or task instruction
    assert "inbox" not in content
    assert "mk-done" not in content


def test_launch_command_opencode_no_dangerously_skip_permissions(tmp_path, monkeypatch):
    """provider='opencode' interactive TUI has no --dangerously-skip-permissions flag
    (that flag only exists on 'opencode run'; interactive TUI has no auto-approve equivalent)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("oc1", "anthropic/claude-opus-4-8", project_dir,
                               provider="opencode")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "--dangerously-skip-permissions" not in content


def test_launch_command_opencode_includes_model_flag(tmp_path, monkeypatch):
    """provider='opencode' writes '-m <model>'."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("oc1", "anthropic/claude-opus-4-8", project_dir,
                               provider="opencode")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "-m anthropic/claude-opus-4-8" in content or "--model anthropic/claude-opus-4-8" in content


def test_launch_command_opencode_sets_mk_actor(tmp_path, monkeypatch):
    """provider='opencode' cmd still sets MK_ACTOR and cd to project."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("oc1", "anthropic/claude-opus-4-8", project_dir,
                               provider="opencode")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert 'set "MK_ACTOR=oc1"' in content
    assert "cd /d" in content


def test_launch_command_opencode_no_permission_mode_flag(tmp_path, monkeypatch):
    """provider='opencode' does NOT write --permission-mode (claude-specific)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cmd = agent.launch_command("oc1", "anthropic/claude-opus-4-8", project_dir,
                               provider="opencode")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "--permission-mode" not in content


def test_launch_command_codex_model_and_reasoning(tmp_path, monkeypatch):
    """provider='codex' writes -m <model> and a valid thinking level as -c model_reasoning_effort."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("cx1", "gpt-5.5", project_dir, provider="codex", effort="high")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "-m gpt-5.5" in content
    assert 'model_reasoning_effort="high"' in content


def test_launch_command_codex_skips_invalid_reasoning(tmp_path, monkeypatch):
    """A non-codex level like claude's 'max' is NOT passed to codex (it would reject it)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("cx1", "gpt-5", project_dir, provider="codex", effort="max")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "model_reasoning_effort" not in content      # 'max' invalid for codex -> skipped


def test_launch_command_codex_xhigh_reasoning(tmp_path, monkeypatch):
    """BUG-5a: 'xhigh' is a valid codex level the wizard offers + persists (GPT-5.5/5.4) — it must
    reach the launch as -c model_reasoning_effort="xhigh" instead of being silently dropped."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("cx1", "gpt-5.5", project_dir, provider="codex", effort="xhigh")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert 'model_reasoning_effort="xhigh"' in content


def test_launch_command_antigravity_omits_effort_flag(tmp_path, monkeypatch):
    """BUG-5b: agy has NO launch-time effort/thinking flag (verified via `agy --help`); effort rides
    in the model-variant name. So the agy launch emits no reasoning/effort flag but keeps --model.
    A model that already carries the "(High)" variant passes through verbatim (no double suffix)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("ag1", "Gemini 3.5 Flash (High)", project_dir,
                               provider="antigravity", effort="high")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "model_reasoning_effort" not in content
    assert "--effort" not in content
    assert '--model "Gemini 3.5 Flash (High)"' in content     # effort conveyed via the model variant


def test_launch_command_antigravity_folds_effort_into_model(tmp_path, monkeypatch):
    """agy effort gap resolved (wired): the launch.cmd FOLDS the picked thinking level into the --model
    variant name (base model + effort -> "(Level)"), so the wizard-picked effort actually reaches `agy`
    even though agy has no effort flag.  No -c/model_reasoning_effort, no --effort."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("ag1", "Gemini 3.5 Flash", project_dir,
                               provider="antigravity", effort="high")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert '--model "Gemini 3.5 Flash (High)"' in content     # base model + effort folded into one value
    assert "model_reasoning_effort" not in content
    assert "--effort" not in content


def test_launch_command_opencode_interactive_no_external_server_flags(tmp_path, monkeypatch):
    """provider='opencode' launches the bare interactive TUI + -m <model>.  Delivery is now fully
    internal — an in-process plugin PULLS /next — so the launch no longer pins an HTTP server port
    for an external push.  --variant has never been a valid v1.17.9 flag, so it must never appear."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("oc1", "opencode/big-pickle", project_dir, provider="opencode", effort="high")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert "--variant" not in content
    assert "--port" not in content and "--hostname" not in content
    assert "-m opencode/big-pickle" in content


def test_launch_command_antigravity_quotes_spaced_model(tmp_path, monkeypatch):
    """antigravity model names carry spaces ('Gemini 3.5 Flash (High)') -> QUOTED so the .cmd passes
    them as a single --model argument instead of splitting on the spaces."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"; project_dir.mkdir()
    cmd = agent.launch_command("ag1", "Gemini 3.5 Flash (High)", project_dir, provider="antigravity")
    content = Path(cmd[2]).read_text(encoding="utf-8")
    assert '--model "Gemini 3.5 Flash (High)"' in content


def test_launch_command_unknown_provider_raises(tmp_path, monkeypatch):
    """Unknown provider raises ValueError."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import pytest
    with pytest.raises(ValueError, match="unknown provider"):
        agent.launch_command("bot", "some-model", project_dir, provider="llama")


# ---------------------------------------------------------------------------
# write_launch_cmd: provider param is threaded through
# ---------------------------------------------------------------------------

def test_write_launch_cmd_gemini_returns_path(tmp_path, monkeypatch):
    """write_launch_cmd with provider='gemini' returns an existing Path."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    p = agent.write_launch_cmd("g1", "gemini-2.5-pro", project_dir, provider="gemini")
    assert p.exists()


def test_write_launch_cmd_opencode_returns_path(tmp_path, monkeypatch):
    """write_launch_cmd with provider='opencode' returns an existing Path."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    p = agent.write_launch_cmd("oc1", "anthropic/claude-opus-4-8", project_dir, provider="opencode")
    assert p.exists()


# ---------------------------------------------------------------------------
# SESSION RESUME for non-claude mains: the written launch.cmd carries each CLI's
# resume/continue flag when resume=True, and omits it when resume=False.
# ---------------------------------------------------------------------------

def _cmd_text(tmp_path, monkeypatch, provider, *, resume, session_id=None, model="m", effort=None):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir(exist_ok=True)
    return agent.write_launch_cmd("main", model, project_dir, provider=provider, effort=effort,
                                  session_id=session_id, resume=resume).read_text(encoding="utf-8")


def test_launch_cmd_codex_resume_appends_resume_subcommand(tmp_path, monkeypatch):
    """resume=True -> the codex launch.cmd ends with the `resume --last` subcommand (after the
    global flags); resume=False -> no resume subcommand. Bypass/model/effort preserved on resume."""
    res = _cmd_text(tmp_path, monkeypatch, "codex", resume=True, model="gpt-5.5", effort="high")
    fresh = _cmd_text(tmp_path, monkeypatch, "codex", resume=False, model="gpt-5.5", effort="high")
    assert "resume --last" in res and "resume --last" not in fresh
    assert "--dangerously-bypass-approvals-and-sandbox" in res
    assert "-m gpt-5.5" in res and 'model_reasoning_effort="high"' in res
    assert " exec " not in res                       # never the headless exec path


def test_launch_cmd_opencode_resume_uses_continue(tmp_path, monkeypatch):
    """resume=True -> the opencode launch.cmd carries --continue; resume=False -> it does not."""
    res = _cmd_text(tmp_path, monkeypatch, "opencode", resume=True, model="anthropic/x")
    fresh = _cmd_text(tmp_path, monkeypatch, "opencode", resume=False, model="anthropic/x")
    assert "--continue" in res and "--continue" not in fresh
    assert "opencode run" not in res                 # still the interactive TUI


def test_launch_cmd_antigravity_resume_uses_continue(tmp_path, monkeypatch):
    """resume=True -> the agy launch.cmd carries --continue; resume=False -> it does not."""
    res = _cmd_text(tmp_path, monkeypatch, "antigravity", resume=True, model="gemini-3-pro")
    fresh = _cmd_text(tmp_path, monkeypatch, "antigravity", resume=False, model="gemini-3-pro")
    assert "--continue" in res and "--continue" not in fresh
    assert "--print" not in res and "-p " not in res  # never headless


def test_launch_cmd_gemini_presets_then_resumes_session_id(tmp_path, monkeypatch):
    """gemini PRE-SETS the id fresh (--session-id <id>) and RESUMES it on restart (--resume <id>)."""
    fresh = _cmd_text(tmp_path, monkeypatch, "gemini", resume=False, session_id="sid-1", model="gemini-2.5-pro")
    res = _cmd_text(tmp_path, monkeypatch, "gemini", resume=True, session_id="sid-1", model="gemini-2.5-pro")
    assert "--session-id sid-1" in fresh and "--resume" not in fresh
    assert "--resume sid-1" in res and "--session-id" not in res
    assert "-p " not in res                          # interactive


def test_launch_cmd_claude_resume_byte_unchanged(tmp_path, monkeypatch):
    """Regression guard: claude's --session-id (fresh) / --resume (restart) line is unchanged."""
    fresh = _cmd_text(tmp_path, monkeypatch, "claude", resume=False, session_id="sid-1", model="claude-opus-4-8")
    res = _cmd_text(tmp_path, monkeypatch, "claude", resume=True, session_id="sid-1", model="claude-opus-4-8")
    assert "claude --permission-mode bypassPermissions --model claude-opus-4-8 --session-id sid-1" in fresh
    assert "claude --permission-mode bypassPermissions --model claude-opus-4-8 --resume sid-1" in res
