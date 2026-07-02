from mkcrew import layouts


class FakeMux:
    def __init__(self):
        self.calls = []
        self._n = 0
        self.titles = {}
        self.options = {}
    def new_session(self, session, window, command):
        self.calls.append(("new_session", window)); self._n += 1
        return f"%{self._n}"
    def new_window(self, session, window, command):
        self.calls.append(("new_window", window)); self._n += 1
        return f"%{self._n}"
    def split_window(self, target, command, vertical=True, size=None):
        self.calls.append(("split_window", command[0], size)); self._n += 1
        return f"%{self._n}"
    def select_layout(self, target, layout="tiled"):
        self.calls.append(("select_layout", layout))
    def set_pane_title(self, target, title):
        self.titles[target] = title
    def select_pane(self, target):
        self.calls.append(("select_pane", target))
    def bind_key(self, key, *command):
        self.calls.append(("bind_key", key, command))
    def set_option(self, name, value):
        self.options[name] = value
    def window_size(self, target):
        return (250, 60)


def _team(*roles):
    return [{"role": r, "model": "m", "mode": "bypassPermissions",
             "effort": None, "provider": "claude"} for r in roles]


def test_tiled_call_sequence_and_panes(monkeypatch):
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    registered = []
    panes = layouts.tiled(mux, _team("main", "opus1"), "P",
                          lambda role, pid: registered.append((role, pid)), "mkcrew")
    kinds = [c[0] for c in mux.calls]
    assert kinds == ["new_session", "split_window", "select_layout", "split_window", "select_layout"]
    assert mux.calls[-2][1].endswith("mk-core-view.exe")     # core pane, full path
    assert mux.calls[-1] == ("select_layout", "tiled")
    assert set(panes) == {"main", "opus1"}
    assert ("main", "%1") in registered and ("opus1", "%2") in registered


def test_get_returns_builder_and_falls_back(capsys):
    assert layouts.get("tiled") is layouts.tiled
    assert layouts.get("nonsense") is layouts.tiled         # unknown -> tiled
    assert "tiled" in layouts.LAYOUTS


def test_even_layouts_registered(monkeypatch):
    """even-horizontal / even-vertical are both registered. even-vertical passes its psmux preset
    straight through (single-window, tall screens); even-horizontal now builds a CORE-ONLY custom
    geometry (a core strip under the agent row), so it NEVER passes the bare 'even-horizontal' preset."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    assert {"even-horizontal", "even-vertical"} <= set(layouts.LAYOUTS)
    mux = FakeMux()                                           # even-vertical: bare preset passed through
    layouts.even_vertical(mux, _team("main", "worker1", "worker2"), "P", lambda r, p: None, "mkcrew")
    assert ("select_layout", "even-vertical") in mux.calls
    mux = FakeMux()                                           # even-horizontal: custom geometry, never the preset
    layouts.even_horizontal(mux, _team("main", "worker1", "worker2"), "P", lambda r, p: None, "mkcrew")
    assert ("select_layout", "even-horizontal") not in mux.calls


def test_even_layouts_core_orientation(monkeypatch):
    """The core table style follows the core slice shape. even-vertical's slice is wide/short -> 'h'
    (tables side by side). even-horizontal now pins a wide/short core STRIP under the agent row -> 'h'."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    seen = []
    real = layouts._core_view_cmd
    monkeypatch.setattr(layouts, "_core_view_cmd",
                        lambda project, orient="v": (seen.append(orient), real(project, orient))[1])
    layouts.even_vertical(FakeMux(), _team("main", "w1"), "P", lambda r, p: None, "mkcrew")
    layouts.even_horizontal(FakeMux(), _team("main", "w1", "w2"), "P", lambda r, p: None, "mkcrew")
    assert seen == ["h", "h"]


def test_files_view_cmd_resolves():
    """_files_view_cmd builds a real launch command (regression: `frozen` must be in scope — the
    real fn is monkeypatched in the layout test, so this exercises it directly)."""
    cmd = layouts._files_view_cmd("E:/proj")
    assert isinstance(cmd, list) and any("filesview" in str(c) or "files-view" in str(c) for c in cmd)


def test_even_horizontal_core_only_no_files(monkeypatch):
    """NORMAL Side-by-side (core-only) at 2 agents: the agents fill a side-by-side row with the live core
    as a full-width strip pinned to the BOTTOM -- NO files pane. select-layout fills cells by pane ORDER,
    so panes are CREATED agents-first (main, worker1) then the core LAST, matching _sidebyside_core_layout.
    Custom geometry (valid checksum), NOT the bare even-horizontal preset."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    seen = []
    monkeypatch.setattr(layouts, "_core_view_cmd",
                        lambda project, orient="v": (seen.append(orient), ["core-stub"])[1])
    monkeypatch.setattr(layouts, "_files_view_cmd", lambda project: ["files-view-stub", str(project)])
    mux = FakeMux()
    panes = layouts.even_horizontal(mux, _team("main", "worker1"), "P", lambda r, p: None, "mkcrew")
    assert set(panes) == {"main", "worker1"}                       # 2 agents placed (core is not a role)
    assert seen == ["h"]                                           # wide/short bottom strip -> tables side by side
    assert mux.calls[0][0] == "new_session"                        # the lead (main) is the session's first pane
    created = [c[1] for c in mux.calls if c[0] == "split_window"]
    assert "files-view-stub" not in created                        # NO files pane
    assert created == ["echo", "core-stub"]                        # worker1, then the core created LAST
    assert ("select_layout", "even-horizontal") not in mux.calls   # custom geometry, not the preset
    geom = [c for c in mux.calls if c[0] == "select_layout"][-1][1]
    csum, g = geom.split(",", 1)
    assert layouts._layout_csum(g) == csum                         # valid tmux checksum
    assert g.startswith("250x60,0,0[")                             # agent row over a full-width core strip


def test_pages_spreads_team_across_tiled_windows(monkeypatch):
    """pages groups agents ~6/window across multiple windows, each tiled, every agent registered."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    registered = []
    team = _team(*[f"a{i}" for i in range(8)])            # 8 agents -> 2 pages (6 + 2)
    panes = layouts.pages(mux, team, "P", lambda r, p: registered.append(r), "mkcrew")
    assert len(panes) == 8 and len(registered) == 8
    assert len([c for c in mux.calls if c[0] == "new_window"]) == 1   # page1=session, page2=1 window
    assert ("select_layout", "tiled") in mux.calls


def test_grid_strip_layout_core_strip_pinned_bottom():
    """_grid_strip_layout: agents gridded on top, core a FULL-WIDTH strip pinned to the bottom --
    this is what makes Pages different from a tiled grid (where core is just another tile)."""
    s = layouts._grid_strip_layout(250, 60, ["1", "3", "5", "7"], "9")
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum            # valid tmux checksum
    assert geom.startswith("250x60,0,0[")                # vertical container: agent rows over a strip
    assert "{" in geom                                   # a row split into columns (the grid)
    assert geom.endswith("250x12,0,48,9]")               # core = full-width (250) strip at the bottom


def test_pages_applies_grid_strip_not_bare_tiled(monkeypatch):
    """Pages finishes each window with the custom grid+strip layout (core as its own strip), not a
    bare 'tiled' that would fold the core into the grid like the tiled template."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    layouts.pages(mux, _team(*[f"a{i}" for i in range(4)]), "P", lambda r, p: None, "mkcrew")
    last = [c for c in mux.calls if c[0] == "select_layout"][-1][1]
    assert "[" in last and last != "tiled"               # the FINAL layout is the custom grid+strip


def test_main_vertical_applies_custom_geometry(monkeypatch):
    """main-vertical applies a CUSTOM layout string (main top-left, core bottom-left, workers right),
    not the bare psmux 'main-vertical' preset."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    panes = layouts.main_vertical(mux, _team("main", "w1", "w2"), "P", lambda r, p: None, "mkcrew")
    assert set(panes) == {"main", "w1", "w2"}
    last = [c for c in mux.calls if c[0] == "select_layout"][-1][1]
    assert "{" in last and "[" in last and last != "main-vertical"   # a custom geometry tree


def test_main_vertical_layout_string_places_main_topleft():
    """_main_vertical_layout: valid tmux checksum, main at top-left, workers in their own column."""
    s = layouts._main_vertical_layout(250, 60, "1", "9", ["3", "5"])
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum            # checksum matches the geometry
    assert geom.startswith("250x60,0,0{")                # column split: workers own the right column
    assert ",0,0,1," in geom and "9]" in geom            # main top-left, core ends the left column
    assert "3" in geom and "5" in geom                   # both workers placed


def test_main_vertical_with_files_reserves_full_height_files_column():
    """_main_vertical_with_files: lead big top-left, workers stacked under it on the left, and the
    3-column files-IDE as a prominent FULL-HEIGHT column on the right (~44%) -- and NO core pane. Valid
    tmux checksum. This is the fix for 'the files-IDE doesn't appear in a LEAD-LEFT cockpit'."""
    s = layouts._main_vertical_with_files(250, 60, "1", ["3", "5"], "9")
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum            # valid tmux checksum
    assert geom.startswith("250x60,0,0{")                # top-level column split: left region | files
    assert geom.count("{") == 1                          # exactly ONE horizontal split (left | files)
    assert ",0,0,1," in geom                             # lead anchors the top-left of the left column
    assert "3" in geom and "5" in geom                   # both workers stacked under the lead (left col)
    assert "x60,140,0,9" in geom                         # files: full-height (60) right column at x=140


def test_main_vertical_with_files_lead_only_no_workers():
    """Degenerate case (lead only): the left column is just the lead, files still full-height right."""
    s = layouts._main_vertical_with_files(250, 60, "1", [], "9")
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum
    assert "[" not in geom                               # no worker stack -> lead is a single left cell
    assert "x60,140,0,9" in geom                         # files still the full-height right column


def test_main_vertical_with_files_scales_2_to_4_agents():
    """_main_vertical_with_files stays VALID (checksum + one cell per agent + a files cell) and keeps the
    left-column rows summing to h for N = 2, 3, 4 agents (1-3 stacked workers). Regression for the
    LEAD-LEFT cockpit losing/cramping panes at higher agent counts."""
    import re
    for nworkers in (1, 2, 3):                                   # N agents = lead + nworkers -> 2, 3, 4
        worker_ids = [str(2 * i + 3) for i in range(nworkers)]   # ids 3, 5, 7
        s = layouts._main_vertical_with_files(250, 60, "1", worker_ids, "9")
        csum, geom = s.split(",", 1)
        assert layouts._layout_csum(geom) == csum                       # valid tmux checksum
        assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == nworkers + 2   # lead + workers + files
        assert ",0,0,1," in geom                                        # lead anchors the top-left
        assert "x60,140,0,9" in geom                                    # files: full-height right column
        for wid in worker_ids:
            assert re.search(rf",{wid}\b", geom)                        # every worker placed
    # the lead dominates (bigger than a worker) but the 3 workers stay readable + evenly sized
    geom = layouts._main_vertical_with_files(250, 60, "1", ["3", "5", "7"], "9").split(",", 1)[1]
    lead_h = int(re.search(r"139x(\d+),0,0,1\b", geom).group(1))
    worker_hs = [int(m) for m in re.findall(r"139x(\d+),0,\d+,(?:3|5|7)\b", geom)]
    assert len(worker_hs) == 3 and all(lead_h > wh for wh in worker_hs)   # lead is the dominant pane
    assert max(worker_hs) - min(worker_hs) <= 1 and min(worker_hs) >= 8   # workers even + readable at h=60


def test_sidebyside_n_layout_reserves_full_height_files_column():
    """_sidebyside_N_layout: SIDE-BY-SIDE with the files-IDE -- the lead a full-height left column, the
    single worker the column beside it (lead | worker | files), and the 3-column files-IDE as a prominent
    FULL-HEIGHT column on the RIGHT (~42%) -- and NO core pane. Valid tmux checksum."""
    import re
    s = layouts._sidebyside_N_layout(250, 60, "1", ["3"], "9")
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum            # valid tmux checksum
    assert geom.startswith("250x60,0,0{")                # top-level column split: left region | files
    assert "x60,145,0,9" in geom                         # files: full-height (60) right column at x=145 (~42%)
    assert re.search(r"\d+x60,0,0,1\b", geom)            # lead: full-height column anchored top-left
    assert re.search(r"\d+x60,\d+,0,3\b", geom)          # the single worker: a full-height column beside the lead


def test_sidebyside_n_layout_lead_only_no_workers():
    """Degenerate case (lead only, N=1): the left region is just the lead, files still full-height right."""
    s = layouts._sidebyside_N_layout(250, 60, "1", [], "9")
    csum, geom = s.split(",", 1)
    assert layouts._layout_csum(geom) == csum
    assert "[" not in geom                               # no worker stack -> no vertical split
    assert geom.count("{") == 1                          # exactly ONE split: left region | files
    assert "x60,145,0,9" in geom                         # files still the full-height right column


def test_sidebyside_n_layout_scales_2_to_4_agents():
    """_sidebyside_N_layout stays VALID (checksum + one cell per agent + a files cell) for N = 2, 3, 4 agents
    (1-3 workers): the lead is a full-height left column, the workers stack in the column beside it so none
    get unreadably thin, and the files-IDE is the full-height right column. Mirrors the LEAD-LEFT scaling
    test -- the fix for Side-by-side cramming the files-IDE/agents via the bare even-horizontal preset."""
    import re
    for nworkers in (1, 2, 3):                                   # N agents = lead + nworkers -> 2, 3, 4
        worker_ids = [str(2 * i + 3) for i in range(nworkers)]   # ids 3, 5, 7
        s = layouts._sidebyside_N_layout(250, 60, "1", worker_ids, "9")
        csum, geom = s.split(",", 1)
        assert layouts._layout_csum(geom) == csum                       # valid tmux checksum
        assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == nworkers + 2   # lead + workers + files
        assert geom.startswith("250x60,0,0{")                           # left region | files (column split)
        assert re.search(r"\d+x60,0,0,1\b", geom)                       # lead full-height, anchored top-left
        assert "x60,145,0,9" in geom                                    # files: full-height right column
        for wid in worker_ids:
            assert re.search(rf",{wid}\b", geom)                        # every worker placed
    # workers stay WIDE (they stack in a column, not split into thin slivers) -> readable at 4 agents
    geom = layouts._sidebyside_N_layout(250, 60, "1", ["3", "5", "7"], "9").split(",", 1)[1]
    worker_ws = [int(re.search(rf"(\d+)x\d+,\d+,\d+,{wid}\b", geom).group(1)) for wid in ("3", "5", "7")]
    assert len(worker_ws) == 3 and all(ww >= 40 for ww in worker_ws)    # each worker wide enough to read


def test_sidebyside_core_layout_core_only_scales_2_and_4():
    """_sidebyside_core_layout (NORMAL even-horizontal geometry, core-only): the agents fill a single ROW
    of side-by-side columns with the live core as a FULL-WIDTH strip pinned to the bottom -- NO files
    column. Valid tmux checksum + exactly one cell per agent + a core cell, for N = 2 and N = 4 agents."""
    import re
    for n in (2, 4):
        agent_ids = [str(2 * i + 1) for i in range(n)]                   # ids 1, 3, 5, 7
        s = layouts._sidebyside_core_layout(250, 60, agent_ids, "9")
        csum, geom = s.split(",", 1)
        assert layouts._layout_csum(geom) == csum                       # valid tmux checksum
        assert geom.startswith("250x60,0,0[")                           # agent row over the core strip
        assert geom.count("{") == 1                                     # ONE row container (a row, not a grid)
        assert geom.endswith(",9]")                                     # core = full-width bottom strip (last cell)
        assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == n + 1   # n agents + the core strip
        for aid in agent_ids:
            assert re.search(rf",{aid}\b", geom)                        # every agent placed


def test_tiled_layout_grids_one_cell_per_pane():
    """_tiled_layout (the GRID template's deterministic geometry): an even grid with exactly one cell per
    pane (agents + the files-IDE), a valid tmux checksum, for 2-5 panes (N = 1-4 agents + files)."""
    import re
    for k in (2, 3, 4, 5):
        ids = [str(i + 1) for i in range(k)]
        s = layouts._tiled_layout(250, 60, ids)
        csum, geom = s.split(",", 1)
        assert layouts._layout_csum(geom) == csum                       # valid tmux checksum
        assert len(re.findall(r"\d+x\d+,\d+,\d+,\d+", geom)) == k        # one cell per pane (none lost)
        assert geom.startswith("250x60,0,0")                            # fills the whole window
        for pid in ids:
            assert re.search(rf",{pid}\b", geom)                        # every pane placed


def test_main_vertical_core_only_no_files(monkeypatch):
    """NORMAL main-vertical (core-only): CREATES a live core pane and NO files-IDE. select-layout fills
    cells by pane ORDER, and _main_vertical_layout's cells are lead, core, workers, so panes are created
    lead -> core -> worker1 -> worker2 (core SECOND). Custom geometry, not the bare 'main-vertical' preset."""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    seen_core = []
    monkeypatch.setattr(layouts, "_core_view_cmd",
                        lambda project, orient="v": (seen_core.append(orient), ["core-stub"])[1])
    monkeypatch.setattr(layouts, "_files_view_cmd", lambda project: ["files-view-stub", str(project)])
    mux = FakeMux()
    panes = layouts.main_vertical(mux, _team("main", "worker1", "worker2"), "P", lambda r, p: None, "mkcrew")
    assert set(panes) == {"main", "worker1", "worker2"}           # 3 agents placed (core is not a role)
    assert seen_core == ["h"]                                     # exactly ONE core pane, horizontal (wide/short strip -> side-by-side tables, no hotkey block)
    assert mux.calls[0][0] == "new_session"                      # lead is the session's first pane
    created = [c[1] for c in mux.calls if c[0] == "split_window"]
    assert "files-view-stub" not in created                      # NO files-IDE pane
    assert created == ["core-stub", "echo", "echo"]              # core created SECOND, then the two workers
    last = [c for c in mux.calls if c[0] == "select_layout"][-1][1]
    assert last != "main-vertical" and "{" in last and "[" in last  # custom geometry, not the bare preset


def test_lead_left_ide_reserves_files_drops_core(monkeypatch):
    """The EXPERIMENTAL lead-left-ide builder CREATES a files-IDE pane and reserves a full-height cell for
    it, and creates NO core pane. select-layout fills cells by pane ORDER, so panes are created
    lead -> worker1 -> worker2 -> files (files LAST) to match _main_vertical_with_files. (This is the old
    files behavior of main-vertical, moved to the experimental variant.)"""
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    seen_core = []
    monkeypatch.setattr(layouts, "_core_view_cmd",
                        lambda project, orient="v": (seen_core.append(orient), ["core-stub"])[1])
    monkeypatch.setattr(layouts, "_files_view_cmd", lambda project: ["files-view-stub", str(project)])
    mux = FakeMux()
    panes = layouts.lead_left_ide(mux, _team("main", "worker1", "worker2"), "P", lambda r, p: None, "mkcrew")
    assert set(panes) == {"main", "worker1", "worker2"}           # 3 agents placed (Files is not a role)
    assert seen_core == []                                        # NO core pane created (files-IDE carries it)
    assert mux.calls[0][0] == "new_session"                      # lead is the session's first pane
    # workers, then the files-IDE LAST -> creation order matches the layout cells
    assert [c[1] for c in mux.calls if c[0] == "split_window"] == ["echo", "echo", "files-view-stub"]
    last = [c for c in mux.calls if c[0] == "select_layout"][-1][1]
    assert last != "main-vertical" and "{" in last and "[" in last  # custom geometry, not the bare preset
    assert "x60,140,0," in last                                    # files: full-height (60) right column


def test_lead_left_ide_registered():
    """The experimental files-IDE LEAD-LEFT builder is registered under its registry key."""
    assert layouts.LAYOUTS.get("lead-left-ide") is layouts.lead_left_ide


def test_hub_call_sequence_core_in_every_window(monkeypatch):
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    registered = []
    panes = layouts.hub(mux, _team("main", "opus1", "opus2"), "P",
                        lambda role, pid: registered.append((role, pid)), "mkcrew")
    kinds = [c[0] for c in mux.calls]
    # each agent: a window (new_session for the first, new_window after) + its own core split
    assert kinds == ["new_session", "split_window", "new_window", "split_window", "new_window", "split_window"]
    for c in mux.calls:                               # every split is the core strip at 25%
        if c[0] == "split_window":
            assert c[1].endswith("mk-core-view.exe") and c[2] == 25
    assert set(panes) == {"main", "opus1", "opus2"}
    # agents=%1/%3/%5, per-window core strips=%2/%4/%6 (never registered)
    assert panes["main"] == "%1" and panes["opus1"] == "%3" and panes["opus2"] == "%5"
    assert ("main", "%1") in registered
    assert all(pid not in ("%2", "%4", "%6") for _, pid in registered)


def test_launch_threads_session_id_and_resume(monkeypatch):
    captured = {}
    def fake_lc(role, model, project, mode=None, effort=None, provider=None,
                session_id=None, resume=False, command=None):
        captured.update(session_id=session_id, resume=resume)
        return ["x"]
    monkeypatch.setattr(layouts.agent, "launch_command", fake_lc)
    layouts._launch({"role": "r", "model": "m", "_session_id": "u", "_resume": True}, "P")
    assert captured["session_id"] == "u" and captured["resume"] is True


def test_launch_threads_custom_command(monkeypatch):
    captured = {}
    def fake_lc(role, model, project, mode=None, effort=None, provider=None,
                session_id=None, resume=False, command=None):
        captured["command"] = command
        return ["x"]
    monkeypatch.setattr(layouts.agent, "launch_command", fake_lc)
    layouts._launch({"role": "r", "model": "m", "provider": "custom", "command": "codex"}, "P")
    assert captured["command"] == "codex"


def test_apply_chrome_sets_key_options(monkeypatch):
    monkeypatch.setattr("mkcrew.frozen.add_workspace_launcher", lambda: "add-workspace.cmd")  # no file write
    mux = FakeMux()
    layouts.apply_chrome(mux)
    assert mux.options["status-position"] == "top"   # the workspace switcher (window names) lives here
    assert any(c[0] == "bind_key" and c[1] == "a" for c in mux.calls)   # Ctrl-b a -> add-workspace popup
    add_ws = next(c for c in mux.calls if c[0] == "bind_key" and c[1] == "a")
    assert add_ws[2][0] == "display-popup"            # Textual app in a real popup (needs DSR-fixed psmux >= 06-23)
    assert "-E" in add_ws[2]                          # close the popup when the app exits
    # item 6: bigger popup so the wider per-agent wizard + full model names fit
    assert add_ws[2][add_ws[2].index("-w") + 1] == "92%"
    assert add_ws[2][add_ws[2].index("-h") + 1] == "90%"
    assert add_ws[2][-1] == "add-workspace.cmd"       # launcher, no --menu-run -> Textual AddWorkspaceApp
    assert "display-menu" not in add_ws[2]
    assert mux.options["mouse"] == "on"
    assert "pane-border-status" not in mux.options   # removed: psmux shows literal #T + it offset the cursor
    assert mux.options["mouse-selection"] == "off"        # psmux #245: stop the row-spanning overlay
    assert mux.options["pwsh-mouse-selection"] == "on"    # psmux #211: per-pane WT-style selection
    # HARDENING: the mouse wheel must never drop a pane into copy-mode (which then swallows the user's
    # keystrokes). Off => the wheel scrolls scrollback directly; the mouse can't trap a pane.
    assert mux.options["scroll-enter-copy-mode"] == "off"
    # HARDENING: an always-available unstick — Ctrl-b Escape force-exits copy-mode so typing reaches the app.
    unstick = next(c for c in mux.calls if c[0] == "bind_key" and c[1] == "Escape")
    assert unstick[2] == ("send-keys", "-X", "cancel")    # exits copy-mode (no-op when not in a mode)
    assert "unstick" in mux.options["status-right"]        # surfaced in the status line for discoverability
    assert "MKCREW" in mux.options["status-left"]


def test_apply_chrome_hardens_copymode_keystroke_swallow(monkeypatch):
    """The mouse->copy-mode keystroke-swallow freeze is hardened two ways: (1) the source fix —
    `scroll-enter-copy-mode off` so a wheel scroll can NEVER drop a pane into copy-mode (psmux then
    routes typing to copy-mode nav and silently swallows it); with it off the wheel scrolls scrollback
    directly. (2) an always-available unstick — Ctrl-b Escape runs `send-keys -X cancel` to force-exit
    copy-mode on the active pane so a trapped user instantly gets back to typing (safe no-op otherwise),
    advertised as `Esc:unstick` in the status line."""
    monkeypatch.setattr("mkcrew.frozen.add_workspace_launcher", lambda: "add-workspace.cmd")
    mux = FakeMux()
    layouts.apply_chrome(mux)
    # (1) source fix: the wheel can't trap a pane
    assert mux.options["scroll-enter-copy-mode"] == "off"
    # (2) unstick bind on the Ctrl-b prefix -> exit copy-mode
    binds = [c for c in mux.calls if c[0] == "bind_key"]
    esc = [c for c in binds if c[1] == "Escape"]
    assert len(esc) == 1 and esc[0][2] == ("send-keys", "-X", "cancel")
    # discoverable: the status line names the unstick key, and the existing a/n/x binds are untouched
    assert "unstick" in mux.options["status-right"] and "Esc" in mux.options["status-right"]
    assert {c[1] for c in binds} >= {"a", "A", "x", "X", "Escape"}   # unstick added, prior binds intact


def test_apply_chrome_shows_workspace_name(monkeypatch):
    """apply_chrome(name='lovely') PREPENDS a workspace-name badge to status-right so the terminal
    cockpit shows which named workspace you're in -- while keeping the fixed core/hint text (incl. the
    Esc:unstick escape hint) and the copy-mode hardening intact. The badge widens status-right-length."""
    monkeypatch.setattr("mkcrew.frozen.add_workspace_launcher", lambda: "add-workspace.cmd")
    mux = FakeMux()
    layouts.apply_chrome(mux, name="lovely")
    sr = mux.options["status-right"]
    assert "lovely" in sr                                   # the workspace name is visible in the top bar
    assert sr.endswith(layouts._STATUS_RIGHT)              # badge PREPENDED; the fixed hints kept verbatim
    assert "unstick" in sr and "Esc" in sr                 # the copy-mode escape hint survives
    assert "core: live" in sr and "add-ws" in sr and "switch" in sr   # the existing hints survive
    assert mux.options["scroll-enter-copy-mode"] == "off"  # copy-mode hardening intact with a name
    esc = next(c for c in mux.calls if c[0] == "bind_key" and c[1] == "Escape")
    assert esc[2] == ("send-keys", "-X", "cancel")         # the Ctrl-b Esc unstick bind survives
    assert int(mux.options["status-right-length"]) > 130   # widened to fit the prepended badge


def test_apply_chrome_no_name_is_backcompat(monkeypatch):
    """apply_chrome() with no name yields the EXACT original status-right string (byte-for-byte), so an
    unnamed workspace's chrome is unchanged."""
    monkeypatch.setattr("mkcrew.frozen.add_workspace_launcher", lambda: "add-workspace.cmd")
    for call in (lambda m: layouts.apply_chrome(m), lambda m: layouts.apply_chrome(m, name=None),
                 lambda m: layouts.apply_chrome(m, name="")):
        mux = FakeMux()
        call(mux)
        assert mux.options["status-right"] == layouts._STATUS_RIGHT   # no badge -> the original string
        assert "⬢" not in mux.options["status-right"]
        assert mux.options["status-right-length"] == "130"            # unbumped when nameless


def test_hub_titles_each_pane(monkeypatch):
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    layouts.hub(mux, _team("main", "opus1"), "P", lambda r, p: None, "mkcrew")
    assert mux.titles["%1"] == "main - claude"           # lead pane
    assert mux.titles["%2"] == "core - control tower"    # core strip
    assert mux.titles["%3"] == "opus1 - claude"          # worker window


def test_tiled_titles_each_pane(monkeypatch):
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    layouts.tiled(mux, _team("main", "opus1"), "P", lambda r, p: None, "mkcrew")
    assert mux.titles["%1"] == "main - claude"
    assert mux.titles["%2"] == "opus1 - claude"
    assert mux.titles["%3"] == "core - control tower"


def test_main_horizontal_uses_named_layout(monkeypatch):
    monkeypatch.setattr(layouts.agent, "launch_command", lambda *a, **k: ["echo", "hi"])
    mux = FakeMux()
    layouts.main_horizontal(mux, _team("main", "opus1"), "P", lambda r, p: None, "mkcrew")
    assert ("select_layout", "main-horizontal") in mux.calls


def test_registry_has_four_layouts():
    assert set(layouts.LAYOUTS) >= {"tiled", "hub", "main-vertical", "main-horizontal"}


def test_panes_per_window_drives_font_density():
    """panes_per_window = the densest single window per layout (the cockpit font scales to it).
    hub/pages stay light at scale; tiled/single-window pack every agent in."""
    assert layouts.panes_per_window("tiled", 9) == 10        # all agents + core, one window
    assert layouts.panes_per_window("main-vertical", 4) == 5
    assert layouts.panes_per_window("hub", 9) == 2           # 1 agent + core strip per tab
    assert layouts.panes_per_window("pages", 9) == 7         # 6 agents + core per page


def test_add_template_layouts_have_no_zero_size_cells():
    """0-size-core regression sweep for every custom `mk add` layout string: at 1..4 agents and the
    window sizes measured in the field (small attached client, the 250x60 build size, a maximized
    conhost), every leaf cell keeps a POSITIVE width and height (an invisible 0-row core would read as
    'core pane absent'), the checksum is valid, and there is exactly one cell per pane (psmux fills
    cells by pane order and DROPS/mis-fills panes on a count mismatch)."""
    import re
    cell_re = re.compile(r"(\d+)x(\d+),\d+,\d+,(\d+)")       # leaf cells only (containers end in [ or {)
    for w, h in [(80, 24), (120, 29), (220, 50), (250, 60), (300, 80)]:
        for n in range(1, 5):                                 # n agents = lead + (n-1) workers
            workers = [str(10 + i) for i in range(n - 1)]
            cases = [
                ("main-vertical", layouts._main_vertical_layout(w, h, "1", "2", workers)),
                ("even-horizontal", layouts._sidebyside_core_layout(w, h, ["1", *workers], "2")),
                ("tiled", layouts._tiled_layout(w, h, ["1", *workers, "2"])),
                ("lead-left-ide", layouts._main_vertical_with_files(w, h, "1", workers, "9")),
                ("sidebyside-ide", layouts._sidebyside_N_layout(w, h, "1", workers, "9")),
            ]
            for name, layout in cases:
                csum, geom = layout.split(",", 1)
                assert layouts._layout_csum(geom) == csum, f"{name} {w}x{h} n={n}: bad checksum"
                cells = cell_re.findall(geom)
                assert len(cells) == n + 1, \
                    f"{name} {w}x{h} n={n}: {len(cells)} cells != {n + 1} panes -> psmux drops panes"
                for cw, ch, pid in cells:
                    assert int(cw) >= 1 and int(ch) >= 1, \
                        f"{name} {w}x{h} n={n}: ZERO-SIZE cell for pane {pid}: {geom}"
