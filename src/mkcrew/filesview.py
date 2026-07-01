# src/mkcrew/filesview.py
"""MKCREW Files pane — a 3-column IDE-style file explorer for the cockpit's reserved Files column.

A Textual app laid out as three side-by-side columns under a title bar:
  • LEFT  — a live CORE STATUS panel (the same "who is doing what" frame the core pane shows,
            read straight from the durable event log and refreshed on a timer).
  • CENTER — a DirectoryTree of the project (the IDE sidebar).
  • RIGHT — a viewer that has two modes: a syntax-highlighted READ-ONLY view (Rich/Pygments) and
            an EDIT mode (a TextArea, `e` to enter, Ctrl+S to save, Esc back to the view).

Navigate with the keyboard (↑↓ + Enter) — that's the reliable path; mouse clicks can land a row off
inside a tmux pane (the status-bar offset, see `_compensate_mouse`).

`_read_capped` is a pure helper (binary + huge-file guards), testable without running the App.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.scrollbar import ScrollBar
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DirectoryTree, Footer, Input, Static, TextArea)

from . import config, coreview, projections
from .eventlog import EventLog

# Noise the IDE sidebar should never show (keeps the tree readable).
_HIDE = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache",
         ".ruff_cache", ".idea", ".vscode", "dist", "build", ".mkcrew", ".DS_Store"}
_MAX_LINES = 2000          # cap the read-only VIEW so a huge file can't stall the pane
_HSCROLL_STEP = 16         # terminal columns per Ctrl-Left/Ctrl-Right notch
_CORE_INNER = 30           # inner width of the embedded compact core frame; its OUTER width is
                           # _CORE_INNER + 4. The #core CSS column is sized to hold that so the core
                           # box never wraps into the file tree (keep the two in sync — see CSS below).
# Inline new-file/folder draft: NO icon in the placeholder — the tree node already renders its own
# 📄/📁 glyph, so an icon here doubled it (`📄 📄 name.ext`). Just the hint text.
_NEW_FILE_PLACEHOLDER = "name.ext"
_NEW_FOLDER_PLACEHOLDER = "folder name"
_BAD_NAME_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _read_capped(path, max_lines=_MAX_LINES):
    """Read a text file for VIEWING -> (text, truncated). Binary or unreadable -> a friendly note
    (truncated=False), so the viewer never renders garbage or crashes the pane."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return f"cannot read file: {e}", False
    if b"\x00" in data[:4096]:
        return f"(binary file — {len(data)} bytes)", False
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]), True
    return text, False


def _middle_truncate(text: str, width: int) -> str:
    """Shorten `text` to at most `width` columns by eliding the MIDDLE (keep the start AND the end),
    so a long path still shows its root drive and its leaf folder — e.g. 'C:\\proj\\…\\src\\app'.
    Returns `text` unchanged when it already fits. Pure + testable."""
    text = text or ""
    if width <= 1 or len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    keep = width - 1                     # one column for the '…' ellipsis
    head = (keep + 1) // 2
    tail = keep - head
    return text[:head] + "…" + (text[-tail:] if tail else "")


def _preview_text(text: str) -> Text:
    """Read-only viewer body with a tight Notepad++-style line-number gutter.

    Rich's built-in Syntax gutter is roomy and punctuation-heavy in narrow panes; this keeps the
    number column left-flush (`1 │ code`) and returns one no-wrap Text renderable so #view can expose
    true horizontal scroll for long lines.
    """
    lines = text.split("\n") if text else [""]
    digits = max(1, len(str(len(lines))))
    out = Text(no_wrap=True, overflow="crop")
    for i, line in enumerate(lines, start=1):
        if i > 1:
            out.append("\n")
        out.append(f"{i:<{digits}}", style="#35e0ff bold")
        out.append(" │ ", style="#1b3a52")
        out.append(line, style="#e6f0ff")
    return out


def _name_error(name: str, kind: str) -> str | None:
    """Return a human error for a new file/folder name, or None when it is safe to create.

    Keep this Windows-safe because MKCREW is Windows-native. IDEs allow extensionless files like
    `README`/`Makefile`, so this validates the path component itself rather than forcing a suffix.
    """
    label = "file" if kind == "file" else "folder"
    if not name:
        return f"Enter a {label} name."
    if name != name.strip():
        return "Names cannot start or end with spaces."
    if name in {".", ".."}:
        return "Use a real name, not . or ..."
    if name.endswith("."):
        return "Names cannot end with a dot."
    if any(ch in _BAD_NAME_CHARS or ch in "\r\n\t" for ch in name):
        return "Names cannot contain < > : \" / \\ | ? * or newlines."
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return f"{stem} is reserved by Windows."
    return None


class _SaveConfirm(ModalScreen[bool]):
    """Nano-style save confirmation: one focused modal, Y/N/Esc, no editor focus ambiguity."""
    CSS = """
    _SaveConfirm { align: center middle; }
    #savebox { width: 58; height: 7; background: #0d2137; border: solid #35e0ff; padding: 1 2; }
    #saveprompt { height: 2; color: #e6f0ff; }
    #savebuttons { height: 1; }
    #savebuttons Button { width: 1fr; height: 1; border: none; margin: 0 1; }
    """
    BINDINGS = [("y", "yes", "Yes"), ("n", "no", "No"), ("escape", "no", "Cancel")]

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="savebox"):
            yield Static(f"Save changes to {self.path.name}?", id="saveprompt")
            with Horizontal(id="savebuttons"):
                yield Button("Yes", id="save_yes")
                yield Button("No", id="save_no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "save_yes")


class _NameInput(Input):
    """The inline new-file/new-folder name field.

    Owns its Esc-cancel so the FOCUSED field reliably dismisses: in a live psmux pane a bubbled Escape
    did NOT reach the app's `escape` binding from a focused Input (it does from the editor's TextArea),
    so the field handles Esc itself here — the focused widget is the first to see the key, so the cancel
    never depends on the event bubbling. (Enter stays on the proven Input.Submitted path.)"""
    def on_key(self, event: events.Key) -> None:
        if event.key != "escape":
            return
        app = self.app
        if isinstance(app, FilesApp) and app._new_kind:
            event.stop()
            event.prevent_default()
            app._end_create()


class _ProjectTree(DirectoryTree):
    """DirectoryTree that hides the usual noise dirs so the tree reads like an IDE sidebar."""
    def filter_paths(self, paths):
        return [p for p in paths if p.name not in _HIDE and not p.name.endswith(".pyc")]

    def _visible_nodes(self, node):
        yield node
        if node.is_expanded:
            for child in node.children:
                yield from self._visible_nodes(child)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Record the visual tree row the user pressed BEFORE Textual's selection handles wrapped
        labels, so a click reliably highlights that row. Creating is the #treebar toolbar's job —
        there are deliberately NO per-row ＋ hit-zones (they rendered inconsistent inline +icons)."""
        try:
            line = event.y + self.scroll_offset.y
            for node in self._visible_nodes(self.root):
                if getattr(node, "line", -1) == line:
                    app = self.app
                    if isinstance(app, FilesApp):
                        app._set_tree_target(node)
                        app._mouse_tree_node = node
                    break
        except Exception:
            pass


class FilesApp(App[None]):
    CSS = """
    Screen { background: #0a1628; }
    #title { height: 1; background: #0d2137; color: #35e0ff; text-style: bold; padding: 0 1; }
    #columns { height: 1fr; }
    /* width MUST hold the compact core box (outer = _CORE_INNER + 4 = 34): content =
       38 - 2 padding - 1 border-right = 35 >= 34, so the box never wraps into the tree (bug: the
       core box was wider than this column and wrapped, interleaving with the file tree). */
    #core { width: 38; height: 100%; background: #0a1628; color: #9ec7e8; padding: 0 1;
            border-right: solid #1b3a52; }
    /* CENTER column = a compact create toolbar + the current-location line + the file tree. */
    #treecol { width: 36; height: 100%; background: #0a1628; border-right: solid #1b3a52; }
    /* Toolbar is 3 rows tall so ＋📄/＋📁 are FAT mouse targets: a click that psmux misreports
       by a row still lands ON a button instead of slipping onto the title/cwd line (see
       _should_compensate). Buttons fill the full width+height of the toolbar. */
    #treebar { height: 3; background: #0d2137; }
    #treebar Button { width: 1fr; min-width: 0; height: 3; border: none; padding: 0; margin: 0;
                      content-align: center middle; background: #14324d; color: #35e0ff;
                      text-style: bold; }
    #treebar Button:focus { background: #1b4a6e; }
    #treebar Button:hover { background: #1f5780; }
    /* The explorer's in-column collapse handle (］/ mouse path). Narrow so the ＋ buttons keep room;
       two ids beat the `#treebar Button` rule's width:1fr. Vanishes with the column — reopen from the
       always-visible control strip in #right. */
    #treebar #tree_close { width: 4; color: #7fb0d8; }
    #treebar #tree_close:hover { background: #1f5780; color: #35e0ff; }
    #cwd { height: 1; background: #0a1628; color: #ffb14e; padding: 0 1; }
    /* Inline create field: a REAL focused Input (replaces the old draft-tree-node hack that lost
       keyboard focus in the live pane — placeholder showed but nothing could be typed). Hidden until
       ＋File/＋Folder (button or n/f) opens it; .focus()'d on show so the name is immediately typable
       and printable keys can never leak into the tree/editor or fire an app shortcut. */
    #newname { display: none; height: 3; margin: 0 1; padding: 0 1; background: #0d2137;
               color: #e6f0ff; border: round #35e0ff; }
    #newname:focus { border: round #ffb14e; }
    #tree { width: 100%; height: 1fr; background: #0a1628; color: #9ec7e8; padding: 0 1;
            scrollbar-size: 1 1; }
    #tree .directory-tree--folder { color: #35e0ff; text-style: bold; }
        #tree .directory-tree--extension { color: #7fb0d8; }
        #tree .tree--cursor { background: #ffb14e; color: #0a1628; text-style: bold; }
        #newerror { height: 1; display: none; background: #300f18; color: #ff6b8a; padding: 0 1; }
        /* #right is the editor/viewer column. width:1fr means it FLEXES to fill whatever horizontal
           space the (collapsible) #core / #treecol columns free — hide both (z) and it goes
           full-width. */
        #right { width: 1fr; height: 100%; }
    /* Panel collapse/maximize control strip — a thin, ALWAYS-VISIBLE toolbar at the top of the editor
       column, so a mouse user can reopen a column the keyboard (z/[/]) collapsed. Buttons mirror the
       keys: max=z, core=[, files=]. */
    #rightbar { height: 1; background: #0d2137; }
    #rightbar Button { width: auto; min-width: 4; height: 1; border: none; padding: 0 1;
                       margin: 0 1 0 0; background: #14324d; color: #35e0ff; text-style: bold; }
    #rightbar Button:hover { background: #1f5780; }
    #rightbar Button:focus { background: #1b4a6e; }
    #pathbar { height: 1; background: #0d2137; color: #ffb14e; padding: 0 1; }
    /* Read-only viewer: scroll BOTH axes. #content sizes to its widest line (width:auto) so long,
       unwrapped lines overflow horizontally and the container scrolls left/right (Syntax is rendered
       with word_wrap=False); tall files scroll vertically. Scrollbars always reserved so they show. */
    #view { height: 1fr; background: #0a1628; padding: 0 1;
            overflow-x: scroll; overflow-y: scroll; scrollbar-size: 1 1;
            scrollbar-color: #35e0ff; scrollbar-background: #0d2137; }
    #content { width: auto; height: auto; }
    /* Editor: soft_wrap is OFF (see compose) so long lines scroll horizontally; tall files scroll
       vertically (TextArea is a ScrollView, overflow auto on both axes). `padding: 0 1` MATCHES #view
       so the text's left/right framing is identical in view vs edit mode (short lines no longer sit at
       a different right inset when toggling). overflow-x: scroll reserves the H-scroll track so it is
       always visible (the keyboard ^←/^→ scroll the same surface). */
    #edit { height: 1fr; background: #0a1628; padding: 0 1; overflow-x: scroll; scrollbar-size: 1 1;
            scrollbar-color: #35e0ff; scrollbar-background: #0d2137; }
    Footer { background: #0d2137; color: #7fb0d8; }
    """
    BINDINGS = [
        ("e", "edit", "Edit"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+shift+v", "paste_clipboard", "Paste"),
        ("ctrl+z", "undo", "Undo"),
        ("ctrl+y", "redo", "Redo"),
        ("ctrl+shift+z", "redo", "Redo"),
        ("escape", "view", "View"),
        ("n", "new_file", "New file"),
        ("f", "new_folder", "New folder"),
        ("r", "reload", "Refresh"),
        # Collapsible panels (VSCode-style): `z` MAXIMIZES the editor by hiding BOTH side columns so
        # #right goes full-width (press again to restore); `[` toggles the core column, `]` the
        # explorer. Inert while typing (see _is_typing) so the keys type into the editor / new-file
        # field. key_display shows the bracket glyph in the Footer (the raw key name is verbose).
        ("z", "toggle_maximize", "Max editor"),
        Binding("left_square_bracket", "toggle_core", "Core", key_display="["),
        Binding("right_square_bracket", "toggle_tree", "Explorer", key_display="]"),
        Binding("ctrl+left", "scroll_left", "Left", priority=True),
        Binding("ctrl+right", "scroll_right", "Right", priority=True),
        # Refocus the file tree from anywhere (incl. the editor, which swallows Tab as a literal tab),
        # so the arrow keys come back to life after opening/editing a file. priority -> wins everywhere.
        Binding("ctrl+t", "focus_tree", "Tree", priority=True),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, project, cockpit=False):
        super().__init__()
        self.project = Path(project).resolve()
        self._current: Path | None = None      # the open file, for edit/save
        self._new_kind: str | None = None      # None | "file" | "folder" while the create Input is open
        self._create_target_node = None        # last folder/file row the user highlighted or clicked
        self._mouse_tree_node = None           # visual row from _ProjectTree.on_mouse_down
        self._last_tree_sig = None             # last directory-listing snapshot; auto-reload skips when it is unchanged
        self._dy = -1 if cockpit else 0        # cockpit mouse-row fix (see on_event)
        self._core_hidden = False              # LEFT core column collapsed?   (toggled by [ or its button)
        self._tree_hidden = False              # CENTER explorer column collapsed? (toggled by ] or its button)

    def _draft_placeholder(self) -> str:
        return _NEW_FILE_PLACEHOLDER if self._new_kind == "file" else _NEW_FOLDER_PLACEHOLDER

    def _should_compensate(self, target) -> bool:
        # The -1 row shift is calibrated for the TREE (several rows down, where psmux's status-row
        # miscount really is +1). Two widgets must be EXEMPT or the shift pushes their clicks off:
        #   • TextArea (editor) — it doesn't carry the psmux row offset; shifting sends its cursor a
        #     row high.
        #   • Button (the ＋File/＋Folder toolbar) — it sits at the very TOP (row 1, under the 1-row
        #     title) where psmux has nothing above it to mis-count, so its offset there is ~0. A
        #     blanket -1 over-corrects and lands a button click on the title bar → the button does
        #     nothing (the live-cockpit "+File/+Folder do nothing" bug). Taking a click already
        #     reported on a button as-is is what makes the toolbar reachable.
        # ponytail: empirical, per-pane — tree needs -1, editor + top-row buttons need 0. The exact
        # psmux pane mouse offset differs per pane type and CAN'T be bench-tested headlessly, so the
        # keyboard path (n = new file, f = new folder) is the reliable one; the button exemption +
        # the fat 3-row toolbar (see CSS) are best-effort mouse robustness, not a psmux fix.
        # Textual scrollbars are also exempt: shifting a scrollbar click/drag one row off the bar is why
        # the editor's horizontal scrollbar felt dead in the cockpit even though keyboard scroll worked.
        return self._dy != 0 and not isinstance(target, (TextArea, Button, ScrollBar))

    def _compensate_mouse(self, event) -> None:
        # In the cockpit, psmux reports pane mouse Y one row low (mis-counts the top status row), so a
        # click lands on the row below. Shift BOTH local + screen Y up one row so routing AND the
        # region check agree (shifting only one rejects the click). Mutates in place.
        if not isinstance(event, events.MouseEvent):
            return
        if isinstance(event, events.MouseMove):
            # Hover/cursor tracking is already delivered on the visual row in the live cockpit.  The
            # -1 tree-click compensation makes hover highlight the row ABOVE the cursor, while clicks
            # still need the shift.  Keep movement literal; compensate press/release/scroll only.
            return
        try:
            target, _ = self.get_widget_at(event.x, event.y)
        except Exception:
            target = None
        if self._should_compensate(target):
            event._y += self._dy
            event._screen_y += self._dy

    async def on_event(self, event: events.Event) -> None:
        self._compensate_mouse(event)   # real mouse routes through here (pilot.click bypasses on_event)
        await super().on_event(event)

    def compose(self) -> ComposeResult:
        # Header: project name + its FULL path so the user always sees which workspace this is.
        yield Static(f" FILES · {self.project.name}  ·  {self.project} ", id="title")
        with Horizontal(id="columns"):
            yield Static(id="core")                              # LEFT: live core status panel
            with Vertical(id="treecol"):                         # CENTER: create toolbar + cwd + tree
                with Horizontal(id="treebar"):                   # clickable IDE create buttons …
                    yield Button("＋ 📄", id="newfilebtn")       # … mirror the `n` keybind …
                    yield Button("＋ 📁", id="newfolderbtn")     # … and the `f` keybind
                    yield Button("‹", id="tree_close")           # … and collapse the explorer column (］)
                yield Static(id="cwd")                           # current-location full path (truncated)
                yield _NameInput(id="newname", placeholder=_NEW_FILE_PLACEHOLDER)  # inline create name field
                yield Static(id="newerror")                      # validation feedback while naming entries
                yield _ProjectTree(str(self.project), id="tree") # the file tree (IDE sidebar)
                # Create naming uses the real focused Input above: ＋File/＋Folder (or n/f) reveals it and
                # moves focus ONTO it, so the name is reliably typable in a live psmux pane (the cwd line
                # already shows WHERE it lands).
            with Vertical(id="right"):                           # RIGHT: viewer / editor column
                with Horizontal(id="rightbar"):                  # collapse/maximize controls (mouse path)
                    yield Button("max", id="max_toggle")         # z — maximize editor / restore all three
                    yield Button("core", id="core_toggle")       # [ — collapse/restore the core column
                    yield Button("files", id="tree_toggle")      # ] — collapse/restore the explorer column
                yield Static("  ↑↓ + enter open · e edit · n new · f folder · z max editor · ^t tree", id="pathbar")
                with ScrollableContainer(id="view"):
                    yield Static(id="content")
                # soft_wrap=False -> long lines do NOT wrap, so the editor scrolls horizontally
                # (left/right) for long lines as well as vertically for big files. show_line_numbers=True
                # gives the edit surface the same left gutter affordance as the read-only viewer.
                yield TextArea("", id="edit", soft_wrap=False, show_line_numbers=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#edit", TextArea).display = False    # start in read-only view
        self.query_one("#newname", Input).display = False    # create field hidden until ＋File/＋Folder
        self.query_one("#newerror", Static).display = False
        self._refresh_core()                                 # paint the core panel immediately …
        self.set_interval(2.0, self._refresh_core)           # … then keep it live
        self.set_interval(3.0, self._auto_reload)            # auto-refresh the tree (agents add files)
        # Establish the directory-listing baseline once the async tree load settles, so the FIRST
        # auto-refresh tick already matches and skips — an idle tree never reloads (never flashes).
        self.set_timer(0.5, self._prime_tree_signature)
        tree = self.query_one("#tree", DirectoryTree)
        tree.root.set_label(_middle_truncate(self.project.name or str(self.project), 22))
        tree.focus()                                         # so ↑↓ work immediately
        self._update_cwd()                                   # paint the current-location line (root)
        self._apply_layout()                                 # all columns shown; sync the toggle labels

    # ── core status panel (LEFT column) ──────────────────────────────────────────────
    def _core_frame(self) -> str:
        """Read THIS project's event log and render the same frame the core pane shows (ANSI text).
        Reuses coreview.render_core — no reimplementation of the team/jobs view here."""
        log = EventLog(config.event_db(str(self.project)))
        try:
            events = log.replay()
        finally:
            log.close()
        return coreview.render_core(
            projections.agents(events),
            list(projections.jobs(events).values()),
            roster=coreview._read_roster(str(self.project)),
            compact=True,
            width=_CORE_INNER,                  # outer = _CORE_INNER + 4 (34) fits the #core column
        )

    def _refresh_core(self) -> None:
        """Re-read the event log and update the #core Static. Any read failure shows a short note
        instead of crashing the pane (the timer keeps trying on the next tick)."""
        try:
            frame = Text.from_ansi(self._core_frame())
            frame.no_wrap = True            # box-drawing lines stay one row each in the narrow column
            frame.overflow = "crop"         # … cropped, not wrapped into a broken grid
        except Exception as e:              # noqa: BLE001 — the pane must survive any read error
            frame = Text(f"core unavailable: {e}", style="#7fb0d8")
        self.query_one("#core", Static).update(frame)

    # ── current-location line (#cwd, CENTER column) ───────────────────────────────────
    def _update_cwd(self) -> None:
        """Keep the '#cwd' line showing WHERE THE USER IS — the full path of the current location
        (the selected dir, a selected file's parent, else the project root), middle-truncated so a
        long path never overflows the narrow tree column (the start + leaf always stay visible)."""
        try:
            cwd = self.query_one("#cwd", Static)
        except Exception:
            return
        width = cwd.content_size.width or 34
        label = f"📁 {_middle_truncate(str(self._target_dir()), max(6, width - 10))}  + here"
        cwd.update(_middle_truncate(label, width))

    def _set_tree_target(self, node, move: bool = True) -> None:
        """Make a tree row the create target and (when `move`) the visible cursor row too.

        `move=False` is for the HIGHLIGHT path: the cursor is ALREADY on `node` (its move is what
        raised the highlight), so re-issuing move_cursor there is redundant — and during the unstable
        post-reload window it feeds a NodeHighlighted→move_cursor→NodeHighlighted loop that ping-pongs
        the cursor between the root and the restored row. Mouse paths still pass move=True (a click
        must MOVE the cursor onto the clicked row)."""
        if self._new_kind:
            return
        self._create_target_node = node
        if move:
            try:
                self.query_one("#tree", DirectoryTree).move_cursor(node)
            except Exception:
                pass
        self._update_cwd()

    def on_tree_node_highlighted(self, event) -> None:
        """Arrow-key navigation already moved the tree cursor — just refresh the current-location line
        and create target to match (do NOT move_cursor again; see _set_tree_target)."""
        self._set_tree_target(event.node, move=False)

    def on_tree_node_selected(self, event) -> None:
        """Mouse selection should target the clicked row even when DirectoryTree does not move cursor."""
        node = self._mouse_tree_node or event.node
        self._mouse_tree_node = None
        self._set_tree_target(node)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """Clicking a folder makes the create buttons act on that folder, like an IDE explorer."""
        self._set_tree_target(event.node)

    # ── open / view (RIGHT column) ───────────────────────────────────────────────────
    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._create_target_node = event.node
        self._open(event.path)

    def _open(self, path) -> None:
        self._current = Path(path)
        text, truncated = _read_capped(path)
        self.query_one("#content", Static).update(_preview_text(text))
        self._show_view()
        rel = os.path.relpath(path, self.project)
        self.query_one("#pathbar", Static).update(
            f"  {rel}" + ("  · truncated" if truncated else "") + "      e edit · ^←/^→ scroll · ^t tree")
        self.query_one("#view", ScrollableContainer).scroll_home(animate=False)
        self._update_cwd()                               # current location = the file's parent dir

    def _show_view(self) -> None:
        self.query_one("#view", ScrollableContainer).display = True
        self.query_one("#edit", TextArea).display = False

    # ── edit / save ─────────────────────────────────────────────────────────────────
    def action_edit(self) -> None:
        """`e` — load the FULL file (never the truncated view) into an editable TextArea. (While the
        create Input is open `e` is a filename character the focused Input consumes, so this binding
        does not fire then.)"""
        if self._current is None:
            return
        data = self._current.read_bytes()
        if b"\x00" in data[:4096]:
            self.notify("binary file — can't edit", severity="warning")
            return
        ta = self.query_one("#edit", TextArea)
        ta.load_text(data.decode("utf-8", errors="replace"))
        self.query_one("#view", ScrollableContainer).display = False
        ta.display = True
        ta.focus()
        self.query_one("#pathbar", Static).update(
            f"  editing {os.path.relpath(self._current, self.project)}      ^←/^→ scroll · ^s save · ^t tree · esc")

    def action_save(self) -> None:
        """Ctrl+S — ask before writing, like nano's explicit save confirmation."""
        ta = self.query_one("#edit", TextArea)
        if self._current is None or not ta.display:
            return
        self.push_screen(_SaveConfirm(self._current), self._finish_save)

    def _finish_save(self, confirmed: bool | None) -> None:
        if confirmed:
            self._write_current_file()

    def _write_current_file(self) -> None:
        ta = self.query_one("#edit", TextArea)
        if self._current is None or not ta.display:
            return
        try:
            self._current.write_text(ta.text, encoding="utf-8")
            self.notify(f"saved {self._current.name}")
        except OSError as e:
            self.notify(f"save failed: {e}", severity="error")

    def action_view(self) -> None:
        """Esc — cancel the inline new-file/folder entry if it's open; else drop edits in progress and
        go back to the highlighted, read-only view. Always returns focus to the tree so the arrow keys
        work again (the editor/viewer otherwise leaves the keyboard stuck off the tree)."""
        if self._new_kind:
            self._end_create()
            return
        if self._current is not None:
            self._open(self._current)                        # reload from disk
        self.action_focus_tree()                             # arrows navigate the tree again

    def action_focus_tree(self) -> None:
        """`Ctrl-T` — refocus the DirectoryTree from anywhere (editor, viewer, a button) so keyboard
        navigation never dead-ends. Cancels an in-progress inline create first."""
        if self._new_kind:
            self._end_create()
            return
        try:
            self.query_one("#tree", DirectoryTree).focus()
        except Exception:                # noqa: BLE001 — focus is best-effort, never fatal
            pass

    def action_reload(self) -> None:
        """`r` — re-read the directory so files the agents just created show up, KEEPING the user's
        highlighted row. A bare DirectoryTree.reload() rebuilds the tree and snaps the cursor back to
        the project root, so this captures the selection and restores it after the reload. (While the
        create Input is open `r` is a filename character the focused Input consumes — no-op here then.)"""
        if self._new_kind:
            return
        self._reload_keep_cursor()

    def _reload_tree(self) -> None:
        """Plain reload — used by the create flow, which then selects the NEW node itself."""
        self.query_one("#tree", DirectoryTree).reload()

    def _reload_keep_cursor(self) -> None:
        """Reload but PRESERVE the user's selection. DirectoryTree.reload() resets the cursor to the
        root (its built-in highlight-restore does not survive our lazy/filtered tree), so capture the
        selected path, await the (async) reload, then move the cursor back onto it."""
        tree = self.query_one("#tree", DirectoryTree)
        keep = self._selected_path()
        self.run_worker(self._reload_and_restore(tree, keep),
                        exclusive=True, group="filesreload")

    async def _reload_and_restore(self, tree, keep) -> None:
        """Await the reload to completion, then restore the highlighted row (so it does not flash to
        the root). Wrapped so a reload/worker hiccup never crashes the pane."""
        try:
            await tree.reload()
        except Exception:                # noqa: BLE001 — the pane must survive any reload error
            return
        if keep is not None:
            self._select_path(keep)

    def _selected_path(self):
        """The on-disk path of the currently highlighted tree row (None when nothing is highlighted)."""
        try:
            node = self.query_one("#tree", DirectoryTree).cursor_node
            p = getattr(getattr(node, "data", None), "path", None)
            return Path(p) if p is not None else None
        except Exception:                # noqa: BLE001
            return None

    def _select_path(self, target) -> None:
        """Move the tree cursor back onto `target` after a reload (DFS so a nested row is found too).
        A silent no-op when the path no longer exists — that row is simply gone."""
        try:
            tree = self.query_one("#tree", DirectoryTree)
            # Materialize the tree's line numbers BEFORE move_cursor: straight after an (awaited)
            # reload the fresh nodes still carry line=-1, and move_cursor onto a -1 line lands the
            # cursor nowhere. (Textual's own _reload uses this same `_tree_lines` warm-up.)
            try:
                _ = tree._tree_lines
            except Exception:            # noqa: BLE001 — best-effort warm-up
                pass
            target = Path(target)
            stack = list(tree.root.children)
            while stack:
                node = stack.pop()
                p = getattr(getattr(node, "data", None), "path", None)
                if p is not None and Path(p) == target:
                    self._set_tree_target(node)
                    return
                stack.extend(node.children)
        except Exception:                # noqa: BLE001
            pass

    async def action_quit(self) -> None:
        """`q` quits normally; while the create Input is open `q` is a filename character the focused
        Input consumes, so the quit binding does not fire then (guarded here as a backstop)."""
        if self._new_kind:
            return
        self.exit()

    def _clipboard_text(self) -> str:
        """Read the Windows clipboard for terminals/psmux sessions where native paste is swallowed."""
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            return ""
        return completed.stdout

    def action_paste_clipboard(self) -> None:
        """Ctrl-Shift-V — explicit OS-clipboard paste fallback for psmux/terminal clipboard gaps."""
        text = self._clipboard_text()
        if not text:
            self.notify("clipboard is empty or unavailable", severity="warning")
            return
        focused = self.focused
        if isinstance(focused, TextArea):
            focused.insert(text)
            focused.focus()
        elif self._new_kind:
            inp = self.query_one("#newname", Input)
            inp.insert_text_at_cursor(text.replace("\r\n", "\n").replace("\n", " "))
            inp.focus()
            self._set_new_error(_name_error(inp.value, self._new_kind) or "")
        else:
            self.notify("focus the editor or start a new file/folder before pasting", severity="warning")

    def action_undo(self) -> None:
        ta = self.query_one("#edit", TextArea)
        if ta.display:
            ta.undo()

    def action_redo(self) -> None:
        ta = self.query_one("#edit", TextArea)
        if ta.display:
            ta.redo()

    def _active_scroller(self):
        """The horizontal-scroll target: editor when editing, otherwise the read-only viewer."""
        ta = self.query_one("#edit", TextArea)
        return ta if ta.display else self.query_one("#view", ScrollableContainer)

    def action_scroll_left(self) -> None:
        """Ctrl-Left — keyboard-accessible horizontal scroll for terminals without H-wheel support."""
        self._active_scroller().scroll_relative(x=-_HSCROLL_STEP, y=0, animate=False, force=True)

    def action_scroll_right(self) -> None:
        """Ctrl-Right — keyboard-accessible horizontal scroll for long lines in view OR edit mode."""
        self._active_scroller().scroll_relative(x=_HSCROLL_STEP, y=0, animate=False, force=True)

    # ── collapsible panels (VSCode-style: maximize the editor for more editing space) ──
    def _is_typing(self) -> bool:
        """True while focus is in the editor (#edit) or the new-file name field (#newname), so the
        collapse toggles (z / [ / ]) stay INERT and those keys type a literal character into the text
        instead of firing a layout change. (A deliberate button CLICK bypasses this — a click is
        explicit intent — so the controls still work while a file is open in the editor.)"""
        if self._new_kind is not None:
            return True
        return isinstance(self.focused, (TextArea, Input))

    def _apply_layout(self) -> None:
        """Show/hide the two side columns to match the collapse flags. #right is width:1fr, so it
        FLEXES to fill whatever horizontal space a hidden column frees — that is the 'more editing
        space' effect. Best-effort: a query miss never crashes the pane."""
        try:
            self.query_one("#core", Static).display = not self._core_hidden
            self.query_one("#treecol", Vertical).display = not self._tree_hidden
        except Exception:                # noqa: BLE001 — layout is best-effort, never fatal
            pass
        self._sync_toggle_labels()

    def _sync_toggle_labels(self) -> None:
        """Reflect the maximize state on the control-strip button so a mouse user sees whether it will
        maximize or restore. (The core/explorer buttons keep a fixed label — the column visibly
        appearing or vanishing is feedback enough.)"""
        try:
            maximized = self._core_hidden and self._tree_hidden
            self.query_one("#max_toggle", Button).label = "restore" if maximized else "max"
        except Exception:                # noqa: BLE001 — the label is cosmetic, never fatal
            pass

    def _do_toggle_core(self) -> None:
        """Flip the LEFT core column's visibility (no typing guard — the button-click path)."""
        self._core_hidden = not self._core_hidden
        self._apply_layout()

    def _do_toggle_tree(self) -> None:
        """Flip the CENTER explorer column's visibility (no typing guard — the button-click path)."""
        self._tree_hidden = not self._tree_hidden
        self._apply_layout()

    def _do_toggle_maximize(self) -> None:
        """Maximize the editor: hide BOTH side columns; if already maximized, restore both."""
        maximized = self._core_hidden and self._tree_hidden
        self._core_hidden = not maximized
        self._tree_hidden = not maximized
        self._apply_layout()

    def action_toggle_core(self) -> None:
        """`[` — collapse/restore the LEFT core column; #right flexes to fill. Inert while typing."""
        if self._is_typing():
            return
        self._do_toggle_core()

    def action_toggle_tree(self) -> None:
        """`]` — collapse/restore the CENTER explorer column; #right flexes to fill. Inert while typing."""
        if self._is_typing():
            return
        self._do_toggle_tree()

    def action_toggle_maximize(self) -> None:
        """`z` — the PRIMARY 'more editing space' toggle: hide BOTH side columns so the editor/viewer
        (#right) goes full-width; press again to restore all three. Inert while typing so `z` types a
        literal 'z' into the editor / name field."""
        if self._is_typing():
            return
        self._do_toggle_maximize()

    def _tree_signature(self):
        """A cheap snapshot of WHAT THE TREE SHOWS: the filtered child listing under every expanded
        directory node. When it is unchanged between ticks the visible tree is idle, so _auto_reload
        skips the disruptive reload (which would otherwise yank the cursor back to the root every
        cycle). A new file an agent drops into a VISIBLE folder changes the listing -> a reload fires."""
        try:
            tree = self.query_one("#tree", DirectoryTree)
        except Exception:                # noqa: BLE001
            return None
        sig = []
        stack = [tree.root]
        while stack:
            node = stack.pop()
            if not (getattr(node, "allow_expand", False) and node.is_expanded):
                continue                                     # only EXPANDED dirs are visible -> watched
            path = getattr(getattr(node, "data", None), "path", None)
            if path is None:
                path = self.project if node is tree.root else None
            if path is None:
                continue
            try:
                names = tuple(sorted(
                    p.name for p in Path(path).iterdir()
                    if p.name not in _HIDE and not p.name.endswith(".pyc")))
            except OSError:
                names = ()
            sig.append((str(path), names))
            stack.extend(node.children)
        return tuple(sig)

    def _prime_tree_signature(self) -> None:
        """Capture the initial listing once the async tree load has settled (shortly after mount), so
        the first auto-refresh tick already matches and an idle tree never reloads."""
        self._last_tree_sig = self._tree_signature()

    def _auto_reload(self) -> None:
        """Timer tick (every few seconds): reload the tree so files/folders the agents or the user
        created appear without pressing `r` — but ONLY when the visible listing actually CHANGED, and
        keeping the user's highlighted row. An unchanged (idle) tree is left untouched so the highlight
        never flashes back to the root. Wrapped so a transient FS/Textual hiccup never crashes the
        pane. Skipped WHILE the create Input is open: a reload moves the tree cursor (changing the
        target directory) out from under the name being typed."""
        if self._new_kind:
            return
        try:
            sig = self._tree_signature()
            if sig is not None and sig == self._last_tree_sig:
                return                                       # nothing visible changed -> don't disturb the cursor
            self._last_tree_sig = sig
            self._reload_keep_cursor()
        except Exception:                # noqa: BLE001 — the pane must survive any reload error
            pass

    # ── new file / new folder (focused inline create field) ─────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the toolbar/control buttons. The ＋File/＋Folder buttons mirror the n/f keybinds (open
        the inline name field AND focus it). The collapse controls (max/core/files + the explorer's ‹)
        call the toggle logic DIRECTLY — bypassing the _is_typing guard, since a click is explicit
        intent — so they work even while a file is open in the editor."""
        bid = event.button.id
        if bid == "newfilebtn":
            self.action_new_file()
        elif bid == "newfolderbtn":
            self.action_new_folder()
        elif bid == "max_toggle":
            self._do_toggle_maximize()
        elif bid == "core_toggle":
            self._do_toggle_core()
        elif bid in ("tree_toggle", "tree_close"):
            self._do_toggle_tree()

    def action_new_file(self) -> None:
        """`n` / ＋File — open the focused name field; the user types the filename (extension included,
        VSCode-style); Enter creates it empty in the highlighted folder and opens it in the editor."""
        if self._new_kind:
            return
        self._begin_create("file")

    def action_new_folder(self) -> None:
        """`f` / ＋Folder — open the focused name field; a name + Enter creates the directory in the
        highlighted folder."""
        if self._new_kind:
            return
        self._begin_create("folder")

    def _begin_create(self, kind: str) -> None:
        """Open the inline create field: reveal the #newname Input, label it, clear it, and MOVE FOCUS
        ONTO IT so the name is immediately typable — whatever the trigger was (the ＋File/＋Folder
        button left focus on the button, the editor left it on the TextArea, the tree on the tree).

        The focused Input natively consumes every printable key — including the e/n/f/r/q app shortcuts —
        so keystrokes can never leak into the tree/editor or fire an app action (the exact live-pane
        failure: the placeholder showed but nothing could be typed). The .focus() is re-applied after a
        refresh so it sticks even when a button press (which focuses the button) opened the field."""
        self._new_kind = kind
        self._mouse_tree_node = None
        self._set_new_error("")
        inp = self.query_one("#newname", Input)
        inp.value = ""
        inp.placeholder = self._draft_placeholder()
        inp.border_title = "New file" if kind == "file" else "New folder"
        inp.border_subtitle = "Enter ✓  ·  Esc ✗"
        inp.display = True
        inp.focus()                                          # take focus off the button/editor/tree …
        self.call_after_refresh(self._refocus_new_input)     # … and make sure it sticks (button press)
        self._update_cwd()

    def _refocus_new_input(self) -> None:
        """Re-assert focus on the name Input after a refresh, defeating any focus contention from the
        button press that opened it (best-effort; only while a create is still open)."""
        if not self._new_kind:
            return
        try:
            self.query_one("#newname", Input).focus()
        except Exception:                # noqa: BLE001 — focus is best-effort, never fatal
            pass

    def _end_create(self, focus_tree: bool = True) -> None:
        """Tear down the create field; return focus to the tree unless a new file opened (then focus
        stays in the editor)."""
        self._new_kind = None
        try:
            inp = self.query_one("#newname", Input)
            inp.value = ""
            inp.display = False
        except Exception:                # noqa: BLE001 — teardown is best-effort
            pass
        self._set_new_error("")
        if focus_tree:
            self.query_one("#tree", DirectoryTree).focus()

    # ── the inline create field (the #newname Input) ────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-validate the name as the user types so the #newerror line gives immediate feedback."""
        if event.input.id != "newname" or not self._new_kind:
            return
        self._set_new_error(_name_error(event.value, self._new_kind) or "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the name field creates the file/folder. The Input emits Submitted, so Enter never
        has to be intercepted as a raw key event."""
        if event.input.id != "newname" or not self._new_kind:
            return
        event.stop()
        self._do_create()

    def _target_dir_node(self):
        """The tree NODE of the folder a new entry lands in: the highlighted directory, a highlighted
        file's PARENT folder, else the tree root (project). Drives _target_dir + the cwd line."""
        tree = self.query_one("#tree", DirectoryTree)
        node = self._create_target_node or tree.cursor_node
        path = getattr(getattr(node, "data", None), "path", None)
        if node is None or path is None:
            return tree.root
        try:
            if Path(path).is_dir():
                return node                                  # highlighted a directory
        except OSError:
            return tree.root
        parent = node.parent                                 # highlighted a file -> its containing folder
        return parent if parent is not None else tree.root

    def _target_dir(self) -> Path:
        """Where a new file/folder lands ON DISK: the directory of `_target_dir_node` (the highlighted
        dir, or a highlighted file's parent), falling back to the project root. The cwd line shows this
        path while the create field is open, so the user can see where the name will land."""
        try:
            path = getattr(getattr(self._target_dir_node(), "data", None), "path", None)
            if path is not None:
                p = Path(path)
                if p.is_dir():
                    return p
        except Exception:               # noqa: BLE001 — fall back to the project root on any tree error
            pass
        return self.project

    def _do_create(self) -> None:
        """Read the name Input and create the file or folder. The user typed the full name — extension
        included for files — so it's used verbatim (no type picker). On a bad/duplicate name the field
        stays open and re-focused so the user can fix it."""
        kind = self._new_kind
        if kind is None:
            return
        inp = self.query_one("#newname", Input)
        raw_name = inp.value
        error = _name_error(raw_name, kind)
        if error:
            self._set_new_error(error)
            self.notify(error, severity="warning")
            inp.focus()
            return
        name = raw_name.strip()
        target = self._target_dir() / name
        if target.exists():
            error = f"Already exists: {name}"
            self._set_new_error(error)
            self.notify(error, severity="warning")
            inp.focus()
            return
        if kind == "folder":
            if self._create_folder(name) is not None:
                self._end_create()
        else:
            if self._create_file(name) is not None:
                self._end_create(focus_tree=False)    # leave focus in the editor

    def _set_new_error(self, message: str) -> None:
        try:
            err = self.query_one("#newerror", Static)
            err.update(message)
            err.display = bool(message)
        except Exception:                # noqa: BLE001 — validation UI is helpful, never fatal
            pass

    def _create_file(self, name: str) -> Path | None:
        """Create an empty file `name` under the selected dir, reload the tree, select it in the
        explorer, and open it in the editor — so it 'shows directly' VSCode-style. Never overwrites —
        returns None (with a notify) if it exists or can't be written."""
        target = self._target_dir() / name
        if target.exists():
            self.notify(f"already exists: {name}", severity="warning")
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        except OSError as e:
            self.notify(f"create failed: {e}", severity="error")
            return None
        self._reload_tree()
        self._open(target)                                   # show it in the RIGHT column …
        self.action_edit()                                   # … and drop into the editor (new, empty)
        self._reveal_in_tree(target)                         # … and select it in the explorer
        return target

    def _create_folder(self, name: str) -> Path | None:
        """Create directory `name` under the selected dir, reload the tree, and select it in the
        explorer. Never overwrites — returns None (with a notify) if it exists or can't be created."""
        target = self._target_dir() / name
        if target.exists():
            self.notify(f"already exists: {name}", severity="warning")
            return None
        try:
            target.mkdir(parents=True)
        except OSError as e:
            self.notify(f"create failed: {e}", severity="error")
            return None
        self._reload_tree()
        self._reveal_in_tree(target)                         # select the new folder in the explorer
        return target

    def _reveal_in_tree(self, path: Path) -> None:
        """Best-effort: after the (async) reload, move the tree cursor onto `path` so a just-created
        file/folder is SELECTED in the explorer, VSCode-style — walking nested folders too, so a file
        created INSIDE a subfolder is selected there (not just top-level entries). Scheduled after a
        refresh; a silent no-op if the node isn't loaded yet (the editor + cwd line already point at it)."""
        target = Path(path)

        def _select() -> None:
            try:
                tree = self.query_one("#tree", DirectoryTree)
                stack = list(tree.root.children)             # DFS so nested (in-subfolder) nodes match
                while stack:
                    node = stack.pop()
                    p = getattr(getattr(node, "data", None), "path", None)
                    if p is not None and Path(p) == target:
                        tree.move_cursor(node)
                        return
                    stack.extend(node.children)
            except Exception:                # noqa: BLE001 — selection is cosmetic, never fatal
                pass

        self.call_after_refresh(_select)


def filesview_main():
    """`mk-files-view <project> [cockpit]`: the cockpit's Files pane (defaults to the cwd). The
    'cockpit' marker turns on the mouse-row compensation for psmux's status-bar offset."""
    project = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    cockpit = len(sys.argv) > 2 and sys.argv[2] == "cockpit"
    FilesApp(project, cockpit=cockpit).run()
    return 0


if __name__ == "__main__":
    filesview_main()
