# src/mkcrew/addworkspace.py
"""MKCREW 'add workspace' picker — add a new workspace from INSIDE the cockpit, no browser.

Ctrl-b A opens this as a popup: pick a folder, a template, and an agent count, press Enter and it
runs `mk add` to spawn the workspace as a new window (tab) in the running session. Keyboard-driven
(reliable in a tmux pane). Styled in the Blueprint HUD language.

`_add_command` is pure (builds the `mk add` argv) and testable without running the App.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import templates

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (Button, ContentSwitcher, DirectoryTree, Footer, Input, Label,
                             RadioButton, RadioSet, Select)

_PROVIDER_OPTIONS = [
    ("claude", "Claude", "Claude Code CLI"),
    ("codex", "Codex", "OpenAI Codex CLI"),
    ("opencode", "OpenCode", "OpenCode TUI - choose a model route next"),
    ("antigravity", "Antigravity", "Google Antigravity CLI (agy)"),
]
_PROVIDERS = [key for key, _label, _desc in _PROVIDER_OPTIONS]
_COUNTS = ["1", "2", "3", "4"]                                          # capped at 4 (layouts fit <=4 well)
_EFFORTS = ["low", "medium", "high", "max"]            # generic fallback thinking levels (per-model lists win)
# The Layout step groups the FROZEN templates registry (single source of truth) into two headed radio
# groups: "Normal" (add-capable core layouts) and "Experimental" (the files-IDE layout).  The selected
# radio's registry key is the template, emitted verbatim as `--template <key>` — no second "Plain" mode
# (the old Template/Plain toggle just offered the same two layouts twice).
_NORMAL_TEMPLATES = [t for t in templates.by_group(templates.NORMAL) if t.add_capable]
_EXPERIMENTAL_TEMPLATES = templates.by_group(templates.EXPERIMENTAL)
_DEFAULT_TEMPLATE_KEY = _NORMAL_TEMPLATES[0].key          # main-vertical (LEAD LEFT)
# Item #6: a tiny ASCII sketch per offered template, shown inline on the Layout step so the user can see
# what each layout looks like (paired with the template's registry description).  Every offered key —
# main-vertical, even-horizontal AND the experimental lead-left-ide — has a sketch, so no option ever
# renders a blank preview.
_TEMPLATE_SKETCHES = {
    "main-vertical": ("┌──────┬───┐\n"
                      "│      │ ◧ │\n"
                      "│ LEAD ├───┤\n"
                      "│      │ ◧ │\n"
                      "└──────┴───┘"),
    "even-horizontal": ("┌────┬────┬────┐\n"
                        "│    │    │    │\n"
                        "│ ◧  │ ◧  │ ◧  │\n"
                        "│    │    │    │\n"
                        "└────┴────┴────┘"),
    # experimental: LEAD LEFT plus the files-IDE pane (core | explorer | editor).
    "lead-left-ide": ("┌─────┬─────┬─────┐\n"
                      "│     │     │     │\n"
                      "│CORE │EXPL │EDIT │\n"
                      "│     │     │     │\n"
                      "└─────┴─────┴─────┘"),
}


def _template_preview(key: str) -> str:
    """Item #6: the inline Layout-step preview for a template — its ASCII sketch plus the registry
    description — so the Layout step shows what the selected template looks like.  Returns a NON-empty
    preview for every offered key (main-vertical, even-horizontal, lead-left-ide); unknown keys -> ""."""
    t = templates.get(key)
    desc = t.desc if t else ""
    sketch = _TEMPLATE_SKETCHES.get(key, "")
    return f"{sketch}\n\n{desc}" if sketch else desc


# Per-CLI model roster.  Each entry is (model_id, FULL label, [effort options for THAT model]).
# The effort list is what fills an agent's "thinking level" radio once it picks this model — so effort
# is per-agent AND model-dependent, never one global value.
_MODELS = {
    "claude": [
        ("claude-opus-4-8", "Claude Opus 4.8", ["low", "medium", "high", "max"]),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", ["low", "medium", "high"]),
        # confirm: user mentioned a possible 3rd Claude model (e.g. Haiku) — omitted until confirmed.
    ],
    "codex": [
        ("gpt-5.5", "GPT-5.5", ["low", "medium", "high", "xhigh"]),
        ("gpt-5.4", "GPT-5.4", ["low", "medium", "high", "xhigh"]),
        # confirm: codex model-id strings for the 5.4 line (assumed to mirror 'gpt-5.5'); the mini
        # variant caps reasoning at 'high' (no 'xhigh').
        ("gpt-5.4-mini", "GPT-5.4 mini", ["low", "medium", "high"]),
    ],
    # agy carries the thinking level INSIDE the model name as a "(Level)" suffix (e.g.
    # "Gemini 3.5 Flash (High)") — it has NO launch-time effort flag (verified `agy --help`).  So each
    # model_id here is the BASE name and the agent's picked effort is FOLDED onto it at launch
    # (agent._agy_model_with_thinking) into the real agy --model value — the chosen effort is no longer
    # dropped.  Levels per `agy models`: Gemini Flash low/medium/high, Gemini Pro low/high.  The
    # Claude-on-agy model has a FIXED "(Thinking)" variant (no level choice), so its model_id already
    # carries that suffix and its effort list is EMPTY (the thinking picker hides, like an OpenCode route).
    "antigravity": [
        ("Gemini 3.5 Flash", "Gemini 3.5 Flash", ["low", "medium", "high"]),
        ("Gemini 3.1 Pro", "Gemini 3.1 Pro", ["low", "high"]),
        ("Claude Opus 4.6 (Thinking)", "Claude Opus 4.6 (Thinking)", []),
    ],
    # confirm: OpenCode routes don't expose a uniform thinking level yet -> effort lists left empty.
    # Routes reused from the previous roster; this picker renders as a Select (OpenCode has many routes).
    "opencode": [
        ("", "Default OpenCode route", []),
        ("opencode/big-pickle", "Zen: big-pickle", []),
        ("opencode/deepseek-v4-flash-free", "Zen: DeepSeek free", []),
        ("opencode-go/minimax-m3", "Go: Minimax M3", []),
        ("opencode-go/qwen3.7-max", "Go: Qwen max", []),
    ],
}

# Quick-launch presets for the native psmux add-workspace menu (Ctrl-b A -> display-menu).  Every
# layout is a templates-registry key (main-vertical / even-horizontal / lead-left-ide) so the native
# menu can't drift from the wizard's grouped picker.  Fields: (menu label, hotkey, provider, count,
# template key, model, effort).
_MENU_PRESETS = [
    ("Claude - Lead Left", "c", "claude", "1", "main-vertical", "", "high"),
    ("Codex - Lead Left", "x", "codex", "1", "main-vertical", "gpt-5.5", "high"),
    ("OpenCode Zen - Lead Left", "o", "opencode", "1", "main-vertical", "opencode/big-pickle", ""),
    ("OpenCode Go - Lead Left", "g", "opencode", "1", "main-vertical", "opencode-go/minimax-m3", ""),
    ("Antigravity - Lead Left", "a", "antigravity", "1", "main-vertical", "", ""),
    ("Claude - Side by Side", "s", "claude", "2", "even-horizontal", "", "high"),
    ("Codex - Side by Side", "X", "codex", "2", "even-horizontal", "gpt-5.5", "high"),
    ("OpenCode Zen - Side by Side", "O", "opencode", "2", "even-horizontal", "opencode/big-pickle", ""),
    ("Claude - Lead Left + Files IDE", "i", "claude", "2", "lead-left-ide", "", "high"),
]

_CYAN = "\033[38;5;45m"
_CYAN_D = "\033[38;5;31m"
_AMBER = "\033[38;5;215m"
_DIM = "\033[38;5;245m"
_TX = "\033[38;5;252m"
_B = "\033[1m"
_R = "\033[0m"


_STEP_NAMES = ["Folder", "Agents", "Layout", "Confirm"]
# Item #2: the Confirm report's usable text width (card width minus its border + padding) — long values
# (esp. the folder path) are middle-truncated to fit this instead of being cut dead at the border.
_SUMMARY_WIDTH = 70
# Bug B: one clean sentence.  The old wording wrapped mid-word in the narrow card ("...Overwri|te?"),
# which is what showed up garbled as "overi".
_OVERWRITE_MSG = "This directory already has a MKCREW workspace. Overwrite it?"


def _model_efforts(cli: str, model_id: str) -> list[str]:
    """The thinking-level options for a given CLI+model (empty list if that model exposes none)."""
    opts = _MODELS.get(cli, [])
    for value, _label, efforts in opts:
        if value == model_id:
            return list(efforts)
    return list(opts[0][2]) if opts else list(_EFFORTS)


def _align_rows(rows: list[tuple[str, str]]) -> str:
    """Item #6 alignment helper: render (label, value) pairs as an aligned key->value report.

    Labels are left-padded to the longest label width, then two spaces, then the value — so every
    value starts in the same column (the tidy apt/Debian-style setup summary the Confirm step wants).
    A row with an empty label passes its value through verbatim (blank spacer / free-form line)."""
    width = max((len(label) for label, _v in rows if label), default=0)
    lines = []
    for label, value in rows:
        lines.append(f"{label.ljust(width)}  {value}" if label else value)
    return "\n".join(lines)


def _truncate_middle(text: str, width: int) -> str:
    """Item #2: middle-truncate `text` to at most `width` cells, keeping the HEAD (a path's drive root)
    and the TAIL (its leaf) with an ellipsis between — so a long folder path reads like
    'E:\\MyProj…\\MKCREW' in the Review instead of being cut dead at the card border with no ellipsis.
    The tail is favoured (the leaf/filename is the useful part).  Short text passes through unchanged."""
    if width < 5 or len(text) <= width:
        return text
    keep = width - 1                       # reserve one cell for the ellipsis
    tail = (keep + 1) // 2                 # favour the tail so the leaf survives
    head = keep - tail
    return f"{text[:head]}…{text[-tail:]}"


def _drives() -> list[str]:
    """Filesystem roots the Browse picker can jump to (item #3): Windows drive letters (C:\\, D:\\, …)
    that currently hold a usable volume; on POSIX just the single '/' root.  Never empty (falls back
    to the cwd's anchor).  Uses os.path.isdir (swallows OSError) so an empty/unreadable removable
    drive — e.g. a card reader at J:\\ that raises WinError 1005 — is skipped, not crashed on."""
    import string
    roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    if roots:
        return roots
    anchor = Path.cwd().anchor or "/"
    return [anchor]


class _DirOnlyTree(DirectoryTree):
    """Item #4: a DirectoryTree that lists ONLY sub-directories.  The Browse overlay is a FOLDER picker,
    so files ('📄 .gitignore', …) are filtered out via Textual's `filter_paths` hook for a clean,
    folders-only tree.  Unreadable entries (OSError on is_dir) are simply skipped, never crash the tree."""

    def filter_paths(self, paths):
        keep = []
        for p in paths:
            try:
                if Path(p).is_dir():
                    keep.append(p)
            except OSError:
                pass
        return keep


class AddWorkspaceApp(App):
    """Keyboard-first 4-step wizard: folder/name -> per-agent CLI+model -> layout -> confirm.

    Fully Tab/arrow/Enter/Esc driven (mouse is unreliable in psmux panes).  Only the active step is
    shown (ContentSwitcher), so each step stays small.  `_build_cmd` reads the live selections into
    the pure `_build_add_command` argv — the whole keyboard path is provable headlessly (see tests)."""

    CSS = """
    Screen { background: #07111f; align: center middle; }
    #card { width: 78; height: 95%; padding: 1 3;
            background: #0d1f33; border: round #35e0ff; }
    #title { color: #35e0ff; text-style: bold; padding-bottom: 1; }
    #hint { color: #587a9a; padding-top: 1; }
    /* Bug A: the steps are a scrollable content area; the nav bar docks below and is ALWAYS visible. */
    #steps { height: 1fr; overflow-y: auto; }
    ContentSwitcher > Vertical { height: auto; }
    /* FIX 1: the Browse pane FILLS #steps (definite height) so its docked #br_nav (Use this folder /
       Cancel) is pinned to the visible card bottom instead of overflowing off-screen + unclickable. */
    #browse { height: 100%; }
    Label { color: #8fc0e8; text-style: bold; }
    Input { border: tall #1b3a52; background: #0a1828; }
    Input:focus { border: tall #35e0ff; }
    #folder_row { height: auto; }                 /* Bug C: folder Input + Browse side by side */
    #folder_row Input { width: 1fr; }
    #browse_btn { width: auto; }
    #dirtree { height: 1fr; border: tall #1b3a52; background: #0a1828; }   /* Bug C/FIX 1: tree flexes so the docked nav fits */
    #dirtree:focus { border: tall #35e0ff; }
    #br_path_row { height: auto; }                /* item #3: type-a-path + Go + Up (Explorer nav) */
    #br_path_row Input { width: 1fr; }
    #br_drives { height: auto; layout: horizontal; }   /* item #3: one button per drive/disk */
    .drivebtn { width: auto; min-width: 6; }
    /* FIX 1: dock the picker's confirm bar to the bottom of #browse (like #nav) so Use this folder /
       Cancel are ALWAYS on-screen + clickable, however tall the tree gets.  #dirtree is 1fr, so
       #br_current (the "what Confirm picks" line) sits in flow right above this docked bar. */
    #br_nav { dock: bottom; height: auto; padding-top: 1; background: #0d1f33; }
    #br_current { color: #35e0ff; text-style: bold; padding-top: 1; }   /* FIX 1: what "Use this folder" picks */
    RadioSet { width: 100%; height: auto;
               background: #0a1828; border: tall #1b3a52; border-title-color: #587a9a; }
    RadioSet:focus { border: tall #35e0ff; }
    /* item #1: short radiosets stay on one row, but each option is CONTENT-sized with a uniform gap.
       The RadioSet default stretches every option to 1fr, which gave SHORT labels big trailing gaps
       while LONG neighbours collided/clipped — so size to content + a fixed gutter instead. */
    #count { layout: horizontal; }
    #count > RadioButton { width: auto; margin: 0 2 0 0; }
    /* item #1: the per-agent CLI / model / effort lists carry the longest, most-uneven labels (e.g.
       'Opus 4.6 Thinking', 'Claude Sonnet 4.6'), so they STACK vertically — one option per line — which
       never collides or clips a model name (a horizontal row can't wrap in Textual). */
    .cli, .model, .effort { layout: vertical; }
    .modelsel { width: 100%; }                  /* OpenCode model picker is a Select (dropdown) */
    Select:focus > SelectCurrent { border: tall #35e0ff; }
    .agent { height: auto; border: round #14324a; padding: 0 1; margin-bottom: 1; }
    .agentlabel { color: #35e0ff; }
    .hidden { display: none; }
    #agents { height: auto; max-height: 30; overflow-y: auto; }   /* fallback only; 1-2 agents never scroll */
    RadioButton { color: #7fb0d8; }
    RadioButton.-selected { color: #35e0ff; text-style: bold; }
    #summary { color: #cfe8ff; padding: 1 0; }
    #template_preview { color: #7fb0d8; padding-top: 1; }   /* item #6: inline layout sketch + description */
    Button { margin-right: 2; text-style: bold;
             background: #14324a; color: #35e0ff; border: tall #1b3a52; }
    Button:focus { border: tall #35e0ff; background: #1a3e5a; }
    #nav { dock: bottom; height: auto; width: 100%; padding-top: 1; background: #0d1f33; }
    #ow_nav, #ws_nav { height: auto; padding-top: 1; }
    #ow_msg { color: #cfe8ff; padding: 1 0; }
    #ws_box { height: auto; max-height: 20; overflow-y: auto; }
    Footer { background: #0d2137; color: #7fb0d8; }
    """
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        # item #5: PRIORITY so the Next / Back affordance shows in the Footer on the FOLDER step too.
        # A focused Input otherwise shadows ctrl+left / ctrl+right with its word-jump bindings (which are
        # show=False), hiding the nav hints — so step 1's footer read only 'esc Cancel'.  Priority keeps
        # the app's nav bindings active (and visible) even while the folder Input has focus.
        Binding("ctrl+right", "next", "Next", priority=True),
        Binding("ctrl+left", "back", "Back", priority=True),
    ]

    current_step = reactive(0, init=False)   # 0..3; on_mount drives the first render

    def __init__(self, start_dir=None):
        super().__init__()
        self._start = str(Path(start_dir or os.getcwd()))
        self._pending_cmd = None             # the argv the Add button would run (exposed for tests)
        self._force = False                  # set by the Overwrite>Yes prompt -> `mk add ... --force`
        self._overlay = None                 # None | "overwrite" | "existing" | "browse" (non-step screens)
        self._workspaces = []                # last `mk workspaces` result (open-existing list)
        self._drive_list = _drives()         # item #3: drive/root jump targets for the Browse picker

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Label("", id="title")
            with ContentSwitcher(initial="step0", id="steps"):
                # --- Step 0: folder & name ---
                with Vertical(id="step0"):
                    yield Label("Folder")
                    # Paste works out of the box: Textual's Input handles the Paste event and inserts the
                    # text — nothing here blocks it.  ponytail: pasting INTO the popup depends on psmux
                    # forwarding the clipboard, which is a psmux concern outside this file (don't fix here).
                    with Horizontal(id="folder_row"):
                        yield Input(value=self._start, id="folder")
                        yield Button("Browse", id="browse_btn")
                    yield Label("Name")
                    yield Input(placeholder="(auto from folder)", id="name")
                    yield Label("Enter: next   Esc: cancel   ·   Browse picks a folder", id="hint")
                # --- Step 1: per-agent CLI + model + (model-dependent) effort ---
                with Vertical(id="step1"):
                    yield Label("How many agents?")
                    with RadioSet(id="count"):
                        for i, n in enumerate(_COUNTS):
                            yield RadioButton(n, value=(i == 0))
                    with Vertical(id="agents"):
                        for i in range(len(_COUNTS)):
                            cls = "agent" if i == 0 else "agent hidden"
                            with Vertical(id=f"agent{i}", classes=cls):
                                yield Label(f"Agent {i + 1}", classes="agentlabel")
                                with RadioSet(id=f"cli{i}", classes="cli") as cli_rs:
                                    cli_rs.border_title = "CLI"
                                    for j, (_k, label, _d) in enumerate(_PROVIDER_OPTIONS):
                                        yield RadioButton(label, value=(j == 0))
                                # default CLI is Claude -> a model RADIO; OpenCode swaps this to a Select.
                                with RadioSet(id=f"model{i}", classes="model") as model_rs:
                                    model_rs.border_title = "Model"
                                    for j, (_v, label, _ef) in enumerate(_MODELS["claude"]):
                                        yield RadioButton(label, value=(j == 0))
                                # effort options come from THIS agent's model (Claude Opus 4.8 by default).
                                with RadioSet(id=f"effort{i}", classes="effort") as eff_rs:
                                    eff_rs.border_title = "Thinking"
                                    for e in _MODELS["claude"][0][2]:
                                        yield RadioButton(e, value=(e == "high"))
                # --- Step 2: layout — ONE grouped picker over the templates registry (Normal /
                #     Experimental), not two duplicate modes.  Each group is a headed RadioSet; the
                #     selected radio's registry key is the template.  The shared preview below is a
                #     DIRECT child of the step (never inside a box that hides), so it refreshes for
                #     EVERY option incl. the experimental one (fixes the old empty-preview bug). ---
                with Vertical(id="step2"):
                    yield Label("Layout")
                    with RadioSet(id="tmpl_normal") as normal_rs:
                        normal_rs.border_title = "Normal"
                        for i, t in enumerate(_NORMAL_TEMPLATES):
                            yield RadioButton(t.label, value=(i == 0))       # default = first Normal
                    with RadioSet(id="tmpl_experimental") as exp_rs:
                        exp_rs.border_title = "Experimental"
                        for t in _EXPERIMENTAL_TEMPLATES:
                            yield RadioButton(t.label, value=False)          # unpressed until chosen
                    yield Label(_template_preview(_DEFAULT_TEMPLATE_KEY), id="template_preview")
                # --- Step 3: confirm (the Create button lives in the docked nav so it's always shown) ---
                with Vertical(id="step3"):
                    yield Label("Review")
                    yield Label("", id="summary")
                # --- Overlay: 'already set up' overwrite prompt (Folder -> Next detection) ---
                with Vertical(id="overwrite"):
                    yield Label("Already set up", classes="agentlabel")
                    yield Label(_OVERWRITE_MSG, id="ow_msg")
                    with Horizontal(id="ow_nav"):
                        yield Button("Yes", id="ow_yes", variant="primary")
                        yield Button("No", id="ow_no")
                # --- Overlay: open an existing workspace (list from `mk workspaces`) ---
                with Vertical(id="existing"):
                    yield Label("Open an existing MKCREW workspace", classes="agentlabel")
                    with Vertical(id="ws_box"):
                        yield Label("", id="ws_list")
                    with Horizontal(id="ws_nav"):
                        yield Button("Open", id="ws_open", variant="primary")
                        yield Button("Back", id="ws_back")
                # --- Overlay: folder picker (Bug C / item #3 / FIX 1) — Explorer-style NAVIGATION:
                #     clicking (or Enter on) a folder DESCENDS into it (re-roots the tree); the drive
                #     buttons jump to a disk; Up climbs to the parent.  NOTHING is saved on click — only
                #     "Use this folder" confirms the currently-browsed dir (shown in #br_current). ---
                with Vertical(id="browse"):
                    # item #3: a SHORT hint so it fits the picker width (the old, longer hint clipped at
                    # the box edge as '…arrows mov').
                    yield Label("Enter/click a folder to open · arrows move · Esc cancels",
                                id="br_hint", classes="agentlabel")
                    # item #3: re-root anywhere — typing a path (Enter/Go) or climbing Up escapes the cwd.
                    with Horizontal(id="br_path_row"):
                        yield Input(value=self._start, placeholder="type a path  ·  Enter to go",
                                    id="br_path")
                        yield Button("Go", id="br_go")
                        yield Button("Up", id="br_up")
                    # item #3: jump to any disk (C:\, D:\, E:\ …) so the picker isn't cwd-locked.
                    with Horizontal(id="br_drives"):
                        for di, drv in enumerate(self._drive_list):
                            yield Button(drv, id=f"drv{di}", classes="drivebtn")
                    yield _DirOnlyTree(self._start, id="dirtree")   # item #4: folders only, no files
                    # FIX 1: show the dir Confirm will pick (the currently-browsed root) so the user
                    # always knows what "Use this folder" selects.
                    yield Label("", id="br_current")
                    with Horizontal(id="br_nav"):
                        yield Button("Use this folder", id="br_use", variant="primary")
                        yield Button("Cancel", id="br_cancel")
            # Bug A: the nav bar is docked to the card's bottom, so Next (steps 0-2) / Create (step 3)
            # stay on-screen no matter how tall the step content is.
            with Horizontal(id="nav"):
                yield Button("Back", id="back")
                yield Button("Next", id="next")
                yield Button("Create Workspace", id="create", variant="primary")
                yield Button("Open existing", id="open_existing")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_agent_blocks(self._count())
        self._refresh_step(0)            # title + switcher + focus #folder (keyboard-immediate)

    # ---- step navigation ---------------------------------------------------
    def watch_current_step(self, step: int) -> None:
        self._refresh_step(step)

    def _refresh_step(self, step: int) -> None:
        try:
            switcher = self.query_one("#steps", ContentSwitcher)
        except Exception:
            return                       # not mounted yet; on_mount will drive the first render
        self._overlay = None             # leaving any overwrite/existing overlay back to the steps
        self.query_one("#nav", Horizontal).display = True
        switcher.current = f"step{step}"
        self.query_one("#title", Label).update(f"Add Workspace · {step + 1}/4 · {_STEP_NAMES[step]}")
        self.query_one("#back", Button).disabled = (step == 0)
        self.query_one("#next", Button).display = (step < 3)            # Next on the first three steps
        self.query_one("#create", Button).display = (step == 3)         # Create only on the Confirm step
        self.query_one("#open_existing", Button).display = (step == 0)   # only on the Folder step
        if step == 1:
            self._sync_agent_blocks(self._count())
        if step == 3:
            self.query_one("#summary", Label).update(self._summary_text())
        self._focus_step(step)

    def _focus_step(self, step: int) -> None:
        target = {0: "#folder", 1: "#count", 2: "#tmpl_normal", 3: "#create"}[step]
        try:
            self.query_one(target).focus()
        except Exception:
            pass

    def action_next(self) -> None:
        if self._overlay:                      # overwrite/existing screens own their navigation
            return
        if self.current_step == 0:             # leaving Folder: detect an existing setup directly
            folder = self._folder_value()
            if folder and _is_existing_setup(folder):
                self._show_overwrite()
                return
        if self.current_step < 3:
            self.current_step += 1

    def action_back(self) -> None:
        if self._overlay:
            return
        if self.current_step > 0:
            self.current_step -= 1

    def action_cancel(self) -> None:
        if self._overlay:                # in overwrite / existing / browse -> cancel back to the Folder step
            self._refresh_step(0)
            return
        self.exit()

    # ---- reactive wiring ---------------------------------------------------
    async def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        rid = event.radio_set.id or ""
        if rid == "count":
            self._sync_agent_blocks(self._count())
        elif rid.startswith("cli"):
            await self._repopulate_model(int(rid[3:]))      # CLI changed -> rebuild model + effort
        elif rid.startswith("model"):
            await self._repopulate_effort(int(rid[5:]))     # model changed -> new effort options
        elif rid in ("tmpl_normal", "tmpl_experimental"):
            # grouped picker: a RadioSet won't deselect in place, so clear the OTHER group to keep the
            # choice global-single, then refresh the shared preview from the selected key.
            self._clear_template_group("tmpl_experimental" if rid == "tmpl_normal" else "tmpl_normal")
            self._sync_template_preview()

    async def on_select_changed(self, event: Select.Changed) -> None:
        sid = event.select.id or ""
        if sid.startswith("model"):                         # OpenCode route (Select dropdown) changed
            await self._repopulate_effort(int(sid[5:]))

    def _sync_agent_blocks(self, n: int) -> None:
        """Show exactly `n` agent blocks (agent0..agent{n-1}); hide the rest."""
        for i in range(len(_COUNTS)):
            self.query_one(f"#agent{i}", Vertical).set_class(i >= n, "hidden")

    async def _repopulate_model(self, i: int) -> None:
        """Rebuild #model{i} from the CLI now selected in #cli{i}.  OpenCode -> a Select dropdown (it
        has many routes); every other CLI -> a short RadioSet.  Then refresh the agent's effort radio."""
        cli = self._agent_cli(i)
        block = self.query_one(f"#agent{i}", Vertical)
        cli_rs = self.query_one(f"#cli{i}", RadioSet)
        old = self.query_one(f"#model{i}")
        if cli == "opencode":
            opts = _MODELS["opencode"]
            new = Select([(label, value) for value, label, _ef in opts],
                         value=opts[0][0], allow_blank=False, compact=True,
                         id=f"model{i}", classes="modelsel")
        else:
            new = RadioSet(*self._model_buttons(cli), id=f"model{i}", classes="model")
        new.border_title = "Model"
        await old.remove()                       # drop first to avoid a duplicate id, then re-mount
        await block.mount(new, after=cli_rs)
        await self._repopulate_effort(i)

    async def _repopulate_effort(self, i: int) -> None:
        """Rebuild #effort{i} (thinking level) from the agent's currently chosen MODEL's effort list.
        A model with no efforts (e.g. an OpenCode route) yields an empty, hidden effort radio."""
        efforts = _model_efforts(self._agent_cli(i), self._agent_model(i))
        block = self.query_one(f"#agent{i}", Vertical)
        model_w = self.query_one(f"#model{i}")
        old = self.query_one(f"#effort{i}", RadioSet)
        default = efforts.index("high") if "high" in efforts else 0
        buttons = [RadioButton(e, value=(j == default)) for j, e in enumerate(efforts)]
        new = RadioSet(*buttons, id=f"effort{i}", classes=("effort hidden" if not efforts else "effort"))
        new.border_title = "Thinking"
        await old.remove()
        await block.mount(new, after=model_w)

    def _model_buttons(self, cli: str) -> list[RadioButton]:
        return [RadioButton(label, value=(j == 0))
                for j, (_v, label, _ef) in enumerate(_MODELS.get(cli, []))]

    def _agent_cli(self, i: int) -> str:
        try:
            ci = self.query_one(f"#cli{i}", RadioSet).pressed_index
        except Exception:
            return _PROVIDERS[0]
        return _PROVIDERS[ci] if 0 <= ci < len(_PROVIDERS) else _PROVIDERS[0]

    def _agent_model(self, i: int) -> str:
        cli = self._agent_cli(i)
        opts = _MODELS.get(cli, [])
        try:
            w = self.query_one(f"#model{i}")
        except Exception:
            return opts[0][0] if opts else ""
        if isinstance(w, Select):
            val = w.value
            return val if isinstance(val, str) else (opts[0][0] if opts else "")
        idx = w.pressed_index
        return opts[idx][0] if 0 <= idx < len(opts) else (opts[0][0] if opts else "")

    def _agent_effort(self, i: int) -> str:
        try:
            rs = self.query_one(f"#effort{i}", RadioSet)
        except Exception:
            return ""
        btn = rs.pressed_button
        return str(btn.label) if btn is not None else ""

    def _clear_template_group(self, rid: str) -> None:
        """Clear a template group's selection so the Normal/Experimental picker stays a SINGLE global
        choice.  A Textual RadioSet re-presses a button toggled off, so we suppress that Changed and
        drop the set's pressed/selected state directly (it starts life zero-pressed; this returns it
        there)."""
        try:
            rs = self.query_one(f"#{rid}", RadioSet)
        except Exception:
            return
        btn = rs.pressed_button
        if btn is None:
            return
        with rs.prevent(RadioButton.Changed):
            btn.value = False
        rs._pressed_button = None
        rs._selected = None

    def _sync_template_preview(self) -> None:
        """Item #6 / Bug B: refresh the shared Layout-step preview (ASCII sketch + registry description)
        from the currently-selected template key (via `_layout()`)."""
        try:
            self.query_one("#template_preview", Label).update(_template_preview(self._layout()))
        except Exception:
            pass

    # ---- reading the live selections --------------------------------------
    def _count(self) -> int:
        try:
            idx = self.query_one("#count", RadioSet).pressed_index
        except Exception:
            return 1
        return int(_COUNTS[idx]) if 0 <= idx < len(_COUNTS) else 1

    def _layout(self) -> str:
        """The selected template key from the grouped Normal/Experimental picker.  Exactly one group is
        ever pressed (the other is cleared on change), so the first pressed group wins; falls back to
        the default key when nothing is pressed yet."""
        for rid, group in (("tmpl_normal", _NORMAL_TEMPLATES),
                           ("tmpl_experimental", _EXPERIMENTAL_TEMPLATES)):
            try:
                idx = self.query_one(f"#{rid}", RadioSet).pressed_index
            except Exception:
                continue
            if 0 <= idx < len(group):
                return group[idx].key
        return _DEFAULT_TEMPLATE_KEY

    def _collect(self):
        """Gather (folder, name, count, providers, models, efforts, layout) from the widgets — every
        list is per-agent and length == count (efforts are each agent's own model-dependent choice)."""
        folder = self.query_one("#folder", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        count = self._count()
        providers, models, efforts = [], [], []
        for i in range(count):
            providers.append(self._agent_cli(i))
            models.append(self._agent_model(i))
            efforts.append(self._agent_effort(i))
        return folder, name, count, providers, models, efforts, self._layout()

    def _build_cmd(self) -> list[str]:
        """The `mk add` argv for the current selections (pure builder; testable).  Appends --force when
        the user chose Overwrite on an already-set-up folder."""
        return _build_add_command(*self._collect(), force=self._force)

    def _summary_text(self) -> str:
        """Item #6: a clean, ALIGNED key->value config report (Debian/apt-style) for the Confirm step —
        Folder / Name / Agents, one CLI · model · effort line per agent, then Template, all column-aligned
        by `_align_rows` so every value starts in the same column."""
        folder, name, count, providers, models, efforts, layout = self._collect()
        if name:
            name_val = name
        elif folder:
            name_val = f"(auto: {Path(folder).name})"
        else:
            name_val = "(auto)"
        rows = [
            ("Folder", folder or "(none)"),
            ("Name", name_val),
            ("Agents", str(count)),
        ]
        for i, (cli, model, eff) in enumerate(zip(providers, models, efforts), start=1):
            parts = [next((l for k, l, _d in _PROVIDER_OPTIONS if k == cli), cli)]
            if model:
                parts.append(next((l for v, l, _ef in _MODELS.get(cli, []) if v == model), model))
            if eff:
                parts.append(eff)
            rows.append((f"Agent {i}", " · ".join(parts)))
        t = templates.get(layout)
        llabel = t.label if t else layout
        rows.append(("Template", layout if llabel == layout else f"{layout}  ({llabel})"))
        # item #2: middle-truncate long values (esp. the folder path) so they fit the Review card with an
        # ellipsis instead of being cut dead at the border.  Budget = text width minus the label column.
        label_w = max((len(label) for label, _v in rows if label), default=0)
        budget = max(16, _SUMMARY_WIDTH - label_w - 2)
        rows = [(label, _truncate_middle(value, budget)) for label, value in rows]
        return _align_rows(rows)

    # ---- actions -----------------------------------------------------------
    def on_input_submitted(self, event) -> None:
        if getattr(event, "input", None) is not None and event.input.id == "br_path":
            self._browse_to(event.input.value.strip())   # #3: Enter in the Browse path box re-roots
            return
        self.action_next()               # Enter in the folder/name fields advances

    async def on_button_pressed(self, event) -> None:
        bid = event.button.id
        if bid == "next":
            self.action_next()
        elif bid == "back":
            self.action_back()
        elif bid == "create":
            self._submit()
        elif bid == "browse_btn":                 # C: open the DirectoryTree folder picker
            self._show_browse()
        elif bid == "br_use":                     # FIX 1: Confirm -> select the currently-browsed dir
            self._pick_folder(self._browse_path())
        elif bid == "br_cancel":                  # C: leave the picker without changing the folder
            self._refresh_step(0)
        elif bid == "br_go":                      # #3: re-root the tree at the typed path
            self._browse_to(self.query_one("#br_path", Input).value.strip())
        elif bid == "br_up":                      # #3: climb to the parent directory
            self._browse_up()
        elif bid and bid.startswith("drv"):       # #3: jump to another drive/disk root
            try:
                self._browse_to(self._drive_list[int(bid[3:])])
            except (ValueError, IndexError):
                pass
        elif bid == "open_existing":
            await self._show_existing()           # B: list already-configured workspaces
        elif bid == "ow_yes":                     # A: overwrite -> keep going, create emits --force
            self._force = True
            self.current_step = 1
        elif bid == "ow_no":                      # A: keep the existing setup -> `mk open <folder>`
            self._open_and_exit(self._folder_value())
        elif bid == "ws_open":
            self._open_selected_workspace()
        elif bid == "ws_back":
            self._refresh_step(0)

    def _folder_value(self) -> str:
        try:
            return self.query_one("#folder", Input).value.strip()
        except Exception:
            return ""

    def _show_overwrite(self) -> None:
        """Folder already has .mkcrew/team.config -> Yes (overwrite, --force) / No (`mk open`)."""
        self._overlay = "overwrite"
        self.query_one("#steps", ContentSwitcher).current = "overwrite"
        self.query_one("#title", Label).update("Add Workspace · Already set up")
        self.query_one("#nav", Horizontal).display = False
        try:
            self.query_one("#ow_yes", Button).focus()
        except Exception:
            pass

    def _show_browse(self) -> None:
        """Bug C / item #3: open an Explorer-style folder picker rooted at the current folder (or its
        parent).  From here the user can climb Up, jump to any drive, or type any path — so they can
        reach any folder on any disk, not just the cwd subtree — then pick with arrows/Enter."""
        self._overlay = "browse"
        tree = self.query_one("#dirtree", DirectoryTree)
        root = Path(self._folder_value() or self._start)
        if not root.is_dir():
            root = root.parent if root.parent.is_dir() else Path(self._start)
        try:
            tree.path = str(root)                # re-root (and reload) at the chosen starting folder
        except Exception:
            pass
        try:
            self.query_one("#br_path", Input).value = str(root)   # mirror the root in the path box
        except Exception:
            pass
        self._set_browse_current(str(root))                       # FIX 1: show what Confirm will pick
        self.query_one("#steps", ContentSwitcher).current = "browse"
        self.query_one("#title", Label).update("Add Workspace · Browse folder")
        self.query_one("#nav", Horizontal).display = False
        try:
            tree.focus()
        except Exception:
            pass

    def _browse_to(self, path: str) -> None:
        """Item #3: re-root the Browse DirectoryTree at `path` (a parent, a drive root, or a typed
        path).  Setting `tree.path` reloads the tree, so the user escapes the cwd subtree entirely.
        A non-directory path is rejected with a notice (the tree stays where it was)."""
        try:
            p = Path(path).expanduser()
        except (OSError, ValueError):
            return
        if not p.is_dir():
            self.notify(f"not a folder: {path}", severity="warning")
            return
        tree = self.query_one("#dirtree", DirectoryTree)
        try:
            tree.path = str(p)                   # reactive: reloads the tree at the new root
        except Exception:
            return
        try:
            self.query_one("#br_path", Input).value = str(p)
        except Exception:
            pass
        self._set_browse_current(str(p))         # FIX 1: keep the "Use this folder" target in sync
        try:
            tree.focus()
        except Exception:
            pass

    def _browse_path(self) -> str:
        """FIX 1: the directory the Browse tree is currently rooted at — what "Use this folder" picks."""
        try:
            return str(self.query_one("#dirtree", DirectoryTree).path)
        except Exception:
            return self._folder_value() or self._start

    def _set_browse_current(self, path: str) -> None:
        """FIX 1: reflect the currently-browsed dir (Confirm's target) in the #br_current label."""
        try:
            self.query_one("#br_current", Label).update(f"Use this folder:  {path}")
        except Exception:
            pass

    def _browse_up(self) -> None:
        """Item #3: climb the Browse tree to its parent directory (no-op at a drive/filesystem root)."""
        try:
            cur = Path(str(self.query_one("#dirtree", DirectoryTree).path))
        except Exception:
            return
        parent = cur.parent
        if str(parent) != str(cur):              # already at a drive root -> parent == self, skip
            self._browse_to(str(parent))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """FIX 1: a folder node was activated (clicked / Enter) in the Browse picker -> NAVIGATE INTO it
        (re-root the tree there) so browsing DESCENDS Explorer-style.  This NO LONGER sets the working
        dir — selecting it is the "Use this folder" button's job (see _browse_path / _pick_folder)."""
        if self._overlay != "browse":
            return
        self._browse_to(str(event.path))

    def _pick_folder(self, path: str) -> None:
        """Drop the picked path into the folder Input and return to the Folder step."""
        try:
            self.query_one("#folder", Input).value = path
        except Exception:
            pass
        self._refresh_step(0)                    # clears the overlay, restores the docked nav, focuses #folder

    async def _show_existing(self) -> None:
        """List already-configured workspaces (from `mk workspaces`); pick one -> `mk open <path>`.

        A missing/erroring/empty `mk workspaces` just shows 'no existing workspaces found' — the
        backend may not implement the command yet, so this must never crash."""
        self._overlay = "existing"
        self._workspaces = _list_workspaces()
        box = self.query_one("#ws_box", Vertical)
        try:
            await self.query_one("#ws_list").remove()    # drop the placeholder / previous list
        except Exception:
            pass
        if self._workspaces:
            buttons = [RadioButton(f"{w['name']}  -  {w['path']}", value=(i == 0))
                       for i, w in enumerate(self._workspaces)]
            await box.mount(RadioSet(*buttons, id="ws_list"))
            self.query_one("#ws_open", Button).disabled = False
        else:
            await box.mount(Label("no existing workspaces found", id="ws_list"))
            self.query_one("#ws_open", Button).disabled = True
        self.query_one("#steps", ContentSwitcher).current = "existing"
        self.query_one("#title", Label).update("Open existing MKCREW workspace")
        self.query_one("#nav", Horizontal).display = False
        try:
            self.query_one("#ws_list" if self._workspaces else "#ws_back").focus()
        except Exception:
            pass

    def _selected_workspace(self):
        if not self._workspaces:
            return None
        try:
            idx = self.query_one("#ws_list", RadioSet).pressed_index
        except Exception:
            return None
        return self._workspaces[idx] if 0 <= idx < len(self._workspaces) else None

    def _open_selected_workspace(self) -> None:
        ws = self._selected_workspace()
        if ws is None:
            self.notify("no workspace selected", severity="warning")
            return
        self._open_and_exit(ws["path"])

    def _open_and_exit(self, folder: str) -> None:
        """Fire `mk open <folder>` (resume an existing setup) and close the picker."""
        cmd = _open_command(folder)
        self._pending_cmd = cmd          # expose for tests / introspection before we launch + exit
        try:
            subprocess.Popen([_mk_exe(), *cmd], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:                           # mk open not on PATH / not built yet
            self.notify(f"mk open failed: {e}", severity="error")
            return
        self.exit()

    def _submit(self) -> None:
        folder = self.query_one("#folder", Input).value.strip()
        if not folder:
            self.notify("pick a folder", severity="warning")
            self.current_step = 0        # send them back to fix it
            return
        # BUG-3: a typo'd / non-existent folder must NOT build the argv and launch a detached `mk add`
        # — that backend exits with "not a directory" off-screen, so the popup just closed with ZERO
        # feedback.  Validate the folder EXISTS first (mirrors _run_wizard's "Folder not found" guard)
        # and surface an inline error, keeping the wizard open, instead of launching into the void.
        try:
            exists = Path(folder).expanduser().is_dir()
        except (OSError, ValueError):
            exists = False
        if not exists:
            self.notify(f"Folder not found: {folder}", severity="error")
            self.current_step = 0        # bounce back to the Folder step to fix the path
            return
        cmd = self._build_cmd()
        self._pending_cmd = cmd          # expose for tests / introspection before we launch + exit
        try:
            subprocess.Popen([_mk_exe(), *cmd], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:                           # mk add not on PATH / not built yet
            self.notify(f"mk add failed: {e}", severity="error")
            return
        self.exit()


def _mk_exe():
    """The mk executable that runs `mk add` (next to this interpreter in dev / the frozen exe)."""
    from . import frozen
    if frozen.is_frozen():
        return sys.executable
    return str(Path(sys.executable).parent / "mk.exe")


def _build_add_command(folder: str, name: str, count: int, providers: list[str],
                       models: list[str], efforts: list[str] | None, layout: str,
                       force: bool = False) -> list[str]:
    """Pure builder for the per-agent `mk add` argv (FROZEN contract, see coordination/PLAN.md).

    `--providers` is always the comma-join of the per-agent CLI keys (length == count).  `--models`
    and `--efforts` are each per-agent comma lists, emitted ONLY if at least one slot is non-empty —
    empty slots stay as empty strings so positions line up (e.g. `claude,,gpt-5.5` / `high,,xhigh`).
    `force=True` appends `--force` so the backend overwrites an existing `.mkcrew` setup."""
    models = models or []
    efforts = efforts or []
    cmd = ["add", folder, "--agents", str(count),
           "--providers", ",".join(providers)]
    if any((m or "").strip() for m in models):
        cmd += ["--models", ",".join((m or "") for m in models)]
    if any((e or "").strip() for e in efforts):
        cmd += ["--efforts", ",".join((e or "") for e in efforts)]
    cmd += ["--template", layout]
    if name:
        cmd += ["--name", name]
    if force:
        cmd += ["--force"]
    return cmd


def _is_existing_setup(folder) -> bool:
    """True iff <folder>/.mkcrew/team.config exists.  A direct filesystem check — the wizard detects an
    already-configured workspace itself (no backend call), per the frozen contract."""
    try:
        return (Path(folder) / ".mkcrew" / "team.config").exists()
    except (OSError, ValueError):
        return False


def _open_command(folder) -> list[str]:
    """Pure builder: the `mk open <folder>` argv (resume an existing workspace, no re-setup)."""
    return ["open", str(folder)]


def _coerce_workspace(obj):
    """Normalize one parsed entry into {'name', 'path'} (or None if unusable)."""
    if isinstance(obj, dict):
        path = str(obj.get("path") or obj.get("folder") or obj.get("dir") or "").strip()
        name = str(obj.get("name") or "").strip()
        if not (path or name):
            return None
        path = path or name
        name = name or Path(path).name
        return {"name": name, "path": path}
    if isinstance(obj, str):
        s = obj.strip()
        if not s:
            return None
        for sep in ("\t", "|"):                     # 'name<TAB|>path' lines
            if sep in s:
                left, _sep, right = s.partition(sep)
                left, right = left.strip(), right.strip()
                if right:
                    return {"name": left or Path(right).name, "path": right}
        return {"name": Path(s).name, "path": s}    # bare path -> name = basename
    return None


def _parse_workspaces(text: str) -> list[dict]:
    """Parse `mk workspaces` output into [{'name','path'}, ...].  Accepts a JSON array/object, JSONL,
    or plain lines ('name<TAB|>path' or a bare path).  Empty / garbage -> [] (never raises)."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)                     # whole-text JSON (array or single object)?
    except ValueError:
        data = None
        is_json = False
    else:
        is_json = True
    if is_json:
        items = data if isinstance(data, list) else [data]
        return [w for w in (_coerce_workspace(o) for o in items) if w]
    rows = []
    for line in text.splitlines():                  # not JSON -> line-based (JSONL or plain)
        line = line.strip()
        if not line:
            continue
        ws = None
        try:
            ws = _coerce_workspace(json.loads(line))
        except ValueError:
            ws = None
        if ws is None:
            ws = _coerce_workspace(line)
        if ws:
            rows.append(ws)
    return rows


def _list_workspaces() -> list[dict]:
    """Run `mk workspaces` and parse it.  A missing / erroring / empty command yields [] so the wizard
    can show 'no existing workspaces found' instead of crashing (the backend may not build it yet)."""
    try:
        result = subprocess.run([_mk_exe(), "workspaces"], capture_output=True, text=True)
    except Exception:
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    return _parse_workspaces(getattr(result, "stdout", "") or "")


def _menu_prompt_command(launcher: str, provider: str, count: str, layout: str,
                         model: str = "", effort: str = "") -> str:
    parts = [f'"{launcher}"', "--menu-run", "--provider", provider,
             "--agents", count, "--template", layout]
    if model:
        parts += ["--model", model]
    if effort:
        parts += ["--effort", effort]
    parts += ["--folder", '"%%"']
    return 'command-prompt -p "Project folder" "run-shell ' + " ".join(parts) + '"'


def menu_command(launcher: str) -> tuple[str, ...]:
    """Native psmux add-workspace menu command.

    This intentionally avoids `display-popup <external command>` because that PTY path renders blank
    on some psmux/Windows builds.  `display-menu` and `command-prompt` are server-side UI surfaces,
    the same class of native overlay as Ctrl-b s.
    """
    command = ["display-menu", "-T", " MKCREW add workspace "]
    for label, key, provider, count, layout, model, effort in _MENU_PRESETS:
        command += [label, key, _menu_prompt_command(launcher, provider, count, layout, model, effort)]
    command += ["Cancel", "q", "display-message cancelled"]
    return tuple(command)


def _flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else ""


def _single_provider_command(folder, name, count, provider, effort, layout, model=""):
    """The menu/popup flows pick ONE provider+model+effort for ALL agents — fan them out to per-agent
    lists so they share the per-agent `_build_add_command` builder (cmd_add accepts the comma lists)."""
    n = int(count) if str(count).isdigit() else 1
    return _build_add_command(folder, name, n, [provider] * n, [model] * n, [effort] * n, layout)


def _display_message(text: str) -> None:
    try:
        from . import frozen
        subprocess.run([frozen.psmux_exe(), "display-message", text], capture_output=True)
    except Exception:
        pass


def _menu_run_main(args: list[str]) -> int:
    folder = _flag(args, "--folder").strip().strip('"')
    provider = _flag(args, "--provider") or "claude"
    count = _flag(args, "--agents") or "1"
    layout = _flag(args, "--template") or "main-vertical"
    model = _flag(args, "--model")
    effort = _flag(args, "--effort")
    if not folder:
        _display_message("MKCREW: no folder given")
        return 2
    cmd = _single_provider_command(folder, "", count, provider, effort, layout, model)
    result = subprocess.run([_mk_exe(), *cmd], capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        detail = result.stdout.strip() or f"added workspace: {Path(folder).name}"
        _display_message(f"MKCREW: {detail}")
    else:
        detail = (result.stderr or result.stdout or "add workspace failed").strip().splitlines()[0]
        _display_message(f"MKCREW: {detail}")
    return result.returncode


def _read_key() -> str:
    """Read one logical key from the Windows console."""
    import msvcrt
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, code)
    return {"\r": "enter", "\x1b": "esc", "\b": "backspace", "\t": "tab"}.get(ch, ch)


def _paint(title: str, body: list[str], step: str = "") -> None:
    print("\033[2J\033[H", end="")
    print(f"{_CYAN}{_B}MKCREW ADD WORKSPACE{_R}  {_CYAN_D}{'─' * 30}{_R}")
    if step:
        print(f"{_DIM}{step}{_R}")
    print(f"\n{_AMBER}{_B}{title}{_R}\n")
    for line in body:
        print(line)
    print(f"\n{_DIM}Up/Down select  Enter accept  Esc cancel{_R}", flush=True)


def _input_text(title: str, default: str = "", step: str = "") -> str | None:
    value = default
    while True:
        shown = value or " "
        _paint(title, [f"{_CYAN}> {_TX}{shown}{_R}"], step)
        key = _read_key()
        if key == "enter":
            return value.strip()
        if key == "esc":
            return None
        if key == "backspace":
            value = value[:-1]
        elif len(key) == 1 and key >= " ":
            value += key


def _select(title: str, options: list[tuple[str, str, str]], default: int = 0, step: str = "") -> str | None:
    idx = max(0, min(default, len(options) - 1))
    while True:
        rows = []
        for i, (_value, label, desc) in enumerate(options):
            pointer = f"{_AMBER}{_B}>" if i == idx else f"{_DIM} "
            color = _TX if i == idx else _DIM
            rows.append(f"{pointer} {color}{label:<28}{_R} {_DIM}{desc}{_R}")
        _paint(title, rows, step)
        key = _read_key()
        if key == "up":
            idx = (idx - 1) % len(options)
        elif key == "down":
            idx = (idx + 1) % len(options)
        elif key == "enter":
            return options[idx][0]
        elif key == "esc":
            return None


def _run_wizard(start_dir=None):
    folder = _input_text("Project folder", str(Path(start_dir or os.getcwd())), "Step 1 of 7")
    if folder is None:
        return None
    folder = str(Path(folder).expanduser())
    if not Path(folder).is_dir():
        _paint("Folder not found", [f"{_AMBER}{folder}{_R}", "Press Enter to close."], "Cancelled")
        while _read_key() != "enter":
            pass
        return None
    name = _input_text("Workspace name", Path(folder).name, "Step 2 of 7")
    if name is None:
        return None
    count = _select("How many agents?", [(n, n, "agent" if n == "1" else "agents") for n in _COUNTS], 0, "Step 3 of 7")
    if count is None:
        return None
    provider = _select("Agent CLI", _PROVIDER_OPTIONS, 0, "Step 4 of 7")
    if provider is None:
        return None
    model_opts = [(value, label, " / ".join(efs) if efs else "route")
                  for value, label, efs in _MODELS[provider]]
    model = _select("Model / route", model_opts, 0, "Step 5 of 7")
    if model is None:
        return None
    # Effort is per-model: offer exactly the chosen model's thinking levels (none -> skip the step).
    efs = _model_efforts(provider, model)
    effort = ""
    if efs:
        default = efs.index("high") if "high" in efs else 0
        effort = _select("Thinking level", [(e, e, "reasoning effort") for e in efs],
                         default, "Step 6 of 7") or ""
    tmpl_opts = [(t.key, t.label, t.desc) for t in templates.wizard_templates()]
    layout = _select("Workspace template", tmpl_opts, 0, "Step 7 of 7")
    if layout is None:
        return None
    return folder, name.strip(), count, provider, effort, layout, model.strip()


def _popup_main(start_dir=None) -> int:
    """Keyboard wizard for psmux display-popup.

    Textual's full-screen renderer is intentionally not used here: on the psmux build this cockpit
    targets, Textual paints correctly in panes but renders blank inside display-popup.  This uses raw
    terminal painting and Windows console keys so the native popup chrome stays visible.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        values = _run_wizard(start_dir)
    except (KeyboardInterrupt, EOFError):
        _paint("Cancelled", ["No workspace was added."], "Esc / Ctrl-C")
        time.sleep(0.35)
        return 130
    if values is None:
        _paint("Cancelled", ["No workspace was added."], "Esc")
        time.sleep(0.35)
        return 1
    folder, name, count, provider, effort, layout, model = values

    _t = templates.get(layout)
    template_label = _t.label if _t else layout
    model_label = model or "default"
    confirm = _select("Confirm workspace", [
        ("yes", "Create workspace", f"{name or Path(folder).name} · {count} {provider} · {model_label} · {template_label}"),
        ("no", "Cancel", "Return without adding anything"),
    ], 0, "Review")
    if confirm != "yes":
        _paint("Cancelled", ["No workspace was added."], "Esc")
        time.sleep(0.35)
        return 1

    cmd = _single_provider_command(folder, name, count, provider, effort, layout, model)
    result = subprocess.run([_mk_exe(), *cmd], capture_output=True, encoding="utf-8", errors="replace")
    if result.stdout.strip():
        _paint("Workspace added", [f"{_TX}{result.stdout.strip()}{_R}"], "Done")
    if result.returncode != 0:
        lines = [f"Command failed with exit code {result.returncode}."]
        if result.stderr.strip():
            lines.append(f"{_AMBER}{result.stderr.strip()}{_R}")
        lines.append("Press Enter to close.")
        _paint("Add workspace failed", lines, "Error")
        while _read_key() != "enter":
            pass
        return result.returncode
    time.sleep(0.35)
    return 0


def addworkspace_main():
    """`mk-add-workspace [start_dir]`: the in-cockpit add-workspace picker."""
    args = sys.argv[1:]
    if args and args[0] == "--menu-run":
        return _menu_run_main(args[1:])
    if args and args[0] == "--popup":
        return _popup_main(args[1] if len(args) > 1 else None)
    AddWorkspaceApp(args[0] if args else None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(addworkspace_main())
