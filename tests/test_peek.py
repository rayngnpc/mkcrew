from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from threading import Event, Lock, get_ident

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import SuspendNotSupported
from textual.pilot import Pilot
from textual.worker import WorkerCancelled
from textual.widgets import Input, Static

from mkcrew import peek
from mkcrew.peek import Entry, PeekApp, Preview, Scan


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    monkeypatch.setattr(peek, "_WINDOWS", False)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Peek tests")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "peek@example.invalid")
    monkeypatch.delenv("GIT_EDITOR", raising=False)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=True, timeout=10).stdout


async def settle(app: PeekApp, pilot: Pilot) -> None:
    await pilot.pause()
    while app.workers:
        for worker in list(app.workers):
            try:
                await worker.wait()
            except WorkerCancelled:
                continue
        await pilot.pause()


def content_text(app: PeekApp, selector: str) -> str:
    content = app.query_one(selector, Static).content
    assert isinstance(content, Text)
    return content.plain


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "tracked.txt").write_text("old\ncontext\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "fixture")
    return tmp_path


@pytest.mark.parametrize(("status", "mark"), [
    (" M", "M"), ("A ", "A"), ("??", "?"), (" D", "D"), ("R ", "R"),
    ("UU", "U"), ("AU", "U"), ("UA", "U"), ("AA", "U"), ("DD", "U"), (" C", "M")])
def test_porcelain_status_precedence(status, mark):
    raw = status + " project/a file.txt\0"
    if "R" in status or "C" in status:
        raw += "project/old name.txt\0"
    assert peek._status(raw, "project/") == [("a file.txt", mark)]


def test_status_skips_rename_source_and_foreign_prefix():
    raw = "R  app/new.txt\0app/old.txt\0?? apple/foreign.txt\0 M app/next.txt\0"
    assert peek._status(raw, "app/") == [("new.txt", "R"), ("next.txt", "M")]


def test_real_scan_tracks_ignored_untracked_and_staged_deleted(repo):
    (repo / ".gitignore").write_text("secret\n", encoding="utf-8")
    (repo / "secret").write_text("ignore", encoding="utf-8")
    (repo / "new file.txt").write_text("new", encoding="utf-8")
    git(repo, "rm", "tracked.txt")
    result = peek.scan(repo)
    entries = {entry.path: entry for entry in result.entries}
    assert result.is_git
    assert entries["tracked.txt"].mark == "D"
    assert entries["tracked.txt"].mtime is None
    assert entries["new file.txt"].mark == "?"
    assert entries["new file.txt"].mtime is not None
    assert "secret" not in entries


def test_real_scan_subfolder_uses_relative_paths(repo):
    sub = repo / "nested dir"
    sub.mkdir()
    (sub / "known.txt").write_text("old", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "nested")
    (sub / "known.txt").write_text("changed", encoding="utf-8")
    (sub / "fresh.txt").write_text("fresh", encoding="utf-8")
    result = peek.scan(sub)
    assert {(e.path, e.mark) for e in result.entries} == {("known.txt", "M"), ("fresh.txt", "?")}


def test_real_scan_includes_rename_destination_only(repo):
    git(repo, "mv", "tracked.txt", "renamed file.txt")
    assert [(e.path, e.mark) for e in peek.scan(repo).entries] == [("renamed file.txt", "R")]


def test_plain_scan_skips_noise_directories_and_bytecode(tmp_path):
    for directory in peek.SKIP:
        folder = tmp_path / directory
        folder.mkdir()
        (folder / "noise").write_text("x", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("yes", encoding="utf-8")
    (tmp_path / "noise.pyc").write_bytes(b"x")
    result = peek.scan(tmp_path)
    assert not result.is_git
    assert [(e.path, e.mark) for e in result.entries] == [("keep.txt", "")]


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("git", 10),
                                   subprocess.CalledProcessError(1, "git")])
def test_scan_falls_back_when_git_fails(tmp_path, monkeypatch, failure):
    (tmp_path / "local").write_text("yes", encoding="utf-8")
    def broken(*args):
        raise failure
    monkeypatch.setattr(peek, "_git", broken)
    assert peek.scan(tmp_path).entries[0].path == "local"
    assert not peek.scan(tmp_path).is_git


def test_scan_caps_walk_and_git_results(tmp_path, monkeypatch):
    monkeypatch.setattr(peek, "CAP", 2)
    for name in ("a", "b", "c"):
        (tmp_path / name).touch()
    assert peek.scan(tmp_path).truncated
    assert len(peek.scan(tmp_path).entries) == 2
    git(tmp_path, "init")
    assert peek.scan(tmp_path).truncated
    assert len(peek.scan(tmp_path).entries) == 2


def test_empty_query_orders_changed_newest_then_unchanged():
    entries = (Entry("z", "M", 2), Entry("b", "D"), Entry("A"), Entry("a", "M", 2),
               Entry("new", "?", 3), Entry("B"))
    matches, total = peek.rank(entries, "")
    assert [e.path for e in matches] == ["new", "a", "z", "b", "A", "B"]
    assert total == 6


def test_ranking_name_hits_then_changed_then_shorter_path():
    entries = (Entry("core/a.txt", "M"), Entry("tests/core.py"), Entry("core.py", "M"),
               Entry("src/core.py", "M"), Entry("other.py"))
    matches, total = peek.rank(entries, "CORE", limit=3)
    assert [e.path for e in matches] == ["core.py", "src/core.py", "tests/core.py"]
    assert total == 4
    assert peek.rank(entries, "CORE src")[0] == [Entry("src/core.py", "M")]


@pytest.mark.parametrize(("seconds", "expected"), [
    (-1, "now"), (0, "now"), (4.99, "now"), (5, "5s"), (59, "59s"),
    (60, "1m"), (3599, "59m"), (3600, "1h"), (86399, "23h"), (86400, "1d")])
def test_age_boundaries(seconds, expected):
    assert peek.age(100_000 - seconds, now=100_000) == expected


def test_age_missing_timestamp_is_blank():
    assert peek.age(None, 100) == ""


@pytest.mark.parametrize(("path", "width", "expected"), [
    ("src/parser/core.py", 18, "src/parser/core.py"),
    ("src/parser/core.py", 16, "…/parser/core.py"),
    ("src/parser/core.py", 9, "…/core.py"),
    ("longfilename.py", 5, "…e.py"), ("x", 0, ""), ("x", 1, "x"),
    ("src/文件.py", 7, "…件.py"), ("src/文件.py", 9, "…/文件.py")])
def test_fit_path_uses_terminal_cells(path, width, expected):
    result = peek.fit_path(path, width)
    assert result == expected
    assert cell_len(result) <= width


def test_diff_preview_starts_at_hunk_and_counts_changes(repo):
    (repo / "tracked.txt").write_text("new\ncontext\nadded\n", encoding="utf-8")
    preview = peek.load_preview(repo, Entry("tracked.txt", "M"))
    assert preview.diff
    assert preview.lines[0].startswith("@@")
    assert (preview.added, preview.removed) == (2, 1)
    assert not any(line.startswith("diff --git") for line in preview.lines)


def test_untracked_text_preview_expands_tabs(tmp_path):
    (tmp_path / "new").write_text("x\ty\n", encoding="utf-8")
    preview = peek.load_preview(tmp_path, Entry("new", "?"))
    assert preview.new and not preview.diff
    assert preview.lines == ("x   y",)


def test_binary_note_reports_actual_size(tmp_path):
    (tmp_path / "binary").write_bytes(b"\0" + b"x" * 14540)
    preview = peek.load_preview(tmp_path, Entry("binary"))
    assert preview.note == "binary file · 14.2 KB"
    assert not preview.lines


def test_preview_limits_lines_and_bytes(tmp_path):
    (tmp_path / "many").write_text("line\n" * 401, encoding="utf-8")
    preview = peek.load_preview(tmp_path, Entry("many"))
    assert len(preview.lines) == 400
    assert preview.note == "first 400 lines"
    (tmp_path / "wide").write_bytes(b"x" * 300_000)
    preview = peek.load_preview(tmp_path, Entry("wide"))
    assert len(preview.lines[0]) == 262_144
    assert preview.note == "first 262,144 bytes"


def test_unborn_repo_diff_falls_back_to_text(tmp_path):
    git(tmp_path, "init")
    (tmp_path / "first").write_text("hello\n", encoding="utf-8")
    git(tmp_path, "add", "first")
    preview = peek.load_preview(tmp_path, Entry("first", "A"))
    assert not preview.diff
    assert preview.lines == ("hello",)


def test_empty_and_missing_preview_notes(tmp_path):
    (tmp_path / "empty").touch()
    assert peek.load_preview(tmp_path, Entry("empty")).note == "empty file"
    assert peek.load_preview(tmp_path, Entry("gone")).note == "file is gone"


def test_mode_only_diff_gets_explicit_note(repo):
    git(repo, "update-index", "--chmod=+x", "tracked.txt")
    assert peek.load_preview(repo, Entry("tracked.txt", "M")).note == "binary or mode-only change"


def test_diff_preview_cap_does_not_corrupt_totals(repo):
    (repo / "tracked.txt").write_text("added\n" * 2100, encoding="utf-8")
    preview = peek.load_preview(repo, Entry("tracked.txt", "M"))
    assert len(preview.lines) == 2000
    assert preview.note == "diff cut at 2,000 lines"
    assert (preview.added, preview.removed) == (2100, 2)


def test_editor_command_honors_git_and_preserves_shell_argv(repo, monkeypatch):
    monkeypatch.setenv("GIT_EDITOR", "my-editor --wait")
    assert peek.editor_command(repo, "a file's.txt") == [
        "sh", "-c", 'my-editor --wait "$@"', "my-editor --wait", str(repo / "a file's.txt")]


@pytest.mark.parametrize("editor", ["code --wait", "notepad", '"C:\\Program Files\\Notepad++\\notepad++.exe" -multiInst',
                                    '"C:/Program Files/Notepad++/notepad++.exe" -multiInst'])
def test_windows_editor_command_passes_quoted_path_to_cmd(tmp_path, monkeypatch, editor):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    monkeypatch.setattr(peek, "_editor", lambda root: editor)
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    path = tmp_path / "a file.txt"
    command = peek.editor_command(tmp_path, path.name)
    assert isinstance(command, str)
    assert command.startswith('"cmd.exe" /d /s /c ')
    payload = command.split(" /c ", 1)[1]
    assert payload.startswith('"') and payload.endswith('"')
    assert payload[1:-1] == f'{editor} "{path}"'
    assert '\\"' not in command


def test_windows_editor_fallback_is_notepad(tmp_path, monkeypatch):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    monkeypatch.setattr(peek, "_git", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    assert peek._editor(tmp_path) == "notepad"


def test_windows_missing_git_editor_falls_back_in_footer_and_command(tmp_path, monkeypatch):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    monkeypatch.setattr(peek.shutil, "which", lambda executable: None)
    def git_editor(root, *args, **kwargs):
        if args == ("var", "GIT_EDITOR"):
            return "vi"
        raise FileNotFoundError()
    monkeypatch.setattr(peek, "_git", git_editor)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert "enter open in notepad" in content_text(app, "#footer")
            assert ' /c "notepad ' in peek.editor_command(tmp_path, "file.txt")
    asyncio.run(scenario())


def test_windows_resolvable_editor_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    monkeypatch.setattr(peek, "_git", lambda *args, **kwargs: "code --wait")
    monkeypatch.setattr(peek.shutil, "which", lambda executable: "C:/bin/code.cmd" if executable == "code" else None)
    assert peek._editor(tmp_path) == "code --wait"


def test_windows_quoted_existing_editor_path_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    executable = tmp_path / "Program Files" / "edit.exe"
    executable.parent.mkdir()
    executable.touch()
    editor = f'"{executable}" -multiInst'
    monkeypatch.setattr(peek, "_git", lambda *args, **kwargs: editor)
    monkeypatch.setattr(peek.shutil, "which", lambda executable: None)
    assert peek._editor(tmp_path) == editor


@pytest.mark.parametrize("failure", [False, True])
def test_windows_copy_uses_unicode_clipboard_even_in_psmux(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    monkeypatch.setenv("TMUX", "psmux")
    calls, messages = [], []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if failure:
            raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(peek.subprocess, "run", run)
    app = PeekApp(tmp_path)
    app.matches = [Entry("thư mục/文件.txt")]
    monkeypatch.setattr(app, "_message", messages.append)
    app.action_copy()
    assert calls[0][0][0] == "powershell"
    assert calls[0][1]["input"] == "thư mục/文件.txt".encode("utf-8")
    assert calls[0][1]["check"] is True
    assert messages[0].startswith("copy failed:" if failure else "copied ")
    assert "Ctrl-b ]" not in messages[0]


def test_editor_fallback_uses_visual_then_editor_then_vi(tmp_path, monkeypatch):
    def missing(*args):
        raise FileNotFoundError()
    monkeypatch.setattr(peek, "_git", missing)
    monkeypatch.setenv("VISUAL", "visual --wait")
    monkeypatch.setenv("EDITOR", "other")
    assert peek.editor_command(tmp_path, "x")[3] == "visual --wait"
    monkeypatch.delenv("VISUAL")
    assert peek.editor_command(tmp_path, "x")[3] == "other"
    monkeypatch.delenv("EDITOR")
    assert peek.editor_command(tmp_path, "x")[3] == "vi"


def test_find_root_uses_nearest_cockpit_ancestor(tmp_path):
    (tmp_path / ".mkcrew").mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".mkcrew").mkdir()
    child = nested / "src"
    child.mkdir()
    assert peek.find_root(child) == nested
    assert peek.find_root(nested) == nested


def test_find_root_without_cockpit_keeps_start(tmp_path):
    assert peek.find_root(tmp_path) == tmp_path


@pytest.mark.parametrize(("width", "height"), [(150, 30), (100, 24), (60, 16)])
def test_pure_renderers_have_exact_height_and_cropped_cell_width(width, height):
    entries = [Entry("目录/" * 20 + "文件.py", "M", 90), Entry("README.md")]
    preview = Preview(entries[0].path, tuple("界" * 200 for _ in range(50)), note="first 400 lines")
    for output in (peek.render_list(entries, width=width, height=height, now=100),
                   peek.render_preview(preview, width=width, height=height)):
        assert isinstance(output, Text)
        assert len(output.plain.split("\n")) == height
        assert all(cell_len(line) <= width for line in output.plain.split("\n"))


def test_selected_row_leaves_default_margin_with_no_residual_colors():
    output = peek.render_list([Entry("src/a", "M", 90), Entry("b")], selected=0,
                              width=40, height=5, now=100)
    rows = output.split("\n")
    console = Console()
    for col in range(39):
        style = rows[1].get_style_at_offset(console, col)
        assert style == Style(color="#ffffff", bgcolor=peek.AZURE)
    assert rows[1].plain[-1] == " "
    assert rows[1].get_style_at_offset(console, 39).bgcolor is None
    assert all(rows[3].get_style_at_offset(console, col).bgcolor is None for col in range(40))


def test_labels_only_empty_query_and_overflow_row_is_nonselectable():
    entries = [Entry("src/a", "M"), Entry("src/b")]
    empty = peek.render_list(entries, width=40, height=7, total=202).plain
    filtered = peek.render_list(entries, "src", width=40, height=7).plain
    assert " CHANGED" in empty and " FILES" in empty
    assert "… 200 more, keep typing" in empty
    assert " CHANGED" not in filtered and " FILES" not in filtered
    assert "no files here" in peek.render_list([], width=40, height=3).plain
    assert "no match" in peek.render_list([], "x", width=40, height=3).plain


@pytest.mark.parametrize("size", [(150, 36), (70, 20)])
def test_app_real_typing_selection_preview_open_copy_and_escape(repo, monkeypatch, size):
    (repo / "tracked.txt").write_text("new\ncontext\n", encoding="utf-8")
    (repo / "tracked-two.txt").write_text("second", encoding="utf-8")
    os.utime(repo / "tracked.txt", (200, 200))
    os.utime(repo / "tracked-two.txt", (100, 100))
    opened = []
    copied = []
    monkeypatch.setattr(PeekApp, "launch_editor", lambda self, path: opened.append(path))
    monkeypatch.setattr(PeekApp, "copy_to_clipboard", lambda self, path: copied.append(path))
    async def scenario():
        app = PeekApp(repo)
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await pilot.pause()
            assert app.focused is app.query_one(Input)
            assert app.query_one("#preview").display == (size[0] >= 90)
            if size[0] >= 90:
                assert "@@" in content_text(app, "#preview")
            await pilot.press("t", "r", "a", "c", "k", "e", "d")
            await settle(app, pilot)
            await pilot.pause()
            assert len(app.matches) == 2
            assert "CHANGED" not in content_text(app, "#files")
            await pilot.press("down")
            await settle(app, pilot)
            path = app.matches[app.selected].path
            assert app.selected == 1
            await pilot.press("enter", "ctrl+y")
            await pilot.pause()
            assert opened == [path]
            assert copied == [path]
            assert f"copied {path}" in content_text(app, "#footer")
            await pilot.press("escape")
        assert not app.is_running
    asyncio.run(scenario())


def test_app_mouse_selects_actual_row_and_keeps_query_focus(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text("line\n" * 80, encoding="utf-8")
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            await pilot.pause()
            assert await pilot.click("#files", offset=(6, 2))
            await settle(app, pilot)
            assert app.matches[app.selected].path == "b"
            assert app.focused is app.query_one(Input)
            await pilot.press("pagedown")
            assert app.preview_offset > 0
            await pilot.press("pageup")
            assert app.preview_offset == 0
            await pilot.click("#files", offset=(6, 0))
            assert app.matches[app.selected].path == "b"
            await pilot.press("ctrl+c")
    asyncio.run(scenario())


def test_refresh_preserves_path_and_reloads_changed_preview(tmp_path):
    (tmp_path / "a").write_text("old", encoding="utf-8")
    (tmp_path / "b").write_text("second", encoding="utf-8")
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            app.action_move(1)
            await settle(app, pilot)
            (tmp_path / "b").write_text("updated", encoding="utf-8")
            os.utime(tmp_path / "b", (100, 100))
            (tmp_path / "0").touch()
            app.refresh_scan()
            await settle(app, pilot)
            assert app.matches[app.selected].path == "b"
            assert app.preview.lines == ("updated",)
    asyncio.run(scenario())


def test_copy_inside_tmux_falls_back_and_reports_paste_hint(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    calls = []
    def tmux(argv):
        calls.append(argv)
        if "-w" in argv:
            raise subprocess.CalledProcessError(1, argv)
        return ""
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            monkeypatch.setenv("TMUX", "test-only")
            monkeypatch.setattr(peek, "_run", tmux)
            await pilot.press("ctrl+y")
            assert calls == [["tmux", "set-buffer", "-w", "--", "a"], ["tmux", "set-buffer", "--", "a"]]
            assert "Ctrl-b ] pastes" in app.status
    asyncio.run(scenario())


def test_deleted_file_does_not_launch_an_editor(repo, monkeypatch):
    git(repo, "rm", "tracked.txt")
    opened = []
    monkeypatch.setattr(PeekApp, "launch_editor", lambda self, path: opened.append(path))
    async def scenario():
        app = PeekApp(repo)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("enter")
            assert not opened
            assert app.status == "deleted, nothing to open"
    asyncio.run(scenario())


def test_main_invalid_directory_reports_error(tmp_path, capsys):
    assert peek.main([str(tmp_path / "missing")]) == 2
    assert f"peek: not a directory: {tmp_path / 'missing'}" in capsys.readouterr().err


def test_main_help(capsys):
    assert peek.main(["--help"]) == 0
    assert capsys.readouterr().out == "usage: peek [dir]\n"


@pytest.mark.parametrize("supported", [True, False])
@pytest.mark.parametrize("windows", [False, True])
def test_launcher_uses_suspend_or_supported_fallback(tmp_path, monkeypatch, supported, windows):
    command = ('"cmd.exe" /d /s /c "editor "a""' if windows
               else ["sh", "-c", 'editor "$@"', "editor", str(tmp_path / "a")])
    calls = []
    def suspend(self):
        if not supported:
            raise SuspendNotSupported("test driver")
        return nullcontext()
    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
    monkeypatch.setattr(peek, "editor_command", lambda root, path: command)
    monkeypatch.setattr(PeekApp, "suspend", suspend)
    monkeypatch.setattr(peek.subprocess, "run", launch)
    PeekApp(tmp_path).launch_editor("a")
    assert calls == [(command, {"cwd": tmp_path, "check": False})]


def test_mouse_wheels_route_to_list_and_preview(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).write_text("line\n" * 80, encoding="utf-8")
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert await pilot._post_mouse_events([events.MouseScrollDown], "#files", offset=(5, 1))
            await settle(app, pilot)
            assert app.matches[app.selected].path == "b"
            assert await pilot._post_mouse_events([events.MouseScrollDown], "#preview", offset=(5, 2))
            await pilot.pause()
            assert app.preview_offset == 3
            assert await pilot._post_mouse_events([events.MouseScrollUp], "#preview", offset=(5, 2))
            await pilot.pause()
            assert app.preview_offset == 0
            assert await pilot._post_mouse_events([events.MouseScrollUp], "#files", offset=(5, 1))
            await settle(app, pilot)
            assert app.matches[app.selected].path == "a"
    asyncio.run(scenario())


def test_selection_scrolls_into_view_and_labels_stay_unselectable(tmp_path):
    for number in range(40):
        (tmp_path / f"file-{number:02}").touch()
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(70, 20)) as pilot:
            await settle(app, pilot)
            await pilot.press(*(["down"] * 25))
            await settle(app, pilot)
            assert app.matches[app.selected].path == "file-25"
            assert "file-25" in content_text(app, "#files")
            assert app.list_offset > 0
            await pilot.press(*(["up"] * 30))
            await settle(app, pilot)
            assert app.selected == 0
            assert " FILES" in content_text(app, "#files")
    asyncio.run(scenario())


def test_running_scan_is_not_doubled_and_editor_refresh_is_queued(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    started, release = Event(), Event()
    lock = Lock()
    state = {"running": 0, "maximum": 0, "calls": 0}
    original_scan = peek.scan
    def slow_scan(root):
        with lock:
            state["calls"] += 1
            state["running"] += 1
            state["maximum"] = max(state["maximum"], state["running"])
        started.set()
        try:
            assert release.wait(3), "scan was not released"
            return original_scan(root)
        finally:
            with lock:
                state["running"] -= 1
    monkeypatch.setattr(peek, "scan", slow_scan)
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", 60)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            try:
                assert await asyncio.to_thread(started.wait, 3)
                app.refresh_scan()
                app.refresh_scan()
                app.refresh_scan(force=True)
                assert len([w for w in app.workers if w.group == "scan"]) == 1
                assert state["calls"] == 1
                assert app.scan_again
            finally:
                release.set()
            await settle(app, pilot)
            assert state["maximum"] == 1
            assert state["calls"] == 2
            assert not app.scan_again
    asyncio.run(scenario())


@pytest.mark.parametrize(("width", "expected", "absent"), [
    (100, "pgup/pgdn scroll", ""), (60, "^y copy", "pgup/pgdn"), (30, "esc close", "enter")])
def test_footer_width_budgets_and_terminal_default_background(tmp_path, width, expected, absent):
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(width, 16)) as pilot:
            await settle(app, pilot)
            footer = content_text(app, "#footer")
            assert expected in footer
            if absent:
                assert absent not in footer
            for widget in (app.screen, *app.screen.query("Horizontal, Static, Input")):
                assert widget.styles.background.ansi == -1
            assert app.query_one(Input).get_component_rich_style("input--cursor").bgcolor == Style.parse("on default").bgcolor
    asyncio.run(scenario())


def test_main_dispatches_nearest_project_without_real_app(tmp_path, monkeypatch):
    (tmp_path / ".mkcrew").mkdir()
    sub = tmp_path / "src"
    sub.mkdir()
    roots = []
    monkeypatch.setattr(PeekApp, "run", lambda self: roots.append(self.root))
    assert peek.main([str(sub)]) == 0
    assert roots == [tmp_path]


def test_render_list_uses_only_its_explicit_clock_input(monkeypatch):
    def unexpected_clock():
        raise AssertionError("renderer accessed wall clock")
    monkeypatch.setattr(peek.time, "time", unexpected_clock)
    entries = [Entry("a", "M", 10)]
    assert "now" in peek.render_list(entries, width=30, height=3).plain
    assert "10s" in peek.render_list(entries, width=30, height=3, now=20).plain


def test_changed_and_filtered_ties_use_path_order():
    entries = (Entry("afile", "M", 10), Entry("Zfile", "M", 10))
    assert [e.path for e in peek.rank(entries, "")[0]] == ["Zfile", "afile"]
    assert [e.path for e in peek.rank(entries, "file")[0]] == ["Zfile", "afile"]
    plain = (Entry("afile"), Entry("Zfile"))
    assert [e.path for e in peek.rank(plain, "")[0]] == ["afile", "Zfile"]


@pytest.mark.parametrize("marked", [False, True])
def test_preview_neutralizes_terminal_controls_in_text_and_diff(repo, marked):
    payload = "a\tESC:\x1b[2J BEL:\x07 CR:\r DEL:\x7f C1:\x85\n"
    (repo / "tracked.txt").write_bytes(payload.encode("utf-8"))
    preview = peek.load_preview(repo, Entry("tracked.txt", "M" if marked else ""))
    rendered = peek.render_preview(preview, width=100, height=12).plain
    for value in (*preview.lines, rendered.replace("\n", "")):
        assert all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in value)
    assert "�[2J" in rendered
    assert "CR:�" in rendered


def test_display_boundary_replaces_all_control_ranges_after_expanding_tabs():
    for code in (*range(32), *range(127, 160)):
        assert peek.display_text(chr(code)) == ("    " if code == 9 else "�")
    assert peek.display_text("a\tb") == "a   b"


def test_control_character_paths_render_safely_in_list_and_header():
    path = "folder/evil\x1b[2J\x07\r\x7f\x85.txt"
    for rendered in (peek.render_list([Entry(path)], width=100, height=4),
                     peek.render_preview(Preview(path, ("safe",)), width=100, height=4)):
        assert all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in rendered.plain.replace("\n", ""))
        assert "evil�[2J����.txt" in rendered.plain


def test_project_name_and_status_neutralize_terminal_controls(tmp_path):
    project = tmp_path / ("project" if os.name == "nt" else "project\x1b[2J\x07")
    project.mkdir()
    async def scenario():
        app = PeekApp(project)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            app._message("copied evil\x1b[2J\x7f\x85.txt")
            if os.name != "nt":
                assert "project�[2J�" in content_text(app, "#header")
            else:
                assert "project" in content_text(app, "#header")
            assert "copied evil�[2J��.txt" in content_text(app, "#footer")
            assert "\x1b" not in content_text(app, "#header") + content_text(app, "#footer")
    asyncio.run(scenario())


def test_git_commands_disable_optional_locks(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(peek, "_run", lambda argv: (commands.append(argv), "")[1])
    peek._git(tmp_path, "status", "--porcelain=v1")
    assert commands == [["git", "--no-optional-locks", "-C", str(tmp_path), "-c",
                         "core.quotepath=off", "status", "--porcelain=v1"]]


def test_scan_walks_untracked_only_through_status(tmp_path, monkeypatch):
    commands = []
    def git_output(root, *args):
        commands.append(args)
        return "" if args[0] == "rev-parse" else "?? new\0" if args[0] == "status" else "tracked\0"
    monkeypatch.setattr(peek, "_git", git_output)
    snapshot = peek.scan(tmp_path)
    assert ("ls-files", "-z", "--cached") in commands
    assert {entry.path for entry in snapshot.entries} == {"tracked", "new"}
    assert len(commands) == 3


def test_scan_stats_only_changed_git_entries(repo, monkeypatch):
    (repo / "new").touch()
    measured = []
    monkeypatch.setattr(peek, "_mtime", lambda path: (measured.append(path.name), 42)[1])
    entries = {entry.path: entry for entry in peek.scan(repo).entries}
    assert measured == ["new"]
    assert entries["tracked.txt"].mtime is None
    assert entries["new"].mtime == 42


def test_plain_scan_never_stats_entries(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    def unexpected_stat(path):
        raise AssertionError("plain scan statted an entry")
    monkeypatch.setattr(peek, "_mtime", unexpected_stat)
    assert peek.scan(tmp_path).entries == (Entry("a"),)


def test_selected_only_probe_reloads_for_size_change_with_same_mtime(tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    original_key, original_load = peek._preview_key, peek.load_preview
    probes, reads = [], []
    def probe(root, entry):
        probes.append(entry.path)
        return original_key(root, entry)
    def load(root, entry):
        reads.append(entry.path)
        return original_load(root, entry)
    monkeypatch.setattr(peek, "_preview_key", probe)
    monkeypatch.setattr(peek, "load_preview", load)
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", 60)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            app.refresh_scan()
            await settle(app, pilot)
            assert probes == ["a", "a"]
            assert reads == ["a"]
            stamp = (tmp_path / "a").stat()
            (tmp_path / "a").write_text("longer content", encoding="utf-8")
            os.utime(tmp_path / "a", ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            app.refresh_scan()
            await settle(app, pilot)
            assert probes == ["a", "a", "a"]
            assert reads == ["a", "a"]
            assert app.preview.lines == ("longer content",)
    asyncio.run(scenario())


def test_list_preview_and_empty_state_margins():
    listed = peek.render_list([Entry("a", "M", 90)], width=30, height=4, now=100)
    assert listed.plain.split("\n")[1].endswith("10s ")
    for output in (listed, peek.render_list([], "x", width=30, height=4),
                   peek.render_list([], width=30, height=4),
                   peek.render_list([Entry("a")], width=30, height=4, total=2)):
        assert all(line.startswith(" ") and line.endswith(" ") for line in output.plain.split("\n"))
    preview = peek.render_preview(Preview("a", ("line",)), width=30, height=4)
    assert all(line.startswith(" ") for line in preview.plain.split("\n"))


@pytest.mark.parametrize(("note", "diff", "new"), [
    ("empty file", False, True), ("file is gone", False, False), ("cannot read: denied", False, False),
    ("binary file · 13 B", False, False), ("binary or mode-only change", True, False)])
def test_contentless_preview_note_is_below_header_without_summary(note, diff, new):
    rendered = peek.render_preview(Preview("a", diff=diff, note=note, new=new), width=60, height=6)
    rows = rendered.plain.split("\n")
    assert rows[0].strip() == "a"
    assert rows[1].strip() == note
    assert all(not row.strip() for row in rows[2:])


def test_truncation_note_stays_on_last_row():
    rendered = peek.render_preview(Preview("a", ("one", "two"), note="first 400 lines"), width=30, height=6)
    assert rendered.plain.split("\n")[-1].strip() == "first 400 lines"


@pytest.mark.parametrize(("size", "expected"), [(13, "13 B"), (14541, "14.2 KB"), (3250585, "3.1 MB")])
def test_binary_size_units(tmp_path, size, expected):
    path = tmp_path / "binary"
    with path.open("wb") as stream:
        stream.write(b"\0")
        stream.truncate(size)
    assert peek.load_preview(tmp_path, Entry("binary")).note == f"binary file · {expected}"


@pytest.mark.parametrize("selected", [-1, 0])
def test_match_bold_applies_only_to_path_not_mark_or_age(selected):
    rendered = peek.render_list([Entry("mfile", "M", 0)], "m", selected,
                                width=30, height=1, now=180)
    row = rendered.split("\n")[0]
    console = Console()
    assert row.get_style_at_offset(console, 4).bold
    assert not row.get_style_at_offset(console, 1).bold
    assert not row.get_style_at_offset(console, row.plain.rindex("3m") + 1).bold


def test_new_selection_has_header_and_blank_body_until_preview_arrives(tmp_path, monkeypatch):
    (tmp_path / "a").write_text("old body", encoding="utf-8")
    (tmp_path / "b").write_text("new body", encoding="utf-8")
    started, release = Event(), Event()
    original = peek.load_preview
    def delayed(root, entry):
        if entry.path == "b":
            started.set()
            assert release.wait(3), "preview was not released"
        return original(root, entry)
    monkeypatch.setattr(peek, "load_preview", delayed)
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", 60)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            try:
                await pilot.press("down")
                assert await asyncio.to_thread(started.wait, 3)
                rows = content_text(app, "#preview").split("\n")
                assert rows[0].strip() == "b"
                assert all(not row.strip() for row in rows[1:])
                assert "loading" not in content_text(app, "#preview")
            finally:
                release.set()
            await settle(app, pilot)
            assert "new body" in content_text(app, "#preview")
    asyncio.run(scenario())


def test_same_path_reload_preserves_body_and_scroll_then_clamps(tmp_path, monkeypatch):
    path = tmp_path / "a"
    path.write_text("old body\n" * 80, encoding="utf-8")
    hold, started, release = Event(), Event(), Event()
    original = peek.load_preview
    def delayed(root, entry):
        if hold.is_set():
            started.set()
            assert release.wait(3), "reload was not released"
        return original(root, entry)
    monkeypatch.setattr(peek, "load_preview", delayed)
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", 60)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("pagedown")
            offset = app.preview_offset
            before = content_text(app, "#preview")
            assert offset > 0
            hold.set()
            path.write_text("short replacement\n", encoding="utf-8")
            try:
                app.refresh_scan()
                assert await asyncio.to_thread(started.wait, 3)
                await pilot.pause()
                assert app.preview_offset == offset
                assert content_text(app, "#preview") == before
            finally:
                release.set()
            await settle(app, pilot)
            assert app.preview_offset == 0
            assert "short replacement" in content_text(app, "#preview")
    asyncio.run(scenario())


def test_scan_worker_failure_keeps_listing_and_timer_recovers(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    fail = Event()
    original = peek.scan
    def flaky(root):
        if fail.is_set():
            raise RuntimeError("bad scan\x1b[2J")
        return original(root)
    monkeypatch.setattr(peek, "scan", flaky)
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", .1)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            before = app.snapshot
            fail.set()
            app.refresh_scan()
            await settle(app, pilot)
            assert app.snapshot == before
            assert "scan failed: bad scan�[2J" in content_text(app, "#footer")
            assert app.is_running and not app.scanning
            fail.clear()
            (tmp_path / "after-failure").touch()
            async with asyncio.timeout(3):
                while "after-failure" not in content_text(app, "#files"):
                    await pilot.pause()
    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["_preview_key", "load_preview"])
def test_preview_worker_failure_becomes_safe_note_and_can_retry(tmp_path, monkeypatch, boundary):
    (tmp_path / "a").write_text("readable", encoding="utf-8")
    original = getattr(peek, boundary)
    def broken(*args):
        raise ValueError("bad preview\x1b[2J")
    monkeypatch.setattr(peek, boundary, broken)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert app.is_running
            rows = content_text(app, "#preview").split("\n")
            assert rows[0].strip() == "a"
            assert rows[1].strip() == "preview failed: bad preview�[2J"
            monkeypatch.setattr(peek, boundary, original)
            app.refresh_scan()
            await settle(app, pilot)
            assert "readable" in content_text(app, "#preview")
    asyncio.run(scenario())


def test_first_paint_is_scanning_without_counts(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    started, release = Event(), Event()
    original = peek.scan
    def slow(root):
        started.set()
        assert release.wait(3), "initial scan was not released"
        return original(root)
    monkeypatch.setattr(peek, "scan", slow)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            try:
                assert await asyncio.to_thread(started.wait, 3)
                await pilot.pause()
                assert content_text(app, "#files").split("\n")[0].startswith(" scanning…")
                assert " files" not in content_text(app, "#header")
                assert " changed" not in content_text(app, "#header")
                assert content_text(app, "#header").endswith(" ")
                assert content_text(app, "#footer").startswith(" ")
            finally:
                release.set()
            await settle(app, pilot)
            assert "1 files" in content_text(app, "#header")
            assert "scanning…" not in content_text(app, "#files")
    asyncio.run(scenario())


def test_refresh_timer_discovers_new_file_without_manual_refresh(tmp_path, monkeypatch):
    (tmp_path / "a").touch()
    monkeypatch.setattr(PeekApp, "REFRESH_INTERVAL", .1)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            (tmp_path / "created-after-start.txt").write_text("new", encoding="utf-8")
            async with asyncio.timeout(3):
                while "created-after-start.txt" not in content_text(app, "#files"):
                    await pilot.pause()
            assert any(entry.path == "created-after-start.txt" for entry in app.matches)
    asyncio.run(scenario())


def test_invalid_directory_error_neutralizes_control_characters(tmp_path, capsys):
    assert peek.main([str(tmp_path / "missing\x1b[2J\x7f")]) == 2
    error = capsys.readouterr().err
    assert "missing�[2J�" in error
    assert "\x1b" not in error and "\x7f" not in error


def test_header_keeps_right_margin_when_counts_exceed_narrow_width(tmp_path):
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(30, 16)) as pilot:
            await settle(app, pilot)
            app.snapshot = Scan(tuple(Entry(f"file-{i}", "M") for i in range(20_000)), True, True)
            app.query_one(Input).value = "missing"
            await pilot.pause()
            assert content_text(app, "#header").endswith(" ")
            assert cell_len(content_text(app, "#header")) == 30
    asyncio.run(scenario())


@pytest.mark.parametrize("marked", [False, True])
def test_crlf_line_endings_do_not_become_replacements_in_text_or_diff(repo, marked):
    git(repo, "config", "core.autocrlf", "false")
    path = repo / "crlf.txt"
    path.write_bytes(b"alpha\r\ntwo\r\n")
    git(repo, "add", "crlf.txt")
    git(repo, "commit", "-m", "CRLF baseline")
    path.write_bytes(b"alpha\r\nthree\r\n")
    preview = peek.load_preview(repo, Entry("crlf.txt", "M" if marked else ""))
    assert preview.lines
    assert all("�" not in line and "\r" not in line for line in preview.lines)
    assert "�" not in peek.render_preview(preview, width=60, height=8).plain
    assert ("+three" in preview.lines) if marked else (preview.lines == ("alpha", "three"))


def test_line_splitting_removes_only_one_trailing_cr_and_sanitizes_interior_cr():
    assert peek._preview_lines("alpha\r\r\nbeta\rmiddle\r\n") == ["alpha�", "beta�middle"]


@pytest.mark.parametrize("width", [0, 1, 2, 30, 90])
@pytest.mark.parametrize("preview", [
    Preview("long/path.txt", ("x" * 200,) * 5),
    Preview("long/path.txt", ("+" + "x" * 200,), True, 7, 2),
    Preview("long/path.txt", ("x" * 200,), new=True),
    Preview("long/path.txt", note="note " * 40),
    Preview("long/path.txt", ("x" * 200,), note="truncated " * 30),
    Preview()])
def test_preview_reserves_right_margin_for_headers_content_and_notes(width, preview):
    rows = peek.render_preview(preview, width=width, height=5).plain.split("\n")
    assert all(cell_len(row) == width for row in rows)
    assert width == 0 or all(row.startswith(" ") and row.endswith(" ") for row in rows)


@pytest.mark.parametrize("path", [".claude/settings.json", ".codex/config.toml", ".codex/hooks.json",
                                  ".opencode/plugins/mkcrew-pull.ts", "AGENTS.md", "CLAUDE.md"])
def test_untracked_generated_file_is_listed_without_change_mark_or_age(repo, path):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("generated", encoding="utf-8")
    entry = next(e for e in peek.scan(repo).entries if e.path == path)
    assert entry == Entry(path)


def test_git_scan_excludes_tracked_and_untracked_mkcrew_directories(repo):
    tracked = repo / ".mkcrew" / "known"
    tracked.parent.mkdir()
    tracked.write_text("old", encoding="utf-8")
    git(repo, "add", ".mkcrew/known")
    git(repo, "commit", "-m", "tracked cockpit state")
    tracked.write_text("changed", encoding="utf-8")
    for path in (".mkcrew/new", "nested/.mkcrew/hidden", ".mkcrew-other/keep", "src/.mkcrewfile"):
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    assert {e.path for e in peek.scan(repo).entries} == {
        "tracked.txt", ".mkcrew-other/keep", "src/.mkcrewfile"}


@pytest.mark.parametrize("status, mark", [(" M", "M"), ("A ", "A"), (" D", "D"), ("R ", "R"), ("UU", "U")])
def test_generated_file_keeps_every_tracked_change_mark(tmp_path, monkeypatch, status, mark):
    def git_output(root, *args):
        return "" if args[0] == "rev-parse" else (
            f"{status} CLAUDE.md\0" + ("old-name\0" if mark == "R" else "")
            if args[0] == "status" else "CLAUDE.md\0")
    monkeypatch.setattr(peek, "_git", git_output)
    entry = next(e for e in peek.scan(tmp_path).entries if e.path == "CLAUDE.md")
    assert entry.mark == mark


def test_generated_files_do_not_count_as_changed_in_header(repo):
    (repo / "CLAUDE.md").write_text("generated", encoding="utf-8")
    (repo / "other.md").write_text("ordinary change", encoding="utf-8")
    async def scenario():
        app = PeekApp(repo)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert "1 changed" in content_text(app, "#header")
            assert app.matches[0].path == "other.md"
            assert next(e for e in app.matches if e.path == "CLAUDE.md").mark == ""
    asyncio.run(scenario())


def test_ctrl_n_and_ctrl_p_move_selection_and_preserve_query(tmp_path):
    for name in ("alpha", "bravo"):
        (tmp_path / name).touch()
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            await pilot.press("a", "ctrl+n")
            await settle(app, pilot)
            assert app.preview.path == "bravo"
            await pilot.press("ctrl+p")
            await settle(app, pilot)
            assert app.preview.path == "alpha"
            assert app.query_one(Input).value == "a"
            assert app.query_one(Input).has_focus
    asyncio.run(scenario())


@pytest.mark.parametrize("command, name", [
    ("/usr/bin/nano --wait", "nano"),
    ('"/opt/My Editors/bin/code" --wait', "code"),
    ("/opt/long-editor-command --wait", "long-editor-"),
    ("/opt/编辑编辑编辑编辑 --wait", "编辑编辑编辑"),
    ("/opt/bad\x1b[2J --wait", "bad�[2J")])
@pytest.mark.parametrize("width", [89, 90, 120])
def test_footer_editor_name_uses_first_command_word_and_cell_limit(tmp_path, monkeypatch, command, name, width):
    monkeypatch.setenv("GIT_EDITOR", command)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(width, 24)) as pilot:
            await settle(app, pilot)
            footer = content_text(app, "#footer")
            if width >= 90:
                assert f"enter open in {name}   " in footer
            else:
                assert "enter open   " in footer and "open in" not in footer
            assert "esc close" in footer
            assert cell_len(footer) == width
            assert "\x1b" not in footer
    asyncio.run(scenario())


def test_editor_label_resolves_once_off_ui_thread_without_blocking_input(tmp_path, monkeypatch):
    (tmp_path / "alpha").touch()
    monkeypatch.setenv("GIT_EDITOR", "/usr/bin/nano --wait")
    started, release = Event(), Event()
    threads = []
    original = peek._git
    def delayed(root, *args):
        if args == ("var", "GIT_EDITOR"):
            threads.append(get_ident())
            started.set()
            assert release.wait(3), "editor resolution was not released"
        return original(root, *args)
    monkeypatch.setattr(peek, "_git", delayed)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            try:
                assert await asyncio.to_thread(started.wait, 3)
                await pilot.press("a")
                assert app.query_one(Input).value == "a"
                assert "enter open   " in content_text(app, "#footer")
                assert "open in" not in content_text(app, "#footer")
                assert threads == [threads[0]] and threads[0] != get_ident()
            finally:
                release.set()
            await settle(app, pilot)
            assert "enter open in nano" in content_text(app, "#footer")
            app.refresh_scan()
            await settle(app, pilot)
            await pilot.resize_terminal(80, 24)
            await pilot.resize_terminal(100, 24)
            await settle(app, pilot)
            assert len(threads) == 1
            assert "enter open in nano" in content_text(app, "#footer")
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["docs/AGENTS.md", ".claude/other.json", ".codex/other.toml"])
def test_untracked_paths_outside_exact_generated_list_keep_change_mark(repo, path):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.touch()
    assert next(e for e in peek.scan(repo).entries if e.path == path).mark == "?"


def test_footer_editor_uses_git_configuration_before_visual(repo, monkeypatch):
    git(repo, "config", "core.editor", '"/opt/My Editor/bin/nano" --wait')
    monkeypatch.setenv("VISUAL", "different-editor")
    async def scenario():
        app = PeekApp(repo)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert "enter open in nano" in content_text(app, "#footer")
    asyncio.run(scenario())


@pytest.mark.parametrize("visual, editor, expected", [
    ("/opt/visual --wait", "/opt/other", "visual"), ("", "/opt/other --wait", "other"), ("", "", "vi")])
def test_footer_editor_uses_visual_editor_vi_fallback(tmp_path, monkeypatch, visual, editor, expected):
    def missing(*args):
        raise FileNotFoundError()
    monkeypatch.setattr(peek, "_git", missing)
    monkeypatch.setenv("VISUAL", visual)
    monkeypatch.setenv("EDITOR", editor)
    monkeypatch.setenv("PATH", str(tmp_path))
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert f"enter open in {expected}" in content_text(app, "#footer")
    asyncio.run(scenario())


def test_editor_resolution_failure_keeps_app_running_with_safe_status(tmp_path, monkeypatch):
    def broken(root):
        raise RuntimeError("editor failure\x1b[2J")
    monkeypatch.setattr(peek, "_editor", broken)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert app.is_running and app.editor_name == ""
            assert "cannot resolve editor: editor failure�[2J" in content_text(app, "#footer")
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX extensionless symlinks and sh argv are not Windows editor behavior")
@pytest.mark.parametrize("word, target_name, expected", [
    ("editor", "vim.basic", "vim"), ("vi", "vim.basic", "vim"), ("editor", "nano", "nano")])
def test_footer_resolves_editor_symlink_chain_for_display_only(tmp_path, monkeypatch, word, target_name, expected):
    binary = tmp_path / "bin"
    binary.mkdir()
    target = tmp_path / "something" / target_name
    target.parent.mkdir()
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    link = tmp_path / "alternative"
    link.symlink_to(target)
    (binary / word).symlink_to(link)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("GIT_EDITOR", word + " --wait")
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert f"enter open in {expected}   " in content_text(app, "#footer")
            assert peek.editor_command(tmp_path, "file.txt")[2:4] == [word + ' --wait "$@"', word + " --wait"]
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows PATHEXT and cmd.exe editor resolution")
@pytest.mark.parametrize("full_path", [False, True])
def test_windows_footer_resolves_editor_command(tmp_path, monkeypatch, full_path):
    monkeypatch.setattr(peek, "_WINDOWS", True)
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "myedit.cmd").write_text("@exit /b 0\r\n", encoding="utf-8")
    program = tmp_path / "Notepad Plus" / "notepad++.exe"
    program.parent.mkdir()
    program.touch()
    editor = f'"{program}" -multiInst' if full_path else "myedit --wait"
    expected = "notepad++" if full_path else "myedit"
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("GIT_EDITOR", editor)
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert f"enter open in {expected}" in content_text(app, "#footer")
            command = peek.editor_command(tmp_path, "file.txt")
            assert isinstance(command, str)
            assert command == f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{editor} "{tmp_path / "file.txt"}""'
    asyncio.run(scenario())


def test_footer_unknown_editor_keeps_configured_name_and_dot(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("VISUAL", "")
    monkeypatch.setenv("EDITOR", "noedit.xyz --wait")
    async def scenario():
        app = PeekApp(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert "enter open in noedit.xyz   " in content_text(app, "#footer")
            assert peek.editor_command(tmp_path, "file.txt")[3] == "noedit.xyz --wait"
    asyncio.run(scenario())


@pytest.mark.parametrize("count, expected", [(1, "1 line"), (2, "2 lines")])
def test_preview_line_count_uses_singular_only_for_one_line(count, expected):
    preview = Preview("a.txt", ("content",) * count)
    header = peek.render_preview(preview, width=50, height=5).plain.split("\n")[0]
    assert header.rstrip().endswith(expected)
    assert header.endswith(" ")


@pytest.mark.parametrize("override", ["true", "false", ":", "'true' --unused"])
def test_noop_git_override_yields_core_editor_with_filtered_child_env(repo, monkeypatch, override):
    git(repo, "config", "core.editor", "configured-editor --wait")
    values = {"GIT_EDITOR": override, "VISUAL": "false --unused", "EDITOR": "':'"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    environments = []
    original = peek._run
    def record(argv, **kwargs):
        environments.append(kwargs.get("env"))
        return original(argv, **kwargs)
    monkeypatch.setattr(peek, "_run", record)
    assert peek.editor_command(repo, "a file.txt")[3] == "configured-editor --wait"
    assert len(environments) == 2
    assert environments[0] is None
    assert all(key not in environments[1] for key in values)
    assert {key: os.environ[key] for key in values} == values


def test_noop_git_override_yields_real_visual_editor(repo, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("VISUAL", "nano --wait")
    monkeypatch.setenv("EDITOR", "false")
    assert peek.editor_command(repo, "file.txt")[3] == "nano --wait"


def test_noop_git_answer_after_clean_retry_uses_vi(repo, monkeypatch):
    git(repo, "config", "core.editor", "false --unused")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("VISUAL", ":")
    monkeypatch.setenv("EDITOR", "false")
    assert peek.editor_command(repo, "file.txt")[3] == "vi"


def test_real_git_editor_override_still_wins_over_core_and_visual(repo, monkeypatch):
    git(repo, "config", "core.editor", "configured-editor")
    monkeypatch.setenv("GIT_EDITOR", "real-editor --wait")
    monkeypatch.setenv("VISUAL", "nano")
    assert peek.editor_command(repo, "file.txt")[3] == "real-editor --wait"


@pytest.mark.parametrize("visual, editor, expected", [
    ("true", "nano --wait", "nano --wait"), ("false", ":", "vi"), (":", "", "vi"),
    ("real-editor --wait", "false", "real-editor --wait"),
    ("'true' --unused", "real-editor", "real-editor"), ("trueish", "false", "trueish")])
def test_no_git_fallback_skips_noop_editor_values(tmp_path, monkeypatch, visual, editor, expected):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(peek, "_git", missing)
    monkeypatch.setenv("VISUAL", visual)
    monkeypatch.setenv("EDITOR", editor)
    assert peek.editor_command(tmp_path, "file.txt")[3] == expected


def test_noop_override_footer_and_enter_use_the_same_real_editor(repo, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("VISUAL", "nano --wait")
    commands = []
    original = subprocess.run
    def run(argv, *args, **kwargs):
        if argv[0] == "sh":
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(peek.subprocess, "run", run)
    async def scenario():
        app = PeekApp(repo)
        async with app.run_test(size=(100, 24)) as pilot:
            await settle(app, pilot)
            assert "enter open in nano" in content_text(app, "#footer")
            await pilot.press("enter")
            await settle(app, pilot)
            assert len(commands) == 1
            assert commands[0] == ["sh", "-c", 'nano --wait "$@"', "nano --wait", str(repo / "tracked.txt")]
            assert os.environ["GIT_EDITOR"] == "true"
    asyncio.run(scenario())


def test_malformed_non_noop_env_does_not_block_configured_editor(repo, monkeypatch):
    git(repo, "config", "core.editor", "configured-editor")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("VISUAL", "'unfinished")
    assert peek.editor_command(repo, "file.txt")[3] == "configured-editor"
