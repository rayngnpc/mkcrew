# src/mkcrew/coreview.py
"""The MKCREW tower (core view): turn the event log into a 'who is doing what' frame.

The view is an INDEPENDENT reader of the durable event log -- no daemon push required. It is drawn
without a box of its own (the pane border already frames it) and FITS THE PANE: given the pane's
columns and rows it picks the richest arrangement that fits, so nothing scrolls out of a short strip.

Look ("dark cockpit"): nothing is lit unless it means something. Text is the terminal's own
foreground, secondary text is grey, and colour is reserved for state -- amber = in motion, green =
settled, red = needs attention, azure = identity / standing by. State is always a WORD first (never
colour alone); the light in the left gutter repeats it as a shape. Glyphs stay inside WGL4 so they
render in conhost/psmux as well as any Linux terminal.
"""
import os
import shutil
import sys
import time

from . import config, projections
from .eventlog import EventLog

import re as _re

# Palette: xterm-256 indices picked to read on dark AND light terminals (mid luminance). Primary
# text uses SGR 39 (the terminal's own foreground) instead of a fixed near-white.
_AZURE, _AMBER = "\033[38;5;33m", "\033[38;5;172m"
_LIME, _RED = "\033[38;5;35m", "\033[38;5;167m"
_VIOLET = "\033[38;5;99m"
_DIM, _RULE, _TX = "\033[38;5;245m", "\033[38;5;240m", "\033[39m"
_B, _R = "\033[1m", "\033[0m"
_CYAN, _CYAN_D, _RESET = _AZURE, _RULE, _R                   # back-compat aliases

_MARK = "⬢"                                                  # the brand hexagon (status bar uses it too)

_ANSI_RE = _re.compile(r"\033\[[0-9;]*m")
def _vlen(s):
    """Visible width — length ignoring ANSI codes, so coloured cells still align in a grid."""
    return len(_ANSI_RE.sub("", s))


def _state_kind(s):
    """Classify a STATE/STATUS word: alert / late / ok / waiting / queued / active / idle."""
    t = (s or "").lower()
    if t.startswith("late-work"):
        return "late"                                        # past its deadline but still producing
    if any(k in t for k in ("incomplete", "fail", "panic", "giveup", "error", "zombie", "timeout")):
        return "alert"
    if any(k in t for k in ("done", "reachable", "complete")):
        return "ok"
    if t.startswith("waiting") or t.startswith("→"):
        return "waiting"                                     # blocked on a teammate's reply
    if "pending" in t:
        return "queued"                                      # accepted, not yet handed to the worker
    if any(k in t for k in ("run", "work", "busy", "deliver", "inject", "rewake", "wait")):
        return "active"
    return "idle"


# kind -> (gutter light, colour). ► in motion, ▲ needs a look, ● settled, ○ not moving (yet).
_KIND = {
    "active":  ("►", _AMBER),
    "queued":  ("○", _AMBER),
    "late":    ("▲", _AMBER),
    "alert":   ("▲", _RED),
    "ok":      ("●", _LIME),
    "waiting": ("○", _AZURE),
    "idle":    ("○", _DIM),
}


def _state_color(s):
    """ANSI colour for a STATE/STATUS value (see _KIND)."""
    return _KIND[_state_kind(s)][1]


def _light(s):
    """The coloured gutter light for a STATE/STATUS value."""
    glyph, color = _KIND[_state_kind(s)]
    return f"{color}{glyph}{_R}"


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


# Pane activity newer than this reads as "still visibly working" -- matches the incident this
# feature exists for: a worker streaming output well past the ask() ceiling must not look idle.
_ACTIVITY_FRESH_SECONDS = 90.0


def _latest_job_for(role, jobs):
    """The most recent job addressed TO `role` (list order is oldest-first, so scan backwards), or
    None.  Mirrors `_waiting_on`'s scan but keyed on `to` instead of `frm` -- used to read a role's
    OWN latest task (in-flight, or its terminal outcome) for the tower's activity states."""
    for j in reversed(list(jobs or [])):
        if getattr(j, "to", None) == role:
            return j
    return None


def _fmt_age(seconds):
    """Compact job-age string for a STATE cell: minutes under an hour ('12m', '49m'), 'HhMM' past
    an hour ('1h05').  Never negative (a clock skew / unknown start reads as '0m')."""
    total_min = int(max(0.0, seconds or 0.0) // 60)
    if total_min < 60:
        return f"{total_min}m"
    h, m = divmod(total_min, 60)
    return f"{h}h{m:02d}"


def _cap_state(s, n=16):
    """Hard cap for a STATE cell string at the column's width budget (the TEAM table caps STATE at
    16).  The table would truncate an overlong cell anyway; this keeps the raw string itself inside
    budget so the contract is explicit, not incidental."""
    return s if len(s) <= n else s[: n - 1] + "…"


def _activity_state(role, state, jobs, activity, now):
    """'working <age>' / 'late-work <age>' when the daemon's /status carried FRESH pane 'activity'
    for `role` -- None on an older daemon (no 'activity' key -> caller falls back to today's state,
    byte-identical) or when the signal is stale/missing.  'working': role's latest job is still
    in-flight ('state' == 'running' from projections.agents).  'late-work': the ask() ceiling
    already gave up on that job (INCOMPLETE) but the pane is STILL visibly producing output --
    exactly the truth the tower hid during the incident (INCOMPLETE read as dead, not late)."""
    if not activity:
        return None
    secs = activity.get(role)
    if secs is None or secs >= _ACTIVITY_FRESH_SECONDS:
        return None
    job = _latest_job_for(role, jobs)
    if job is None:
        return None
    age = _fmt_age(now - (job.created_ts or now))
    if state == "running":
        return _cap_state(f"working {age}")
    if (job.status or "").upper() == "INCOMPLETE":
        return _cap_state(f"late-work {age}")
    return None


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
    trims long runs of them as erased trailing cells — which collapsed a coloured row's columns onto
    each other and ate inter-column padding in headers. The BOLD attribute marks the cells non-blank
    so the pane keeps them, and bold on a space has no glyph to embolden, so it is visually identical
    to a plain space. (Verified by capture-pane probes: fg-only/plain pad is trimmed; bold pad
    survives at any length.)"""
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
    """Right-pad a fully-assembled line to `width` VISIBLE columns with BOLD spaces (`_fill`) so a
    block keeps its width in a psmux/tmux pane (which would otherwise trim the trailing plain pad).
    Width is the ANSI-stripped visible length, so coloured rows align."""
    pad = width - _vlen(line)
    return line + _fill(pad) if pad > 0 else line


def _beside(left, right, gap=3):
    """Two text blocks side by side: the left block padded to its widest VISIBLE line PLUS a `gap`
    gutter, then the right block. The padding is psmux-safe (`_pad_line`), so the right column never
    slides onto the left in a cockpit pane and the gutter never collapses. A row with no right
    content is left unpadded, so the taller table's extra rows keep their column."""
    L, R = left.split("\n"), right.split("\n")
    w = max((_vlen(s) for s in L), default=0)
    out = []
    for i in range(max(len(L), len(R))):
        l = L[i] if i < len(L) else ""
        r = R[i] if i < len(R) else ""
        out.append(_pad_line(l, w + gap) + r if r else l)
    return "\n".join(out)


def _label(text):
    """A section label: bold, in the terminal's own foreground."""
    return f"{_B}{text}{_R}"


# ── rows ──────────────────────────────────────────────────────────────────────────────────────

_DEFAULT_COLS = 96            # width assumed when the caller does not know the pane (tests, pipes)
_DEFAULT_COLS_WIDE = 160      # ... and for orient="h", which is only ever used on wide strips
_SIDE_BY_SIDE_MIN = 132       # below this a strip stacks TEAM over TASKS when the rows allow it
_GUTTER = " "                 # one blank column so text never touches the pane border


def _team_rows(agents, roster, jobs=(), activity=None, now=None):
    """[(role, provider, model, state, task)] in roster order (event order when there is no roster)."""
    now = time.time() if now is None else now

    def _st(role, info):
        state = info.get("state", "idle")
        if state in ("idle", "", "-", None):                 # blocked asker is WAITING, never 'idle'
            to = _waiting_on(role, jobs)
            if to:
                return f"waiting→{to}"                       # wins over working/late-work below
        live = _activity_state(role, state, jobs, activity, now)
        if live is not None:
            return live
        return state or "idle"

    rows = []
    if roster:
        for a in roster:
            role = a.get("role", "?")
            info = agents.get(role, {})
            rows.append((role, a.get("provider", "claude"), a.get("model", ""), _st(role, info),
                         info.get("task") or info.get("job") or ""))
    elif agents:
        for name, info in agents.items():
            rows.append((name, info.get("provider", "agent"), "", _st(name, info),
                         info.get("task") or info.get("job") or ""))
    return rows


def _role_color(role):
    """Identity colour of a seat, the same on every surface: the lead is azure, the planner violet,
    everyone else the terminal's own foreground."""
    if role == "main":
        return f"{_AZURE}{_B}"
    if "plan" in (role or ""):
        return _VIOLET
    return _TX


_CLI_NAME = {"antigravity": "agy"}                           # provider id -> the binary people type


def _cli_text(provider, model, with_model):
    """'codex gpt-6.1-sol' when there is room for the model, else just the CLI name."""
    provider = _CLI_NAME.get(provider, provider)
    m = (model or "").strip()
    if m.startswith("claude-"):
        m = m[len("claude-"):]                               # 'claude opus-5-5', not 'claude claude-opus-5-5'
    return f"{provider} {m}" if (m and with_model) else (provider or "agent")


def _counts(rows):
    kinds = [_state_kind(r[3]) for r in rows]
    return {"agents": len(rows),
            "active": kinds.count("active") + kinds.count("late") + kinds.count("queued"),
            "waiting": kinds.count("waiting"), "alerts": kinds.count("alert")}


def _head(title, mode, counts, width, exact):
    """The one-line header: brand mark + workspace on the left, the crew tally on the right (the
    right part is dropped piece by piece on a narrow pane). `exact` right-aligns to `width`."""
    left = f"{_GUTTER}{_AZURE}{_MARK}{_R} {_B}{title}{_R}"
    if mode and mode != "standard":                          # badge ONLY when non-default
        left += f"   {_DIM}mode{_R} {_B}{mode}{_R}"
    n = counts["agents"]
    parts = [(1, f"{_TX}{n}{_R} {_DIM}agent{'' if n == 1 else 's'}{_R}")]      # (keep-priority, text)
    if counts["active"]:
        parts.append((2, f"{_AMBER}{counts['active']} active{_R}"))
    if counts["waiting"]:
        parts.append((0, f"{_AZURE}{counts['waiting']} waiting{_R}"))
    if counts["alerts"]:
        parts.append((3, f"{_RED}{counts['alerts']} alert{'' if counts['alerts'] == 1 else 's'}{_R}"))
    while parts:
        right = "  ".join(text for _prio, text in parts)
        room = width - _vlen(left) - _vlen(right) - 1
        if not exact:
            return f"{left}   {right}"
        if room >= 2:
            return f"{left}{_fill(room)}{right}"
        parts.remove(min(parts))                             # narrow pane: shed the least important tally
    return left


def _team_block(rows, width, colhdr=True, task=True, limit=None, label=True):
    """TEAM section: one row per seat -- light, role, CLI, state word, and the task in hand."""
    out = [f"{_GUTTER}{_label('TEAM')}"] if label else []
    if not rows:
        out.append(f"{_GUTTER}  {_DIM}(none){_R}")
        return out
    shown = rows if limit is None or len(rows) <= limit else rows[:max(1, limit - 1)]
    with_model = width >= 118
    clis = [_cli_text(p, m, with_model) for _r, p, m, _s, _t in rows]
    wr = _width("ROLE", [r[0] for r in rows], 12)
    wc = _width("CLI", clis, 28 if with_model else 11)
    ws = 16                                                  # fits 'waiting→worker2'; FIXED so rows never jump
    show_cli = width >= 3 + wr + 2 + wc + 2 + ws + 1
    if not show_cli:
        ws = max(5, min(16, width - (3 + wr + 2) - 1))
    wt = width - (3 + wr + 2 + (wc + 2 if show_cli else 0) + ws + 2) - 1
    show_task = task and wt >= 12
    if colhdr:
        cells = [_cell("ROLE", wr, _DIM)] + ([_cell("CLI", wc, _DIM)] if show_cli else []) \
                + [_cell("STATE", ws, _DIM)] + ([f"{_DIM}TASK{_R}"] if show_task else [])
        out.append(f"{_GUTTER}  " + "  ".join(cells))
    for (role, _p, _m, state, text), cli in zip(shown, clis):
        cells = [_cell(role, wr, _role_color(role))] + ([_cell(cli, wc, _DIM)] if show_cli else []) \
                + [_cell(state, ws, _state_color(state))]
        if show_task and text:
            cells.append(f"{_TX}{_one_line(text, wt)}{_R}")
        out.append(f"{_GUTTER}{_light(state)} " + "  ".join(cells))
    if len(shown) < len(rows):
        out.append(f"{_GUTTER}  {_DIM}+{len(rows) - len(shown)} more{_R}")
    return out


def _job_age(j, now):
    """Age column for a task: '' when the start is unknown, 'now' under a minute, else _fmt_age."""
    ts = getattr(j, "created_ts", 0.0) or 0.0
    if not ts:
        return ""
    age = _fmt_age(now - ts)
    return "now" if age == "0m" else age


def _jobs_block(jobs, recent, width=_DEFAULT_COLS, now=None, colhdr=True, names=(), label=True):
    """TASKS section, newest first: light, age, job id, who asked whom, status word, and the ask
    itself. A hidden-count note says how many older tasks rolled off."""
    now = time.time() if now is None else now
    jobs = list(jobs)
    visible = jobs[-recent:][::-1] if recent > 0 else []
    head = f"{_GUTTER}{_label('TASKS')}"
    if len(jobs) > len(visible):
        head += f"  {_DIM}newest {len(visible)} of {len(jobs)}{_R}"
    out = [head] if label else []
    if not jobs:
        hint = "  give the lead a goal; its asks show up here" if width >= 64 else ""
        out.append(f"{_GUTTER}  {_DIM}(no tasks yet){hint}{_R}")
        return out
    if not visible:
        return out
    ages = [_job_age(j, now) for j in visible]
    wa = 4 if any(ages) else 0                               # '1h05'; FIXED widths keep rows from jumping
    wj = _width("JOB", [j.id for j in visible], 16, floor=12)
    wf = _width("FROM", [j.frm for j in visible], 10)        # asks only flow from the lead, so this is stable
    wo = _width("TO", [j.to for j in visible] + list(names), 10)
    wst = 10                                                 # 'INCOMPLETE'
    # A narrow pane sheds columns in this order: the ask's text, its age, who asked (nearly always
    # the lead), then the job id. Light + TO + STATUS is the least a task row can say.
    fixed = 3 + wo + 2 + wst
    show_id = width >= fixed + wj + 2 + 1
    fixed += (wj + 2) if show_id else 0
    show_from = width >= fixed + wf + 3 + 1
    fixed += (wf + 3) if show_from else 0
    show_age = bool(wa) and width >= fixed + wa + 2 + 14
    wtxt = width - fixed - ((wa + 2) if show_age else 0) - 2 - 1
    show_text = wtxt >= 12
    if colhdr:
        cells = ([_cell("AGE", wa, _DIM)] if show_age else []) + ([_cell("JOB", wj, _DIM)] if show_id else []) + [
            (_fill(wf - 4) + _cell("FROM", 4, _DIM) + _fill(3) if show_from else "") + _cell("TO", wo, _DIM),
            _cell("STATUS", wst, _DIM)] + ([f"{_DIM}TASK{_R}"] if show_text else [])
        out.append(f"{_GUTTER}  " + "  ".join(cells))
    for j, age in zip(visible, ages):
        route = _cell(j.to, wo, _TX)
        if show_from:
            frm = _one_line(j.frm or "-", wf)
            route = _fill(wf - len(frm)) + f"{_TX}{frm}{_R}{_DIM} → {_R}" + route
        cells = ([f"{_fill(wa - len(age))}{_DIM}{age}{_R}"] if show_age else []) \
                + ([_cell(j.id, wj, _DIM)] if show_id else []) + [route, _cell(j.status, wst, _state_color(j.status))]
        text = getattr(j, "text", "") or ""
        if show_text and text:
            cells.append(f"{_TX}{_one_line(text, wtxt)}{_R}")
        out.append(f"{_GUTTER}{_light(j.status)} " + "  ".join(cells))
    return out


# Cockpit keyboard cheatsheet — the REAL binds, not invented. Sources — all verified against the code /
# `tmux list-keys`:
#   • Cockpit (Ctrl-b …): a/A, f/F, x/X and Esc are REBOUND by layouts.apply_chrome (display-popup
#     add-workspace; display-popup files peek; confirm-before kill-window; send-keys -X cancel to
#     unstick copy-mode). n/d/z/o/[/c are genuinely useful DEFAULTS surfaced here (curated, NOT every
#     default): n next-window, d detach-client, z resize-pane -Z (zoom/fullscreen a pane), o
#     select-pane (arrows switch too), [ copy-mode (scrollback), c new-window.
# Edit this list if the binds change.
_HOTKEYS = {
    "Cockpit (Ctrl-b …)": [
        ("a / A", "Add workspace"),    # apply_chrome: display-popup add-workspace
        ("f", "Peek files"),           # apply_chrome: display-popup files peek
        ("n", "Switch ws"),            # default next-window
        ("x / X", "Close ws"),         # apply_chrome: confirm-before kill-window
        ("d", "Detach"),               # default detach-client
        ("z", "Zoom pane"),            # default resize-pane -Z (fullscreen the pane)
        ("o", "Switch pane"),          # default select-pane -t + (arrows switch too)
        ("[", "Scroll mode"),          # default copy-mode (scrollback)
        ("c", "New window"),           # default new-window
        ("Esc", "Unstick"),            # apply_chrome: send-keys -X cancel (exit copy-mode)
    ],
}


def _hotkeys_block(width=_DEFAULT_COLS):
    """HOTKEYS section: the cockpit's REAL keybinds, flowed into as many 'key action' columns as the
    pane is wide (newspaper order would hide the common keys at the bottom, so it reads left to
    right). Static -- fixed chrome, not event-log state."""
    names = list(_HOTKEYS)
    kw = max(len(k) for g in names for k, _ in _HOTKEYS[g])
    aw = max(len(a) for g in names for _, a in _HOTKEYS[g])
    per_row = max(1, (width - 3) // (kw + 1 + aw + 3))
    out = [f"{_GUTTER}{_label('HOTKEYS')}"]
    for g in names:
        out.append(f"{_GUTTER}  {_DIM}{g}{_R}")
        keys = _HOTKEYS[g]
        for i in range(0, len(keys), per_row):
            cells = [_cell(k, kw, _B) + " " + _cell(a, aw, _DIM) for k, a in keys[i:i + per_row]]
            out.append(f"{_GUTTER}  " + _fill(3).join(cells))
    return out


def _arrange(head, team_rows, jobs, now, width, k, side, blank, colhdr, label, with_head):
    """One candidate arrangement of the frame, as a list of lines (see render_core)."""
    names = [r[0] for r in team_rows]
    if side:
        probe = _team_block(team_rows, width, colhdr, task=False, label=label)
        tw = max((_vlen(line) for line in probe), default=12)
        left = _team_block(team_rows, tw + 1, colhdr, task=False, label=label)
        right = _jobs_block(jobs, k, max(20, width - tw - 4), now, colhdr, names, label)
        body = _beside("\n".join(left), "\n".join(right), gap=3).split("\n") if right else left
    else:
        body = _team_block(team_rows, width, colhdr, label=label)
        if k or not jobs:
            body = body + ([""] if blank else []) + _jobs_block(jobs, k, width, now, colhdr, names, label)
    return ([head] if with_head else []) + ([""] if (blank and with_head) else []) + body


# Arrangements from richest to tersest: (blank lines, column headers, section labels, header line).
_DENSITY = [(True, True, True, True), (False, True, True, True), (False, False, True, True),
            (False, False, True, False), (False, False, False, False)]


def render_core(agents, jobs, recent=5, roster=None, orient="v",
                mode="standard", activity=None, now=None, title=None, cols=None, rows=None, keys=True):
    """Pure: (agents state, jobs[, roster]) -> the styled tower frame (ANSI text, no outer box).

    Two sections, TEAM (who is doing what) and TASKS (newest `recent`, newest-first, with a
    hidden-count note). `cols`/`rows` are the pane size: the frame never exceeds them -- it sheds
    blank lines, then column headers, then labels, then older tasks, until it fits, so a short strip
    shows a complete (if terser) picture instead of the tail of a tall one. orient="h" marks a
    wide/short strip: TEAM and TASKS go SIDE BY SIDE when the strip is wide enough (or too short to
    stack). `activity`/`now` feed the TEAM section's 'working <age>'/'late-work <age>' states
    (see _activity_state) -- both default to "no daemon activity data". `title` is the workspace
    name shown in the header; `keys=False` leaves the HOTKEYS cheatsheet out (`mk status`)."""
    now = time.time() if now is None else now
    jobs = list(jobs)
    W = max(24, cols or (_DEFAULT_COLS_WIDE if orient == "h" else _DEFAULT_COLS))
    team_rows = _team_rows(agents, roster, jobs, activity, now)
    head = _head(title or "MKCREW tower", mode, _counts(team_rows), W, exact=cols is not None)

    stacked = [(False,) + d for d in _DENSITY]
    beside = [(True,) + d for d in _DENSITY]
    if orient != "h":
        plans = stacked
    elif W >= _SIDE_BY_SIDE_MIN:
        plans = beside[:3] + stacked[:3] + beside[3:] + stacked[3:]
    else:
        plans = stacked[:3] + beside[:3] + stacked[3:] + beside[3:]
    want = min(recent, len(jobs))
    if rows is None:
        frame = _arrange(head, team_rows, jobs, now, W, want, *plans[0])
    else:
        budget = max(1, rows)                                # _paint never scrolls: every row is usable
        best = None
        for plan in plans:
            for k in range(want, -1, -1):                    # the most tasks this arrangement can show
                cand = _arrange(head, team_rows, jobs, now, W, k, *plan)
                if len(cand) <= budget and max(_vlen(line) for line in cand) <= W:
                    if best is None or k > best[0]:
                        best = (k, cand)
                    break
            if best and best[0] >= min(3, want):             # good enough -- prefer the earlier plan
                break
        if best is None:                                     # tiny pane: crew rows only, cut to fit
            best = (0, _team_block(team_rows, W, colhdr=False, limit=budget, label=False))
        frame = best[1][:budget]
    if keys and orient != "h":
        if rows is None:
            frame = frame + [""] + _hotkeys_block(W)
        else:
            block = [""] + _hotkeys_block(W)
            if len(frame) + len(block) <= rows:
                frame = frame + block
    return "\n".join(frame)


def frame_from_events(events, roster=None, orient="v", mode="standard", activity=None, **layout):
    """Pure: a list of Event -> the rendered tower frame. `activity` is the daemon's optional live
    pane-activity map (see render_core); events alone can never carry it -- raw pane captures are
    daemon-memory only, never persisted to the durable log. `layout` passes title/cols/rows/keys
    straight through to render_core."""
    return render_core(projections.agents(events), list(projections.jobs(events).values()),
                       roster=roster, orient=orient, mode=mode, activity=activity, **layout)


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


def _workspace_title(project=None):
    """The header label for a project: its saved workspace name, else the folder name. Never raises."""
    from pathlib import Path
    try:
        from . import teamconfig
        base = Path(project) if project else Path.cwd()
        return teamconfig.load_name(base) or base.name or None
    except Exception:
        return None


def status_main():
    """`mk status`: print one tower frame read from the durable event log -- WITH the configured
    roster, mode badge, and live pane activity, exactly like the tower pane. Events alone list an
    agent only after its first job, so a FRESH cockpit rendered an EMPTY team table here (live
    incident: the lead ran `mk status`, saw no crew, and spiraled into role discovery even though
    its bootstrap named every teammate). The roster keeps idle agents visible from second zero.
    The key cheatsheet is left out: this snapshot is read by agents as often as by people."""
    try:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")   # glyph / colour safety when piped
    except Exception:
        pass
    try:
        from . import teamconfig
        mode = teamconfig.load_mode(os.getcwd())
    except Exception:
        mode = "standard"
    log = EventLog(config.event_db())
    try:
        cols = shutil.get_terminal_size((_DEFAULT_COLS, 24)).columns if sys.stdout.isatty() else None
        print(frame_from_events(log.replay(), roster=_read_roster(), mode=mode,
                                activity=_fetch_activity(), title=_workspace_title(),
                                cols=cols, keys=False) + _RESET)
    finally:
        log.close()
    return 0


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _paint(frame):
    """Repaint the pane in ONE write: cursor home, every line followed by erase-to-end-of-line, then
    erase-below. No separate clear, so the pane never flashes blank between refreshes."""
    sys.stdout.write("\033[H" + "\033[K\r\n".join(frame.split("\n")) + "\033[K\033[J" + _RESET)
    sys.stdout.flush()


def _fetch_activity():
    """Best-effort GET of the running daemon's /status 'activity' map -- the ONE piece of tower
    truth that can never live in the durable event log (raw pane captures are daemon-memory only,
    by design not persisted across restarts). Returns None on anything short of a clean 200: no
    port file (daemon not running/this project has no live cockpit), a stale/unreadable port, a
    connection error, or an OLDER daemon whose /status has no 'activity' key -- frame_from_events
    then renders with no activity data (the hard backward-compat guarantee). Mirrors tui.py's
    fetch_status() but kept local: coreview stays an INDEPENDENT event-log reader that merely
    enriches itself with live daemon state when it happens to be reachable, never requires it."""
    import json
    import urllib.request
    try:
        port = int(config.port_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status", method="GET")
        with urllib.request.urlopen(req, timeout=0.5) as r:
            return json.loads(r.read()).get("activity")
    except Exception:
        return None


def coreview_run(iterations=None, interval=2.0, clear=None, roster=None, orient="v", width=None, project=None):
    """Render the tower on a loop, fitted to the pane. iterations=None loops forever (live pane); a
    finite count is used by tests. `width` overrides the detected pane width (for tests); `project`
    selects that project's event DB. One EventLog read connection for the run.

    `clear=None` (the live pane) repaints in place with `_paint` -- one write per refresh, cursor
    hidden, so the pane neither flashes nor shows a stray cursor. Passing a `clear` callable keeps
    the old clear-then-print behaviour (tests pass a no-op)."""
    log = EventLog(config.event_db(project))
    live = clear is None and sys.stdout.isatty()
    n = 0
    try:
        if live:
            if os.name == "nt":   # the old tower ran cls each tick; cmd.exe also turns on VT processing, which _paint needs
                _clear_screen()
            sys.stdout.write("\033[?25l")                    # hide the cursor in a status pane
        while iterations is None or n < iterations:
            size = shutil.get_terminal_size((80, 24))
            w = width or size.columns
            # per-tick mode read: `mk mode` persists to team.config, so the badge follows a live
            # switch on the next refresh (tiny json read; no daemon round-trip needed).
            try:
                from . import teamconfig
                mode = teamconfig.load_mode(project) if project else "standard"
            except Exception:
                mode = "standard"
            activity = _fetch_activity()   # None (daemon down / older daemon) -> plain states
            frame = frame_from_events(log.replay(), roster=roster, orient=orient, mode=mode,
                                      activity=activity, title=_workspace_title(project) if project else None,
                                      cols=w, rows=size.lines if live else None)
            if live:
                _paint(frame)
            else:
                (clear or _clear_screen)()
                print(frame + _RESET, flush=True)
            n += 1
            if iterations is not None and n >= iterations:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if live:
            sys.stdout.write("\033[?25h")                    # give the cursor back on the way out
            sys.stdout.flush()
        log.close()


def coreview_main():
    """`mk-core-view`: live self-updating tower pane (runs forever). argv[1] (if given) is the
    project dir (loads the team roster); argv[2] is the orientation ('h' for wide/short strips,
    'v' default) -- the layout builder picks it per template."""
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
