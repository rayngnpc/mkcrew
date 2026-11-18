"""Read-only project peek, independent of the cockpit's engine."""
from __future__ import annotations

import os
import ntpath
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final

from rich.cells import cell_len
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Input, Static
from textual.worker import get_current_worker

AZURE: Final = "#0087ff"
AMBER: Final = "#d78700"
GREEN: Final = "#00af5f"
RED: Final = "#d75f5f"
GREY: Final = "#8a8a8a"
RULE: Final = "#585858"
MARKS: Final = {"M": AMBER, "A": GREEN, "?": GREEN, "D": RED, "R": AMBER, "U": RED}
SKIP: Final = set(".git node_modules .venv venv __pycache__ .mypy_cache .pytest_cache .ruff_cache .mkcrew dist build".split())
# Project files written by agent.py's ensure_* hook, plugin and instruction writers.
CREW_FILES: Final = frozenset({".claude/settings.json", ".codex/config.toml", ".codex/hooks.json",
                              ".opencode/plugins/mkcrew-pull.ts", "AGENTS.md", "CLAUDE.md"})
CAP: Final = 20_000
_CONTROLS: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")
PreviewKey = tuple[str, str, int | None, int | None]
_WINDOWS = os.name == "nt"


@dataclass(frozen=True, slots=True)
class Entry:
    path: str
    mark: str = ""
    mtime: float | None = None


@dataclass(frozen=True, slots=True)
class Scan:
    entries: tuple[Entry, ...] = ()
    is_git: bool = False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Preview:
    path: str = ""
    lines: tuple[str, ...] = ()
    diff: bool = False
    added: int = 0
    removed: int = 0
    note: str = ""
    new: bool = False


def find_root(start: Path) -> Path:
    """Use the nearest cockpit project, rather than the enclosing Git root."""
    start = Path(start).resolve()
    return next((p for p in (start, *start.parents) if (p / ".mkcrew").is_dir()), start)


def _run(argv: list[str], *, env: Mapping[str, str] | None = None) -> str:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=10, check=True, env=env).stdout.decode("utf-8", errors="replace")


def _git(root: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    argv = ["git", "--no-optional-locks", "-C", str(root), "-c", "core.quotepath=off", *args]
    return _run(argv, env=env) if env is not None else _run(argv)


def display_text(value: str) -> str:
    """Neutralize terminal controls at the display boundary, after tab expansion."""
    return _CONTROLS.sub("�", value.expandtabs(4))


def _preview_lines(value: str) -> list[str]:
    return [display_text(line.removesuffix("\r")) for line in value.removesuffix("\n").split("\n")] if value else []


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB"


def _status(raw: str, prefix: str = "") -> list[tuple[str, str]]:
    """Parse porcelain NUL records; rename/copy source paths are separate fields."""
    records = iter(raw.split("\0"))
    result = []
    for record in records:
        if not record:
            continue
        xy, path = record[:2], record[3:]
        if "R" in xy or "C" in xy:
            next(records, None)
        if not path.startswith(prefix):
            continue
        mark = next(mark for condition, mark in (
            (xy == "??", "?"), ("U" in xy or xy in {"AA", "DD"}, "U"),
            ("D" in xy, "D"), ("R" in xy, "R"), ("A" in xy, "A"), (True, "M")) if condition)
        result.append((path.removeprefix(prefix), mark))
    return result


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _preview_key(root: Path, entry: Entry) -> PreviewKey:
    try:
        stat = (root / entry.path).stat()
    except OSError:
        return entry.path, entry.mark, None, None
    return entry.path, entry.mark, stat.st_mtime_ns, stat.st_size


def scan(root: Path) -> Scan:
    """List Git-visible files, falling back to a bounded plain directory walk."""
    root = Path(root)
    try:
        prefix = _git(root, "rev-parse", "--show-prefix").rstrip("\r\n")
        marks = dict(_status(_git(root, "status", "--porcelain=v1", "-z",
                                  "--untracked-files=all", "--", "."), prefix))
        paths = dict.fromkeys(p for p in _git(root, "ls-files", "-z", "--cached").split("\0") if p)
        paths.update(dict.fromkeys(marks))
        paths = {p: None for p in paths if ".mkcrew" not in p.split("/")[:-1]}
        marks = {p: mark for p, mark in marks.items()
                 if p in paths and (mark != "?" or p not in CREW_FILES)}
        entries = tuple(Entry(p, marks.get(p, ""), _mtime(root / p) if p in marks else None)
                        for p in list(paths)[:CAP])
        return Scan(entries, True, len(paths) > CAP)
    except (OSError, subprocess.SubprocessError):
        entries = []
        for directory, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for name in sorted(files):
                if name.endswith(".pyc"):
                    continue
                if len(entries) == CAP:
                    return Scan(tuple(entries), truncated=True)
                path = Path(directory) / name
                entries.append(Entry(path.relative_to(root).as_posix()))
        return Scan(tuple(entries))


def rank(entries: tuple[Entry, ...], query: str, limit: int = 200) -> tuple[list[Entry], int]:
    """Match every term, then rank filename hits before directory-only hits."""
    terms = query.casefold().split()
    matches = [e for e in entries if all(term in e.path.casefold() for term in terms)]
    if terms:
        matches.sort(key=lambda e: (-sum(t in e.path.rsplit("/", 1)[-1].casefold() for t in terms),
                                    not bool(e.mark), cell_len(e.path), e.path))
    else:
        matches.sort(key=lambda e: (not bool(e.mark), -(e.mtime if e.mtime is not None else float("-inf"))
                                    if e.mark else 0, e.path if e.mark else e.path.casefold(), e.path))
    return matches[:limit], len(matches)


def age(mtime: float | None, now: float | None = None) -> str:
    if mtime is None:
        return ""
    seconds = max(0, (time.time() if now is None else now) - mtime)
    if seconds < 5:
        return "now"
    for unit, divisor in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= divisor:
            return f"{int(seconds // divisor)}{unit}"
    return "now"


def fit_path(path: str, width: int) -> str:
    """Keep the filename and as much trailing directory context as will fit."""
    path = display_text(path)
    if width <= 0:
        return ""
    if cell_len(path) <= width:
        return path
    parts = path.split("/")
    suffix = parts.pop()
    if cell_len(suffix) + 2 <= width:
        while parts and cell_len("…/" + parts[-1] + "/" + suffix) <= width:
            suffix = parts.pop() + "/" + suffix
        return "…/" + suffix
    tail = ""
    for char in reversed(suffix):
        if cell_len(char + tail) > width - 1:
            break
        tail = char + tail
    return "…" + tail


def load_preview(root: Path, entry: Entry) -> Preview:
    """Read a bounded diff or text preview without touching the UI thread."""
    if entry.mark in {"M", "A", "D", "R", "U"}:
        try:
            lines = _preview_lines(_git(root, "diff", "HEAD", "--no-color", "--no-ext-diff", "--", entry.path))
        except (OSError, subprocess.SubprocessError):
            lines = None
        if lines is not None:
            first = next((i for i, line in enumerate(lines) if line.startswith("@@")), None)
            if first is None:
                return Preview(entry.path, note="binary or mode-only change", diff=True)
            diff = lines[first:]
            return Preview(entry.path, tuple(diff[:2000]), True,
                           sum(line.startswith("+") for line in diff),
                           sum(line.startswith("-") for line in diff),
                           "diff cut at 2,000 lines" if len(diff) > 2000 else "")
    try:
        with (root / entry.path).open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            data = stream.read(262_144)
    except FileNotFoundError:
        return Preview(entry.path, note="file is gone")
    except OSError as error:
        return Preview(entry.path, note=f"cannot read: {error.strerror}")
    if b"\0" in data[:8192]:
        return Preview(entry.path, note=f"binary file · {_size(size)}")
    lines = _preview_lines(data.decode("utf-8", errors="replace"))
    note = "first 400 lines" if len(lines) > 400 else "first 262,144 bytes" if size > len(data) else ""
    return Preview(entry.path, tuple(lines[:400]), note=note or ("" if lines else "empty file"),
                   new=entry.mark == "?")


def _noop_editor(command: str) -> bool:
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return bool(words) and words[0] in {"true", "false", ":"}


def _editor(root: Path) -> str:
    try:
        editor = _git(root, "var", "GIT_EDITOR").strip()
        if _noop_editor(editor):
            env = {key: value for key, value in os.environ.items()
                   if key not in {"GIT_EDITOR", "VISUAL", "EDITOR"} or not _noop_editor(value)}
            editor = _git(root, "var", "GIT_EDITOR", env=env).strip()
        editor = ("notepad" if _WINDOWS else "vi") if _noop_editor(editor) else editor
    except (OSError, subprocess.SubprocessError):
        editors = (os.environ.get("VISUAL"), os.environ.get("EDITOR"))
        editor = next((editor for editor in editors if editor and not _noop_editor(editor)),
                      "notepad" if _WINDOWS else "vi")
    if _WINDOWS:
        try:
            words = shlex.split(editor, posix=False)
        except ValueError:
            words = []
        program = words[0].strip('"') if words else ""
        if not program or not (shutil.which(program) or Path(program).is_file()):
            return "notepad"
    return editor


def editor_command(root: Path, path: str) -> list[str] | str:
    editor = _editor(root)
    if _WINDOWS:
        absolute = str((root / path).absolute())
        return f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{editor} "{absolute}""'
    return ["sh", "-c", editor + ' "$@"', editor, str((root / path).absolute())]


def _frame(rows: list[Text], width: int, height: int, left: int = 0, right: int = 0) -> Text:
    rows = rows[:max(0, height)]
    rows.extend(Text() for _ in range(max(0, height - len(rows))))
    left = min(left, max(0, width))
    right = min(right, max(0, width - left))
    for row in rows:
        row.truncate(max(0, width - left - right), overflow="crop", pad=True)
    return Text("\n", no_wrap=True, overflow="crop").join(Text.assemble(" " * left, row, " " * right)
                                                          for row in rows)


def _pair(left: Text, right: Text, width: int) -> Text:
    left = left.copy()
    right = right.copy()
    right.truncate(max(0, width), overflow="crop")
    left.truncate(max(0, width - cell_len(right.plain) - 1), overflow="crop")
    left.append(" " * max(0, width - cell_len(left.plain) - cell_len(right.plain)))
    left.append_text(right)
    return left


def _list_rows(entries: list[Entry], query: str, total: int) -> list[Entry | str]:
    rows: list[Entry | str] = []
    previous = None
    for entry in entries:
        changed = bool(entry.mark)
        if not query.strip() and changed != previous:
            rows.append(" CHANGED" if changed else " FILES")
            previous = changed
        rows.append(entry)
    if total > len(entries):
        rows.append(f" … {total - len(entries):,} more, keep typing")
    return rows or [" no match" if query.strip() else " no files here"]


def render_list(entries: list[Entry], query: str = "", selected: int = 0, *, width: int,
                height: int, offset: int = 0, total: int | None = None,
                now: float = 0.0, scanning: bool = False) -> Text:
    """Render one bounded viewport; geometry and selection are explicit inputs."""
    rows = []
    if scanning:
        return _frame([Text(" scanning…", GREY)], width, height)
    content_width = max(0, width - 1)
    chosen = entries[selected] if 0 <= selected < len(entries) else None
    for item in _list_rows(entries, query, len(entries) if total is None else total)[offset:offset + height]:
        if isinstance(item, str):
            rows.append(Text(item, GREY))
            continue
        stamp = age(item.mtime, now) if item.mark else ""
        path = fit_path(item.path, max(0, content_width - 4 - (cell_len(stamp) + 1 if stamp else 0)))
        directory, slash, name = path.rpartition("/")
        row = Text.assemble(" ", (item.mark or " ", MARKS.get(item.mark, "default")), "  ",
                            (directory + slash, GREY), name)
        row = _pair(row, Text(stamp, GREY), content_width) if stamp else row
        row.truncate(content_width, overflow="crop", pad=True)
        if item == chosen:
            row = Text(row.plain, style=f"#ffffff on {AZURE}")
        for term in query.split():
            for match in re.finditer(re.escape(term), path, re.IGNORECASE):
                row.stylize("bold", 4 + match.start(), 4 + match.end())
        rows.append(Text.assemble(row, " "))
    return _frame(rows, width, height)


def render_preview(preview: Preview, *, width: int, height: int, offset: int = 0) -> Text:
    """Render a fixed header, a cropped content page and a truncation note."""
    if not preview.path:
        return _frame([], width, height, left=1, right=1)
    summary = Text.assemble((f"+{preview.added}", GREEN), (f" −{preview.removed}", RED)) if preview.diff else (
        Text("new file", GREEN) if preview.new else Text(
            f"{len(preview.lines)} {'line' if len(preview.lines) == 1 else 'lines'}", GREY))
    if not preview.lines:
        summary = Text()
    rows = [_pair(Text(fit_path(preview.path, max(0, width - 2)), "bold"), summary, max(0, width - 2))]
    digits = cell_len(str(len(preview.lines)))
    content_height = max(0, height - 1 - bool(preview.note))
    for number, line in enumerate(preview.lines[offset:offset + content_height], offset + 1):
        if preview.diff:
            color = GREY if line.startswith("@@") else GREEN if line.startswith("+") else RED if line.startswith("-") else "default"
            rows.append(Text(display_text(line), color))
        else:
            rows.append(Text.assemble((f"{number:>{digits}}", GREY), "  ", display_text(line)))
    if preview.note and height > 1:
        if preview.lines:
            rows.extend(Text() for _ in range(max(0, height - 1 - len(rows))))
        rows.append(Text(display_text(preview.note), GREY))
    return _frame(rows, width, height, left=1, right=1)


class PeekApp(App[None]):
    """Mutable navigation state around two pure body renderers."""

    ENABLE_COMMAND_PALETTE = False
    REFRESH_INTERVAL = 2.0
    BINDINGS = [Binding(key, action, show=False, priority=True) for key, action in (
        ("up", "move(-1)"), ("ctrl+p", "move(-1)"), ("down", "move(1)"), ("ctrl+n", "move(1)"),
        ("pageup", "page(-1)"),
        ("pagedown", "page(1)"), ("enter", "open"), ("ctrl+y", "copy"),
        ("escape", "quit"), ("ctrl+c", "quit"))]
    CSS = """
    Screen, Horizontal, Static, Input, Input:focus {
        background: ansi_default; color: ansi_default; border: none;
        padding: 0; margin: 0; background-tint: transparent;
    }
    Screen { layout: vertical; }
    #header, #query, #footer { height: 1; }
    #prompt { width: 3; color: #0087ff; }
    Input { height: 1; width: 1fr; }
    Input > .input--cursor { background: ansi_default; color: ansi_default; text-style: underline; }
    Input > .input--selection { background: ansi_default; color: ansi_default; text-style: underline; }
    Input > .input--placeholder { color: #8a8a8a; text-style: none; }
    #body { height: 1fr; }
    #files, #preview, #rule { height: 1fr; overflow: hidden hidden; }
    #preview { width: 1fr; }
    #rule { width: 1; color: #585858; }
    """

    def __init__(self, root: Path) -> None:
        super().__init__(ansi_color=True)
        self.root = Path(root)
        self.snapshot = Scan()
        self.matches: list[Entry] = []
        self.total = self.selected = self.list_offset = self.preview_offset = 0
        self.preview = Preview()
        self.preview_entry: Entry | None = None
        self.preview_key: PreviewKey | None = None
        self.pending_key: PreviewKey | None = None
        self.scanned = False
        self.scanning = False
        self.scan_again = False
        self.status = ""
        self.editor_name = ""
        self.status_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="query"):
            yield Static(" › ", id="prompt")
            yield Input(placeholder="type to filter", id="query-input")
        with Horizontal(id="body"):
            yield Static(id="files")
            yield Static(id="rule")
            yield Static(id="preview")
        yield Static(id="footer")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._resolve_editor()
        self.set_interval(self.REFRESH_INTERVAL, self.refresh_scan)
        self.refresh_scan()
        self.redraw()

    @work(thread=True, group="editor")
    def _resolve_editor(self) -> None:
        try:
            words = shlex.split(_editor(self.root), posix=not _WINDOWS)
            if _WINDOWS:
                words = [word.strip('"') for word in words]
            executable = shutil.which(words[0]) if words else None
            filename = Path(os.path.realpath(executable)).name.split(".", 1)[0] if executable else (
                (ntpath.basename(words[0]) if _WINDOWS else Path(words[0]).name) if words else "")
            name = Text(display_text(filename))
            name.truncate(12, overflow="crop")
        except Exception as error:
            if not get_current_worker().is_cancelled:
                self.call_from_thread(self._message, f"cannot resolve editor: {error}")
            return
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._accept_editor, name.plain)

    def _accept_editor(self, name: str) -> None:
        self.editor_name = name
        self.redraw()

    def refresh_scan(self, force: bool = False) -> None:
        self.scan_again = self.scan_again or (self.scanning and force)
        if not self.scanning:
            self.scanning = True
            self._scan()

    @work(thread=True, group="scan")
    def _scan(self) -> None:
        try:
            snapshot = scan(self.root)
        except Exception as error:
            if not get_current_worker().is_cancelled:
                self.call_from_thread(self._scan_failed, str(error))
            return
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._accept_scan, snapshot)

    def _scan_failed(self, reason: str) -> None:
        self.scanning = self.scan_again = False
        self._message(f"scan failed: {reason}")

    def _accept_scan(self, snapshot: Scan) -> None:
        self.scanning = False
        self.scanned = True
        self.snapshot = snapshot
        self._filter(check_preview=True)
        if self.scan_again:
            self.scan_again = False
            self.refresh_scan()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter()

    def _filter(self, check_preview: bool = False) -> None:
        path = self.matches[self.selected].path if self.matches else None
        self.matches, self.total = rank(self.snapshot.entries, self.query_one(Input).value)
        self.selected = next((i for i, entry in enumerate(self.matches) if entry.path == path), 0)
        self._selection_changed(check_preview)

    def _selection_changed(self, check_preview: bool = False) -> None:
        entry = self.matches[self.selected] if self.matches else None
        if entry != self.preview_entry or check_preview:
            same_path = entry is not None and entry.path == self.preview.path
            self.preview_entry = entry
            if not same_path:
                self.preview_offset = 0
                self.preview_key = None
                self.pending_key = None
                self.preview = Preview(entry.path) if entry else Preview()
            if entry:
                self._check_preview(entry)
        rows = _list_rows(self.matches, self.query_one(Input).value, self.total)
        row = next((i for i, item in enumerate(rows) if item == entry), 0)
        anchor = row - int(row > 0 and isinstance(rows[row - 1], str))
        height = max(1, self.query_one("#files").size.height)
        self.list_offset = max(0, min(self.list_offset, anchor, max(0, len(rows) - height)))
        if row >= self.list_offset + height:
            self.list_offset = row - height + 1
        self.redraw()

    @work(thread=True, exclusive=True, group="preview-check")
    def _check_preview(self, entry: Entry) -> None:
        try:
            key = _preview_key(self.root, entry)
        except Exception as error:
            if not get_current_worker().is_cancelled:
                self.call_from_thread(self._accept_preview, entry,
                                      Preview(entry.path, note=f"preview failed: {error}"), None)
            return
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._consider_preview, entry, key)

    def _consider_preview(self, entry: Entry, key: PreviewKey) -> None:
        if entry == self.preview_entry and key not in (self.preview_key, self.pending_key):
            self.pending_key = key
            self._load_preview(entry, key)

    @work(thread=True, exclusive=True, group="preview")
    def _load_preview(self, entry: Entry, key: PreviewKey | None) -> None:
        try:
            preview = load_preview(self.root, entry)
        except Exception as error:
            key = None
            preview = Preview(entry.path, note=f"preview failed: {error}")
        if not get_current_worker().is_cancelled:
            self.call_from_thread(self._accept_preview, entry, preview, key)

    def _accept_preview(self, entry: Entry, preview: Preview, key: PreviewKey | None) -> None:
        if entry == self.preview_entry:
            self.pending_key = None
            self.preview_key = key
            self.preview = preview
            self._scroll_preview(0)

    def on_resize(self) -> None:
        if self.is_mounted:
            self.call_after_refresh(self._selection_changed)

    def redraw(self) -> None:
        width = self.size.width
        query = self.query_one(Input).value
        files = self.query_one("#files", Static)
        preview = self.query_one("#preview", Static)
        rule = self.query_one("#rule", Static)
        preview.display = rule.display = width >= 90
        files.styles.width = min(64, max(34, int(width * .38))) if width >= 90 else "1fr"
        height = max(0, self.size.height - 3)
        list_width = min(64, max(34, int(width * .38))) if width >= 90 else width
        files.update(render_list(self.matches, query, self.selected, width=list_width,
                                 height=height, offset=self.list_offset, total=self.total,
                                 now=time.time(), scanning=not self.scanned))
        rule.update(Text("\n".join("│" for _ in range(height)), RULE))
        preview.update(render_preview(self.preview, width=max(0, width - list_width - 1),
                                      height=height, offset=self.preview_offset))
        count = "20000+" if self.snapshot.truncated else str(len(self.snapshot.entries))
        right = Text()
        changed = sum(bool(entry.mark) for entry in self.snapshot.entries)
        if self.scanned:
            if self.snapshot.is_git:
                right.append(f"{changed} changed", AMBER if changed else GREY)
                right.append(" · ", GREY)
            right.append(f"{self.total} of {count} files" if query.strip() else f"{count} files", GREY)
        left = Text.assemble((" ⬢ ", AZURE), ("peek", "bold"), ("  " + display_text(self.root.name), GREY))
        self.query_one("#header", Static).update(_frame([_pair(left, right, max(0, width - 1))], width, 1))
        if self.status:
            footer = Text.assemble(("● ", GREEN), self.status)
        else:
            opening = f"open in {self.editor_name}" if width >= 90 and self.editor_name else "open"
            hints = [("↑↓", "move"), ("enter", opening), ("^y", "copy path"),
                     ("pgup/pgdn", "scroll"), ("esc", "close")] if width >= 70 else (
                         [("enter", "open"), ("^y", "copy"), ("esc", "close")] if width >= 40 else [("esc", "close")])
            footer = Text("   ").join(Text.assemble((key, "bold"), (" " + meaning, GREY)) for key, meaning in hints)
        self.query_one("#footer", Static).update(_frame([Text.assemble(" ", footer)], width, 1))

    def action_move(self, delta: int) -> None:
        self.selected = max(0, min(len(self.matches) - 1, self.selected + delta))
        self._selection_changed()

    def _scroll_preview(self, delta: int) -> None:
        page = max(1, self.size.height - 4 - bool(self.preview.note))
        self.preview_offset = max(0, min(max(0, len(self.preview.lines) - page), self.preview_offset + delta))
        self.redraw()

    def action_page(self, direction: int) -> None:
        self._scroll_preview(direction * max(1, self.size.height - 4 - bool(self.preview.note)))

    def on_click(self, event: events.Click) -> None:
        files = self.query_one("#files")
        offset = event.get_content_offset(files)
        if event.widget == files and offset is not None:
            rows = _list_rows(self.matches, self.query_one(Input).value, self.total)
            row = self.list_offset + offset.y
            if 0 <= row < len(rows):
                item = rows[row]
                if isinstance(item, Entry):
                    self.selected = self.matches.index(item)
                    self._selection_changed()
        self.query_one(Input).focus()

    def _wheel(self, event: events.MouseEvent, delta: int) -> None:
        if event.widget == self.query_one("#files"):
            self.action_move(delta)
        if event.widget == self.query_one("#preview"):
            self._scroll_preview(delta * 3)
        event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel(event, 1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel(event, -1)

    def _message(self, text: str) -> None:
        self.status = display_text(text)
        if self.status_timer:
            self.status_timer.stop()
        self.status_timer = self.set_timer(2.5, partial(self._message, "")) if text else None
        self.redraw()

    def launch_editor(self, path: str) -> None:
        command = editor_command(self.root, path)
        try:
            with self.suspend():
                subprocess.run(command, cwd=self.root, check=False)
        except SuspendNotSupported:
            subprocess.run(command, cwd=self.root, check=False)

    def action_open(self) -> None:
        if not self.matches:
            return
        entry = self.matches[self.selected]
        if entry.mark == "D" or not (self.root / entry.path).exists():
            self._message("deleted, nothing to open")
            return
        try:
            self.launch_editor(entry.path)
        except OSError as error:
            self._message(f"cannot open editor: {error.strerror}")
        self.refresh_scan(force=True)

    def action_copy(self) -> None:
        if not self.matches:
            return
        path = self.matches[self.selected].path
        try:
            if _WINDOWS:
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                "[Console]::InputEncoding=[Text.Encoding]::UTF8; "
                                "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                               input=path.encode("utf-8"), capture_output=True, timeout=10, check=True)
            elif os.environ.get("TMUX"):
                try:
                    _run(["tmux", "set-buffer", "-w", "--", path])
                except (OSError, subprocess.SubprocessError):
                    _run(["tmux", "set-buffer", "--", path])
            else:
                self.copy_to_clipboard(path)
        except (OSError, subprocess.SubprocessError) as error:
            self._message(f"copy failed: {error}")
            return
        self._message(f"copied {path}" + (" · Ctrl-b ] pastes" if not _WINDOWS and os.environ.get("TMUX") else ""))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-h", "--help"}:
        print("usage: peek [dir]")
        return 0
    root = Path(args[0] if args else Path.cwd()).expanduser()
    if not root.is_dir():
        print(f"peek: not a directory: {display_text(str(root))}", file=sys.stderr)
        return 2
    PeekApp(find_root(root)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
