# tests/test_cli.py
import pytest
import urllib.error
from mkcrew import cli

def test_dispatch_table_has_core_commands():
    assert {"init", "start", "attach", "kill", "panic"}.issubset(cli.COMMANDS.keys())


def test_main_routes_ask_to_ask_cli(monkeypatch):
    """`mk ask <role> <msg>` folds into the mk CLI and forwards argv to ask_cli.main."""
    import mkcrew.ask_cli as _ask
    seen = {}

    def fake_ask_main(argv):
        seen["argv"] = argv
        return 7

    monkeypatch.setattr(_ask, "main", fake_ask_main)
    ret = cli.main(["ask", "opus1", "do X"])
    assert ret == 7
    assert seen["argv"] == ["opus1", "do X"]


def test_main_routes_status_to_coreview(monkeypatch):
    """`mk status` folds into the mk CLI and calls coreview.status_main."""
    import mkcrew.coreview as _cv
    called = {}

    def fake_status_main():
        called["hit"] = True
        return 0

    monkeypatch.setattr(_cv, "status_main", fake_status_main)
    ret = cli.main(["status"])
    assert ret == 0
    assert called.get("hit") is True


# ---------------------------------------------------------------------------
# P1-4: pend / trace / repair registered + basic arg handling
# ---------------------------------------------------------------------------

def test_dispatch_table_has_p14_commands():
    """COMMANDS must contain pend, trace, and repair."""
    assert {"pend", "trace", "repair"}.issubset(cli.COMMANDS.keys())


def test_cmd_repair_usage_when_first_arg_is_not_resubmit(capsys):
    """cmd_repair prints usage if first arg is not 'resubmit'."""
    cli.cmd_repair([])
    out = capsys.readouterr().out
    assert "usage" in out.lower()

    cli.cmd_repair(["bad"])
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_cmd_trace_usage_when_no_arg(capsys):
    """cmd_trace prints usage if no job_id provided."""
    cli.cmd_trace([])
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_cmd_repair_calls_post(monkeypatch, capsys):
    """cmd_repair resubmit <id> calls _post('/repair', {'job_id': id})."""
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(cli, "_post", fake_post)
    cli.cmd_repair(["resubmit", "job42"])
    assert len(calls) == 1
    assert calls[0][0] == "/repair"
    assert calls[0][1] == {"job_id": "job42"}
    out = capsys.readouterr().out
    assert "ok" in out.lower() or "resubmit" in out.lower()


def test_cmd_pend_calls_get(monkeypatch, capsys):
    """cmd_pend calls _get('/jobs') and prints a table."""
    def fake_get(path):
        assert path == "/jobs"
        return 200, {"jobs": [
            {"id": "job1", "from": "main", "to": "worker", "status": "DELIVERED", "retry_count": 0}
        ]}

    monkeypatch.setattr(cli, "_get", fake_get)
    cli.cmd_pend([])
    out = capsys.readouterr().out
    assert "job1" in out
    assert "worker" in out


def test_cmd_trace_calls_get(monkeypatch, capsys):
    """cmd_trace <id> calls _get('/jobs/<id>') and prints detail + events."""
    def fake_get(path):
        assert path == "/jobs/job1"
        return 200, {
            "id": "job1", "from": "main", "to": "worker",
            "status": "DELIVERED", "reply": "", "retry_count": 0,
            "events": [
                {"ts": 1000000.0, "label": "created"},
                {"ts": 1000001.0, "label": "delivered"},
            ],
        }

    monkeypatch.setattr(cli, "_get", fake_get)
    cli.cmd_trace(["job1"])
    out = capsys.readouterr().out
    assert "job1" in out
    assert "created" in out
    assert "delivered" in out


def test_cmd_trace_handles_404(monkeypatch, capsys):
    """cmd_trace prints an error message when job is not found."""
    def fake_get(path):
        return 404, {"error": "unknown job"}

    monkeypatch.setattr(cli, "_get", fake_get)
    cli.cmd_trace(["jobXXX"])
    out = capsys.readouterr().out
    assert "error" in out.lower() or "unknown" in out.lower()


# ---------------------------------------------------------------------------
# Finding 1: _require_port / _post / _get daemon-down error handling
# ---------------------------------------------------------------------------

def test_require_port_exits_when_port_file_missing(monkeypatch, tmp_path):
    """_require_port raises SystemExit with a friendly message when port file absent."""
    import mkcrew.config as _cfg
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "nonexistent.port")
    with pytest.raises(SystemExit) as exc_info:
        cli._require_port()
    msg = str(exc_info.value)
    assert "mkd not reachable" in msg
    assert "mk start" in msg


def test_require_port_exits_when_port_file_empty(monkeypatch, tmp_path):
    """_require_port raises SystemExit when the port file exists but is empty."""
    import mkcrew.config as _cfg
    port_file = tmp_path / "mkd.port"
    port_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(_cfg, "port_file", lambda: port_file)
    with pytest.raises(SystemExit) as exc_info:
        cli._require_port()
    msg = str(exc_info.value)
    assert "mkd not reachable" in msg


def test_post_exits_on_connection_refused(monkeypatch, tmp_path):
    """_post raises SystemExit (not URLError) on connection failure."""
    import mkcrew.config as _cfg
    port_file = tmp_path / "mkd.port"
    port_file.write_text("19999", encoding="utf-8")
    monkeypatch.setattr(_cfg, "port_file", lambda: port_file)

    import urllib.request
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        cli._post("/register", {"agent": "main"})
    msg = str(exc_info.value)
    assert "mkd not reachable" in msg


def test_get_exits_on_connection_refused(monkeypatch, tmp_path):
    """_get raises SystemExit (not URLError) on connection failure."""
    import mkcrew.config as _cfg
    port_file = tmp_path / "mkd.port"
    port_file.write_text("19999", encoding="utf-8")
    monkeypatch.setattr(_cfg, "port_file", lambda: port_file)

    import urllib.request
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        cli._get("/jobs")
    msg = str(exc_info.value)
    assert "mkd not reachable" in msg


def test_get_handles_empty_http_error_body(monkeypatch, tmp_path):
    """_get returns a dict with 'error' key when HTTPError body is empty."""
    import mkcrew.config as _cfg
    port_file = tmp_path / "mkd.port"
    port_file.write_text("19999", encoding="utf-8")
    monkeypatch.setattr(_cfg, "port_file", lambda: port_file)

    import io, urllib.request, urllib.error
    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://localhost/", 500, "Server Error", {}, None)
        def read(self):
            return b""  # empty body

    def fake_urlopen(req, timeout=None):
        raise FakeHTTPError()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    code, data = cli._get("/jobs")
    assert code == 500
    assert "error" in data


# ---------------------------------------------------------------------------
# Finding 3c: _session_exists helper and cmd_start existing-session guard
# ---------------------------------------------------------------------------

def test_session_exists_returns_false_when_has_session_fails(monkeypatch):
    """_session_exists returns False when psmux has-session exits nonzero."""
    import subprocess
    from mkcrew.psmux import PsmuxBackend

    def fake_run(self, *args):
        result = subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
        return result

    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    mux = PsmuxBackend()
    assert cli._session_exists(mux, "mkcrew") is False


def test_session_exists_returns_true_when_has_session_succeeds(monkeypatch):
    """_session_exists returns True when psmux has-session exits zero."""
    import subprocess
    from mkcrew.psmux import PsmuxBackend

    def fake_run(self, *args):
        result = subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
        return result

    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    mux = PsmuxBackend()
    assert cli._session_exists(mux, "mkcrew") is True


def test_cmd_start_exits_when_session_already_running(monkeypatch, tmp_path):
    """cmd_start raises SystemExit when a 'mkcrew' psmux session already exists."""
    import subprocess
    from mkcrew import config as _cfg
    from mkcrew.psmux import PsmuxBackend

    # Fake out ensure_project_hook so it's a no-op
    import mkcrew.agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)

    # Make _session_exists return True (session already running)
    def fake_run(self, *args):
        result = subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
        return result
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_start([])
    msg = str(exc_info.value)
    assert "already running" in msg
    assert "mk kill" in msg


def test_kill_daemon_refuses_unverified_pid(monkeypatch, tmp_path):
    """Safety: _kill_daemon must NOT taskkill a pid that isn't our daemon
    (a stale pid the OS reused for, e.g., explorer.exe)."""
    import subprocess
    from mkcrew import config as _cfg
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _cfg.pid_file().write_text("4321", encoding="utf-8")
    monkeypatch.setattr(cli, "_pid_is_mkd", lambda pid: False)
    calls = []
    def fake_run(args, **k):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._kill_daemon()
    assert not any(a and a[0] == "taskkill" for a in calls), "must not kill an unverified pid"


def test_kill_daemon_kills_verified_mkd_pid(monkeypatch, tmp_path):
    """_kill_daemon taskkills the pid once verified to be our daemon."""
    import subprocess
    from mkcrew import config as _cfg
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _cfg.pid_file().write_text("4321", encoding="utf-8")
    monkeypatch.setattr(cli, "_pid_is_mkd", lambda pid: True)
    calls = []
    def fake_run(args, **k):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._kill_daemon()
    assert any(a and a[0] == "taskkill" and "4321" in a for a in calls)


def test_lead_prompt_embeds_full_mk_path_single_line():
    """The lead prompt must carry the absolute mk.exe path (bare `mk` isn't on the
    agent's PATH) joined to the `ask` subcommand, and stay single-line for send_line()."""
    from mkcrew import prompts
    p = prompts.lead_prompt("C:/x/Scripts/mk.exe")
    assert "C:/x/Scripts/mk.exe ask" in p   # the architectural mk-ask delegation join
    assert "\n" not in p
    assert "delegate" in p.lower()


def test_cmd_start_dispatches_configured_layout(monkeypatch, tmp_path):
    """cmd_start must build the cockpit via layouts.get(load_layout(...))."""
    import pytest
    from mkcrew import cli, layouts, teamconfig, config as _cfg, agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(cli, "PsmuxBackend",
                        lambda: type("M", (), {"kill_server": lambda self: None})())

    chosen = {}
    class _Stop(Exception):
        pass

    def fake_get(name):
        chosen["name"] = name
        raise _Stop()           # short-circuit before the trust-poll / prompt tail

    monkeypatch.setattr(layouts, "get", fake_get)
    with pytest.raises(_Stop):
        cli.cmd_start([])
    assert chosen["name"] == "hub"


def test_cmd_layout_lists_current_and_available(monkeypatch, capsys):
    from mkcrew import cli, teamconfig
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    cli.cmd_layout([])
    out = capsys.readouterr().out
    assert "hub" in out and "tiled" in out


def test_cmd_layout_sets_known_layout(monkeypatch, capsys):
    from mkcrew import cli, teamconfig
    seen = {}
    monkeypatch.setattr(teamconfig, "set_layout", lambda p, name: seen.setdefault("name", name))
    cli.cmd_layout(["tiled"])
    assert seen["name"] == "tiled"
    assert "tiled" in capsys.readouterr().out


def test_cmd_layout_rejects_unknown(monkeypatch, capsys):
    from mkcrew import cli, teamconfig
    called = {"set": False}
    monkeypatch.setattr(teamconfig, "set_layout", lambda p, name: called.update(set=True))
    cli.cmd_layout(["bogus"])
    out = capsys.readouterr().out
    assert "unknown" in out.lower()
    assert called["set"] is False


def test_layout_registered_in_commands():
    from mkcrew import cli
    assert "layout" in cli.COMMANDS


def test_cmd_start_fresh_clears_sessions(monkeypatch, tmp_path):
    """`mk start --fresh` wipes the session store before building."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("uuid-x", True))
    cleared = {"hit": False}
    monkeypatch.setattr(sessions, "clear", lambda p: cleared.update(hit=True))
    monkeypatch.setattr(cli, "PsmuxBackend",
                        lambda: type("M", (), {"kill_server": lambda self: None})())

    class _Stop(Exception):
        pass

    def fake_get(name):
        raise _Stop()                       # short-circuit after sessions are resolved
    monkeypatch.setattr(layouts, "get", fake_get)
    with pytest.raises(_Stop):
        cli.cmd_start(["--fresh"])
    assert cleared["hit"] is True


def test_cmd_start_skips_bootstrap_for_resumed_agents(monkeypatch, tmp_path):
    """Fresh agents get their bootstrap prompt; resumed agents do not."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [
        {"role": "main", "model": "m", "provider": "claude"},
        {"role": "planner", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("uuid-" + role, role == "main"))
    monkeypatch.setattr(sessions, "is_resumable",
                        lambda p, sid, provider="claude": True)   # the saved session resumes
    monkeypatch.setattr(layouts, "get",
                        lambda name: (lambda mux, team, project, register, session: {"main": "%1", "planner": "%2"}))
    sent = []
    fake_mux = type("M", (), {
        "kill_server": lambda self: None,
        "capture": lambda self, p: "trust",         # trust-poll matches immediately, no spin
        "send_enter": lambda self, p: None,
        "send_line": lambda self, p, t: sent.append(p),
        "set_option": lambda self, n, v: None,       # cmd_start now calls apply_chrome(mux)
        "bind_key": lambda self, key, *cmd: None,     # apply_chrome binds Ctrl-b A -> add-workspace
    })()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    cli.cmd_start(["--no-attach"])
    assert "%1" in sent          # main (fresh) bootstrapped
    assert "%2" not in sent      # planner (resumed) NOT bootstrapped


def test_cmd_start_resumes_non_claude_main_on_restart(monkeypatch, tmp_path):
    """A non-claude (codex) main mints an id like claude and, having launched before (is_new=False),
    is RESUMED on restart -> not re-bootstrapped. is_resumable is called with the codex provider so
    its continue-last rule (resume on 'launched before') applies."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_codex_hook", lambda p, r: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [
        {"role": "main", "model": "m", "provider": "codex"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(teamconfig, "team_changes", lambda p, team: [])  # clean resume, no team-update prompt
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("uuid-" + role, False))  # launched before
    seen = {}
    monkeypatch.setattr(sessions, "is_resumable",
                        lambda p, sid, provider="claude": seen.update(provider=provider) or True)
    monkeypatch.setattr(layouts, "get",
                        lambda name: (lambda mux, team, project, register, session: {"main": "%1"}))
    sent = []
    fake_mux = type("M", (), {
        "kill_server": lambda self: None,
        "capture": lambda self, p: "trust",
        "send_enter": lambda self, p: None,
        "send_line": lambda self, p, t: sent.append(p),
        "set_option": lambda self, n, v: None,
        "bind_key": lambda self, key, *cmd: None,
    })()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    cli.cmd_start(["--no-attach"])
    assert seen.get("provider") == "codex"   # provider threaded into is_resumable
    assert "%1" not in sent                  # codex main resumed -> NOT re-bootstrapped


def _stub_cmd_start_until_layout(monkeypatch, tmp_path, team):
    """Stub cmd_start's side effects and short-circuit at layouts.get (which runs AFTER the resume
    loop), so a test can inspect the per-agent `_resume` flags cmd_start computed. Returns the _Stop
    exception class to assert on. `team` is mutated in place by cmd_start (a['_resume'] = ...)."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    for fn in ("ensure_project_hook", "ensure_codex_hook", "ensure_opencode_plugin",
               "ensure_project_claude_md", "ensure_project_agents_md"):
        monkeypatch.setattr(_agent, fn, lambda *a: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(teamconfig, "load_team", lambda p: team)
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("uuid-" + role, False))  # all launched before
    monkeypatch.setattr(cli, "PsmuxBackend",
                        lambda: type("M", (), {"kill_server": lambda self: None})())

    class _Stop(Exception):
        pass

    def fake_get(name):
        raise _Stop()                        # stop AFTER the resume loop, BEFORE building panes
    monkeypatch.setattr(layouts, "get", fake_get)
    return _Stop


def test_cmd_start_two_codex_agents_launch_fresh_not_co_resumed(monkeypatch, tmp_path):
    """THE FIX end-to-end: two codex agents in one team can't co-resume the single 'last' codex session,
    so cmd_start marks BOTH fresh (_resume False) -> each starts its own new session, never sharing
    history. (Real sessions.is_resumable is used; only the collision guard flips these to fresh.)"""
    team = [{"role": "worker1", "model": "m", "provider": "codex"},
            {"role": "worker2", "model": "m", "provider": "codex"}]
    _Stop = _stub_cmd_start_until_layout(monkeypatch, tmp_path, team)
    with pytest.raises(_Stop):
        cli.cmd_start([])
    assert team[0]["_resume"] is False and team[1]["_resume"] is False   # 2 codex -> both fresh (isolated)


def test_cmd_start_sole_codex_resumes_two_gemini_still_resume(monkeypatch, tmp_path):
    """A SOLE codex resumes (unchanged); two gemini also resume even though shared, because gemini
    pre-sets a per-role uuid (distinct session targets), so the collision guard does not apply to it."""
    team = [{"role": "main", "model": "m", "provider": "codex"},
            {"role": "g1", "model": "m", "provider": "gemini"},
            {"role": "g2", "model": "m", "provider": "gemini"}]
    _Stop = _stub_cmd_start_until_layout(monkeypatch, tmp_path, team)
    with pytest.raises(_Stop):
        cli.cmd_start([])
    assert team[0]["_resume"] is True                                    # sole codex -> resumes
    assert team[1]["_resume"] is True and team[2]["_resume"] is True     # gemini per-role uuid -> both resume


def test_cmd_init_flags_write_custom_config(monkeypatch, tmp_path):
    """`mk init --agents 3 --layout tiled --providers claude,gemini,codex` writes that config."""
    from mkcrew import cli
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(cli, "install_skills", lambda p: [])
    monkeypatch.setattr(cli, "scaffold_self_improvement", lambda p: [])
    cli.cmd_init(["--agents", "3", "--layout", "tiled", "--providers", "claude,gemini,codex"])
    import json
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"
    assert [a["role"] for a in data["agents"]] == ["main", "worker1", "worker2"]
    assert data["agents"][1]["provider"] == "gemini"
    assert data["agents"][2]["provider"] == "codex"   # codex is first-class now


def test_cmd_init_prompts_when_interactive(monkeypatch, tmp_path):
    """With no flags and no config, prompts drive the config."""
    from mkcrew import cli
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(cli, "install_skills", lambda p: [])
    monkeypatch.setattr(cli, "scaffold_self_improvement", lambda p: [])
    answers = iter(["2", "tiled", ""])           # agents=2, layout=tiled, providers=blank
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    cli.cmd_init([])
    import json
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"
    assert len(data["agents"]) == 2


def test_cmd_init_bare_rerun_does_not_overwrite(monkeypatch, tmp_path):
    """Bare `mk init` with an existing config leaves it untouched."""
    from mkcrew import cli, teamconfig
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(cli, "install_skills", lambda p: [])
    monkeypatch.setattr(cli, "scaffold_self_improvement", lambda p: [])
    teamconfig.write_team(tmp_path, teamconfig.build_team(5), "hub")
    cli.cmd_init([])
    import json
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert len(data["agents"]) == 5


def test_cmd_start_applies_chrome(monkeypatch, tmp_path):
    """cmd_start calls layouts.apply_chrome after building the cockpit."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    monkeypatch.setattr(_agent, "ensure_project_hook", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_claude_md", lambda p: None)
    monkeypatch.setattr(_agent, "ensure_project_agents_md", lambda p: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("u", True))
    monkeypatch.setattr(layouts, "get", lambda name: (lambda *a, **k: {"main": "%1"}))
    chrome = {"hit": False}
    monkeypatch.setattr(layouts, "apply_chrome", lambda mux, name=None: chrome.update(hit=True))
    fake_mux = type("M", (), {
        "kill_server": lambda self: None, "capture": lambda self, p: "trust",
        "send_enter": lambda self, p: None, "send_line": lambda self, p, t: None})()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    cli.cmd_start(["--no-attach"])
    assert chrome["hit"] is True


def test_cmd_start_auto_attaches_unless_no_attach(monkeypatch, tmp_path):
    """`mk start` attaches automatically; `mk start --no-attach` skips it (Studio drives its own)."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    for fn in ("ensure_project_hook", "ensure_codex_hook", "ensure_opencode_plugin",
               "ensure_project_claude_md", "ensure_project_agents_md"):
        monkeypatch.setattr(_agent, fn, lambda *a: None)   # ensure_codex_hook now takes (project, role)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("u", True))
    monkeypatch.setattr(layouts, "get", lambda name: (lambda *a, **k: {"main": "%1"}))
    monkeypatch.setattr(layouts, "apply_chrome", lambda mux, name=None: None)
    fake_mux = type("M", (), {
        "kill_server": lambda self: None, "capture": lambda self, p: "trust",
        "send_enter": lambda self, p: None, "send_line": lambda self, p, t: None})()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    n = {"attach": 0}
    monkeypatch.setattr(cli, "cmd_attach", lambda argv: n.update(attach=n["attach"] + 1))

    cli.cmd_start([])                 # default -> auto-attach
    cli.cmd_start(["--no-attach"])    # opt-out -> no attach
    assert n["attach"] == 1


def test_studio_registered_in_commands():
    from mkcrew import cli
    assert "studio" in cli.COMMANDS


def test_cmd_studio_starts_server(monkeypatch):
    from mkcrew import cli, studio
    called = {"hit": False}
    monkeypatch.setattr(studio, "serve", lambda *a, **k: called.update(hit=True))
    cli.cmd_studio([])
    assert called["hit"] is True


def test_clear_stale_daemon_files_removes_port_and_pid(monkeypatch, tmp_path):
    """_clear_stale_daemon_files deletes a leftover port/pid file so `mk start`'s wait loop
    blocks for the NEW daemon's fresh port instead of reading a dead one (the /register-refused bug)."""
    from mkcrew import config as _cfg
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _cfg.port_file().write_text("50000", encoding="utf-8")
    _cfg.pid_file().write_text("123", encoding="utf-8")
    cli._clear_stale_daemon_files()
    assert not _cfg.port_file().exists()
    assert not _cfg.pid_file().exists()


def test_clear_stale_daemon_files_ok_when_absent(monkeypatch, tmp_path):
    """No error when there is nothing to clear (missing_ok)."""
    from mkcrew import config as _cfg
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cli._clear_stale_daemon_files()           # must not raise
    assert not _cfg.port_file().exists()


def test_cmd_layout_flips_single_window_live(tmp_path, monkeypatch, capsys):
    """`mk layout main-vertical` on a running cockpit already in a single-window layout flips it
    LIVE via select-layout (no teardown) and persists it; hub (structural) does not flip live."""
    import subprocess
    from mkcrew import teamconfig
    from mkcrew.psmux import PsmuxBackend
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    teamconfig.set_layout(tmp_path, "tiled")                        # current = single-window
    monkeypatch.setattr(PsmuxBackend, "_run",                       # session is "running"
                        lambda self, *a: subprocess.CompletedProcess(a, 0, "", ""))
    calls = []
    monkeypatch.setattr(PsmuxBackend, "select_layout", lambda self, t, l: calls.append((t, l)))

    cli.cmd_layout(["main-vertical"])
    assert calls == [("mkcrew", "main-vertical")]                   # applied live
    assert "live" in capsys.readouterr().out
    assert teamconfig.load_layout(tmp_path) == "main-vertical"      # and persisted for next start

    calls.clear()
    cli.cmd_layout(["hub"])                                     # structural -> no live flip
    assert calls == []
    assert "next `mk start`" in capsys.readouterr().out


def test_best_preset_covers_ratio_and_count():
    """The auto-picker maps (pane count, screen ratio) to a sensible single-window preset."""
    assert cli._best_preset(3, 240, 50) == "even-horizontal"   # wide screen, few panes -> a row
    assert cli._best_preset(3, 80, 90) == "even-vertical"      # tall screen, few panes -> a column
    assert cli._best_preset(4, 120, 50) == "tiled"             # squarish, few panes -> grid
    assert cli._best_preset(9, 240, 50) == "tiled"             # many panes -> grid regardless
    assert cli._best_preset(2, 240, 50) == "even-horizontal"   # two panes -> split the long axis
    assert cli._best_preset(2, 80, 90) == "even-vertical"


def test_cmd_layout_auto_measures_and_applies_live(tmp_path, monkeypatch, capsys):
    """`mk layout auto` measures the running window + pane count and applies the best preset live."""
    import subprocess
    from mkcrew import teamconfig
    from mkcrew.psmux import PsmuxBackend
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    teamconfig.write_team(tmp_path, [{"role": "main", "model": "m"}, {"role": "w", "model": "m"}], "tiled")

    def fake_run(self, *a):
        if a and a[0] == "display-message":
            return subprocess.CompletedProcess(a, 0, "240 50\n", "")   # a wide screen
        return subprocess.CompletedProcess(a, 0, "", "")               # has-session etc. -> ok
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    calls = []
    monkeypatch.setattr(PsmuxBackend, "select_layout", lambda self, t, l: calls.append((t, l)))

    cli.cmd_layout(["auto"])
    assert calls == [("mkcrew", "even-horizontal")]                # 3 panes on a wide screen -> a row
    assert teamconfig.load_layout(tmp_path) == "even-horizontal"
    assert "auto ->" in capsys.readouterr().out


def test_cmd_add_main_vertical_core_no_files(monkeypatch, tmp_path):
    """`mk add` with the (default, NORMAL) main-vertical template builds a live CORE pane and carries NO
    files-IDE. _main_vertical_layout's cells are lead, core, workers, so panes are created lead -> core ->
    workers (core SECOND). The final select-layout is a custom geometry string, not the bare
    'main-vertical' preset that would dump the core on the right with the workers."""
    from mkcrew import cli, layouts, teamconfig, frozen
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)
    monkeypatch.setattr(teamconfig, "build_team",
                        lambda count, providers=None, models=None, efforts=None:
                        [{"role": r, "model": "m", "provider": "claude"}
                         for r in ["main", "worker1", "worker2"][:count]])
    monkeypatch.setattr(layouts, "_launch", lambda a, project: ["agent", a["role"]])
    monkeypatch.setattr(frozen, "core_view_cmd", lambda p, orient="v": ["core-stub"])
    monkeypatch.setattr(frozen, "files_view_cmd", lambda p: ["files-stub"])
    calls = []
    class FakeMux:
        def __init__(self):
            self._n = 0
        def new_window(self, s, w, cmd, cwd=None):
            self._n += 1; calls.append(("split", cmd)); return f"%{self._n}"
        def split_window(self, t, cmd, vertical=True, size=None):
            self._n += 1; calls.append(("split", cmd)); return f"%{self._n}"
        def set_pane_title(self, t, title):
            pass
        def select_layout(self, t, layout="tiled"):
            calls.append(("layout", layout))
        def window_size(self, t):
            return (250, 60)
    monkeypatch.setattr(cli, "PsmuxBackend", FakeMux)
    cli.cmd_add([str(tmp_path), "--agents", "3", "--template", "main-vertical"])
    created = [c[1] for c in calls if c[0] == "split"]
    assert ["files-stub"] not in created                 # NORMAL template -> no files-IDE pane
    assert created.count(["core-stub"]) == 1             # exactly one live core pane
    assert created[1] == ["core-stub"]                   # core created SECOND (before the workers)
    assert created[0][0] == "agent" and created[0][1].endswith(".main")   # lead created FIRST
    assert sum(1 for c in created if c[0] == "agent") == 3                 # all 3 agents created
    geom = [c[1] for c in calls if c[0] == "layout"][-1]
    assert geom != "main-vertical" and "{" in geom and "[" in geom         # custom geometry, not the preset


def _add_recording_mux(monkeypatch, providers):
    """Stub cmd_add with a live session + a mux that records every pane creation / select-layout in
    order. `providers` is the per-agent provider list; build_team yields one agent per provider.
    Returns the shared `calls` list (entries: ('split', cmd) | ('layout', layout))."""
    from mkcrew import cli, layouts, teamconfig, frozen
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)
    roles = ["main", "worker1", "worker2", "worker3", "reviewer"]
    monkeypatch.setattr(teamconfig, "build_team",
                        lambda count, providers=None, models=None, efforts=None:
                        [{"role": roles[i], "model": "m", "provider": (providers[i] if providers else "claude")}
                         for i in range(count)])
    monkeypatch.setattr(layouts, "_launch", lambda a, project: ["agent", a["role"], a.get("provider")])
    monkeypatch.setattr(frozen, "core_view_cmd", lambda p, orient="v": ["core-stub"])
    monkeypatch.setattr(frozen, "files_view_cmd", lambda p: ["files-stub"])
    calls = []
    class FakeMux:
        def __init__(self):
            self._n = 0
        def new_window(self, s, w, cmd, cwd=None):
            self._n += 1; calls.append(("split", cmd)); return f"%{self._n}"
        def split_window(self, t, cmd, vertical=True, size=None):
            self._n += 1; calls.append(("split", cmd)); return f"%{self._n}"
        def set_pane_title(self, t, title):
            pass
        def select_layout(self, t, layout="tiled"):
            calls.append(("layout", layout))
        def window_size(self, t):
            return (250, 60)
    monkeypatch.setattr(cli, "PsmuxBackend", FakeMux)
    return calls


def test_cmd_add_rebalances_between_splits_and_spawns_all_agents(monkeypatch, tmp_path):
    """ROOT-CAUSE regression for the lost/cramped panes (#1/#10/#12): cmd_add must REBALANCE ('tiled')
    after every split so the next split has room. Without it psmux halves the active pane each split until
    a split fails and cmd_add aborts mid-build -> agents/files go missing. Verifies ALL N agent panes spawn
    -- including the multi-codex case (1 claude + 3 codex) that was losing panes -- and that a 'tiled'
    rebalance immediately follows every split."""
    from mkcrew import cli
    providers = ["claude", "codex", "codex", "codex"]
    calls = _add_recording_mux(monkeypatch, providers)
    cli.cmd_add([str(tmp_path), "--agents", "4",
                     "--providers", ",".join(providers), "--template", "main-vertical"])
    created = [c[1] for c in calls if c[0] == "split"]
    assert ["files-stub"] not in created                            # NORMAL main-vertical -> no files pane
    assert created.count(["core-stub"]) == 1 and created[1] == ["core-stub"]   # one core pane, created SECOND
    assert sum(1 for c in created if c[0] == "agent") == 4           # ALL 4 agents spawned (not fewer)
    assert sum(1 for c in created if len(c) > 2 and c[2] == "codex") == 3   # the 3 codex agents are present
    # the FIX: every split EXCEPT the first (the lead's new_window) is immediately followed by a rebalance
    for i, c in enumerate(calls):
        if c[0] == "split" and i > 0:
            assert calls[i + 1] == ("layout", "tiled"), f"split at index {i} not rebalanced"
    final = [c[1] for c in calls if c[0] == "layout"][-1]
    assert final != "main-vertical" and "{" in final and "[" in final   # custom geometry applied at the end


def test_cmd_add_tiled_grids_agents_and_core_no_files(monkeypatch, tmp_path):
    """`mk add --template tiled` (GRID) builds an even grid of the agents + a live CORE pane via the
    DETERMINISTIC _tiled_layout string (not the bare 'tiled' preset as the final layout) and carries NO
    files-IDE. Valid checksum, one cell per pane at 4 agents + core."""
    import re
    from mkcrew import cli, layouts
    calls = _add_recording_mux(monkeypatch, ["claude"] * 4)
    cli.cmd_add([str(tmp_path), "--agents", "4", "--template", "tiled"])
    created = [c[1] for c in calls if c[0] == "split"]
    assert ["files-stub"] not in created                             # no files-IDE (NORMAL/plain template)
    assert sum(1 for c in created if c[0] == "agent") == 4           # all 4 agents spawned
    assert created.count(["core-stub"]) == 1 and created[-1] == ["core-stub"]   # core created LAST
    final = [c[1] for c in calls if c[0] == "layout"][-1]
    assert final != "tiled"                                          # a computed grid string, not the preset
    csum, geom = final.split(",", 1)
    assert layouts._layout_csum(geom) == csum                        # valid tmux checksum
    assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == 5         # 4 agents + core = 5 grid cells


def test_cmd_add_even_horizontal_core_no_files(monkeypatch, tmp_path):
    """`mk add --template even-horizontal` (NORMAL SIDE-BY-SIDE) builds the tailored _sidebyside_core_layout:
    the agents in a side-by-side row + a full-width core strip, and carries NO files-IDE. Panes are created
    lead -> workers -> core (core LAST, matching the custom layout's cells), every split rebalanced to
    'tiled' so all panes spawn, and the FINAL select-layout is the custom geometry string -- NOT the bare
    psmux 'even-horizontal' preset."""
    import re
    from mkcrew import cli, layouts
    calls = _add_recording_mux(monkeypatch, ["claude"] * 3)
    cli.cmd_add([str(tmp_path), "--agents", "3", "--template", "even-horizontal"])
    created = [c[1] for c in calls if c[0] == "split"]
    assert ["files-stub"] not in created                            # no files-IDE (NORMAL template)
    assert sum(1 for c in created if c[0] == "agent") == 3           # all 3 agents spawned
    assert created.count(["core-stub"]) == 1 and created[-1] == ["core-stub"]     # core created LAST
    assert created[0][0] == "agent" and created[0][1].endswith(".main")           # lead created FIRST
    # the FIX: every split EXCEPT the lead's new_window is immediately followed by a 'tiled' rebalance
    for i, c in enumerate(calls):
        if c[0] == "split" and i > 0:
            assert calls[i + 1] == ("layout", "tiled"), f"split at index {i} not rebalanced"
    final = [c[1] for c in calls if c[0] == "layout"][-1]
    assert final != "even-horizontal" and "[" in final              # tailored geometry, not the bare preset
    csum, geom = final.split(",", 1)
    assert layouts._layout_csum(geom) == csum                        # valid tmux checksum
    assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == 4         # 3 agents + core = 4 cells


def test_cmd_add_lead_left_ide_files_no_core(monkeypatch, tmp_path):
    """`mk add --template lead-left-ide` (the EXPERIMENTAL template) builds the files-IDE LEAD-LEFT via
    _main_vertical_with_files: a files-IDE pane and NO core pane (the registry's includes_files_ide=True
    branch). Panes are created lead -> workers -> files (files LAST, matching the layout's cells); the
    final select-layout is the custom files-IDE geometry, not a bare preset."""
    import re
    from mkcrew import cli, layouts
    calls = _add_recording_mux(monkeypatch, ["claude"] * 3)
    cli.cmd_add([str(tmp_path), "--agents", "3", "--template", "lead-left-ide"])
    created = [c[1] for c in calls if c[0] == "split"]
    assert ["core-stub"] not in created                             # EXPERIMENTAL -> no core pane (files-IDE carries it)
    assert sum(1 for c in created if c[0] == "agent") == 3          # all 3 agents spawned
    assert created.count(["files-stub"]) == 1 and created[-1] == ["files-stub"]   # files created LAST
    assert created[0][0] == "agent" and created[0][1].endswith(".main")           # lead created FIRST
    final = [c[1] for c in calls if c[0] == "layout"][-1]
    assert final not in ("lead-left-ide", "main-vertical") and "{" in final        # custom files-IDE geometry
    csum, geom = final.split(",", 1)
    assert layouts._layout_csum(geom) == csum                        # valid tmux checksum
    assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == 4         # 3 agents + files = 4 cells
    assert "x60,140,0," in geom                                      # files: full-height right column


class _AddMux:
    """Minimal psmux stand-in for cmd_add: records pane-creation commands, ignores geometry."""
    def __init__(self):
        self._n = 0
        self.created = []
    def new_window(self, s, w, cmd, cwd=None):
        self._n += 1; self.created.append(cmd); return f"%{self._n}"
    def split_window(self, t, cmd, vertical=True, size=None):
        self._n += 1; self.created.append(cmd); return f"%{self._n}"
    def set_pane_title(self, t, title):
        pass
    def select_layout(self, t, layout="tiled"):
        pass
    def window_size(self, t):
        return (250, 60)


def _stub_add(monkeypatch):
    """Common cmd_add stubs: live session, stubbed launch/core/files, captured build_team args."""
    from mkcrew import cli, layouts, teamconfig, frozen
    captured = {}
    def fake_build_team(count, providers=None, models=None, efforts=None):
        captured.update(count=count, providers=providers, models=models, efforts=efforts)
        return [{"role": r, "model": "m", "provider": "claude"}
                for r in ["main", "worker1", "worker2", "reviewer"][:count]]
    monkeypatch.setattr(teamconfig, "build_team", fake_build_team)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)
    monkeypatch.setattr(layouts, "_launch", lambda a, project: ["agent", a["role"]])
    monkeypatch.setattr(frozen, "core_view_cmd", lambda p, orient="v": ["core-stub"])
    monkeypatch.setattr(frozen, "files_view_cmd", lambda p: ["files-stub"])
    monkeypatch.setattr(cli, "PsmuxBackend", _AddMux)
    return captured


def test_cmd_add_per_agent_efforts(monkeypatch, tmp_path):
    """`mk add --efforts e1,..,eN` is a per-agent comma list (like --models) -> build_team(efforts=[...]);
    a single --effort replicates across agents (back-compat)."""
    from mkcrew import cli
    captured = _stub_add(monkeypatch)
    ws1 = tmp_path / "ws1"; ws1.mkdir()
    cli.cmd_add([str(ws1), "--agents", "3", "--efforts", "low,medium,high", "--template", "main-vertical"])
    assert captured["efforts"] == ["low", "medium", "high"]

    ws2 = tmp_path / "ws2"; ws2.mkdir()
    cli.cmd_add([str(ws2), "--agents", "2", "--effort", "max", "--template", "tiled"])
    assert captured["efforts"] == ["max", "max"]       # single --effort -> replicated per agent


def test_cmd_add_efforts_pad_and_truncate(monkeypatch, tmp_path):
    """`--efforts` is padded (with the single --effort/default) and truncated to the agent count."""
    from mkcrew import cli
    captured = _stub_add(monkeypatch)
    ws = tmp_path / "padws"; ws.mkdir()
    cli.cmd_add([str(ws), "--agents", "3", "--effort", "high", "--efforts", "low", "--template", "tiled"])
    assert captured["efforts"] == ["low", "high", "high"]   # 1 given -> padded to 3 with the single --effort


def test_cmd_add_force_overwrites_existing_mkcrew(monkeypatch, tmp_path):
    """`mk add` persists `<folder>/.mkcrew/team.config` (resumable via `mk open`) and refuses to clobber an
    existing setup unless --force; --force overwrites it with the new team."""
    import json, pytest
    from mkcrew import cli, teamconfig
    teamconfig.write_team(tmp_path, teamconfig.build_team(5), "hub")    # a pre-existing setup
    _stub_add(monkeypatch)                                              # (re-stubs build_team after the seed)

    with pytest.raises(SystemExit):                                    # no --force -> refuse
        cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "tiled"])
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert len(data["agents"]) == 5 and data["layout"] == "hub"        # untouched

    cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "tiled", "--force"])   # --force -> overwrite
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"
    assert [a["role"] for a in data["agents"]] == ["main", "worker1"]  # new team, PLAIN roles (resumable)


# ---------------------------------------------------------------------------
# FIX #3: one cockpit per directory — cockpit.lock + os-level liveness
# ---------------------------------------------------------------------------

def test_cockpit_live_at_detects_liveness(tmp_path, monkeypatch):
    """_cockpit_live_at: a live-pid lock -> True; a dead-pid (stale) lock -> False; a missing or
    garbage lock -> False (all safe to clobber)."""
    from mkcrew import cli
    lock = tmp_path / ".mkcrew" / "cockpit.lock"
    lock.parent.mkdir(parents=True)
    assert cli._cockpit_live_at(tmp_path) is False        # no lock at all
    lock.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: True)
    assert cli._cockpit_live_at(tmp_path) is True          # recorded pid still running
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: False)
    assert cli._cockpit_live_at(tmp_path) is False         # stale lock from a dead pid
    lock.write_text("not-a-pid", encoding="utf-8")
    assert cli._cockpit_live_at(tmp_path) is False         # garbage lock


def test_cmd_add_refuses_live_cockpit_even_with_force(monkeypatch, tmp_path):
    """FIX #3: a directory whose OWN cockpit is LIVE is refused even with --force — clobbering a
    running cockpit's config breaks its live agents. The existing team.config is left untouched."""
    import json, pytest
    from mkcrew import cli, teamconfig
    teamconfig.write_team(tmp_path, teamconfig.build_team(5), "hub")       # the live cockpit's config
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)
    monkeypatch.setattr(cli, "_cockpit_live_at", lambda p: True)       # ...and the cockpit is LIVE
    monkeypatch.setattr(cli, "PsmuxBackend", _AddMux)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "tiled", "--force"])
    msg = str(exc.value)
    assert "already running" in msg and "mk open" in msg
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert len(data["agents"]) == 5 and data["layout"] == "hub"           # untouched despite --force


def test_cmd_add_stale_lock_does_not_block(monkeypatch, tmp_path):
    """A stale lock (its pid is dead) is ignored: cmd_add proceeds and writes the workspace config."""
    import json
    from mkcrew import cli
    lock = tmp_path / ".mkcrew" / "cockpit.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("999999", encoding="utf-8")                            # a long-dead pid
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: False)          # ...confirmed not running
    _stub_add(monkeypatch)
    cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "tiled", "--force"])
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"                                       # proceeded despite the stale lock


def test_cockpit_live_at_trusts_live_project_marker(tmp_path, monkeypatch):
    """ROOT-CAUSE regression (add-own-dir bug): cockpit.lock records the DAEMON's pid, and the daemon
    can die/crash while the psmux session (the cockpit the user is typing in) lives on — measured live:
    the lock pid was dead, `_cockpit_live_at` said False, and `mk add` clobbered the live cockpit's own
    directory into duplicate tabs. The live-cockpit project marker (cockpit_project.txt, rewritten by
    every `mk start`) must count as live-at-this-dir even when the pid lock is stale or missing."""
    from mkcrew import cli, config
    marker = tmp_path / "cockpit_project.txt"
    monkeypatch.setattr(config, "cockpit_project_file", lambda: marker)
    proj = tmp_path / "ws"
    proj.mkdir()
    assert cli._cockpit_live_at(proj) is False                 # no marker, no lock -> not live
    marker.write_text(str(proj), encoding="utf-8")
    assert cli._cockpit_live_at(proj) is True                  # marker names THIS dir -> live (no lock needed)
    lock = proj / ".mkcrew" / "cockpit.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("13032", encoding="utf-8")                 # the measured field state: dead daemon pid...
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: False)
    assert cli._cockpit_live_at(proj) is True                  # ...but the marker still says live HERE
    marker.write_text(str(tmp_path / "other"), encoding="utf-8")
    assert cli._cockpit_live_at(proj) is False                 # marker elsewhere -> falls back to the dead lock


def test_cmd_add_refuses_duplicate_window_name(monkeypatch, tmp_path):
    """Duplicate-tab guard: psmux resolves window targets BY NAME to the FIRST match, so adding a
    workspace whose name is already a tab would route every split/select-layout into the OLD window —
    mangling it (psmux drops the excess panes on the fixed-cell final layout: the measured 'agent
    panes but no core' tab) and leaving the new tab as one bare pane. cmd_add must refuse up front,
    creating NOTHING and leaving any existing config untouched."""
    import pytest
    from mkcrew import cli
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)

    class DupMux(_AddMux):
        def window_names(self, session):
            return ["main", tmp_path.name]                     # this workspace's name is already a tab

    monkeypatch.setattr(cli, "PsmuxBackend", DupMux)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "main-vertical", "--force"])
    assert "already exists" in str(exc.value)
    assert not (tmp_path / ".mkcrew" / "team.config").exists()  # refused BEFORE writing/creating anything


def test_cmd_add_fresh_window_name_passes_duplicate_guard(monkeypatch, tmp_path):
    """The duplicate-tab guard only refuses a COLLIDING name: `--name` picks a fresh tab name and the
    add proceeds normally (window created, config written)."""
    import json
    from mkcrew import cli
    _stub_add(monkeypatch)

    class NamedMux(_AddMux):
        def window_names(self, session):
            return ["main", tmp_path.name]                     # the folder's default name IS taken...

    monkeypatch.setattr(cli, "PsmuxBackend", NamedMux)
    cli.cmd_add([str(tmp_path), "--agents", "2", "--template", "tiled", "--name", "fresh"])
    data = json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"                           # ...but --name fresh proceeds fine


def test_cmd_add_persists_workspace_name(monkeypatch, tmp_path):
    """FIX #4: `mk add --name X` persists the workspace identity (read back via load_name)."""
    from mkcrew import cli, teamconfig
    _stub_add(monkeypatch)
    ws = tmp_path / "wsname"; ws.mkdir()
    cli.cmd_add([str(ws), "--agents", "2", "--template", "tiled", "--name", "Testing"])
    assert teamconfig.load_name(ws) == "Testing"


def test_cmd_start_writes_cockpit_lock(monkeypatch, tmp_path):
    """cmd_start records the spawned daemon's pid in <project>/.mkcrew/cockpit.lock (FIX #3 marker)."""
    from mkcrew import cli, layouts, teamconfig, sessions, config as _cfg, agent as _agent
    proj = tmp_path / "proj"; proj.mkdir()
    monkeypatch.setattr(cli, "_project_dir", lambda: proj)
    for fn in ("ensure_project_hook", "ensure_opencode_plugin",
               "ensure_project_claude_md", "ensure_project_agents_md"):
        monkeypatch.setattr(_agent, fn, lambda *a: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None, "pid": 4321})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("u", True))
    monkeypatch.setattr(layouts, "get", lambda name: (lambda *a, **k: {"main": "%1"}))
    monkeypatch.setattr(layouts, "apply_chrome", lambda mux, name=None: None)
    fake_mux = type("M", (), {
        "kill_server": lambda self: None, "capture": lambda self, p: "trust",
        "send_enter": lambda self, p: None, "send_line": lambda self, p, t: None})()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    cli.cmd_start(["--no-attach"])
    assert (proj / ".mkcrew" / "cockpit.lock").read_text(encoding="utf-8") == "4321"


def test_cmd_kill_clears_cockpit_lock(monkeypatch, tmp_path):
    """cmd_kill removes the live cockpit's per-workspace lock (project read from the cockpit marker)."""
    from mkcrew import cli, config as _cfg
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    proj = tmp_path / "proj"; (proj / ".mkcrew").mkdir(parents=True)
    (proj / ".mkcrew" / "cockpit.lock").write_text("4321", encoding="utf-8")
    _cfg.cockpit_project_file().write_text(str(proj), encoding="utf-8")
    monkeypatch.setattr(cli, "PsmuxBackend",
                        lambda: type("M", (), {"kill_server": lambda self: None})())
    monkeypatch.setattr(cli, "_kill_daemon", lambda: None)
    cli.cmd_kill([])
    assert not (proj / ".mkcrew" / "cockpit.lock").exists()


def test_cmd_start_threads_workspace_name_into_lead_prompt(monkeypatch, tmp_path):
    """FIX #4: `mk start --name X` persists the workspace identity and threads it into the lead
    briefing via prompts.lead_prompt(..., name=X)."""
    from mkcrew import cli, layouts, teamconfig, sessions, prompts, config as _cfg, agent as _agent
    proj = tmp_path / "proj"; proj.mkdir()
    monkeypatch.setattr(cli, "_project_dir", lambda: proj)
    for fn in ("ensure_project_hook", "ensure_opencode_plugin",
               "ensure_project_claude_md", "ensure_project_agents_md"):
        monkeypatch.setattr(_agent, fn, lambda *a: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None, "pid": 1})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_post", lambda *a, **k: {})
    monkeypatch.setattr(teamconfig, "load_team", lambda p: [{"role": "main", "model": "m", "provider": "claude"}])
    monkeypatch.setattr(teamconfig, "load_layout", lambda p: "hub")
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("u", True))
    monkeypatch.setattr(layouts, "get", lambda name: (lambda *a, **k: {"main": "%1"}))
    chrome = {}
    monkeypatch.setattr(layouts, "apply_chrome", lambda mux, name=None: chrome.update(name=name))
    seen = {}
    monkeypatch.setattr(prompts, "lead_prompt",
                        lambda mk, team=None, mode="standard", provider="claude", name=None:
                        seen.update(name=name) or "PROMPT")
    renamed = {}
    fake_mux = type("M", (), {
        "kill_server": lambda self: None, "capture": lambda self, p: "trust",
        "send_enter": lambda self, p: None, "send_line": lambda self, p, t: None,
        "rename_window": lambda self, target, nm: renamed.update(target=target, name=nm)})()
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: fake_mux)
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)
    cli.cmd_start(["--no-attach", "--name", "Testing"])
    assert seen.get("name") == "Testing"                  # threaded into the lead briefing
    assert teamconfig.load_name(proj) == "Testing"        # and persisted for next start
    assert chrome.get("name") == "Testing"                # threaded into the terminal chrome badge
    assert renamed.get("name") == "Testing"               # + the primary window tab named after the workspace
    assert renamed.get("target", "").endswith(":0")       # window 0 (the lead's window)


def test_open_and_workspaces_registered_in_commands():
    from mkcrew import cli
    assert "open" in cli.COMMANDS and "workspaces" in cli.COMMANDS


def test_cmd_open_usage_when_no_folder(capsys):
    import pytest
    from mkcrew import cli
    with pytest.raises(SystemExit) as exc:
        cli.cmd_open([])
    assert "usage" in str(exc.value).lower()


def test_cmd_open_exits_when_no_config(tmp_path):
    """`mk open <folder>` refuses a folder with no `.mkcrew/team.config` (nothing to resume)."""
    import pytest
    from mkcrew import cli
    with pytest.raises(SystemExit) as exc:
        cli.cmd_open([str(tmp_path)])
    assert ".mkcrew" in str(exc.value)


def test_cmd_open_resumes_from_folder_config(monkeypatch, tmp_path):
    """`mk open <folder>` resumes a workspace from its OWN `.mkcrew` config (no re-setup): it reuses
    cmd_start's machinery pointed at <folder>, so the layout it builds comes from the folder's config."""
    import pytest
    from mkcrew import cli, layouts, teamconfig, config as _cfg, agent as _agent, sessions
    teamconfig.write_team(tmp_path, [{"role": "main", "model": "m", "provider": "claude"}], "tiled")
    for fn in ("ensure_project_hook", "ensure_opencode_plugin",
               "ensure_project_claude_md", "ensure_project_agents_md"):
        monkeypatch.setattr(_agent, fn, lambda *a: None)
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: False)
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"poll": lambda self: None})())
    monkeypatch.setattr(cli, "_clear_stale_daemon_files", lambda: None)
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "mkd.port")
    (tmp_path / "mkd.port").write_text("1", encoding="utf-8")
    monkeypatch.setattr(_cfg, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(sessions, "ensure", lambda p, role: ("u", True))
    monkeypatch.setattr(cli, "PsmuxBackend",
                        lambda: type("M", (), {"kill_server": lambda self: None})())
    chosen = {}
    class _Stop(Exception):
        pass
    def fake_get(name):
        chosen["name"] = name
        raise _Stop()                       # short-circuit before the trust-poll / prompt tail
    monkeypatch.setattr(layouts, "get", fake_get)
    with pytest.raises(_Stop):
        cli.cmd_open([str(tmp_path), "--no-attach"])
    assert chosen["name"] == "tiled"        # built the FOLDER's saved layout, not the default 'hub'


def test_cmd_workspaces_lists_configured_setups(monkeypatch, tmp_path, capsys):
    """`mk workspaces` emits JSONL {name,path} for each `.mkcrew/team.config` found under the roots."""
    import json
    from mkcrew import cli, teamconfig
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))         # keep runtime/cockpit files inside tmp
    teamconfig.write_team(tmp_path / "proj1", teamconfig.build_team(2), "tiled")
    teamconfig.write_team(tmp_path / "proj2", teamconfig.build_team(3), "hub")
    (tmp_path / "noproj").mkdir()                             # a dir without .mkcrew -> not listed
    monkeypatch.setattr(cli, "_workspace_roots", lambda: [tmp_path])
    cli.cmd_workspaces([])
    rows = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    by_name = {r["name"]: r["path"] for r in rows}
    assert "proj1" in by_name and "proj2" in by_name
    assert "noproj" not in by_name
    assert by_name["proj1"].replace("\\", "/").endswith("proj1")


def test_cmd_relayout_rebuilds_and_resumes(tmp_path, monkeypatch, capsys):
    """`mk relayout <name>` from the launch terminal persists the layout then kill->start->attach to
    rebuild (sessions resume); refuses (no rebuild) when run INSIDE the cockpit."""
    from mkcrew import teamconfig
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    teamconfig.set_layout(tmp_path, "hub")
    seq = []
    monkeypatch.setattr(cli, "cmd_kill", lambda a: seq.append("kill"))
    monkeypatch.setattr(cli, "cmd_start", lambda a: seq.append("start"))
    monkeypatch.setattr(cli, "cmd_attach", lambda a: seq.append("attach"))
    # inside the cockpit -> refuses, no rebuild
    monkeypatch.setenv("TMUX", "/x/sock,1,0")
    cli.cmd_relayout(["pages"])
    assert seq == [] and "detach" in capsys.readouterr().out.lower()
    # from the launch terminal -> rebuilds in order + persists the new layout
    monkeypatch.delenv("TMUX", raising=False)
    cli.cmd_relayout(["pages"])
    assert seq == ["kill", "start", "attach"]
    assert teamconfig.load_layout(tmp_path) == "pages"


# ---------------------------------------------------------------------------
# Add-workspace QA: BUG-1 (--agents guard) + BUG-2 (mixed-provider message)
# ---------------------------------------------------------------------------

def test_cmd_add_non_numeric_agents_exits_cleanly(monkeypatch, tmp_path):
    """BUG-1: `mk add <dir> --agents abc` (or 3.5) exits cleanly with a friendly message — NOT an
    uncaught ValueError/traceback — guarded like cmd_init's --agents parse."""
    import pytest
    from mkcrew import cli
    monkeypatch.setattr(cli, "_session_exists", lambda mux, s: True)
    monkeypatch.setattr(cli, "PsmuxBackend", _AddMux)
    for i, bad in enumerate(("abc", "3.5")):
        ws = tmp_path / f"ws{i}"; ws.mkdir()
        with pytest.raises(SystemExit) as exc:
            cli.cmd_add([str(ws), "--agents", bad, "--template", "tiled"])
        assert "--agents must be a number" in str(exc.value)


def test_cmd_add_mixed_team_message_names_real_providers(monkeypatch, tmp_path, capsys):
    """BUG-2: the success line reflects the ACTUAL per-agent providers — a mixed team must not read
    "2 claude agent(s)" off the singular --provider default."""
    from mkcrew import cli
    _add_recording_mux(monkeypatch, ["claude", "codex"])
    cli.cmd_add([str(tmp_path), "--agents", "2",
                     "--providers", "claude,codex", "--template", "main-vertical"])
    out = capsys.readouterr().out
    assert "added workspace" in out
    assert "claude" in out and "codex" in out          # both real providers named
    assert "2 claude" not in out                        # not the bogus singular-default miscount


def test_cmd_mode_shows_sets_and_validates(tmp_path, monkeypatch, capsys):
    """`mk mode` shows current + options; `mk mode thorough` persists (daemon down -> next-start
    note, no crash); an unknown mode exits with the valid list."""
    import pytest
    from mkcrew import teamconfig
    monkeypatch.setattr(cli, "_project_dir", lambda: tmp_path)
    monkeypatch.setattr("mkcrew.config.port_file", lambda: tmp_path / "no-such-port")
    cli.cmd_mode([])
    out = capsys.readouterr().out
    assert "standard" in out and "thorough" in out and "plan-first" in out
    cli.cmd_mode(["thorough"])
    assert teamconfig.load_mode(tmp_path) == "thorough"
    assert "next" in capsys.readouterr().out                # daemon not running -> next-start note
    with pytest.raises(SystemExit):
        cli.cmd_mode(["warp9"])
