import asyncio

from rich.style import Style
from textual import events
from textual.containers import ScrollableContainer
from textual.scrollbar import ScrollBar
from textual.widgets import Button, DirectoryTree, Input, Static, TextArea

from mkcrew import filesview
from mkcrew.filesview import (
    _middle_truncate, _name_error, _preview_text, _read_capped, FilesApp)


def test_read_capped_normal(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print('hi')\nx = 1\n", encoding="utf-8")
    text, truncated = _read_capped(f)
    assert "print('hi')" in text and not truncated


def test_read_capped_truncates_huge(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("\n".join(str(i) for i in range(5000)), encoding="utf-8")
    text, truncated = _read_capped(f, max_lines=100)
    assert truncated and text.count("\n") == 99          # exactly 100 lines kept


def test_read_capped_binary(tmp_path):
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x89PNG\r\n\x00\x00binary\x00stuff")
    text, truncated = _read_capped(f)
    assert "binary file" in text and not truncated


def test_read_capped_missing(tmp_path):
    text, truncated = _read_capped(tmp_path / "nope.txt")
    assert "cannot read" in text and not truncated


def test_cockpit_compensates_mouse_both_coords():
    """on_event shifts a real mouse event up one row (local AND screen) in the cockpit, cancelling
    psmux's status-bar offset; standalone leaves it untouched. Shifting both coords is the fix — a
    prior attempt shifted only the local y, so the screen-offset region check rejected the click.
    (pilot.click bypasses App.on_event, so the real mouse path is exercised via _compensate_mouse.)"""
    def mk():
        return events.MouseDown(widget=None, x=5, y=5, delta_x=0, delta_y=0, button=1,
                                shift=False, meta=False, ctrl=False, screen_x=5, screen_y=5)
    e = mk(); FilesApp("E:/x", cockpit=True)._compensate_mouse(e)
    assert (e.y, e.screen_y) == (4, 4)        # cockpit: shifted up one row, both coords
    e = mk(); FilesApp("E:/x", cockpit=False)._compensate_mouse(e)
    assert (e.y, e.screen_y) == (5, 5)        # standalone: untouched


def test_compensation_skips_the_editor():
    """The row shift applies to the tree/view but NOT the editor — shifting the TextArea sends its
    cursor one line high (the editor doesn't carry the psmux row offset the tree does)."""
    on = FilesApp("E:/x", cockpit=True)
    assert on._should_compensate(object()) is True              # non-editor (tree/view) -> shift
    assert on._should_compensate(TextArea("")) is False         # editor -> no shift
    assert FilesApp("E:/x", cockpit=False)._should_compensate(object()) is False   # standalone -> never


def test_compensation_skips_toolbar_buttons():
    """ROOT CAUSE of the live-cockpit '＋File/＋Folder do nothing': the -1 shift is calibrated for the
    TREE (rows down, where psmux's offset is +1), but the toolbar sits at the very TOP (row 1, under
    the title) where psmux's offset is ~0 — so the blanket -1 over-corrected button clicks UP onto the
    title bar. The fix exempts Button (like the editor): a click already reported on a button is taken
    as-is, never shifted off it."""
    on = FilesApp("E:/x", cockpit=True)
    assert on._should_compensate(Button("＋ File")) is False     # top-row button -> NO shift (the fix)
    assert on._should_compensate(TextArea("")) is False          # editor -> no shift
    assert on._should_compensate(ScrollBar(vertical=False)) is False  # editor H-scrollbar -> NO shift
    assert on._should_compensate(object()) is True               # tree/view -> still shifts


def test_cockpit_does_not_compensate_mouse_move_hover():
    """Hover uses MouseMove. Shifting hover up made the tree cursor highlight the row ABOVE the
    visible pointer; clicks still compensate via MouseDown/MouseUp, but movement must stay literal."""
    e = events.MouseMove(widget=None, x=5, y=5, delta_x=0, delta_y=0, button=0,
                         shift=False, meta=False, ctrl=False, screen_x=5, screen_y=5)
    FilesApp("E:/x", cockpit=True)._compensate_mouse(e)
    assert (e.y, e.screen_y) == (5, 5)


def test_preview_text_uses_left_flush_gutter_without_dots():
    """Preview line numbers are a tight left-flush gutter (`1 │ code`), not Rich's roomy/dotted
    Syntax gutter."""
    body = _preview_text("alpha\nbeta")
    assert body.plain.splitlines() == ["1 │ alpha", "2 │ beta"]
    assert "1." not in body.plain and "2." not in body.plain


def test_name_validation_rejects_bad_file_and_folder_names():
    assert _name_error("", "file") == "Enter a file name."
    assert "spaces" in (_name_error(" bad.md", "file") or "")
    assert "contain" in (_name_error("bad/name.md", "file") or "")
    assert "dot" in (_name_error("bad.", "folder") or "")
    assert "reserved" in (_name_error("CON", "file") or "")
    assert _name_error("test.md", "file") is None
    assert _name_error("worker1_demo", "folder") is None


def test_app_mounts_three_columns(tmp_path):
    """Headless mount of the 3-column IDE: CSS parses, compose runs, and the LEFT core panel,
    CENTER tree, and RIGHT viewer+editor widgets all exist. The core panel renders the SAME frame
    coreview.render_core produces (it always emits 'MKCREW core'), proving it's wired to the real
    view and not a stub."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:   # entering = CSS parsed + compose ran OK
            await pilot.pause()
            assert app.query_one("#core", Static) is not None        # LEFT  — core status
            assert app.query_one("#tree") is not None                # CENTER — file tree
            assert app.query_one("#view", ScrollableContainer) is not None   # RIGHT — viewer
            assert app.query_one("#edit", TextArea) is not None      # RIGHT — editor
            core_text = str(app.query_one("#core", Static).render()).lower()
            assert "core" in core_text                               # render_core frame painted
    asyncio.run(go())


def test_app_mounts_and_opens_a_file(tmp_path):
    """Headless mount: CSS parses, compose runs, and opening a file fills the path bar + viewer."""
    (tmp_path / "hello.py").write_text("def hi():\n    return 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(tmp_path)
        async with app.run_test() as pilot:               # entering = CSS parsed + compose ran OK
            app._open(tmp_path / "hello.py")
            await pilot.pause()
            assert "hello.py" in str(app.query_one("#pathbar", Static).render())
    asyncio.run(go())


def test_edit_and_save_writes_file(tmp_path):
    """e -> edit, Ctrl+S -> save asks first; Y writes the full editor text back to disk."""
    f = tmp_path / "edit_me.py"
    f.write_text("old = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(tmp_path)
        async with app.run_test() as pilot:
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            app.query_one("#edit", TextArea).load_text("new = 2\n")
            app.action_save()
            for _ in range(10):
                await pilot.pause()
                if len(app.screen.query("#save_yes")):
                    break
            assert f.read_text(encoding="utf-8") == "old = 1\n"       # no write until confirmed
            assert app.screen.query_one("#save_yes", Button) is not None  # modal confirmation is visible
            await pilot.press("y")                                     # nano-style yes
            await pilot.pause()
    asyncio.run(go())
    assert f.read_text(encoding="utf-8") == "new = 2\n"


def test_files_core_uses_compact_rendering(tmp_path, monkeypatch):
    """The embedded Files rail must ask coreview for the narrow compact frame, not the wide table."""
    calls = []

    def fake_render_core(*args, **kwargs):
        calls.append(kwargs)
        return "MKCREW core compact"

    monkeypatch.setattr(filesview.coreview, "render_core", fake_render_core)

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert calls
            assert calls[-1]["compact"] is True
    asyncio.run(go())


def test_save_confirmation_no_does_not_write(tmp_path):
    """N on the save prompt cancels the write."""
    f = tmp_path / "edit_me.py"
    f.write_text("old = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(tmp_path)
        async with app.run_test() as pilot:
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            app.query_one("#edit", TextArea).load_text("new = 2\n")
            app.action_save()
            for _ in range(10):
                await pilot.pause()
                if len(app.screen.query("#save_no")):
                    break
            await pilot.press("n")
            await pilot.pause()
    asyncio.run(go())
    assert f.read_text(encoding="utf-8") == "old = 1\n"


def test_binary_is_not_editable(tmp_path):
    """`e` on a binary file is refused (no crash, editor stays hidden)."""
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00\x01\x02 binary")

    async def go():
        app = FilesApp(tmp_path)
        async with app.run_test() as pilot:
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            assert app.query_one("#edit", TextArea).display is False
    asyncio.run(go())


def test_mouse_click_in_tree_opens_file(tmp_path):
    """PROOF a REAL MOUSE CLICK opens a file — not a faked `_open` call. `pilot.click` dispatches
    genuine MouseDown/MouseUp at the screen row of 'hello.py' in the #tree DirectoryTree, so the file
    only opens if the app truly routes the click to the tree's selection handler. After the click the
    app's `_current` points at that file AND the #content viewer holds its source."""
    (tmp_path / "hello.py").write_text("print('hi')\n" * 5, encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(30):                          # wait for the async directory load to settle
                await pilot.pause()
                if tree.root.children and getattr(tree.root.children[0], "line", -1) >= 0:
                    node = tree.root.children[0]
                    break
            assert node is not None and node.data is not None
            assert node.data.path.name == "hello.py"
            assert app._current is None                  # nothing is open before the click

            row = node.line - tree.scroll_offset.y       # the visible #tree row of hello.py
            opened = await pilot.click("#tree", offset=(6, row))   # a real click on that file's row
            for _ in range(5):
                await pilot.pause()

            assert opened
            assert app._current == (tmp_path / "hello.py")         # the click opened THIS file
            static = app.query_one("#content", Static)
            assert "print('hi')" in str(static.render())           # …and its source is in the viewer
    asyncio.run(go())


def test_mouse_scroll_moves_the_view(tmp_path):
    """PROOF a REAL MOUSE WHEEL scrolls the viewer. Open a long file, then post a MouseScrollDown
    event (the wheel-down notch) to the #view ScrollableContainer and assert its scroll_offset.y
    increases — i.e. the content actually moved, it isn't just a no-op."""
    f = tmp_path / "long.py"
    f.write_text("".join(f"row_{i} = {i}\n" for i in range(200)), encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(6):
                await pilot.pause()
            app._open(f)                                 # load the long file into the read-only viewer
            await pilot.pause()
            view = app.query_one("#view", ScrollableContainer)
            assert view.max_scroll_y > 0                 # the file is taller than the viewport
            assert view.scroll_offset.y == 0             # _open scrolls to the top first

            # One real wheel-down notch over the viewer. MouseScrollDown ctor (Textual 8.x):
            # (widget, x, y, delta_x, delta_y, button, shift, meta, ctrl, screen_x, screen_y).
            view.post_message(events.MouseScrollDown(
                widget=view, x=view.region.x + 2, y=view.region.y + 2,
                delta_x=0, delta_y=0, button=0, shift=False, meta=False, ctrl=False,
                screen_x=view.region.x + 2, screen_y=view.region.y + 2))
            await pilot.pause()
            await pilot.wait_for_animation()

            assert view.scroll_offset.y > 0              # the wheel moved the view down
    asyncio.run(go())


# ---------------------------------------------------------------------------
# FIX 2: editor + viewer scroll BOTH axes (H for long lines, V for big files)
# ---------------------------------------------------------------------------

def test_editor_soft_wrap_is_off_for_horizontal_scroll(tmp_path):
    """The edit TextArea has soft_wrap=False so long lines do NOT wrap — they scroll left/right
    instead. (Soft wrap on would hide the right end of long lines with no way to reach it.)"""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            ta = app.query_one("#edit", TextArea)
            assert ta.soft_wrap is False
            assert ta.show_line_numbers is True
    asyncio.run(go())


def test_viewer_and_editor_containers_allow_both_scroll_axes(tmp_path):
    """The read-only viewer (#view) is set to scroll on BOTH axes and its #content sizes to its widest
    line (width:auto, so long lines overflow -> H-scroll); the editor (#edit, a ScrollView) scrolls on
    both axes too. Style-level proof that neither axis is clipped/hidden."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            view = app.query_one("#view", ScrollableContainer)
            assert str(view.styles.overflow_x) in ("scroll", "auto")   # viewer H-scroll allowed
            assert str(view.styles.overflow_y) in ("scroll", "auto")   # viewer V-scroll allowed
            assert str(app.query_one("#content").styles.width) == "auto"  # content grows to longest line
            ta = app.query_one("#edit", TextArea)
            assert str(ta.styles.overflow_x) in ("scroll", "auto")     # editor H-scroll allowed
            assert str(ta.styles.overflow_y) in ("scroll", "auto")     # editor V-scroll allowed
    asyncio.run(go())


def test_viewer_scrolls_a_wide_and_tall_file_both_ways(tmp_path):
    """FUNCTIONAL proof: open a file with LONG lines AND many lines and the read-only viewer gains
    scroll room on BOTH axes (max_scroll_x > 0 = horizontal, max_scroll_y > 0 = vertical) — i.e. long
    lines aren't wrapped/clipped and a big file isn't truncated to the viewport."""
    f = tmp_path / "wide.py"
    f.write_text("\n".join("x = " + "A" * 300 for _ in range(300)), encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(8):
                await pilot.pause()
            app._open(f)
            for _ in range(5):
                await pilot.pause()
            view = app.query_one("#view", ScrollableContainer)
            assert view.max_scroll_x > 0                 # the long lines scroll horizontally
            assert view.max_scroll_y > 0                 # the tall file scrolls vertically
    asyncio.run(go())


def test_editor_scrolls_a_wide_and_tall_file_both_ways(tmp_path):
    """FUNCTIONAL proof for the editor: with a long-line, many-line file loaded, the TextArea has scroll
    room on BOTH axes (soft_wrap off -> long lines extend the virtual width -> H-scroll; big file ->
    V-scroll)."""
    f = tmp_path / "wide.py"
    f.write_text("\n".join("x = " + "A" * 300 for _ in range(300)), encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(8):
                await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()                            # load the full file into the TextArea
            for _ in range(5):
                await pilot.pause()
            ta = app.query_one("#edit", TextArea)
            assert ta.max_scroll_x > 0                   # long lines scroll left/right (soft_wrap off)
            assert ta.max_scroll_y > 0                   # big file scrolls up/down
    asyncio.run(go())


def test_keyboard_horizontal_scroll_moves_viewer_and_editor(tmp_path):
    """Ctrl-Right/Ctrl-Left actions make horizontal scroll reachable in terminals that don't expose
    a horizontal mouse wheel. Works in read-only view and edit mode."""
    f = tmp_path / "wide.py"
    f.write_text("\n".join("x = " + "A" * 300 for _ in range(60)), encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(8):
                await pilot.pause()
            app._open(f)
            for _ in range(5):
                await pilot.pause()
            view = app.query_one("#view", ScrollableContainer)
            assert view.max_scroll_x > 0
            app.action_scroll_right()
            await pilot.pause()
            assert view.scroll_offset.x > 0
            app.action_scroll_left()
            await pilot.pause()
            assert view.scroll_offset.x == 0

            app.action_edit()
            for _ in range(5):
                await pilot.pause()
            ta = app.query_one("#edit", TextArea)
            assert ta.max_scroll_x > 0
            await pilot.press("ctrl+right")                       # priority app binding wins over word-nav
            await pilot.pause()
            assert ta.scroll_offset.x > 0
    asyncio.run(go())


def test_clipboard_fallback_pastes_into_editor_and_name_field(tmp_path, monkeypatch):
    """Ctrl-Shift-V has an explicit OS-clipboard fallback for psmux/terminal sessions where native
    paste is swallowed. It pastes multiline text into the editor and single-line text into the name field."""
    f = tmp_path / "edit.py"
    f.write_text("", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        monkeypatch.setattr(app, "_clipboard_text", lambda: "print('hi')\nprint('bye')\n")
        async with app.run_test(size=(120, 30)) as pilot:
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            app.action_paste_clipboard()
            await pilot.pause()
            assert "print('bye')" in app.query_one("#edit", TextArea).text

            app.action_new_file()
            await pilot.pause()
            monkeypatch.setattr(app, "_clipboard_text", lambda: "pasted\nname.md")
            app.action_paste_clipboard()
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert inp.display is True                                 # the create field is shown …
            assert inp.value == "pasted name.md"                      # … and the paste landed in it
    asyncio.run(go())


def test_editor_undo_and_redo_actions(tmp_path):
    """Ctrl-Z/Ctrl-Y actions drive TextArea's real undo stack."""
    f = tmp_path / "edit.py"
    f.write_text("one", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            ta = app.query_one("#edit", TextArea)
            ta.insert(" two")
            assert ta.text == " twoone"
            app.action_undo()
            assert ta.text == "one"
            app.action_redo()
            assert ta.text == " twoone"
    asyncio.run(go())


# ---------------------------------------------------------------------------
# IDE features: auto-refresh + VSCode-style INLINE new file / new folder (no type picker)
# ---------------------------------------------------------------------------

async def _tree_names(pilot, app, want, tries=40):
    """Pump the event loop until the #tree's root children include `want` (the directory load + a
    reload are async); return the set of child names seen."""
    names: set[str] = set()
    for _ in range(tries):
        await pilot.pause()
        names = {n.data.path.name for n in app.query_one("#tree", DirectoryTree).root.children if n.data}
        if want in names:
            break
    return names


async def _type_create_name(pilot, name: str) -> None:
    await pilot.press(*list(name))


def test_no_type_picker_anywhere(tmp_path):
    """REGRESSION (the user's complaint): creating a file must NOT show a file-TYPE picker. Opening the
    new-file entry reveals the plain #newname Input and NO RadioSet/RadioButton — the old '#newext'
    picker widget is gone for good (the user types the extension into the name, VSCode-style)."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert len(app.query("RadioSet")) == 0               # no type picker ever mounted …
            assert len(app.query("RadioButton")) == 0
            assert len(app.query("#newext")) == 0                # … and the old picker id is gone
            app.action_new_file()                                # open the inline create entry
            await pilot.pause()
            assert len(app.query("RadioSet")) == 0               # still none after opening the entry
            assert len(app.query("#newext")) == 0
            inp = app.query_one("#newname", Input)               # the create field is a plain Input …
            assert inp.display is True                           # … shown once the entry opens
    asyncio.run(go())


def test_inline_create_uses_typed_name_verbatim(tmp_path):
    """VSCode-style: the user types the FULL name (extension included) and it's used verbatim — no
    extension is auto-appended and no type is inferred. 'readme.md' -> readme.md; a bare 'plain' stays
    extensionless 'plain' (the picker that used to append an extension is gone)."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            await _type_create_name(pilot, "readme.md")             # full name, extension typed in
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "readme.md").is_file()             # created exactly as typed
            assert not (tmp_path / "readme").exists()

            app.action_new_file()
            await pilot.pause()
            await _type_create_name(pilot, "plain")                # no extension typed
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "plain").is_file()                 # stays extensionless (nothing appended)
            assert not (tmp_path / "plain.md").exists()
    asyncio.run(go())


def test_create_mode_captures_printable_bound_keys(tmp_path):
    """Printable keys that are normally app shortcuts still type into the draft while naming."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            await _type_create_name(pilot, "query.py")
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "query.py").is_file()

            app.action_new_folder()
            await pilot.pause()
            await _type_create_name(pilot, "child_folder")
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "child_folder").is_dir()
    asyncio.run(go())


def test_new_actions_surfaced_in_footer_bindings():
    """`n`/`f` are bound with Footer labels (so the new create actions show in the Footer)."""
    by_key = {}
    for binding in FilesApp.BINDINGS:
        if isinstance(binding, tuple) and len(binding) == 3:
            key, action, label = binding
            by_key[key] = (action, label)
    assert by_key["n"] == ("new_file", "New file")
    assert by_key["f"] == ("new_folder", "New folder")


def test_action_new_file_creates_appears_and_opens(tmp_path):
    """`n` opens the inline name entry; typing a full filename + Enter creates an empty file in the
    project, the tree reload surfaces it, and it opens in the editor (#current set, #edit shown) — so
    the file 'shows directly' in the explorer, VSCode-style."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()                                # open the inline create entry
            await pilot.pause()
            assert app._new_kind == "file"
            assert app.query_one("#newname", Input).display is True   # the focused name field is shown
            await _type_create_name(pilot, "x.py")                # the user types the full name + ext
            await pilot.press("enter")                           # Enter in the name field creates it
            await pilot.pause()

            target = tmp_path / "x.py"
            assert target.is_file()                              # created (empty) on disk
            assert app._current == target                        # …opened…
            assert app.query_one("#edit", TextArea).display is True   # …in the editor
            assert app._new_kind is None                         # entry dismissed
            assert app.query_one("#newname", Input).display is False   # field hidden after create
            assert "x.py" in await _tree_names(pilot, app, "x.py")   # appears in the tree after reload
    asyncio.run(go())


def test_action_new_folder_creates_directory(tmp_path):
    """`f` opens the same inline name entry (no type picker — folders have no extension); a name +
    Enter creates the directory and the tree reload surfaces it."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_folder()
            await pilot.pause()
            assert app._new_kind == "folder"
            assert app.query_one("#newname", Input).display is True   # the focused name field is shown
            assert len(app.query("#newext")) == 0                # no type picker for a folder (or ever)
            await _type_create_name(pilot, "newpkg")
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "newpkg").is_dir()                # directory created
            assert app._new_kind is None
            assert app.query_one("#newname", Input).display is False
            assert "newpkg" in await _tree_names(pilot, app, "newpkg")
    asyncio.run(go())


def test_new_path_guard_never_overwrites(tmp_path):
    """Guard: creating a name that already exists is refused — the file is left untouched and the
    inline entry stays open (so the user can pick another name)."""
    f = tmp_path / "keep.py"
    f.write_text("ORIGINAL = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            await _type_create_name(pilot, "keep.py")             # collides with the existing file
            await pilot.press("enter")
            await pilot.pause()
            assert app._new_kind == "file"                       # still open (creation refused)
            assert app.query_one("#newname", Input).display is True
    asyncio.run(go())
    assert f.read_text(encoding="utf-8") == "ORIGINAL = 1\n"     # untouched on disk


def test_invalid_create_name_keeps_inline_entry_open_with_error(tmp_path):
    """IDE-like validation: invalid names are rejected in-place; the draft stays open so the user can fix it."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            await _type_create_name(pilot, "bad/name.md")
            await pilot.press("enter")
            await pilot.pause()
            assert app._new_kind == "file"
            assert app.query_one("#newname", Input).display is True
            err = app.query_one("#newerror", Static)
            assert err.display is True
            assert "cannot contain" in str(err.render())
            assert not (tmp_path / "bad" / "name.md").exists()

            app.query_one("#newname", Input).value = ""          # clear the bad name, type a good one
            await _type_create_name(pilot, "fixed.md")
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "fixed.md").is_file()
            assert app.query_one("#newerror", Static).display is False
    asyncio.run(go())


def test_auto_reload_refreshes_tree_without_error(tmp_path):
    """`_auto_reload` (the 3s timer callback) reloads the tree without raising and surfaces a file
    created after mount — i.e. agents' new files appear without pressing `r`."""
    (tmp_path / "first.py").write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            (tmp_path / "second.py").write_text("y = 2\n", encoding="utf-8")  # appears AFTER mount
            app._auto_reload()                                   # the timer callback — must not raise
            assert "second.py" in await _tree_names(pilot, app, "second.py")
    asyncio.run(go())


def test_auto_reload_skips_when_nothing_changed(tmp_path):
    """BUG B fix (primary): an idle tree (no FS change) must NOT reload on the timer tick — a reload
    rebuilds the tree and snaps the cursor back to the project root, which is the user's 'highlight
    flashes to the root every few seconds' bug. _auto_reload snapshots the visible listing and SKIPS
    the reload while it is unchanged; a new file changes the listing, so a reload fires again."""
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await _tree_names(pilot, app, "a.py")                # let the async load settle

            reloads = []
            app._reload_keep_cursor = lambda: reloads.append(1)  # spy on the actual reload
            app._last_tree_sig = None                            # deterministic baseline

            app._auto_reload()                                   # 1st tick: baseline differs -> reload
            assert reloads == [1]
            app._auto_reload()                                   # 2nd tick: UNCHANGED -> skip
            assert reloads == [1]                                # … no extra reload (cursor undisturbed)

            (tmp_path / "b.py").write_text("y\n", encoding="utf-8")  # an agent drops a new file
            app._auto_reload()                                   # listing changed -> reload again
            assert reloads == [1, 1]
    asyncio.run(go())


def test_auto_reload_preserves_highlighted_subfolder(tmp_path):
    """BUG B fix (backstop): when a reload DOES fire (the listing changed), the user's highlighted row
    must SURVIVE it instead of snapping to the project root. Highlight a subfolder, force a reload, and
    the cursor lands back on that subfolder — and the newly created file still appears in the tree."""
    (tmp_path / "worker1_demo").mkdir()

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(40):
                await pilot.pause()
                for child in tree.root.children:
                    if child.data and child.data.path.name == "worker1_demo" and getattr(child, "line", -1) >= 0:
                        node = child
                        break
                if node is not None:
                    break
            assert node is not None
            tree.move_cursor(node)                               # highlight the subfolder
            await pilot.pause()
            assert tree.cursor_node.data.path.name == "worker1_demo"

            (tmp_path / "new_from_agent.py").write_text("z\n", encoding="utf-8")  # forces a real reload
            app._last_tree_sig = None
            app._auto_reload()                                   # listing changed -> reload fires

            restored = None
            for _ in range(40):                                  # wait for reload + cursor restore
                await pilot.pause()
                cn = tree.cursor_node
                name = cn.data.path.name if cn and cn.data else None
                if name == "worker1_demo":
                    restored = name
                    break
            assert restored == "worker1_demo"                    # selection survived the refresh …
            names = {n.data.path.name for n in tree.root.children if n.data}
            assert "new_from_agent.py" in names                  # … and the new file appeared
    asyncio.run(go())


def test_new_file_lands_in_selected_directory(tmp_path):
    """FIX 1 (the user's spec): with a subfolder HIGHLIGHTED, the typed name lands in the focused create
    Input, the target stays that folder, and Enter creates the file INSIDE it (not at the project root)
    and opens it in the editor."""
    (tmp_path / "pkg").mkdir()

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(40):                                  # wait for the async directory load
                await pilot.pause()
                for n in tree.root.children:
                    if n.data and n.data.path.name == "pkg" and getattr(n, "_line", -1) >= 0:
                        node = n
                        break
                if node is not None:
                    break
            assert node is not None
            tree.move_cursor(node)                               # highlight the 'pkg' directory
            await pilot.pause()
            assert app._target_dir() == (tmp_path / "pkg")
            assert app._target_dir_node() is node                # the create will target THIS node

            app.action_new_file()                                # open the focused create field
            await pilot.pause()
            await pilot.press("i", "n", "s", "i", "d", "e", ".", "p", "y") # type into the focused Input
            await pilot.pause()

            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert inp.value == "inside.py"                      # the focused Input holds the typed name
            assert app._target_dir() == (tmp_path / "pkg")       # Enter still creates under the folder

            await pilot.press("enter")                           # Enter creates it INSIDE pkg
            await pilot.pause()
            target = tmp_path / "pkg" / "inside.py"
            assert target.is_file()                              # created inside the selected dir …
            assert not (tmp_path / "inside.py").exists()         # … NOT at the project root
            assert app._current == target                        # … opened …
            assert app.query_one("#edit", TextArea).display is True   # … in the editor
            assert app.query_one("#newname", Input).display is False   # field hidden after the create
    asyncio.run(go())


def test_selected_folder_shows_no_inline_create_icons(tmp_path):
    """BUG A fix: highlighting a folder must NOT sprout per-directory ＋File/＋Folder icons next to its
    row (they rendered inconsistently — ＋File showed, ＋Folder didn't). The ONLY create affordance is
    the #treebar toolbar, so the rendered row label is just the folder's own glyph + name — no ＋."""
    (tmp_path / "worker1_demo").mkdir()

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(40):
                await pilot.pause()
                for child in tree.root.children:
                    if child.data and child.data.path.name == "worker1_demo":
                        node = child
                        break
                if node is not None:
                    break
            assert node is not None
            tree.move_cursor(node)                                # highlight the folder
            await pilot.pause()

            label = tree.render_label(node, Style(), Style()).plain
            assert "＋" not in label                              # no inline create affordance (was '＋📄  ＋📁') …
            assert "📄" not in label                             # … no stray file glyph appended to a folder row …
            assert "worker1_demo" in label                       # … just the folder row itself
            # the ONLY create affordance is the toolbar (still present)
            assert app.query_one("#newfilebtn", Button) is not None
            assert app.query_one("#newfolderbtn", Button) is not None
    asyncio.run(go())


def test_mouse_clicked_folder_then_new_file_creates_inside_that_folder(tmp_path):
    """Real click path: click a folder row, press n, type a name, Enter creates inside that folder."""
    (tmp_path / "worker1_demo").mkdir()

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(40):
                await pilot.pause()
                for child in tree.root.children:
                    if child.data and child.data.path.name == "worker1_demo" and getattr(child, "line", -1) >= 0:
                        node = child
                        break
                if node is not None:
                    break
            assert node is not None

            row = node.line - tree.scroll_offset.y
            clicked = await pilot.click("#tree", offset=(6, row))
            await pilot.pause()
            assert clicked
            assert app._target_dir() == tmp_path / "worker1_demo"

            await pilot.press("n")
            await pilot.press("c", "l", "i", "c", "k", "e", "d", ".", "p", "y")
            await pilot.press("enter")
            await pilot.pause()

            target = tmp_path / "worker1_demo" / "clicked.py"
            assert target.is_file()
            assert not (tmp_path / "clicked.py").exists()
            assert app._current == target
    asyncio.run(go())


def test_clicking_folder_row_only_highlights_never_starts_create(tmp_path):
    """BUG A fix: the per-row ＋ hit-zones are gone. Clicking a folder row — even at its far right edge
    where the old ＋icons sat — only HIGHLIGHTS the folder (so the toolbar/`n`/`f` act on it); it must
    NOT open an inline create entry. Creating is the toolbar's / keybinds' job, never a row click."""
    (tmp_path / "worker1_demo").mkdir()

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            tree = app.query_one("#tree", DirectoryTree)
            node = None
            for _ in range(40):
                await pilot.pause()
                for child in tree.root.children:
                    if child.data and child.data.path.name == "worker1_demo" and getattr(child, "line", -1) >= 0:
                        node = child
                        break
                if node is not None:
                    break
            assert node is not None
            y = node.line - tree.scroll_offset.y
            right_edge_x = max(0, tree.content_size.width - 1)   # the OLD ＋icon hit-zone, now plain

            clicked = await pilot.click("#tree", offset=(right_edge_x, y))
            await pilot.pause()
            assert clicked
            assert app._target_dir() == tmp_path / "worker1_demo"  # the click highlighted the folder …
            assert app._new_kind is None                           # … but did NOT open a create entry
            assert app.query_one("#newname", Input).display is False
    asyncio.run(go())


def test_inline_create_targets_project_root_when_nothing_special_selected(tmp_path):
    """At the project root (default highlight), the create targets the project ROOT — i.e. with no
    subfolder highlighted, Enter creates the entry directly in the project."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_folder()
            await pilot.pause()
            await pilot.press("t", "o", "p", "d", "i", "r")
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert inp.value == "topdir"                         # the typed name is in the focused field
            assert app._target_dir() == tmp_path                 # … and it targets the project root
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "topdir").is_dir()                # created at the project root
    asyncio.run(go())


def test_inline_create_field_uses_icon_free_placeholder(tmp_path):
    """The create field is a plain Input whose placeholder is the icon-free hint ('name.ext' /
    'folder name'). (The old draft-tree-node doubled the 📄/📁 glyph; the Input carries no icon.)"""
    assert "📄" not in filesview._NEW_FILE_PLACEHOLDER     # constants are icon-free …
    assert "📁" not in filesview._NEW_FOLDER_PLACEHOLDER

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert inp.placeholder == filesview._NEW_FILE_PLACEHOLDER   # "name.ext" hint, no icon
            assert "📄" not in inp.placeholder

            app.action_view()                              # cancel
            await pilot.pause()
            app.action_new_folder()
            await pilot.pause()
            assert app.query_one("#newname", Input).placeholder == filesview._NEW_FOLDER_PLACEHOLDER
    asyncio.run(go())


def test_ctrl_t_refocuses_tree_from_editor(tmp_path):
    """Bug: after opening/editing a file the keyboard dead-ended (Tab is swallowed by the editor as a
    literal tab, so the tree couldn't be refocused and the arrow keys died). Ctrl-T refocuses the tree
    from anywhere, restoring arrow navigation."""
    from textual.binding import Binding
    keys = [b.key if isinstance(b, Binding) else b[0] for b in FilesApp.BINDINGS]
    assert "ctrl+t" in keys                                # the refocus key is bound

    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            assert isinstance(app.focused, TextArea)               # focus is trapped in the editor
            app.action_focus_tree()                                # Ctrl-T
            await pilot.pause()
            assert app.focused is app.query_one("#tree", DirectoryTree)   # …back on the tree
    asyncio.run(go())


def test_escape_returns_focus_to_tree(tmp_path):
    """Esc from the editor also returns focus to the tree (so arrows work again), not just cancels."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            app.action_view()                                      # the Esc binding
            await pilot.pause()
            assert app.focused is app.query_one("#tree", DirectoryTree)
    asyncio.run(go())


def test_escape_cancels_new_entry(tmp_path):
    """Esc dismisses the inline create entry (keyboard-first) without creating anything."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            assert app.query_one("#newname", Input).display is True
            await _type_create_name(pilot, "ghost.py")
            await pilot.press("escape")                           # Esc while the Input is focused -> cancel
            await pilot.pause()
            assert app._new_kind is None
            assert app.query_one("#newname", Input).display is False
            assert not (tmp_path / "ghost.py").exists()          # nothing was created
    asyncio.run(go())


# ---------------------------------------------------------------------------
# IDE features: clickable create BUTTONS + the current-working-directory display
# ---------------------------------------------------------------------------

def test_middle_truncate_keeps_start_and_end():
    """The pure path helper: a short path is unchanged; a long one is elided in the MIDDLE (keeping
    the drive/root at the start AND the leaf at the end) to exactly `width` columns."""
    assert _middle_truncate("short/path", 40) == "short/path"          # fits -> untouched
    long = "C:/Users/me/AppData/Local/Temp/deeply/nested/project/src"
    out = _middle_truncate(long, 24)
    assert len(out) == 24 and "…" in out
    assert out.startswith("C:/") and out.endswith("src")              # start + end both kept


def test_create_buttons_exist_and_are_focusable(tmp_path):
    """The toolbar exposes a '＋ File' and a '＋ Folder' Button (keybind-only creation is gone); both
    are real, keyboard-focusable Buttons sitting above the tree."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            fbtn = app.query_one("#newfilebtn", Button)
            dbtn = app.query_one("#newfolderbtn", Button)
            assert fbtn is not None and dbtn is not None
            assert fbtn.can_focus and dbtn.can_focus                  # tab-reachable
    asyncio.run(go())


def test_new_file_button_press_opens_inline_entry(tmp_path):
    """on_button_pressed for '＋ File' runs the SAME flow as the `n` keybind — the inline name entry
    shows in FILE mode (no type picker), proving the button triggers the create flow."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.on_button_pressed(Button.Pressed(app.query_one("#newfilebtn", Button)))
            await pilot.pause()
            assert app._new_kind == "file"
            assert app.query_one("#newname", Input).display is True
            assert len(app.query("#newext")) == 0                # no type picker, ever
    asyncio.run(go())


def test_new_folder_button_press_opens_inline_entry(tmp_path):
    """on_button_pressed for '＋ Folder' opens the inline name entry in folder mode (no type picker —
    folders have no extension)."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.on_button_pressed(Button.Pressed(app.query_one("#newfolderbtn", Button)))
            await pilot.pause()
            assert app._new_kind == "folder"
            assert app.query_one("#newname", Input).display is True
            assert len(app.query("#newext")) == 0
    asyncio.run(go())


def test_new_file_button_click_opens_inline_entry(tmp_path):
    """PROOF a REAL MOUSE CLICK on the '＋ File' button opens the inline name entry — not a faked call.
    `pilot.click` dispatches a genuine click at the button's region, so the entry only shows if the
    button truly routes its press to action_new_file."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._new_kind is None                             # nothing open before the click
            clicked = await pilot.click("#newfilebtn")
            await pilot.pause()
            assert clicked
            assert app._new_kind == "file"                           # the click opened the create flow
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert app.focused is inp                                # the click MOVED FOCUS onto the field
    asyncio.run(go())


def test_keypress_n_opens_new_file_inline_entry(tmp_path):
    """DEPENDABLE PATH: pressing `n` (real key event, tree focused on mount) opens the inline name
    entry in FILE mode (no type picker) — the reliable route the live-cockpit mouse can't always hit."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._new_kind is None                             # nothing open before the keypress
            await pilot.press("n")                                   # genuine key event, not action_*()
            await pilot.pause()
            assert app._new_kind == "file"                           # `n` opened the create flow
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert app.focused is inp                                # `n` opened AND focused the field
            assert len(app.query("#newext")) == 0                    # no type picker shown
    asyncio.run(go())


def test_keypress_f_opens_new_folder_inline_entry(tmp_path):
    """DEPENDABLE PATH: pressing `f` (real key event) opens the inline name entry in FOLDER mode (no
    type picker — folders have no extension). Keyboard is the route guaranteed to work."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._new_kind is None
            await pilot.press("f")
            await pilot.pause()
            assert app._new_kind == "folder"                         # `f` opened the create flow
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert app.focused is inp                                # `f` opened AND focused the field
            assert len(app.query("#newext")) == 0                    # folder -> no type picker
    asyncio.run(go())


def test_button_click_create_is_focusable_typable_and_repeatable(tmp_path):
    """LIVE-BUG REGRESSION (the user's report): clicking ＋File must open a name field that is FOCUSED
    and typable, create the file on Enter, and work AGAIN for a SECOND file. The old draft-tree-node
    relied on keystrokes bubbling to an app-level handler, which failed in the live pane (the placeholder
    showed but nothing could be typed). With a real focused Input the typed value lands in the field."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # First file via a REAL mouse click on the toolbar button
            assert await pilot.click("#newfilebtn")
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert app.focused is inp                            # the click focused the field (the fix)
            await pilot.press(*list("one.py"))                   # keys land in the focused Input …
            assert inp.value == "one.py"                         # … the name is genuinely typed
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "one.py").is_file()               # created on Enter
            assert app._new_kind is None

            # SECOND file, also via the button — the create must be repeatable immediately
            assert await pilot.click("#newfilebtn")
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert inp.display is True
            assert app.focused is inp
            await pilot.press(*list("two.py"))
            assert inp.value == "two.py"
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "two.py").is_file()               # the second file is created too
    asyncio.run(go())


def test_button_create_from_editor_focuses_field_not_textarea(tmp_path):
    """ROOT-CAUSE REGRESSION: with a file OPEN IN THE EDITOR (TextArea focused), clicking ＋File moves
    focus onto the name field — so the typed name lands in the field, NOT as text inside the open file.
    (The old approach let a focused TextArea swallow the keystrokes, so the name could not be typed.)"""
    f = tmp_path / "open.py"
    f.write_text("EXISTING\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()                                    # focus the editor (TextArea)
            await pilot.pause()
            assert isinstance(app.focused, TextArea)
            assert await pilot.click("#newfilebtn")              # start a create from the editor
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert app.focused is inp                            # focus moved OFF the TextArea onto the field
            await pilot.press(*list("made.py"))
            assert inp.value == "made.py"                        # typed into the field …
            assert "made" not in app.query_one("#edit", TextArea).text   # … NOT into the open file
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "made.py").is_file()              # and the new file is created
    asyncio.run(go())


def test_header_shows_project_full_path(tmp_path):
    """The header (#title) shows the project root's FULL path — not just its name — so the user always
    sees which workspace this is. Uses a tmp project dir and asserts its path string appears."""
    proj = tmp_path / "myproj"
    proj.mkdir()

    async def go():
        app = FilesApp(str(proj))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            title = str(app.query_one("#title", Static).render())
            assert str(app.project) in title                         # full resolved path in the header
            assert "myproj" in title
    asyncio.run(go())


def test_cwd_line_shows_current_directory(tmp_path):
    """The '#cwd' line shows WHERE THE USER IS — defaulting to the project root, and following the
    selected file's parent once a file is opened. The leaf folder survives middle-truncation."""
    proj = tmp_path / "wsroot"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(proj))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            cwd = str(app.query_one("#cwd", Static).render())
            assert "wsroot" in cwd                                   # defaults to the project root
            app._open(proj / "a.py")
            await pilot.pause()
            cwd2 = str(app.query_one("#cwd", Static).render())
            assert "wsroot" in cwd2                                  # parent dir of the opened file
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Collapsible panels (VSCode-style): z maximizes the editor, [ core, ] explorer + buttons
# ---------------------------------------------------------------------------

def test_z_maximizes_editor_hiding_core_and_tree_then_restores(tmp_path):
    """PRIMARY feature: `z` collapses BOTH side columns so the editor column (#right, width:1fr) goes
    FULL-WIDTH; pressing `z` again restores all three. Proven by the display flags AND by #right's
    width growing to fill the freed space, then returning."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            core = app.query_one("#core"); treecol = app.query_one("#treecol")
            right = app.query_one("#right")
            assert core.display and treecol.display               # all three shown initially
            w_before = right.size.width
            await pilot.press("z")                                # maximize the editor
            for _ in range(4):
                await pilot.pause()
            assert not core.display and not treecol.display       # both side columns collapsed …
            assert right.display                                  # … editor column stays …
            assert right.size.width > w_before                    # … and widened to fill the freed space
            await pilot.press("z")                                # restore
            for _ in range(4):
                await pilot.pause()
            assert core.display and treecol.display               # all three back
            assert right.size.width == w_before                   # editor back to its shared width
    asyncio.run(go())


def test_bracket_left_toggles_core_column_only(tmp_path):
    """`[` hides/shows the LEFT #core column without touching the explorer; #right flexes to fill."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            core = app.query_one("#core"); treecol = app.query_one("#treecol")
            right = app.query_one("#right")
            w_before = right.size.width
            await pilot.press("[")                                # collapse core only
            for _ in range(3):
                await pilot.pause()
            assert not core.display                               # core gone …
            assert treecol.display                                # … explorer untouched …
            assert right.size.width > w_before                    # … editor flexed wider
            await pilot.press("[")                                # restore core
            for _ in range(3):
                await pilot.pause()
            assert core.display and treecol.display
    asyncio.run(go())


def test_bracket_right_toggles_explorer_column_only(tmp_path):
    """`]` hides/shows the CENTER explorer (#treecol) column without touching the core column."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            core = app.query_one("#core"); treecol = app.query_one("#treecol")
            await pilot.press("]")                                # collapse explorer only
            for _ in range(3):
                await pilot.pause()
            assert not treecol.display and core.display           # explorer gone, core untouched
            await pilot.press("]")                                # restore explorer
            for _ in range(3):
                await pilot.pause()
            assert treecol.display and core.display
    asyncio.run(go())


def test_collapse_toggles_inert_while_editing(tmp_path):
    """The toggles must NOT fire while the editor is focused — `z` types a literal 'z' into the file
    instead of collapsing (so the user can type z/[/] as text). Proven with a real key press."""
    f = tmp_path / "a.py"
    f.write_text("hello\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            ta = app.query_one("#edit", TextArea)
            assert isinstance(app.focused, TextArea)
            core = app.query_one("#core")
            await pilot.press("z")                                # z while editing …
            await pilot.pause()
            assert core.display                                   # … does NOT collapse the core column …
            assert "z" in ta.text                                 # … it types 'z' into the editor
    asyncio.run(go())


def test_collapse_toggles_inert_while_naming_new_file(tmp_path):
    """The toggles are also inert while the inline new-file name field is focused — `]` types a literal
    ']' into the name instead of collapsing the explorer (']' is a legal filename character)."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_new_file()
            await pilot.pause()
            inp = app.query_one("#newname", Input)
            assert app.focused is inp
            treecol = app.query_one("#treecol")
            await pilot.press("]")                                # ] while naming …
            await pilot.pause()
            assert treecol.display                                # … does NOT collapse the explorer …
            assert "]" in inp.value                               # … it types ']' into the name field
    asyncio.run(go())


def test_control_bar_buttons_toggle_panels(tmp_path):
    """The always-visible control strip in #right mirrors the keys: 'max' collapses/restores BOTH side
    columns, 'core' toggles the core column, 'files' toggles the explorer — so a mouse user gets the
    same collapse/reopen the keyboard offers."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            core = app.query_one("#core"); treecol = app.query_one("#treecol")
            press = lambda i: app.on_button_pressed(Button.Pressed(app.query_one(i, Button)))

            press("#max_toggle")                                  # maximize -> both hidden
            await pilot.pause()
            assert not core.display and not treecol.display
            press("#max_toggle")                                  # restore -> both shown
            await pilot.pause()
            assert core.display and treecol.display

            press("#core_toggle")                                 # core button -> core only
            await pilot.pause()
            assert not core.display and treecol.display
            press("#core_toggle")
            await pilot.pause()

            press("#tree_toggle")                                 # files button -> explorer only
            await pilot.pause()
            assert core.display and not treecol.display
            press("#tree_toggle")
            await pilot.pause()
            assert core.display and treecol.display
    asyncio.run(go())


def test_max_button_real_click_maximizes(tmp_path):
    """PROOF a REAL MOUSE CLICK on the 'max' control collapses both side columns — it routes through
    on_button_pressed, it isn't a faked call."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            core = app.query_one("#core"); treecol = app.query_one("#treecol")
            clicked = await pilot.click("#max_toggle")
            for _ in range(4):
                await pilot.pause()
            assert clicked
            assert not core.display and not treecol.display        # the click maximized the editor
    asyncio.run(go())


def test_explorer_close_button_collapses_explorer(tmp_path):
    """The explorer's in-toolbar '‹' handle collapses the #treecol column (same as ] / the files
    button); it vanishes with the column and the user reopens from the always-visible control strip."""
    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            treecol = app.query_one("#treecol")
            assert treecol.display
            app.on_button_pressed(Button.Pressed(app.query_one("#tree_close", Button)))
            await pilot.pause()
            assert not treecol.display                            # collapsed by the in-column handle
    asyncio.run(go())


def test_toggle_button_works_while_editing(tmp_path):
    """A deliberate button CLICK toggles a panel even while the editor is focused — the typing guard
    applies only to the z/[/] KEYS (so those type into the editor); a click is explicit intent."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")

    async def go():
        app = FilesApp(str(tmp_path))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._open(f)
            await pilot.pause()
            app.action_edit()
            await pilot.pause()
            assert isinstance(app.focused, TextArea)              # editor focused (a KEY would be inert)
            core = app.query_one("#core")
            app.on_button_pressed(Button.Pressed(app.query_one("#core_toggle", Button)))
            await pilot.pause()
            assert not core.display                               # the click collapsed core despite editing
    asyncio.run(go())


def test_collapse_toggles_surfaced_in_footer_bindings():
    """z/[/] are bound with Footer descriptions so the collapse controls are discoverable; the bracket
    keys carry a key_display so the Footer shows '['/']' rather than the verbose key name."""
    from textual.binding import Binding
    by_action = {}
    for b in FilesApp.BINDINGS:
        key = b.key if isinstance(b, Binding) else b[0]
        action = b.action if isinstance(b, Binding) else b[1]
        desc = b.description if isinstance(b, Binding) else b[2]
        by_action[action] = (key, desc)
    assert {"toggle_maximize", "toggle_core", "toggle_tree"} <= set(by_action)
    assert by_action["toggle_maximize"][0] == "z"                 # z maximizes the editor
    assert by_action["toggle_core"][0] == "left_square_bracket"   # [ toggles the core column
    assert by_action["toggle_tree"][0] == "right_square_bracket"  # ] toggles the explorer column
    assert all(desc for _, desc in by_action.values())           # each surfaces a Footer label
    kd = {b.action: b.key_display for b in FilesApp.BINDINGS
          if isinstance(b, Binding) and b.action in ("toggle_core", "toggle_tree")}
    assert kd["toggle_core"] == "[" and kd["toggle_tree"] == "]"  # Footer shows the glyphs
