# src/mkcrew/layouts.py
"""Cockpit layout registry: each builder arranges the team in one psmux session
and returns {role: pane_id}. cmd_start picks one by name from team.config."""
import math
import sys
from pathlib import Path
from . import agent


def _core_view_cmd(project, orient="v") -> list[str]:
    """Core-view pane command (full path; psmux won't resolve a bare name) + the project dir, so the
    live pane loads the team roster. `orient` picks the core style. Frozen-aware: inside MKCREW.exe it
    re-invokes the single exe (`MKCREW.exe core-view ...`) since mk-core-view.exe isn't bundled."""
    from . import frozen
    return frozen.core_view_cmd(project, orient)


def _launch(a, project):
    return agent.launch_command(
        a["role"], a["model"], project,
        mode=a.get("mode", "bypassPermissions"),
        effort=a.get("effort"), provider=a.get("provider", "claude"),
        session_id=a.get("_session_id"), resume=a.get("_resume", False),
        command=a.get("command"),
    )


def _label(a) -> str:
    """Pane-border label: 'role - provider'."""
    return f"{a['role']} - {a.get('provider', 'claude')}"


# status-right: the FIXED core-live + Ctrl-b hint string (the workspace *switcher* is the window tabs on
# the LEFT; these are the key hints on the RIGHT). apply_chrome(name=...) PREPENDS a workspace-name badge
# to this via _status_right when the workspace is named, so a user in the terminal can see WHICH named
# workspace they're in. Keep the `Esc:unstick` hint here (the copy-mode escape hatch, see apply_chrome).
_STATUS_RIGHT = ("#[fg=colour39,bold]* core: live #[fg=colour244]| Ctrl-b "
                 "#[fg=colour215]a#[fg=colour244]:add-ws #[fg=colour215]n#[fg=colour244]:switch "
                 "#[fg=colour215]x#[fg=colour244]:close #[fg=colour215]Esc#[fg=colour244]:unstick ")
_STATUS_RIGHT_LEN = "130"          # fits the fixed hint string (no name badge)
_STATUS_RIGHT_LEN_NAMED = "170"    # + room for the prepended "⬢ <workspace> · " badge


def _status_right(name=None) -> str:
    """The status-right value. When the workspace is NAMED, PREPEND a bright hexagon badge
    (`⬢ <name> ·`) to the fixed hint string so the TERMINAL cockpit shows which named workspace you're
    in -- this badge is a GLOBAL option, so it shows in EVERY window (unlike the per-window role/page
    tab names). An empty/None name yields the EXACT original string (back-compat)."""
    if name:
        return f"#[fg=colour215,bold]⬢ {name} #[fg=colour244]· " + _STATUS_RIGHT
    return _STATUS_RIGHT


_CHROME = [
    ("status", "on"),
    ("status-position", "top"),        # the cockpit's workspace switcher (window names) lives here.
                                       # NOTE: psmux mis-counts this top row in its mouse->pane Y, so
                                       # clicks land one row low in interactive panes -> use up/down+Enter.
    ("status-style", "bg=colour234,fg=colour250"),
    ("status-left", "#[bg=colour29,fg=colour231,bold] MKCREW #[bg=colour234,fg=colour29,bold] > #[default]"),
    ("status-left-length", "20"),
    ("window-status-format", " #W "),
    ("window-status-current-format", "#[bg=colour39,fg=colour16,bold] #W #[default]"),
    ("window-status-separator", ""),
    ("status-right", _STATUS_RIGHT),   # apply_chrome(name=...) overrides this with a name-badged variant
    ("status-right-length", _STATUS_RIGHT_LEN),   # +the Esc:unstick hint (copy-mode escape hatch, see apply_chrome)
    ("pane-active-border-style", "fg=colour39,bold"),
    ("pane-border-style", "fg=colour238"),
    ("mouse", "on"),
    ("mouse-selection", "off"),        # psmux #245: stop psmux's row-spanning drag overlay
    ("pwsh-mouse-selection", "on"),    # psmux #211: per-pane Windows-Terminal-style selection
    # HARDENING (mouse -> copy-mode keystroke-swallow freeze): a wheel-scroll must NOT be able to drop a
    # pane into COPY-MODE, because psmux then routes the user's keystrokes to copy-mode navigation and
    # SILENTLY SWALLOWS them ("can't type"). With this off, a wheel scrolls the pane's scrollback DIRECTLY
    # (psmux #193) and PageUp forwards to the app (psmux #284) -- so the mouse can never trap a pane.
    # Alt-screen TUIs (claude) already get the wheel forwarded to them; this also covers the Windows-ConPTY
    # case where psmux's alt-screen detection heuristic misfires (cursor mid-screen) and would otherwise
    # mis-enter copy-mode. Deliberate copy-mode via Ctrl-b [ still works; Ctrl-b Esc force-exits it.
    ("scroll-enter-copy-mode", "off"),
    ("window-size", "latest"),         # window tracks the attached client's size...
    ("aggressive-resize", "on"),       # ...so panes reflow to fill the real terminal (any resolution)
]


def apply_chrome(mux, name=None) -> None:
    """Apply MKCREW cockpit branding/chrome (global psmux options).

    `name` is the workspace's human name (from .mkcrew/workspace.json). When set, a workspace-name
    badge is PREPENDED to status-right (and the bar widened to fit) so the TERMINAL cockpit shows which
    named workspace you're in -- the badge shows in every window. Empty/None -> the exact original
    nameless chrome (back-compat).

    Cosmetic + best-effort: PsmuxBackend.set_option goes through _run, which ignores a
    non-zero psmux return, so an unsupported option no-ops without aborting the rest."""
    for opt_name, value in _CHROME:
        mux.set_option(opt_name, value)
    if name:                                                 # prepend the workspace-name badge + widen the bar
        mux.set_option("status-right", _status_right(name))
        mux.set_option("status-right-length", _STATUS_RIGHT_LEN_NAMED)
    from . import frozen
    # Ctrl-b a / A -> add-workspace via display-popup running the Textual app. This needs the DSR-fixed
    # psmux (>= 2026-06-23, PR #388 "conpty-dsr-hang"): the popup answers ESC[6n so PSReadLine/Textual
    # paint instead of hanging blank. On an OLDER psmux this renders blank -- fall back to the native
    # menu by re-binding to addworkspace.menu_command(launcher), which is kept in addworkspace.py.
    launcher = frozen.add_workspace_launcher()
    for _key in ("a", "A"):                                  # bigger popup (92% x 90%) so the wider
        mux.bind_key(_key, "display-popup", "-E", "-w", "92%", "-h", "90%", launcher)  # per-agent wizard + full model names fit
    for _key in ("x", "X"):                          # Ctrl-b x -> close the current workspace (confirm y/n)
        mux.bind_key(_key, "confirm-before", "kill-window")
    # Ctrl-b Escape -> UNSTICK: force-exit copy-mode on the active pane so keystrokes reach the app again.
    # This is the always-available escape hatch for the mouse->copy-mode keystroke-swallow trap: if a pane
    # ever lands in copy-mode (a deliberate Ctrl-b [, or a stray wheel on a pane whose alt-screen state was
    # mis-detected), psmux routes typing to copy-mode navigation and silently swallows it. `send-keys -X
    # cancel` exits copy-mode instantly (verified) and is a safe no-op when the pane is NOT in a mode.
    # Plain Esc / q also exit copy-mode; this bind is the discoverable, works-from-anywhere one, surfaced
    # as `Esc:unstick` in status-right. (scroll-enter-copy-mode=off above already stops the MOUSE from
    # trapping a pane -- this covers any remaining path back to typing.)
    mux.bind_key("Escape", "send-keys", "-X", "cancel")


def _layout_csum(s: str) -> str:
    """tmux layout-string checksum (a running 16-bit rotate+add over the geometry)."""
    cs = 0
    for ch in s:
        cs = ((cs >> 1) + ((cs & 1) << 15)) & 0xffff
        cs = (cs + ord(ch)) & 0xffff
    return f"{cs:04x}"


def _main_vertical_layout(w: int, h: int, main_id: str, core_id: str, worker_ids: list) -> str:
    """psmux/tmux layout string: lead big top-left, core small bottom-left under it, workers stacked
    down the right column (workers own the whole right side). Pane ids are the NUMERIC part of '%N'.
    Scales for any worker count; tmux re-scales it on attach/resize."""
    core_h = min(12, max(4, h // 4))
    main_h = h - core_h - 1
    if not worker_ids:                                       # just lead + core -> one column
        g = f"{w}x{h},0,0[{w}x{main_h},0,0,{main_id},{w}x{core_h},0,{main_h + 1},{core_id}]"
        return f"{_layout_csum(g)},{g}"
    lw = max(20, round(w * 0.58)); rw = w - lw - 1; rx = lw + 1
    left = f"{lw}x{h},0,0[{lw}x{main_h},0,0,{main_id},{lw}x{core_h},0,{main_h + 1},{core_id}]"
    n = len(worker_ids); avail = h - (n - 1); hs = [avail // n] * n
    for i in range(avail - sum(hs)):
        hs[i] += 1
    parts, y = [], 0
    for wid, wh in zip(worker_ids, hs):
        parts.append(f"{rw}x{wh},{rx},{y},{wid}"); y += wh + 1
    right = f"{rw}x{h},{rx},0[{','.join(parts)}]" if n > 1 else f"{rw}x{h},{rx},0,{worker_ids[0]}"
    g = f"{w}x{h},0,0{{{left},{right}}}"
    return f"{_layout_csum(g)},{g}"


_LEAD_WEIGHT = 2.0   # in LEAD-LEFT, the lead pane is ~this many workers tall (big, but workers stay readable)


def _main_vertical_with_files(w: int, h: int, main_id: str, worker_ids: list, files_id: str) -> str:
    """psmux/tmux layout string for LEAD-LEFT *with* the files-IDE: lead big top-left, the workers stacked
    under it down the left column, and the 3-column files-IDE (core | explorer | editor) as a prominent
    FULL-HEIGHT column on the RIGHT (~44%). There is NO separate core pane -- the files-IDE already shows
    the live core on its left, so a second one would be a duplicate. Pane ids are the NUMERIC part of '%N';
    the cell order is lead -> workers -> files, which the caller MUST also use as the pane CREATION order
    (select-layout fills cells by pane order and ignores the ids written in the string). Handles 1-4 workers
    cleanly: the left-column rows always sum to h, so the geometry stays valid at any agent count/screen."""
    rw = max(24, round(w * 0.44)); lw = w - rw - 1; rx = lw + 1      # files = full-height right column (~44%)
    n = len(worker_ids)
    if n == 0:                                                       # just the lead fills the left column
        left = f"{lw}x{h},0,0,{main_id}"
    else:
        body = h - n                                                # rows for panes (n separators between them)
        # Weighted split: the lead stays BIGGER than a worker (~_LEAD_WEIGHT workers tall) WITHOUT hogging a
        # full half -- so 3-4 stacked workers keep readable sizes instead of being squeezed to a few rows.
        # (The old "lead = half the column" left each worker ~1/6 of the height -> the manual-resize problem.)
        main_h = max(6, round(body * _LEAD_WEIGHT / (_LEAD_WEIGHT + n)))
        if body - main_h < n:                                       # guard: leave >=1 row per stacked worker
            main_h = max(1, body - n)
        base, extra = divmod(body - main_h, n)                      # split the rest evenly among the workers
        cells, y = [f"{lw}x{main_h},0,0,{main_id}"], main_h + 1
        for i, wid in enumerate(worker_ids):
            wh = base + (1 if i < extra else 0)
            cells.append(f"{lw}x{wh},0,{y},{wid}"); y += wh + 1
        left = f"{lw}x{h},0,0[{','.join(cells)}]"
    files = f"{rw}x{h},{rx},0,{files_id}"                            # files-IDE: full-height right column
    g = f"{w}x{h},0,0{{{left},{files}}}"
    return f"{_layout_csum(g)},{g}"


def _sidebyside_N_layout(w: int, h: int, lead_id: str, worker_ids: list, files_id: str) -> str:
    """psmux/tmux layout string for SIDE-BY-SIDE (even-horizontal) *with* the files-IDE: the agents fill a
    LEFT region as side-by-side columns -- the lead its own FULL-HEIGHT column, the workers in the column
    beside it -- and the 3-column files-IDE (core | explorer | editor) is a prominent FULL-HEIGHT column on
    the RIGHT (~42%). There is NO separate core pane -- the files-IDE already shows the live core on its
    left, so a second one would duplicate it. At 2 agents this reads as lead | worker | files (three
    columns); at 3-4 the extra workers STACK in the workers column (staying full width) so none get the
    unreadably-thin slivers a bare even-horizontal preset would give. Pane ids are the NUMERIC part of '%N';
    the cell order is lead -> workers -> files, which the caller MUST also use as the pane CREATION order
    (select-layout fills cells by pane order and ignores the ids in the string). The left-region cols/rows
    always sum to w/h, so the geometry stays valid at any agent count/screen."""
    rw = max(24, round(w * 0.42)); lw = w - rw - 1; rx = lw + 1      # files = full-height right column (~42%)
    n = len(worker_ids)
    if n == 0:                                                       # just the lead fills the left region
        left = f"{lw}x{h},0,0,{lead_id}"
    else:
        lcw = lw - 1 - (lw - 1) // 2; wcw = (lw - 1) // 2; wx = lcw + 1   # lead | workers cols (1-col gap), lead bigger
        lead_cell = f"{lcw}x{h},0,0,{lead_id}"                       # lead: full-height left column
        if n == 1:                                                  # single worker -> its own full-height column
            workers_col = f"{wcw}x{h},{wx},0,{worker_ids[0]}"       #   -> lead | worker | files
        else:                                                       # 2-3 workers -> stacked rows in the workers column
            avail = h - (n - 1); hs = [avail // n] * n              # row heights sum to h (n-1 separators)
            for i in range(avail - sum(hs)):
                hs[i] += 1
            cells, y = [], 0
            for wid, wh in zip(worker_ids, hs):
                cells.append(f"{wcw}x{wh},{wx},{y},{wid}"); y += wh + 1
            workers_col = f"{wcw}x{h},{wx},0[{','.join(cells)}]"
        left = f"{lw}x{h},0,0{{{lead_cell},{workers_col}}}"         # lead | workers (side by side)
    files = f"{rw}x{h},{rx},0,{files_id}"                           # files-IDE: full-height right column
    g = f"{w}x{h},0,0{{{left},{files}}}"
    return f"{_layout_csum(g)},{g}"


def _tiled_layout(w: int, h: int, pane_ids: list) -> str:
    """psmux/tmux layout string for the GRID template: an even row-major grid of ALL panes (agents + the
    files-IDE -- one equal cell each). Used by `mk add --template tiled` so the grid is a DETERMINISTIC,
    valid layout string (one cell per pane, rows/cols computed from the count) instead of leaning on psmux's
    bare 'tiled' preset. Cells are filled in pane CREATION order, so the caller MUST create panes in the same
    order as `pane_ids` (lead -> workers -> files). Ids are the NUMERIC part of '%N'. Mirrors
    `_grid_strip_layout` minus the pinned core strip (the files-IDE already carries the core)."""
    k = len(pane_ids)
    cols = max(1, math.ceil(math.sqrt(k)))
    rows = max(1, math.ceil(k / cols))
    avail = h - (rows - 1); rh = [avail // rows] * rows              # row heights sum to h (rows-1 separators)
    for i in range(avail - sum(rh)):
        rh[i] += 1
    parts, y, idx = [], 0, 0
    for r in range(rows):
        ncol = min(cols, k - idx)                                   # last row may hold fewer columns
        availw = w - (ncol - 1); cw = [availw // ncol] * ncol       # col widths sum to w (ncol-1 separators)
        for i in range(availw - sum(cw)):
            cw[i] += 1
        x, cells = 0, []
        for c in range(ncol):
            cells.append(f"{cw[c]}x{rh[r]},{x},{y},{pane_ids[idx]}"); x += cw[c] + 1; idx += 1
        parts.append(f"{w}x{rh[r]},0,{y}{{{','.join(cells)}}}" if ncol > 1 else cells[0])
        y += rh[r] + 1
    g = f"{w}x{h},0,0[{','.join(parts)}]" if len(parts) > 1 else parts[0]
    return f"{_layout_csum(g)},{g}"


def _grid_strip_layout(w: int, h: int, agent_ids: list, core_id: str) -> str:
    """psmux/tmux layout string: agents in a grid filling the top region, the core as a FULL-WIDTH
    strip pinned to the bottom. This is Pages' identity -- 'core strip on each' -- and what makes it
    DIFFERENT from tiled (where the core is just another equal tile in one grid). Ids are numeric."""
    k = len(agent_ids)
    cs = min(12, max(4, h // 5))                          # core strip height (bottom, full width)
    th = h - cs - 1                                       # top grid region
    cols = max(1, math.ceil(math.sqrt(k)))
    rows = max(1, math.ceil(k / cols))
    avail = th - (rows - 1); rh = [avail // rows] * rows
    for i in range(avail - sum(rh)):
        rh[i] += 1
    parts, y, idx = [], 0, 0
    for r in range(rows):
        ncol = min(cols, k - idx)
        availw = w - (ncol - 1); cw = [availw // ncol] * ncol
        for i in range(availw - sum(cw)):
            cw[i] += 1
        x, cells = 0, []
        for c in range(ncol):
            cells.append(f"{cw[c]}x{rh[r]},{x},{y},{agent_ids[idx]}"); x += cw[c] + 1; idx += 1
        parts.append(f"{w}x{rh[r]},0,{y}{{{','.join(cells)}}}" if ncol > 1 else cells[0])
        y += rh[r] + 1
    parts.append(f"{w}x{cs},0,{th + 1},{core_id}")        # the full-width core strip
    g = f"{w}x{h},0,0[{','.join(parts)}]"
    return f"{_layout_csum(g)},{g}"


def _single_window(mux, team, project, register, session, layout_name, orient="v") -> dict:
    """All agents + a core pane in ONE window, applying `layout_name` after each split. `orient`
    is the core-pane table style ('h' side-by-side for a wide/short core slice)."""
    panes = {}
    first = True
    for a in team:
        role = a["role"]
        if first:
            pid = mux.new_session(session, role, _launch(a, project))
            first = False
        else:
            pid = mux.split_window(f"{session}:0", _launch(a, project))
            mux.select_layout(session, layout_name)
        panes[role] = pid
        register(role, pid)
        mux.set_pane_title(pid, _label(a))
    core_pid = mux.split_window(f"{session}:0", _core_view_cmd(project, orient))
    mux.set_pane_title(core_pid, "core - control tower")
    mux.select_layout(session, layout_name)
    return panes


def tiled(mux, team, project, register, session) -> dict:
    """One window, every agent + core, tiled."""
    return _single_window(mux, team, project, register, session, "tiled")


def main_vertical(mux, team, project, register, session) -> dict:
    """NORMAL LEAD-LEFT (core-only): lead big top-left, a live core status pane small bottom-left UNDER
    it, and the workers stacked down the right column. NO files-IDE pane. Custom geometry via
    _main_vertical_layout (the bare psmux 'main-vertical' preset would dump the core on the right with the
    workers). select-layout fills cells by pane ORDER, and _main_vertical_layout's cell order is
    lead -> core -> workers, so we CREATE panes in exactly that order. (The files-IDE LEAD-LEFT variant is
    the experimental 'lead-left-ide' builder.)"""
    a0 = team[0]
    main_id = mux.new_session(session, a0["role"], _launch(a0, project))   # cell 1: lead (top-left)
    panes = {a0["role"]: main_id}
    register(a0["role"], main_id)
    mux.set_pane_title(main_id, _label(a0))
    core_id = mux.split_window(f"{session}:0", _core_view_cmd(project, "h"))  # cell 2: core (wide/short bottom-left strip -> 'h' side-by-side tables)
    mux.select_layout(session, "tiled")                                   # rebalance so the next split has room
    mux.set_pane_title(core_id, "core - control tower")
    worker_ids = []
    for a in team[1:]:                                                    # cells 3+: workers (right column)
        pid = mux.split_window(f"{session}:0", _launch(a, project))
        mux.select_layout(session, "tiled")
        worker_ids.append(pid)
        panes[a["role"]] = pid
        register(a["role"], pid)
        mux.set_pane_title(pid, _label(a))
    w, h = mux.window_size(session)
    mux.select_layout(session, _main_vertical_layout(w, h, main_id[1:], core_id[1:],
                                                     [p[1:] for p in worker_ids]))
    mux.select_pane(main_id)                                # land the user in the lead
    return panes


def lead_left_ide(mux, team, project, register, session) -> dict:
    """EXPERIMENTAL LEAD-LEFT + files-IDE: lead big top-left, the workers stacked under it down the left
    column, and the 3-column files-IDE (core | explorer | editor) as a prominent FULL-HEIGHT column on the
    right. No separate core pane -- the files-IDE already shows the live core on its left, so a second one
    is a duplicate. Custom geometry via a layout string (the bare psmux 'main-vertical' preset would cram
    the files-IDE into a tiny right-stack cell). select-layout fills cells by pane ORDER, so we CREATE
    panes lead -> workers -> files to match _main_vertical_with_files's cell order."""
    panes, worker_ids, main_id = {}, [], None
    for i, a in enumerate(team):
        role = a["role"]
        if i == 0:
            pid = mux.new_session(session, role, _launch(a, project)); main_id = pid
        else:
            pid = mux.split_window(f"{session}:0", _launch(a, project))
            mux.select_layout(session, "tiled")             # rebalance so the next split has room
            worker_ids.append(pid)
        panes[role] = pid
        register(role, pid)
        mux.set_pane_title(pid, _label(a))
    files_id = mux.split_window(f"{session}:0", _files_view_cmd(project))   # the files-IDE (last cell, right)
    mux.select_layout(session, "tiled")
    mux.set_pane_title(files_id, "files - core | explorer | editor")
    w, h = mux.window_size(session)
    mux.select_layout(session, _main_vertical_with_files(w, h, main_id[1:],
                                                        [p[1:] for p in worker_ids], files_id[1:]))
    mux.select_pane(main_id)                                # land the user in the lead
    return panes


def main_horizontal(mux, team, project, register, session) -> dict:
    """One window: lead dominant on top, the rest in a row below (psmux main-horizontal)."""
    return _single_window(mux, team, project, register, session, "main-horizontal")


def _files_view_cmd(project) -> list:
    """The Files pane command — the IDE-style file explorer (full path for psmux + the project dir)."""
    from . import frozen
    return frozen.files_view_cmd(project)


def _sidebyside_core_layout(w: int, h: int, agent_ids: list, core_id: str) -> str:
    """psmux/tmux layout string for NORMAL Side-by-side (even-horizontal), core-only: the agents fill a
    single ROW of equal-width side-by-side columns on top, and the live core is a FULL-WIDTH status strip
    pinned to the BOTTOM. NO files pane. Ids are the NUMERIC part of '%N'; the cell order is agents (row)
    then the core strip, so the caller MUST create panes agents-first, core-last (select-layout fills cells
    by pane order and ignores the ids in the string). Scales to any agent count; tmux re-scales on resize."""
    cs = min(12, max(4, h // 5))                          # core strip height (bottom, full width)
    th = h - cs - 1                                       # top region for the agent row
    n = len(agent_ids)
    availw = w - (n - 1); cw = [availw // n] * n          # column widths sum to w (n-1 separators)
    for i in range(availw - sum(cw)):
        cw[i] += 1
    x, cells = 0, []
    for i, pid in enumerate(agent_ids):
        cells.append(f"{cw[i]}x{th},{x},0,{pid}"); x += cw[i] + 1
    row = f"{w}x{th},0,0{{{','.join(cells)}}}" if n > 1 else cells[0]
    core = f"{w}x{cs},0,{th + 1},{core_id}"               # the full-width core strip
    g = f"{w}x{h},0,0[{row},{core}]"
    return f"{_layout_csum(g)},{g}"


def even_horizontal(mux, team, project, register, session) -> dict:
    """NORMAL Side-by-side (core-only): the agents in a single ROW of side-by-side columns, with the live
    core as a full-width status strip pinned to the bottom. NO files pane. Applied at ALL agent counts via
    a custom geometry string (the bare psmux 'even-horizontal' preset gives no core strip). select-layout
    fills cells by pane ORDER, and _sidebyside_core_layout's cell order is agents -> core, so we CREATE the
    agents first and the core LAST."""
    panes, agent_ids = {}, []
    for i, a in enumerate(team):                                          # cells 1..n: the agents (row)
        role = a["role"]
        if i == 0:
            pid = mux.new_session(session, role, _launch(a, project))
        else:
            pid = mux.split_window(f"{session}:0", _launch(a, project))
            mux.select_layout(session, "tiled")                          # rebalance so the next split has room
        agent_ids.append(pid)
        panes[role] = pid
        register(role, pid)
        mux.set_pane_title(pid, _label(a))
    core_id = mux.split_window(f"{session}:0", _core_view_cmd(project, "h"))   # last cell: core (wide/short strip)
    mux.select_layout(session, "tiled")
    mux.set_pane_title(core_id, "core - control tower")
    w, h = mux.window_size(session)
    mux.select_layout(session, _sidebyside_core_layout(w, h, [p[1:] for p in agent_ids], core_id[1:]))
    mux.select_pane(agent_ids[0])                                         # land the user in the lead
    return panes


def even_vertical(mux, team, project, register, session) -> dict:
    """One window: agents STACKED down the height (psmux even-vertical) — fits tall/portrait screens
    with a small team. Crowds at high counts; use 'tiled' (grid) or 'hub' (tabs) there. The core
    slice is wide/short like the stack, so its tables go side by side ('h')."""
    return _single_window(mux, team, project, register, session, "even-vertical", orient="h")


def hub(mux, team, project, register, session) -> dict:
    """#3 Hub: each agent gets its own window, and EVERY window carries a core strip (bottom ~25%)
    so the control tower stays visible whichever agent you're viewing. (psmux can only split by a
    WINDOW target, never a pane id, so the core strip splits f"{session}:{i}".)"""
    panes = {}
    for i, a in enumerate(team):
        role = a["role"]
        if i == 0:
            pid = mux.new_session(session, role, _launch(a, project))
        else:
            pid = mux.new_window(session, role, _launch(a, project))
        panes[role] = pid
        register(role, pid)
        mux.set_pane_title(pid, _label(a))
        # core is a wide/short strip here -> render its tables side by side ('h'), not stacked
        core_pid = mux.split_window(f"{session}:{i}", _core_view_cmd(project, "h"), size=25)  # split the WINDOW
        mux.set_pane_title(core_pid, "core - control tower")
    return panes


def _chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


_PER_PAGE = 6   # agents per window in the paged (scale) layouts — a readability knob


def panes_per_window(layout: str, n: int) -> int:
    """Panes in the DENSEST single window for `layout` with `n` agents — this drives the cockpit font
    (it scales to the most crowded window you'll look at, not the raw agent count). hub/pages stay
    readable at scale because each tab holds few panes; tiled/even pack EVERY agent into one window."""
    if layout == "hub":       return 2                                  # 1 agent + a core strip / tab
    if layout == "pages":     return min(n, _PER_PAGE) + 1              # ~6 agents + a core strip / tab
    return n + 1                                                        # single-window: all agents + core


def pages(mux, team, project, register, session) -> dict:
    """SCALE: agents spread across windows, ~6 per window, each window a GRID with a full-width core
    strip pinned to the bottom. Tab between pages. Unlike 'tiled' (everyone in ONE grid, the core
    just another equal tile), pages caps each tab at a readable handful and keeps the control tower
    as its own strip on every page -- that strip + the page tabs are what set it apart from tiled."""
    panes = {}
    for wi, page in enumerate(_chunks(team, _PER_PAGE)):
        win = f"{session}:{wi}"                       # split/tile by WINDOW target (psmux can't use pane ids)
        page_ids = []
        for j, a in enumerate(page):
            role = a["role"]
            if wi == 0 and j == 0:
                pid = mux.new_session(session, role, _launch(a, project))
            elif j == 0:
                pid = mux.new_window(session, role, _launch(a, project))
            else:
                pid = mux.split_window(win, _launch(a, project))
                mux.select_layout(win, "tiled")       # rebalance so the next split has room
            page_ids.append(pid)
            panes[role] = pid
            register(role, pid)
            mux.set_pane_title(pid, _label(a))
        core_pid = mux.split_window(win, _core_view_cmd(project, "h"), size=20)
        mux.set_pane_title(core_pid, "core - control tower")
        w, h = mux.window_size(win)                   # grid agents on top, core strip on the bottom
        mux.select_layout(win, _grid_strip_layout(w, h, [p[1:] for p in page_ids], core_pid[1:]))
    return panes


LAYOUTS = {"tiled": tiled, "hub": hub, "main-vertical": main_vertical, "main-horizontal": main_horizontal,
           "even-horizontal": even_horizontal, "even-vertical": even_vertical, "lead-left-ide": lead_left_ide,
           "pages": pages}


def get(name: str):
    """Return the builder for `name`, falling back to tiled (with a warning) if unknown."""
    builder = LAYOUTS.get(name)
    if builder is None:
        print(f"warning: unknown layout {name!r}, using 'tiled'")
        return tiled
    return builder
