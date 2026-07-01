import json
from pathlib import Path
from mkcrew import teamconfig


def test_default_team_has_8_agents():
    team = teamconfig.default_team()
    assert len(team) == 8


def test_default_team_roles():
    team = teamconfig.default_team()
    roles = [a["role"] for a in team]
    assert roles == [
        "main",
        "worker1", "worker2", "worker3",
        "worker4", "worker5", "worker6",
        "planner",
    ]


def test_default_team_main_agent():
    team = teamconfig.default_team()
    main = next(a for a in team if a["role"] == "main")
    assert main["model"] == "claude-opus-4-8"
    assert main["effort"] == "max"
    assert main["window"] == "main"
    assert main["mode"] == "bypassPermissions"


def test_default_team_opus_agents():
    team = teamconfig.default_team()
    for role in ("worker1", "worker3", "worker5"):   # the opus-model workers
        a = next(x for x in team if x["role"] == role)
        assert a["model"] == "claude-opus-4-8"
        assert a["effort"] == "high"
        assert a["window"] == role
        assert a["mode"] == "bypassPermissions"


def test_default_team_sonnet_agents():
    team = teamconfig.default_team()
    for role in ("worker2", "worker4", "worker6"):   # the sonnet-model workers
        a = next(x for x in team if x["role"] == role)
        assert a["model"] == "claude-sonnet-4-6"
        assert a["effort"] == "high"
        assert a["window"] == role
        assert a["mode"] == "bypassPermissions"


def test_default_team_has_no_reviewer():
    """The default roster is uniform workers + planner — there is NO special 'reviewer' role."""
    team = teamconfig.default_team()
    assert all(a["role"] != "reviewer" for a in team)


def test_default_team_planner():
    team = teamconfig.default_team()
    planner = next(a for a in team if a["role"] == "planner")
    assert planner["model"] == "claude-opus-4-8"
    assert planner["effort"] == "high"
    assert planner["window"] == "plan"
    # bypassPermissions (NOT plan mode): plan mode blocks the cross-dir inbox read
    # and the side-effecting mk-done. Planner read-only is prompt-enforced in Phase 2.
    assert planner["mode"] == "bypassPermissions"


def test_load_team_creates_config_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    team = teamconfig.load_team(project_dir)
    assert len(team) == 8
    config_file = project_dir / ".mkcrew" / "team.config"
    assert config_file.exists()


def test_load_team_written_config_is_valid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    teamconfig.load_team(project_dir)
    config_file = project_dir / ".mkcrew" / "team.config"
    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["entry_window"] == "main"
    assert len(data["agents"]) == 8


def test_load_team_round_trips_custom_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    custom = {
        "entry_window": "main",
        "agents": [
            {"role": "main", "model": "claude-opus-4-8", "effort": "high",
             "window": "main", "mode": "bypassPermissions"},
        ],
    }
    (mk_dir / "team.config").write_text(json.dumps(custom), encoding="utf-8")
    team = teamconfig.load_team(project_dir)
    assert len(team) == 1
    assert team[0]["role"] == "main"
    assert team[0]["model"] == "claude-opus-4-8"


def test_load_team_does_not_overwrite_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    custom = {
        "entry_window": "custom",
        "agents": [
            {"role": "solo", "model": "claude-sonnet-4-6", "effort": "low",
             "window": "solo", "mode": "bypassPermissions"},
        ],
    }
    cfg_path = mk_dir / "team.config"
    cfg_path.write_text(json.dumps(custom), encoding="utf-8")
    teamconfig.load_team(project_dir)
    # Should still be the original content
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["entry_window"] == "custom"
    assert data["agents"][0]["role"] == "solo"


# ---------------------------------------------------------------------------
# Finding 2: corrupt-config error handling + provider defaulting
# ---------------------------------------------------------------------------

def test_load_team_raises_systemexit_on_corrupt_json(tmp_path, monkeypatch):
    """load_team raises SystemExit (not JSONDecodeError) when config is corrupt."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    (mk_dir / "team.config").write_text("{not valid json{{", encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        teamconfig.load_team(project_dir)
    msg = str(exc_info.value)
    assert "invalid" in msg.lower()
    assert "mk init" in msg


def test_load_team_raises_systemexit_on_missing_agents_key(tmp_path, monkeypatch):
    """load_team raises SystemExit when config JSON lacks the 'agents' key."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    (mk_dir / "team.config").write_text(
        json.dumps({"entry_window": "main"}), encoding="utf-8"
    )
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        teamconfig.load_team(project_dir)
    msg = str(exc_info.value)
    assert "invalid" in msg.lower()
    assert "mk init" in msg


def test_load_team_defaults_provider_to_claude(tmp_path, monkeypatch):
    """Agents without a 'provider' field default to 'claude' after load."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    # Agent record has no 'provider' key — simulates an older config
    config_data = {
        "entry_window": "main",
        "agents": [
            {"role": "main", "model": "claude-opus-4-8", "effort": "max",
             "window": "main", "mode": "bypassPermissions"},
        ],
    }
    (mk_dir / "team.config").write_text(json.dumps(config_data), encoding="utf-8")
    team = teamconfig.load_team(project_dir)
    assert team[0]["provider"] == "claude"


def test_load_team_preserves_explicit_provider(tmp_path, monkeypatch):
    """Agents that already have a 'provider' field keep their value."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    project_dir = tmp_path / "myproj"
    mk_dir = project_dir / ".mkcrew"
    mk_dir.mkdir(parents=True)
    config_data = {
        "entry_window": "main",
        "agents": [
            {"role": "gemini1", "model": "gemini-2.5-pro", "effort": "high",
             "window": "gemini1", "mode": "bypassPermissions", "provider": "gemini"},
        ],
    }
    (mk_dir / "team.config").write_text(json.dumps(config_data), encoding="utf-8")
    team = teamconfig.load_team(project_dir)
    assert team[0]["provider"] == "gemini"


# ---------------------------------------------------------------------------
# Slice 1: layout field + load_layout / set_layout
# ---------------------------------------------------------------------------

def test_load_layout_defaults_to_hub_when_absent(tmp_path):
    assert teamconfig.load_layout(tmp_path) == "hub"        # no config file


def test_load_layout_reads_configured_value(tmp_path):
    cfg = tmp_path / ".mkcrew" / "team.config"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"layout": "tiled", "agents": []}), encoding="utf-8")
    assert teamconfig.load_layout(tmp_path) == "tiled"


def test_set_layout_writes_and_preserves_agents(tmp_path):
    teamconfig.dump_default(tmp_path)                        # writes default agents + layout
    teamconfig.set_layout(tmp_path, "tiled")
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"
    assert len(data["agents"]) == 8                          # agents preserved


def test_dump_default_includes_layout_hub(tmp_path):
    teamconfig.dump_default(tmp_path)
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "hub"


def test_build_team_count_maps_priority_roles():
    roles = [a["role"] for a in teamconfig.build_team(4)]
    assert roles == ["main", "worker1", "worker2", "worker3"]


def test_build_team_count_one_is_just_main():
    assert [a["role"] for a in teamconfig.build_team(1)] == ["main"]


def test_build_team_clamps_over_and_under():
    assert len(teamconfig.build_team(99)) == 8
    assert len(teamconfig.build_team(0)) == 1


def test_team_changes_detects_cli_swaps_and_is_empty_when_unchanged(tmp_path):
    """team_changes reports CLI swaps / joins / leaves, returns [] when nothing changed, and
    persists a snapshot — so a resumed lead is told ONLY what's new."""
    t1 = [{"role": "main", "provider": "claude"}, {"role": "worker1", "provider": "claude"}]
    first = teamconfig.team_changes(tmp_path, t1)
    assert any("worker1" in c and "joined" in c for c in first)   # first run: everyone joins
    assert teamconfig.team_changes(tmp_path, t1) == []            # same team -> no changes
    t2 = [{"role": "main", "provider": "claude"}, {"role": "worker1", "provider": "codex"}]
    assert teamconfig.team_changes(tmp_path, t2) == ["worker1 is now codex (was claude)"]


def test_build_team_codex_is_first_class():
    """Selecting 'codex' yields the first-class codex provider, not custom."""
    team = teamconfig.build_team(2, ["claude", "codex"])
    assert team[1]["provider"] == "codex" and "command" not in team[1]


def test_build_team_applies_builtin_provider():
    agents = teamconfig.build_team(2, ["claude", "gemini"])
    assert agents[1]["provider"] == "gemini"
    assert "command" not in agents[1]


def test_build_team_custom_command_for_unknown_provider():
    agents = teamconfig.build_team(2, ["claude", "codex --full-auto"])
    assert agents[1]["provider"] == "custom"
    assert agents[1]["command"] == "codex --full-auto"


def test_build_team_ignores_blank_and_extra_provider_entries():
    agents = teamconfig.build_team(2, ["", "gemini", "opencode"])
    assert "provider" not in agents[0] or agents[0]["provider"] == "claude"
    assert agents[1]["provider"] == "gemini"


def test_write_team_writes_layout_and_agents(tmp_path):
    teamconfig.write_team(tmp_path, teamconfig.build_team(2), "tiled")
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled" and data["entry_window"] == "main"
    assert len(data["agents"]) == 2


def test_build_team_applies_per_agent_model_and_effort():
    """models[i]/efforts[i] override that agent's model/thinking; blank keeps the roster default."""
    agents = teamconfig.build_team(3, ["claude", "codex", "opencode"],
                                   models=["", "gpt-5-codex", "anthropic/x"],
                                   efforts=["high", "medium", ""])
    assert agents[1]["model"] == "gpt-5-codex" and agents[1]["effort"] == "medium"
    assert agents[2]["model"] == "anthropic/x"
    assert agents[0]["effort"] == "high"
    assert agents[0]["model"] == teamconfig.default_team()[0]["model"]   # blank model -> roster default


def test_build_team_non_claude_blank_model_drops_claude_default():
    """BUG-4: a non-claude agent with NO explicit model must NOT inherit the claude roster default —
    leave it blank so team.config reflects reality and the provider CLI picks its own default."""
    agents = teamconfig.build_team(2, ["claude", "opencode"])   # no models given
    assert agents[1]["provider"] == "opencode"
    assert agents[1]["model"] == ""                             # NOT "claude-sonnet-4-6"
    assert agents[0]["model"] == teamconfig.default_team()[0]["model"]   # claude agent keeps its default
    # an EXPLICIT (even claude-*) model on a non-claude agent is still honored — only inherited defaults drop
    agents2 = teamconfig.build_team(2, ["claude", "codex"], models=["", "gpt-5-codex"])
    assert agents2[1]["model"] == "gpt-5-codex"


def test_mode_persists_and_loads(tmp_path):
    """write_team stores the core mode; load_mode reads it back, defaulting to 'standard'."""
    teamconfig.write_team(tmp_path, teamconfig.build_team(2), "tiled", "fast")
    assert teamconfig.load_mode(tmp_path) == "fast"
    teamconfig.write_team(tmp_path, teamconfig.build_team(2), "tiled")   # default
    assert teamconfig.load_mode(tmp_path) == "standard"
    assert teamconfig.load_mode(tmp_path / "nope") == "standard"          # absent -> default


# ---------------------------------------------------------------------------
# FIX #4: name-as-identity — set_name persists, load_name reads back
# ---------------------------------------------------------------------------

def test_name_persists_and_loads(tmp_path):
    """set_name writes the workspace identity to .mkcrew/workspace.json; load_name reads it back."""
    assert teamconfig.load_name(tmp_path) is None                         # absent -> None
    teamconfig.set_name(tmp_path, "Testing")
    assert teamconfig.load_name(tmp_path) == "Testing"
    assert (tmp_path / ".mkcrew" / "workspace.json").exists()


def test_name_survives_team_config_rewrites(tmp_path):
    """The name lives in workspace.json, so write_team / set_layout never clobber it."""
    teamconfig.set_name(tmp_path, "Prod")
    teamconfig.write_team(tmp_path, teamconfig.build_team(3), "tiled")    # rewrites team.config
    teamconfig.set_layout(tmp_path, "main-vertical")                       # rewrites team.config again
    assert teamconfig.load_name(tmp_path) == "Prod"


def test_set_name_blank_clears_and_trims(tmp_path):
    """A blank/whitespace name clears the identity; a name is stored trimmed."""
    teamconfig.set_name(tmp_path, "  Spaced  ")
    assert teamconfig.load_name(tmp_path) == "Spaced"
    teamconfig.set_name(tmp_path, "   ")
    assert teamconfig.load_name(tmp_path) is None
    teamconfig.set_name(tmp_path, None)
    assert teamconfig.load_name(tmp_path) is None


def test_load_name_handles_corrupt_workspace_json(tmp_path):
    """A corrupt workspace.json reads back as None (no name), never raises."""
    p = tmp_path / ".mkcrew" / "workspace.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert teamconfig.load_name(tmp_path) is None
