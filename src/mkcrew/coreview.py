# src/mkcrew/coreview.py
"""The MKCREW core view: turn the event log into a 'who is doing what' frame.

ASCII-only output (psmux/Windows-Terminal encoding safety). The view is an
INDEPENDENT reader of the durable event log -- no daemon push required.
"""
import os
import shutil
import sys
import time

from . import config, projections
from .eventlog import EventLog

import re as _re

# ── Blueprint HUD palette (ANSI 256) — matches the Studio: cyan = structure/headers,
#    amber = active, lime = done/live, red = failed, dim = idle/secondary. Status is shown as a
#    COLOURED WORD (never colour alone — the word itself carries the meaning). ──
_CYAN, _CYAN_D = "\033[38;5;45m", "\033[38;5;31m"
_AMBER, _LIME, _RED = "\033[38;5;215m", "\033[38;5;84m", "\033[38;5;203m"
_DIM, _TX = "\033[38;5;245m", "\033[38;5;252m"
_B, _R = "\033[1m", "\033[0m"
_AZURE, _RESET = _CYAN, _R                                   # back-compat aliases

_ANSI_RE = _re.compile(r"\033\[[0-9;]*m")
def _vlen(s):
    """Visible width — length ignoring ANSI codes, so coloured cells still align in a grid."""
    return len(_ANSI_RE.sub("", s))


def _state_color(s):
    """ANSI colour for a STATE/STATUS value: done=lime, busy=amber, failed=red, idle=dim."""
    t = (s or "").lower()
    if any(k in t for k in ("incomplete", "fail", "panic", "giveup", "error", "zombie", "timeout")):
        return _RED
    if any(k in t for k in ("done", "reachable", "complete")):
        return _LIME
    if any(k in t for k in ("run", "work", "busy", "deliver", "inject", "pending", "rewake", "wait")):
        return _AMBER
    return _DIM                                              # idle / unknown


_TERMINAL_STATUSES = ("done", "fail", "panic", "giveup", "cancel")


def _waiting_on(role, jobs):
    """The teammate `role` is BLOCKED waiting on (its newest in-flight outgoing ask), or None.
    `mk ask` blocks the asker until the reply lands — so an agent with a non-terminal outgoing job
    is 'waiting -> to', NOT 'idle' (labelling a blocked lead 'idle' hid a working cockpit from the
    user: the lead pane looks silent while the tower said nothing was happening)."""
    for j in reversed(list(jobs or [])):
        if getattr(j, "frm", None) == role and \
                not any(k in (getattr(j, "status", "") or "").lower() for k in _TERMINAL_STATUSES):
            return getattr(j, "to", None)
    return None


def _grid(headers, rows, styles=None):
    """Headers + rows -> a Unicode box-drawing table.  Column widths use VISIBLE length, so a
    `styles` map {col_index: fn(value)->ansi} can colour a column without breaking alignment.
    Headers are cyan + bold; borders are a subtle dim-cyan rule (gridlines don't fight the data)."""
    styles = styles or {}
    body = [[str(c) for c in r] for r in rows]
    widths = [max([len(headers[i])] + [len(r[i]) for r in body]) for i in range(len(headers))]
    V = f"{_CYAN_D}│{_R}"                               # │ vertical border
    def hbar(l, m, r):
        return f"{_CYAN_D}{l}" + m.join("─" * (w + 2) for w in widths) + f"{r}{_R}"
    def row(cells, colors):
        out = [(f" {col}{c}{_R}{' ' * (w - len(c))} " if col else f" {c:<{w}} ")
               for c, w, col in zip(cells, widths, colors)]
        return V + V.join(out) + V
    head = row(headers, [f"{_CYAN}{_B}"] * len(headers))
    drawn = [row(r, [styles.get(i, lambda _v: _TX)(r[i]) for i in range(len(r))]) for r in body]
    return "\n".join([hbar("┌", "┬", "┐"), head,
                      hbar("├", "┼", "┤"), *drawn,
                      hbar("└", "┴", "┘")])


def _one_line(s, n=30):
    """Collapse to a single line and truncate to n chars (with an ellipsis) for a table cell."""
    s = " ".join((s or "").split())
    return (s[:n - 1] + "…") if len(s) > n else s       # … ellipsis (truncation-strategy rule)


def _width(label, values, cap, floor=None):
    """Readable bounded column width for terminal panes: wide enough for content, never runaway."""
    floor = floor or len(label)
    longest = max([len(label)] + [len(str(v or "")) for v in values])
    return max(floor, min(cap, longest))


def _fill(n):
    """`n` padding spaces that a psmux/tmux pane will KEEP, as `\\033[1m  \\033[0m` (BOLD).

    A pane treats a space whose only attribute is a foreground colour (or none) as a BLANK cell and
    trims long runs of them as erased trailing cells — which collapsed a coloured row's right border
    onto its text (`│ TEAM│`, `│ main … -│`) and ate inter-column padding in headers
    (`ACTIONKEYACTION`, `nNew file`). The BOLD attribute marks the cells non-blank so the pane keeps
    them, and bold on a space has no glyph to embolden, so it is visually identical to a plain space.
    (Verified by capture-pane probes: fg-only/plain pad is trimmed; bold pad survives at any length.)"""
    return f"{_B}{' ' * n}{_R}" if n > 0 else ""


def _cell(value, width, color=""):
    """Left-align a single table cell while keeping ANSI colour out of the width calculation. The pad
    is BOLD (`_fill`) so a cockpit pane keeps it; an UNCOLOURED cell is only ever used inside an outer
    (already bold) span, so its plain pad is kept too."""
    text = _one_line(str(value or "-"), width)
    pad = width - _vlen(text)
    if color:
        return f"{color}{text}{_R}{_fill(pad)}"
    return f"{text}{' ' * pad}"


def _pad_line(line, width):
    """Right-pad a fully-assembled line to `width` VISIBLE columns with BOLD spaces (`_fill`) so the
    box's right border stays aligned in a psmux/tmux pane (which would otherwise trim the trailing
    plain pad and hug the text). Width is the ANSI-stripped visible length, so coloured rows align."""
    pad = width - _vlen(line)
    return line + _fill(pad) if pad > 0 else line


def _table_header(columns):
    widths = [w for _, w in columns]
    header = "  " + "  ".join(_cell(name, width, f"{_CYAN}{_B}") for name, width in columns)
    rule = f"  {_CYAN_D}" + "  ".join("─" * width for width in widths) + f"{_R}"
    return [header, rule]


def _beside(left, right, gap=3):
    """Two text blocks side by side: the left block padded to its widest VISIBLE line PLUS a `gap`
    gutter, then the right block. The padding is psmux-safe (`_pad_line`), so the right column never
    slides onto the left in a cockpit pane and the gutter never collapses (the `orient='h'` columns
    used to collide). A row with no right content is left unpadded — the box frame pads it — so the
    taller table's extra rows keep their column instead of falling back to column 0."""
    L, R = left.split("\n"), right.split("\n")
    w = max((_vlen(s) for s in L), default=0)
    out = []
    for i in range(max(len(L), len(R))):
        l = L[i] if i < len(L) else ""
        r = R[i] if i < len(R) else ""
        out.append(_pad_line(l, w + gap) + r if r else l)
    return "\n".join(out)


def _label(text):
    """A section label in the HUD style: cyan + bold."""
    return f"{_CYAN}{_B}{text}{_R}"


def _box(rows, width):
    """Wrap content rows in a box of inner `width` (the 'table' frame).  Each row is ('l', text)
    padded to width, or ('rule', _) for a in-frame ├──┤ separator.  Padding uses VISIBLE width so
    coloured cells still align inside the borders."""
    V = f"{_CYAN_D}│{_R}"
    def bar(l, r):
        return f"{_CYAN_D}{l}{'─' * (width + 2)}{r}{_R}"
    out = [bar("┌", "┐")]
    for kind, text in rows:
        out.append(bar("├", "┤") if kind == "rule"
                   else f"{V} {_pad_line(text, width)} {V}")
    out.append(bar("└", "┘"))
    return "\n".join(out)


def _status_rail(agents, jobs, roster):
    """Compact cockpit summary for the core header."""
    total = len(roster or agents)
    busy = sum(1 for info in agents.values() if _state_color(info.get("state")) == _AMBER)
    failed = sum(1 for info in agents.values() if _state_color(info.get("state")) == _RED)
    latest = jobs[-1].status if jobs else "idle"
    return (f"{_DIM}team {_TX}{total}{_R}  "
            f"{_DIM}busy {_AMBER}{busy}{_R}  "
            f"{_DIM}alerts {_RED if failed else _DIM}{failed}{_R}  "
            f"{_DIM}latest {_state_color(latest)}{latest}{_R}")


def _team_block(agents, roster, jobs=()):
    """TEAM section as a compact roster table: role, CLI, state, and current task at a glance."""
    out = [_label("TEAM")]
    rows = []

    def _st(role, info):
        state = info.get("state", "idle")
        if state in ("idle", "", "-", None):                 # blocked asker is WAITING, never 'idle'
            to = _waiting_on(role, jobs)
            if to:
                return f"waiting→{to}"
        return state

    if roster:
        for a in roster:
            role = a.get("role", "?")
            info = agents.get(role, {})
            rows.append((role, a.get("provider", "claude"), _st(role, info),
                         info.get("task") or info.get("job") or "-"))
    elif agents:
        for name, info in agents.items():
            rows.append((name, info.get("provider", "agent"), _st(name, info),
                         info.get("task") or info.get("job") or "-"))
    if not rows:
        out.append(f"  {_DIM}(none){_R}")
        return out

    columns = [
        ("ROLE", _width("ROLE", [r[0] for r in rows], 14)),
        ("CLI", _width("CLI", [r[1] for r in rows], 10)),
        ("STATE", _width("STATE", [r[2] for r in rows], 16)),   # fits 'waiting→worker2'
        ("TASK", _width("TASK", [r[3] for r in rows], 34, floor=18)),
    ]
    out.extend(_table_header(columns))
    for role, cli, state, task in rows:
        out.append("  " + "  ".join([
            _cell(role, columns[0][1], f"{_CYAN}{_B}"),
            _cell(cli, columns[1][1], _DIM),
            _cell(state, columns[2][1], _state_color(state)),
            _cell(task, columns[3][1], _TX if task != "-" else _DIM),
        ]))
    return out


def _jobs_block(jobs, recent):
    """TASKS section as a clean fixed-column table, newest-first, with hidden-count context."""
    jobs = list(jobs)
    visible = jobs[-recent:][::-1]
    label = _label("TASKS")
    if len(jobs) > recent:
        label += f"  {_DIM}(newest {recent} of {len(jobs)}){_R}"
    out = [label]
    if not visible:
        out.append(f"  {_DIM}(no tasks yet){_R}")
        return out

    columns = [
        ("JOB", _width("JOB", [j.id for j in visible], 16)),
        ("FROM", _width("FROM", [j.frm for j in visible], 10)),
        ("TO", _width("TO", [j.to for j in visible], 12)),
        ("STATUS", _width("STATUS", [j.status for j in visible], 14)),
    ]
    out.extend(_table_header(columns))
    for j in visible:
        out.append("  " + "  ".join([
            _cell(j.id, columns[0][1], f"{_CYAN}{_B}"),
            _cell(j.frm, columns[1][1], _TX),
            _cell(j.to, columns[2][1], _TX),
            _cell(j.status, columns[3][1], _state_color(j.status)),
        ]))
    return out


# Cockpit keyboard cheatsheet — the REAL binds, not invented, GROUPED so a user can tell the two
# contexts apart: the SAME physical key does different things under the Ctrl-b prefix vs. bare in the
# Files pane (e.g. z, [, ], n, Esc). Sources — all verified against the code / `psmux list-keys`:
#   • Cockpit (Ctrl-b …): a/A, x/X and Esc are REBOUND by layouts.apply_chrome (display-popup
#     add-workspace; confirm-before kill-window; send-keys -X cancel to unstick copy-mode). n/d/z/o/[/c
#     are genuinely useful psmux DEFAULTS surfaced here (curated, NOT every default): n next-window
#     (status-right advertises it as :switch), d detach-client, z resize-pane -Z (zoom/fullscreen a
#     pane), o select-pane (arrows switch too), [ copy-mode (scrollback), c new-window.
#   • Files pane: the bare keys from filesview.FilesApp.BINDINGS — z/[/] collapse panels; n/f/e/Ctrl-s/
#     r/Esc/Ctrl-t drive the explorer + editor. The max/core/files/close buttons mirror z/[/]/].
# Edit these lists if the binds change. Rendered as two 2-column KEY→ACTION tables (see _hotkeys_block).
_HOTKEYS = {
    "Cockpit (Ctrl-b …)": [
        ("a / A", "Add workspace"),    # apply_chrome: display-popup add-workspace
        ("n", "Switch ws"),            # psmux default next-window (status-right n:switch)
        ("x / X", "Close ws"),         # apply_chrome: confirm-before kill-window
        ("d", "Detach"),               # psmux default detach-client
        ("z", "Zoom pane"),            # psmux default resize-pane -Z (fullscreen the pane)
        ("o", "Switch pane"),          # psmux default select-pane -t + (arrows switch too)
        ("[", "Scroll mode"),          # psmux default copy-mode (scrollback)
        ("c", "New window"),           # psmux default new-window
        ("Esc", "Unstick"),            # apply_chrome: send-keys -X cancel (exit copy-mode)
    ],
    "Files pane": [
        ("z", "Max editor"),           # filesview BINDINGS: toggle_maximize
        ("[", "Core"),                 # filesview BINDINGS: toggle_core
        ("]", "Explorer"),             # filesview BINDINGS: toggle_tree
        ("n", "New file"),             # filesview BINDINGS: new_file
        ("f", "New folder"),           # filesview BINDINGS: new_folder
        ("e", "Edit"),                 # filesview BINDINGS: edit
        ("Ctrl-s", "Save"),            # filesview BINDINGS: save (ctrl+s)
        ("r", "Reload"),               # filesview BINDINGS: reload
        ("Esc", "View"),               # filesview BINDINGS: view (also cancels a new-file entry)
        ("Ctrl-t", "Tree"),            # filesview BINDINGS: focus_tree (ctrl+t)
    ],
}
_HOTKEYS_NOTE = "mouse: max/core/files/close buttons"   # Files control-strip buttons mirror z [ ] ]


def _columns2(items):
    """Split a flat KEY→ACTION list into two NEWSPAPER columns (down the left, then down the right) so a
    long group renders half as tall in the short core pane. Returns [(left_pair, right_pair_or_None), …]."""
    half = (len(items) + 1) // 2
    left, right = items[:half], items[half:]
    return [(left[i], right[i] if i < len(right) else None) for i in range(half)]


def _hotkeys_block():
    """HOTKEYS section: the cockpit's REAL keybinds, GROUPED into a 'Cockpit (Ctrl-b …)' prefix group and
    a 'Files pane' bare-key group, each a compact 2-column KEY→ACTION table in the same HUD style as
    TEAM/TASKS (cyan headers/keys, `_TX` actions). One shared column width across BOTH groups keeps the
    tables aligned under a single header. Static — fixed chrome, not event-log state — so it needs no args
    and renders even on an empty cockpit; self-contained (one call site in render_core)."""
    all_keys = [k for group in _HOTKEYS.values() for k, _ in group]
    all_actions = [a for group in _HOTKEYS.values() for _, a in group]
    kw = _width("KEY", all_keys, 8, floor=5)
    aw = _width("ACTION", all_actions, 16, floor=11)
    columns = [("KEY", kw), ("ACTION", aw), ("KEY", kw), ("ACTION", aw)]
    out = [_label("HOTKEYS")]
    out.extend(_table_header(columns))
    for group, keys in _HOTKEYS.items():
        out.append(f"  {_CYAN_D}{_B}{group}{_R}")            # dim-cyan sub-header per context
        for (lk, la), right in _columns2(keys):
            cells = [_cell(lk, kw, f"{_CYAN}{_B}"), _cell(la, aw, _TX)]
            if right:                                        # short trailing row (odd group) is box-padded
                rk, ra = right
                cells += [_cell(rk, kw, f"{_CYAN}{_B}"), _cell(ra, aw, _TX)]
            out.append("  " + "  ".join(cells))
    out.append(f"  {_DIM}{_HOTKEYS_NOTE}{_R}")
    return out


def _compact_status(agents, jobs, roster):
    total = len(roster or agents)
    busy = sum(1 for info in agents.values() if _state_color(info.get("state")) == _AMBER)
    failed = sum(1 for info in agents.values() if _state_color(info.get("state")) == _RED)
    latest = jobs[-1].status if jobs else "idle"
    return (f"{_DIM}team {_TX}{total}{_R}  {_DIM}busy {_AMBER}{busy}{_R}  "
            f"{_DIM}err {_RED if failed else _DIM}{failed}{_R}  {_state_color(latest)}{_one_line(latest, 7)}{_R}")


def _compact_core(agents, jobs, recent, roster, width=30):
    """Narrow core frame for the Files IDE's left column.

    The full core table is intentionally wide. Inside `filesview` the core column is narrow, so the
    full frame wrapped (the box drawing split across rows and interleaved with the file tree). This
    renders a bounded frame whose OUTER width is `width + 4` (│ + space + width + space + │); the
    caller (filesview) sizes the `#core` column to hold that so it never wraps — keep the two in sync."""
    rows = [("l", f"{_CYAN}{_B}MKCREW core{_R}"), ("l", _compact_status(agents, jobs, roster)), ("rule", "")]

    rows.append(("l", _label("TEAM")))
    team_rows = []

    def _st(role, info):                                     # compact rail: '→worker2' fits the 8-cell
        state = info.get("state", "idle")
        if state in ("idle", "", "-", None):
            to = _waiting_on(role, jobs)
            if to:
                return f"→{to}"
        return state

    if roster:
        for a in roster:
            role = a.get("role", "?")
            info = agents.get(role, {})
            team_rows.append((role, a.get("provider", "claude"), _st(role, info)))
    elif agents:
        for role, info in agents.items():
            team_rows.append((role, info.get("provider", "agent"), _st(role, info)))
    if not team_rows:
        rows.append(("l", f"  {_DIM}(none){_R}"))
    else:
        rows.append(("l", f"  {_CYAN}{_B}{_cell('ROLE', 9)} {_cell('CLI', 7)} {_cell('STATE', 8)}{_R}"))
        rows.append(("l", f"  {_CYAN_D}{'─' * 9} {'─' * 7} {'─' * 8}{_R}"))
        for role, cli, state in team_rows[:6]:
            rows.append(("l", "  " + " ".join([
                _cell(role, 9, f"{_CYAN}{_B}"),
                _cell(cli, 7, _DIM),
                _cell(state, 8, _state_color(state)),
            ])))
        if len(team_rows) > 6:
            rows.append(("l", f"  {_DIM}+{len(team_rows) - 6} more{_R}"))

    rows.append(("rule", ""))
    rows.append(("l", _label("TASKS")))
    visible = list(jobs)[-recent:][::-1]
    if not visible:
        rows.append(("l", f"  {_DIM}(no tasks yet){_R}"))
    else:
        rows.append(("l", f"  {_CYAN}{_B}{_cell('JOB', 12)} {_cell('TO', 7)} {_cell('STATUS', 7)}{_R}"))
        rows.append(("l", f"  {_CYAN_D}{'─' * 12} {'─' * 7} {'─' * 7}{_R}"))
        for j in visible[:5]:
            rows.append(("l", "  " + " ".join([
                _cell(j.id, 12, f"{_CYAN}{_B}"),
                _cell(j.to, 7, _TX),
                _cell(j.status, 7, _state_color(j.status)),
            ])))

    rows.append(("rule", ""))
    rows.append(("l", _label("HOTKEYS")))
    for line in ("n file   f folder", "e edit   ^s save", "^←/^→ scroll"):
        rows.append(("l", f"  {_TX}{line}{_R}"))
    return _box(rows, width)


def render_core(agents, jobs, recent=5, roster=None, orient="v", compact=False, width=30,
                mode="standard"):
    """Pure: (agents state, jobs[, roster]) -> the styled core-frame text (ANSI + box-drawing).

    Two sections, TEAM (who is doing what) and JOBS (newest `recent`, newest-first, with a
    hidden-count header). orient="h" puts them SIDE BY SIDE for wide/short core strips (Hub,
    Pages) where a stacked second table scrolls out of view; "v" stacks them (default).
    `compact=True` renders the narrow frame for the Files-IDE left rail at inner `width` (its outer
    width is `width + 4`; the caller sizes the column to match so it never wraps)."""
    if compact:
        return _compact_core(agents, jobs, recent, roster, width)
    team, jobsb = _team_block(agents, roster, jobs), _jobs_block(jobs, recent)
    rail = _status_rail(agents, jobs, roster)
    headline = f"{_CYAN}{_B}MKCREW core{_R}   {rail}"
    if mode and mode != "standard":                          # badge ONLY when non-default: standard
        headline += f"  {_DIM}mode {_AMBER}{mode}{_R}"       # cockpits render byte-identical to before
    if orient == "h":
        # TEAM | TASKS side by side, with a gutter, the shorter table padded, all wrapped in the SAME
        # box frame as the vertical view (the strip used to have no frame and ragged, colliding columns).
        combined = _beside("\n".join(team), "\n".join(jobsb)).split("\n")
        rows = [("l", headline), ("rule", "")] + [("l", c) for c in combined]
        width = max(_vlen(t) for k, t in rows if k == "l")
        return _box(rows, width)
    keys = _hotkeys_block()                                  # keyboard cheatsheet, below TASKS
    rows = ([("l", headline), ("rule", "")]
            + [("l", x) for x in team] + [("rule", "")] + [("l", x) for x in jobsb]
            + [("rule", "")] + [("l", x) for x in keys])
    width = max(_vlen(t) for k, t in rows if k == "l")
    return _box(rows, width)


def frame_from_events(events, roster=None, orient="v", mode="standard"):
    """Pure: a list of Event -> the rendered core frame (team + recent-jobs tables)."""
    return render_core(projections.agents(events), list(projections.jobs(events).values()),
                       roster=roster, orient=orient, mode=mode)


def _read_roster(project=None):
    """Read the configured team (role + provider) from <project>/.mkcrew/team.config, or CWD if
    no project given. No side effects (unlike teamconfig.load_team). Returns a list or None."""
    import json
    from pathlib import Path
    try:
        base = Path(project) if project else Path.cwd()
        cfg = base / ".mkcrew" / "team.config"
        if not cfg.exists():
            return None
        return json.loads(cfg.read_text(encoding="utf-8")).get("agents") or None
    except Exception:
        return None


def status_main():
    """`mk status`: print one core frame read from the durable event log."""
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")   # box-drawing / colour safety when piped
    except Exception:
        pass
    log = EventLog(config.event_db())
    try:
        print(frame_from_events(log.replay()))
    finally:
        log.close()
    return 0


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _center(frame, width):
    """Left-pad every line equally so the whole frame sits horizontally centred in `width` columns
    (preserves the tables' internal alignment; a no-op once the content is as wide as the pane)."""
    lines = frame.split("\n")
    block = max((_vlen(l) for l in lines), default=0)
    margin = " " * max(0, (width - block) // 2)
    return "\n".join(margin + l for l in lines)


def coreview_run(iterations=None, interval=2.0, clear=_clear_screen, roster=None, orient="v", width=None, project=None):
    """Render the core frame on a loop, horizontally centred in the pane. iterations=None loops
    forever (live pane); a finite count is used by tests. `width` overrides the detected pane width
    (for tests); `project` selects that project's event DB. One EventLog read connection for the run."""
    log = EventLog(config.event_db(project))
    n = 0
    try:
        while iterations is None or n < iterations:
            clear()
            w = width or shutil.get_terminal_size((80, 24)).columns
            # per-tick mode read: `mk mode` persists to team.config, so the badge follows a live
            # switch on the next refresh (tiny json read; no daemon round-trip needed).
            try:
                from . import teamconfig
                mode = teamconfig.load_mode(project) if project else "standard"
            except Exception:
                mode = "standard"
            frame = frame_from_events(log.replay(), roster=roster, orient=orient, mode=mode)
            print(_center(frame, w) + _RESET, flush=True)
            n += 1
            if iterations is not None and n >= iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()


def coreview_main():
    """`mk-core-view`: live self-updating core pane (runs forever). argv[1] (if given) is the
    project dir (loads the team roster); argv[2] is the orientation ('h' for wide/short strips
    like Hub/Pages, 'v' stacked default) -- the layout builder picks it per template."""
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")   # belt-and-suspenders for the pane
    except Exception:
        pass
    project = sys.argv[1] if len(sys.argv) > 1 else None
    orient = sys.argv[2] if len(sys.argv) > 2 else "v"
    coreview_run(roster=_read_roster(project), orient=orient, project=project)
    return 0
