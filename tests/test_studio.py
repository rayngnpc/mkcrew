from pathlib import Path
from mkcrew import studio


def test_detect_clis_reports_known_providers(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/x/" + name if name in ("claude", "gemini") else None)
    clis = studio.detect_clis()
    assert clis == {"claude": True, "codex": False, "opencode": False, "antigravity": False}
    assert "gemini" not in clis     # removed from the provider list


def test_detect_clis_maps_antigravity_to_the_agy_binary(monkeypatch):
    """antigravity is detected via its actual binary `agy`, not the provider name."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/x/agy" if name == "agy" else None)
    assert studio.detect_clis()["antigravity"] is True


def test_list_templates_reflects_registry():
    keys = [t["key"] for t in studio.list_templates()]
    assert "main-vertical" in keys and "even-horizontal" in keys   # the trimmed supported set
    assert all("name" in t for t in studio.list_templates())


def test_list_modes_has_starter_set():
    keys = [m["key"] for m in studio.list_modes()]
    assert keys == ["standard", "fast", "thorough", "plan-first", "architect",
                "warroom", "chief", "venture"]                      # postures, in display order
    assert keys[0] == "standard"                                    # default stays first (UI preselects [0])


def test_template_meta_has_fit_ranges():
    """Every template exposes the agent-range + screen hints the Studio gallery sorts cards by."""
    for t in studio.list_templates():
        assert t["min"] >= 1 and t["max"] >= t["min"], t
        assert t["screen"] in (None, "wide", "tall"), t


import json as _json


def test_read_config_defaults_when_absent(tmp_path):
    cfg = studio.read_config(tmp_path)
    assert cfg["layout"] == "main-vertical"
    assert len(cfg["agents"]) == 8          # default team, NOT written to disk
    assert not (tmp_path / ".mkcrew" / "team.config").exists()


def test_save_config_writes_team(tmp_path):
    res = studio.save_config(tmp_path, count=3, layout="tiled", providers=["claude", "gemini", "codex"])
    assert res["ok"] is True
    data = _json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["layout"] == "tiled"
    assert [a["role"] for a in data["agents"]] == ["main", "worker1", "worker2"]
    assert data["agents"][2]["provider"] == "codex"


def test_save_config_persists_model_and_effort(tmp_path):
    """Per-agent model + thinking from the modal land in team.config."""
    studio.save_config(tmp_path, count=2, layout="tiled", providers=["claude", "codex"],
                       models=["claude-opus-4-8", "gpt-5-codex"], efforts=["max", "high"])
    data = _json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert data["agents"][1]["model"] == "gpt-5-codex" and data["agents"][1]["effort"] == "high"


def test_provider_catalog_has_models_and_thinking():
    """The default catalog: model suggestions for every provider; thinking only for claude + codex."""
    assert all(p in studio._PROVIDER_MODELS for p in ("claude", "codex", "opencode", "antigravity"))
    assert set(studio._PROVIDER_THINKING) == {"claude", "codex", "opencode"}   # antigravity = no toggle
    # opencode covers both zen (opencode/<id>) and go (opencode-go/<id>)
    oc = studio._PROVIDER_MODELS["opencode"]
    assert any(m.startswith("opencode/") for m in oc) and any(m.startswith("opencode-go/") for m in oc)


def test_catalog_defaults_unless_saved_then_reset(tmp_path, monkeypatch):
    """No file -> code defaults (default updates always reach non-customisers; no stale seed). A saved
    edit persists; {'reset': true} deletes the file -> back to defaults."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cat = studio.load_catalog()
    assert "codex" in cat["models"] and not (tmp_path / "mkcrew" / "models.json").exists()   # NOT seeded
    studio.save_catalog({"models": {"codex": ["x"]}, "thinking": {}})
    assert studio.load_catalog()["models"]["codex"] == ["x"]                 # explicit edit persists
    studio.save_catalog({"reset": True})
    assert not (tmp_path / "mkcrew" / "models.json").exists()                # reset deletes the file
    assert studio.load_catalog()["models"]["codex"] == studio._PROVIDER_MODELS["codex"]   # -> defaults


def test_read_config_surfaces_workspace_name(tmp_path):
    """FIX #4: read_config surfaces the workspace identity — the persisted name when set, else the
    folder name as a fallback (never a bare 'main')."""
    from mkcrew import teamconfig
    # No persisted name -> the folder name is the label
    assert studio.read_config(tmp_path)["name"] == tmp_path.name
    # A persisted name wins
    teamconfig.write_team(tmp_path, teamconfig.build_team(2), "tiled")
    teamconfig.set_name(tmp_path, "Testing")
    cfg = studio.read_config(tmp_path)
    assert cfg["name"] == "Testing"


def test_save_config_persists_workspace_name(tmp_path):
    """save_config persists a non-blank name and echoes the resolved label back; omitting it keeps
    the folder-name fallback and writes no name file."""
    from mkcrew import teamconfig
    res = studio.save_config(tmp_path, count=2, layout="tiled", name="Prod")
    assert res["name"] == "Prod"
    assert teamconfig.load_name(tmp_path) == "Prod"

    ws2 = tmp_path / "ws2"; ws2.mkdir()
    res2 = studio.save_config(ws2, count=2, layout="tiled")               # no name given
    assert res2["name"] == "ws2"                                          # folder-name fallback
    assert teamconfig.load_name(ws2) is None                             # nothing persisted


def test_is_session_running(monkeypatch):
    import subprocess as sp
    from mkcrew.psmux import PsmuxBackend
    monkeypatch.setattr(PsmuxBackend, "_run",
                        lambda self, *a: sp.CompletedProcess(a, 0, "", ""))
    assert studio.is_session_running() is True
    monkeypatch.setattr(PsmuxBackend, "_run",
                        lambda self, *a: sp.CompletedProcess(a, 1, "", ""))
    assert studio.is_session_running() is False


def test_launch_opens_cockpit_when_not_running(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio, "is_session_running", lambda: False)
    seen = {}
    monkeypatch.setattr(studio.subprocess, "Popen", lambda args, **k: seen.update(args=args))
    res = studio.launch("E:\\some\\project")
    assert res == {"ok": True, "was_running": False}
    # runs a .cmd SCRIPT via `cmd /k` (bat is the LAST arg; a `conhost.exe` prefix may precede it so
    # the cockpit opens in the classic console host where the adaptive font API works)
    args = seen["args"]
    assert args[-3] == "cmd" and args[-2] == "/k"
    assert args[0] in ("conhost.exe", "cmd")
    bat = Path(args[-1])
    assert bat.exists() and bat.suffix == ".cmd"
    text = bat.read_text(encoding="utf-8")
    assert 'cd /d "E:\\some\\project"' in text
    assert "start" in text and "attach" in text          # not running -> both


def test_launch_rebuilds_when_running(monkeypatch, tmp_path):
    """A running session is torn down and rebuilt so the just-saved config (new template/providers)
    actually applies: kill -> start -> attach, not attach-only."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio, "is_session_running", lambda: True)
    seen = {}
    monkeypatch.setattr(studio.subprocess, "Popen", lambda args, **k: seen.update(args=args))
    res = studio.launch("E:\\some\\project")
    assert res["was_running"] is True
    text = Path(seen["args"][-1]).read_text(encoding="utf-8")         # bat is the last arg (conhost-prefixed)
    assert "kill" in text and "start" in text and "attach" in text   # full rebuild applies the config


def test_launch_conflict_when_a_different_project_is_running(monkeypatch, tmp_path):
    """A live cockpit owned by ANOTHER project -> launch returns conflict and spawns NOTHING (no
    silent kill); force=True proceeds with the replace."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio, "is_session_running", lambda: True)
    from mkcrew import config
    config.cockpit_project_file().write_text("E:\\OTHER\\project", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(studio.subprocess, "Popen", lambda args, **k: seen.update(args=args))
    res = studio.launch("E:\\THIS\\project")
    assert res.get("conflict") is True and "args" not in seen          # blocked, nothing launched
    assert "OTHER" in res["running_project"]
    res2 = studio.launch("E:\\THIS\\project", force=True)
    assert res2["ok"] is True and "args" in seen                       # force replaces


def test_launch_same_project_running_rebuilds_no_conflict(monkeypatch, tmp_path):
    """Re-launching the SAME project that's live is a rebuild, not a conflict (no annoying prompt)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio, "is_session_running", lambda: True)
    from mkcrew import config
    config.cockpit_project_file().write_text("E:\\proj", encoding="utf-8")
    monkeypatch.setattr(studio.subprocess, "Popen", lambda args, **k: None)
    res = studio.launch("E:\\proj")
    assert res.get("conflict") is not True and res["ok"] is True


import urllib.request, threading


def _get(httpd, path):
    port = httpd.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read().decode()


def test_server_serves_api_and_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio, "detect_clis", lambda: {"claude": True, "codex": False, "gemini": False, "opencode": False})
    httpd = studio.make_server(project_dir=tmp_path)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        import json as J
        s, body = _get(httpd, "/api/clis")
        assert s == 200 and J.loads(body)["clis"]["claude"] is True and "claude" in J.loads(body)["models"]
        s, body = _get(httpd, "/api/templates"); assert s == 200 and any(x["key"] == "main-vertical" for x in J.loads(body)["templates"])
        s, body = _get(httpd, "/api/modes");     assert s == 200 and len(J.loads(body)["modes"]) == 8
        s, body = _get(httpd, "/");              assert s == 200 and "MKCREW Studio" in body
    finally:
        httpd.shutdown()


def test_pick_folder_sets_project_on_selection(tmp_path, monkeypatch):
    """pick_folder runs the native dialog; on a selection it set_project()s the chosen path."""
    import subprocess as sp
    target = tmp_path / "picked"; target.mkdir()
    monkeypatch.setattr(studio.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, str(target) + "\r\n", ""))
    res = studio.pick_folder()
    assert res["ok"] is True and res["path"] == str(target)
    assert studio.get_project() == str(target)


def test_pick_folder_cancel_returns_not_ok(monkeypatch):
    """A cancelled dialog (empty stdout) returns ok=False so the UI can no-op (keep current folder)."""
    import subprocess as sp
    monkeypatch.setattr(studio.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, 0, "", ""))
    assert studio.pick_folder()["ok"] is False


def test_list_dirs_lists_subdirectories(tmp_path):
    (tmp_path / "alpha").mkdir(); (tmp_path / "beta").mkdir(); (tmp_path / "f.txt").write_text("x")
    res = studio.list_dirs(str(tmp_path))
    names = [d["name"] for d in res["dirs"]]
    assert names == ["alpha", "beta"]          # dirs only, sorted, no files
    assert res["path"] == str(tmp_path)
    assert res["parent"] == str(tmp_path.parent)


def test_project_state_get_set(tmp_path):
    studio.set_project(str(tmp_path))
    assert studio.get_project() == str(tmp_path)


def test_server_project_and_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "proj").mkdir()
    httpd = studio.make_server(project_dir=tmp_path)
    import threading; threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import json as J, urllib.request
        port = httpd.server_address[1]
        # set project
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/project",
            data=J.dumps({"path": str(tmp_path / "proj")}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        assert J.loads(urllib.request.urlopen(req, timeout=5).read())["ok"] is True
        got = J.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/project", timeout=5).read())
        assert got["path"] == str(tmp_path / "proj")
        dirs = J.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dirs?path={tmp_path}", timeout=5).read())
        assert "proj" in [d["name"] for d in dirs["dirs"]]
    finally:
        httpd.shutdown()


def _post(httpd, path, obj):
    import json as J
    port = httpd.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        data=J.dumps(obj).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode()


def test_profiles_http_roundtrip_preserves_config(monkeypatch, tmp_path):
    """Save -> Load over HTTP: POST /api/profiles then GET /api/profiles reads the SAME config back,
    losslessly — count/layout/providers AND per-agent models/efforts, mode, and workspace name all
    survive the round-trip (the POST handler passes every field through to profiles.save)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    httpd = studio.make_server(project_dir=tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import json as J
        sent = {"name": "My Team", "count": 3, "layout": "tiled",
                "providers": ["claude", "codex", "opencode"],
                "models": ["claude-opus-4-8", "gpt-5.5", ""], "efforts": ["max", "high", ""],
                "mode": "fast", "wsName": "Prod"}
        s, _ = _post(httpd, "/api/profiles", sent); assert s == 200
        s, body = _get(httpd, "/api/profiles"); assert s == 200
        got = next(p for p in J.loads(body)["profiles"] if p["name"] == "My Team")
        assert got["count"] == 3 and got["layout"] == "tiled"
        assert got["providers"] == ["claude", "codex", "opencode"]
        assert got["models"] == ["claude-opus-4-8", "gpt-5.5", ""]     # per-agent fields survive the HTTP path
        assert got["efforts"] == ["max", "high", ""]
        assert got["mode"] == "fast" and got["wsName"] == "Prod"       # mode + workspace name survive
    finally:
        httpd.shutdown()


def test_profiles_storage_layer_roundtrips_full_config(tmp_path, monkeypatch):
    """The profiles STORE (profiles.save / list_profiles) is lossless for the COMPLETE config —
    per-agent models/efforts, mode, and the workspace name all survive. So a lossless save->load
    needs only the POST /api/profiles handler widened to pass those fields through to profiles.save."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew import profiles
    full = {"count": 4, "layout": "lead-left-ide", "mode": "fast",
            "providers": ["claude", "codex", "opencode", "claude"],
            "models": ["claude-opus-4-8", "gpt-5.5", "opencode/big-pickle", ""],
            "efforts": ["max", "high", "low", ""], "wsName": "Testing"}
    profiles.save("Complete", full)
    got = next(p for p in profiles.list_profiles() if p["name"] == "Complete")
    for key, val in full.items():
        assert got[key] == val, key


def test_serve_falls_back_to_free_port_when_default_is_denied(tmp_path, monkeypatch):
    """LIVE INCIDENT: Windows (winnat/Hyper-V) reserves moving TCP port blocks; when 8765 lands
    inside one, bind raises WinError 10013 and `mk studio` died with a traceback. serve() must
    fall back to an OS-picked port -- and the printed/opened URL always carries the REAL port."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    import socket, threading
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))                      # occupy a port -> same OSError family
    busy_port = blocker.getsockname()[1]
    opened = {}
    monkeypatch.setattr(studio.webbrowser, "open", lambda url: opened.setdefault("url", url))
    served = {}
    real_forever = studio.ThreadingHTTPServer.serve_forever
    def capture_and_stop(self, *a, **k):                # run serve() to its URL logic, then return
        served["port"] = self.server_address[1]
        self.server_close()
    monkeypatch.setattr(studio.ThreadingHTTPServer, "serve_forever", capture_and_stop)
    try:
        studio.serve(project_dir=tmp_path, port=busy_port, open_browser=True)
    finally:
        blocker.close()
        monkeypatch.setattr(studio.ThreadingHTTPServer, "serve_forever", real_forever)
    assert served["port"] != busy_port and served["port"] > 0      # fell back to a REAL free port
    assert opened["url"].endswith(f":{served['port']}")            # browser got the actual port


def test_load_accounts_filters_and_derives_labels(monkeypatch, tmp_path):
    """load_accounts() reads accounts.json, expands ~, drops entries with an unknown provider or no
    bin, and derives a label from the bin basename when absent. These become the Studio provider
    dropdown's account options (value = provider@bin, parsed back by teamconfig.build_team)."""
    import json
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew import config
    config.runtime_root().mkdir(parents=True, exist_ok=True)
    (config.runtime_root() / "accounts.json").write_text(json.dumps([
        {"label": "claudew (work)", "provider": "claude", "bin": "~/bin/claudew"},
        {"provider": "codex", "bin": "/x/codexw"},         # no label -> derived from basename
        {"provider": "bogus", "bin": "/x/y"},              # unknown provider -> dropped
        {"provider": "opencode"},                          # no bin -> dropped
    ]), encoding="utf-8")
    accts = studio.load_accounts()
    assert [a["provider"] for a in accts] == ["claude", "codex"]
    assert accts[0]["label"] == "claudew (work)"
    assert accts[0]["bin"].replace("\\", "/").endswith("/bin/claudew") and "~" not in accts[0]["bin"]     # ~ expanded
    assert accts[1]["label"] == "codex · codexw"                                       # derived


def test_pages_template_fits_six_agents_others_stay_four():
    """The 4->6 cap is per-template: pages (paged grids, <=6 agents/window by design) advertises 6 so
    the fit-sort steers big teams to it; single-window layouts stay honest at 4 (readability)."""
    from mkcrew import templates
    assert templates.get("pages").max_agents == 6
    for key in ("main-vertical", "even-horizontal", "lead-left-ide"):
        assert templates.get(key).max_agents == 4, key


def test_save_config_chief_mode_persists_planner_seat(tmp_path):
    """Studio save passes the mode into build_team, so a 4-agent chief/warroom team.config carries
    the planner in its last seat (what the roster chips + modal rows display via roleFor)."""
    studio.save_config(tmp_path, count=4, layout="pages", mode="chief",
                       providers=["claude", "claude", "codex", "antigravity"])
    data = _json.loads((tmp_path / ".mkcrew" / "team.config").read_text(encoding="utf-8"))
    assert [a["role"] for a in data["agents"]] == ["main", "worker1", "worker2", "planner"]
    assert data["agents"][3]["provider"] == "antigravity"
    assert data["mode"] == "chief"



def test_load_accounts_hides_bare_binary_accounts_from_picker(tmp_path, monkeypatch):
    """A bare-binary account (bin == the provider's own CLI, e.g. claude/"claude") is the SAME thing
    as the plain provider dropdown option -- hide it from the picker. It stays in accounts.json as the
    default:true anchor so a bare provider keeps resolving to PERSONAL (config.load_accounts returns
    it; only the Studio option list filters). agy counts as antigravity's bare binary."""
    import json as _json
    from mkcrew import config
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config.runtime_root().mkdir(parents=True, exist_ok=True)
    (config.runtime_root() / "accounts.json").write_text(_json.dumps([
        {"label": "Claude - personal", "provider": "claude", "bin": "claude", "default": True},
        {"label": "Claude - work", "provider": "claude", "bin": "C:/u/bin/claudew.cmd"},
        {"label": "Agy - personal", "provider": "antigravity", "bin": "agy", "default": True},
    ]), encoding="utf-8")
    labels = [a["label"] for a in studio.load_accounts()]
    assert labels == ["Claude - work"]                              # bare claude + bare agy hidden
    assert len(config.load_accounts()) == 3                        # resolution layer sees ALL
    assert config.default_account_bin("claude") == "claude"        # personal anchor intact
