import asyncio
import re
from pathlib import Path
from unittest.mock import patch

from textual.widgets import (Button, ContentSwitcher, DirectoryTree, Input, Label, RadioButton,
                             RadioSet)

from mkcrew import templates
from mkcrew.addworkspace import (AddWorkspaceApp, _DirOnlyTree, _MODELS, _OVERWRITE_MSG, _PROVIDERS,
                                 _align_rows, _build_add_command, _is_existing_setup,
                                 _list_workspaces, _menu_run_main, _open_command, _parse_workspaces,
                                 _popup_main, _template_preview, _truncate_middle,
                                 menu_command)


# ---------------------------------------------------------------------------
# Pure command builder (_build_add_command) — per-agent providers/models
# ---------------------------------------------------------------------------

def test_build_add_command_two_agents_exact_argv():
    """(a) 2 agents claude+codex, models opus+gpt-5.5, PER-AGENT efforts high+medium -> exact argv."""
    cmd = _build_add_command("F", "ws", 2, ["claude", "codex"],
                             ["claude-opus-4-8", "gpt-5.5"], ["high", "medium"], "tiled")
    assert cmd == ["add", "F", "--agents", "2",
                   "--providers", "claude,codex",
                   "--models", "claude-opus-4-8,gpt-5.5",
                   "--efforts", "high,medium",
                   "--template", "tiled", "--name", "ws"]


def test_build_add_command_all_default_models_omits_flag():
    """(b) 3 agents, every model AND effort blank -> NO --models / --efforts (providers still join)."""
    cmd = _build_add_command("F", "", 3, ["claude", "claude", "codex"],
                             ["", "", ""], ["", "", ""], "hub")
    assert "--models" not in cmd
    assert "--efforts" not in cmd
    assert cmd[cmd.index("--providers") + 1] == "claude,claude,codex"
    assert cmd[cmd.index("--agents") + 1] == "3"
    assert "--name" not in cmd


def test_build_add_command_padded_empty_model_and_effort():
    """(c) a blank slot among non-blank ones -> padded comma lists keep models AND efforts aligned."""
    cmd = _build_add_command("F", "", 3, ["claude", "codex", "codex"],
                             ["claude-opus-4-8", "", "gpt-5.5"], ["max", "", "xhigh"], "tiled")
    assert cmd[cmd.index("--models") + 1] == "claude-opus-4-8,,gpt-5.5"
    assert cmd[cmd.index("--efforts") + 1] == "max,,xhigh"   # per-agent effort, empty slot aligned


def test_build_add_command_plain_and_template_both_template_flag():
    """(d) a template key and a raw 'plain' layout key both surface as --template <key>."""
    tmpl = _build_add_command("F", "", 1, ["claude"], [""], ["high"], "main-vertical")
    plain = _build_add_command("F", "", 1, ["claude"], [""], ["high"], "even-vertical")
    assert tmpl[tmpl.index("--template") + 1] == "main-vertical"
    assert plain[plain.index("--template") + 1] == "even-vertical"


# ---------------------------------------------------------------------------
# Textual wizard — headless mount + keyboard-only pilot
# ---------------------------------------------------------------------------

def test_wizard_mounts_and_builds_default_command(tmp_path):
    """Headless mount (CSS parses, all four steps compose) + the default selections build the right
    `mk add` argv: 1 claude agent, default model (no --models), high effort, the first template."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert app.current_step == 0
            cmd = app._build_cmd()
            assert cmd[0] == "add"
            assert cmd[1] == str(tmp_path)
            assert cmd[cmd.index("--agents") + 1] == "1"
            assert cmd[cmd.index("--providers") + 1] == "claude"
            assert cmd[cmd.index("--efforts") + 1] == "high"
            assert cmd[cmd.index("--template") + 1] == "main-vertical"
            assert cmd[cmd.index("--models") + 1] == "claude-opus-4-8"   # concrete default model
            assert "--name" not in cmd                   # empty name -> no flag (auto)
            app.query_one("#name", Input).value = "myws"
            assert app._build_cmd()[-2:] == ["--name", "myws"]
    asyncio.run(go())


def test_wizard_keyboard_flow_builds_per_agent_command(tmp_path):
    """Drive the whole wizard by KEYBOARD, then CLICK the Create button: pick 2 agents, set Agent 1's
    CLI to Codex (proving its model RadioSet repopulates to GPT-5.5 and its effort radio repopulates
    to Codex's levels incl. 'xhigh'), choose a plain layout, advance to Confirm, and `pilot.click` the
    Create button — then assert the EXACT per-agent argv the app built and launched."""
    captured = {}

    async def go():
        with patch("mkcrew.addworkspace.subprocess.Popen",
                   lambda *a, **k: captured.setdefault("argv", a[0])):
            app = AddWorkspaceApp(start_dir=str(tmp_path))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                # Step 0 -> 1: Enter in the focused folder field advances.
                await pilot.press("enter")
                await pilot.pause()
                assert app.current_step == 1

                # Count -> 2 (focus the radioset, arrow to highlight, Enter to commit).
                app.query_one("#count", RadioSet).focus()
                await pilot.pause()
                await pilot.press("down", "enter")
                await pilot.pause()
                assert app._count() == 2
                assert not app.query_one("#agent1").has_class("hidden")   # 2nd block now visible
                assert app.query_one("#agent2").has_class("hidden")       # 3rd still hidden

                # Agent 1's CLI -> Codex; model + effort radios must repopulate to Codex's roster.
                app.query_one("#cli0", RadioSet).focus()
                await pilot.press("down", "enter")
                await pilot.pause()
                model_labels = [str(b.label) for b in app.query_one("#model0", RadioSet).query(RadioButton)]
                assert model_labels == ["GPT-5.5", "GPT-5.4", "GPT-5.4 mini"]   # Codex roster (item #4)
                effort_labels = [str(b.label) for b in app.query_one("#effort0", RadioSet).query(RadioButton)]
                assert "xhigh" in effort_labels                           # effort came from GPT-5.5's list

                # Step 1 -> 2 (ctrl+right is an app binding; the focused RadioSet ignores it).
                await pilot.press("ctrl+right")
                await pilot.pause()
                assert app.current_step == 2

                # Layout step: keep the default template (Normal group, first option = main-vertical).
                assert app.query_one("#tmpl_normal", RadioSet).pressed_index == 0
                assert app._layout() == "main-vertical"

                # Step 2 -> 3, then CREATE via a REAL mouse click on the button.
                await pilot.press("ctrl+right")
                await pilot.pause()
                assert app.current_step == 3
                await pilot.click("#create")
                await pilot.pause()
            captured["pending"] = app._pending_cmd

    asyncio.run(go())

    expected = ["add", str(tmp_path), "--agents", "2",
                "--providers", "codex,claude",                 # agent0 = codex, agent1 = claude default
                "--models", "gpt-5.5,claude-opus-4-8",         # agent0 = GPT-5.5, agent1 = Claude Opus 4.8
                "--efforts", "high,high",                      # per-agent: codex 'high' + claude 'high'
                "--template", "main-vertical"]                 # default template (Normal group, index 0)
    assert captured["pending"] == expected         # the argv the app built from the keyboard+click path
    assert captured["argv"][1:] == expected        # ...and what it actually launched (argv[0] = mk exe)


def test_wizard_create_rejects_nonexistent_folder(tmp_path):
    """BUG-3: clicking Create with a typo'd / non-existent folder must NOT build the argv and launch a
    detached `mk add` (the backend exits with 'not a directory' off-screen, so the popup just closed with
    ZERO feedback).  Instead the wizard surfaces an inline 'Folder not found' error, launches nothing, and
    STAYS open on the Folder step so the path can be fixed.  (The valid-folder Create path above, which
    DOES launch, must keep working — proving the guard only blocks missing folders.)"""
    captured = {}
    notes = []

    async def go():
        with patch("mkcrew.addworkspace.subprocess.Popen",
                   lambda *a, **k: captured.setdefault("argv", a[0])):
            app = AddWorkspaceApp(str(tmp_path))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.notify = lambda message, **kw: notes.append((str(message), kw))  # capture inline feedback
                missing = str(tmp_path / "nope_typo_dir")          # a real-looking but NON-existent path
                app.query_one("#folder", Input).value = missing
                app.current_step = 3                               # jump to the Confirm step
                await pilot.pause()
                await pilot.click("#create")                       # trigger Create on the bad path
                await pilot.pause()
                # (1) launched NOTHING — no detached `mk add` fired off-screen into the void
                assert "argv" not in captured
                assert app._pending_cmd is None
                # (2) an inline ERROR was surfaced naming the missing folder (not a blank close)
                assert any("Folder not found" in m and missing in m for m, _kw in notes)
                assert any(kw.get("severity") == "error" for _m, kw in notes)
                # (3) the app did NOT exit — still running, bounced back to the Folder step to fix it
                assert app.is_running
                assert app.current_step == 0
                assert app.query_one("#steps", ContentSwitcher).current == "step0"
    asyncio.run(go())


def test_wizard_responds_to_mouse_click(tmp_path):
    """PROOF the wizard answers a REAL MOUSE, not only the keyboard. `pilot.click` dispatches genuine
    MouseDown/MouseUp through the app's event system, so these two passing clicks prove the app reacts
    to the mouse:
      (1) clicking the #next button advances the wizard a step (the ContentSwitcher's current view
          flips step0 -> step1), and
      (2) clicking the agents-count "2" RadioButton flips the #count RadioSet's pressed selection.
    Both targets are exercised at the cramped 80x24 size. With the nav bar now docked to the card's
    bottom (Bug A), #next stays on-screen on every step; the count radios sit at the top of step 1 —
    so both click targets are fully on-screen."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            switcher = app.query_one("#steps", ContentSwitcher)
            assert switcher.current == "step0"                  # wizard starts on the Folder step

            # (1) a real mouse click on Next advances the wizard one step (mouse navigation, not keys).
            await pilot.click("#next")
            await pilot.pause()
            assert app.current_step == 1 and switcher.current == "step1"   # the click advanced the step

            # (2) a real mouse click on the agents-count "2" radio selects it (default is "1").
            count = app.query_one("#count", RadioSet)
            assert count.pressed_index == 0 and app._count() == 1          # default = 1 agent
            await pilot.click(list(count.query(RadioButton))[1])           # click the "2" RadioButton
            await pilot.pause()
            assert count.pressed_index == 1 and app._count() == 2          # the click moved it to "2"
    asyncio.run(go())


def test_wizard_opencode_switches_model_picker_to_select(tmp_path):
    """Requirement 3: when an agent's CLI is OpenCode the model picker becomes a Textual Select
    (dropdown), and OpenCode routes carry no thinking level -> that agent's effort radio goes empty
    and hidden, and no --efforts slot is emitted for it."""
    from textual.widgets import Select

    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.current_step = 1                                            # jump to the agents step
            await pilot.pause()
            assert isinstance(app.query_one("#model0", RadioSet), RadioSet)  # Claude default = radio
            # Agent 0 CLI -> OpenCode (claude, codex, OpenCode => two downs from the top).
            app.query_one("#cli0", RadioSet).focus()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert app._agent_cli(0) == "opencode"
            assert isinstance(app.query_one("#model0"), Select)             # picker is now a dropdown
            assert app.query_one("#effort0", RadioSet).has_class("hidden")  # OpenCode -> no thinking level
            cmd = app._build_cmd()
            assert cmd[cmd.index("--providers") + 1] == "opencode"
            assert "--efforts" not in cmd                                   # blank effort slot omitted
    asyncio.run(go())


def test_wizard_antigravity_effort_offered_and_folds_into_model(tmp_path):
    """agy effort is HONEST end-to-end (no silently-dropped control): picking Antigravity still SHOWS a
    thinking picker (it is NOT removed/hidden for a Gemini model), its level is emitted in --efforts,
    and that base model + effort FOLD (agent._agy_model_with_thinking) into the real agy --model
    "(Level)" variant — so the picked effort actually reaches the launch."""
    from mkcrew import agent

    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.current_step = 1                                            # the agents step
            await pilot.pause()
            # Agent 0 CLI -> Antigravity (claude, codex, opencode, antigravity => three downs).
            app.query_one("#cli0", RadioSet).focus()
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            assert app._agent_cli(0) == "antigravity"
            # the thinking picker is PRESENT (not hidden) for the default Gemini model — effort NOT removed
            assert not app.query_one("#effort0", RadioSet).has_class("hidden")
            assert app._agent_model(0) == "Gemini 3.5 Flash"               # base name (no suffix yet)
            assert app._agent_effort(0) == "high"                          # defaults to high
            cmd = app._build_cmd()
            assert cmd[cmd.index("--providers") + 1] == "antigravity"
            assert cmd[cmd.index("--models") + 1] == "Gemini 3.5 Flash"     # base model passed through
            assert cmd[cmd.index("--efforts") + 1] == "high"               # effort PERSISTED, not dropped
            # end-to-end: the persisted base-model + effort fold into the real agy --model value
            launched = agent._agent_command_line("antigravity", "Gemini 3.5 Flash",
                                                 "bypassPermissions", "high", "w", str(tmp_path))
            assert '--model "Gemini 3.5 Flash (High)"' in launched
    asyncio.run(go())


def test_wizard_antigravity_builds_valid_command_for_agy_agent(tmp_path):
    """A pure-builder check that an antigravity agent still produces a VALID `mk add` argv: provider
    antigravity, the base model in --models, and the picked thinking level in --efforts (the wizard
    offers + persists effort for agy; the launch folds it into the model name)."""
    cmd = _build_add_command(str(tmp_path), "ws", 1, ["antigravity"],
                             ["Gemini 3.5 Flash"], ["high"], "main-vertical")
    assert cmd[cmd.index("--providers") + 1] == "antigravity"
    assert cmd[cmd.index("--models") + 1] == "Gemini 3.5 Flash"
    assert cmd[cmd.index("--efforts") + 1] == "high"
    assert cmd[cmd.index("--template") + 1] == "main-vertical"


# ---------------------------------------------------------------------------
# Round-2 bugs: A (docked nav always visible), B (overwrite text), C (Browse picker)
# ---------------------------------------------------------------------------

def _on_screen(region, size) -> bool:
    """True iff a widget's region is laid out and fully inside the viewport."""
    return (region.area > 0 and region.x >= 0 and region.y >= 0
            and region.right <= size.width and region.bottom <= size.height)


def test_wizard_layout_step_docked_nav_button_visible_and_advances(tmp_path):
    """Bug A: at the LAYOUT step (3/4), even at a cramped 80x24, the docked nav keeps a WORKING Next
    button fully on-screen; clicking it advances to Confirm, where Create is likewise on-screen."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.current_step = 2                                  # jump to the Layout step (shown "3/4")
            await pilot.pause()
            assert app.query_one("#steps", ContentSwitcher).current == "step2"
            nxt = app.query_one("#next", Button)
            assert nxt.display                                    # Next shows on steps 0-2
            assert _on_screen(nxt.region, app.size)               # ...and is fully on-screen at 80x24
            await pilot.click("#next")                            # the visible Next advances to Confirm
            await pilot.pause()
            assert app.current_step == 3
            create = app.query_one("#create", Button)
            assert create.display and not app.query_one("#next", Button).display
            assert _on_screen(create.region, app.size)            # Create reachable on the Confirm step
    asyncio.run(go())


def test_wizard_overwrite_prompt_text_is_clean(tmp_path):
    """Bug B: the existing-setup overwrite prompt is one clean sentence containing 'Overwrite' (the old
    wording wrapped mid-word in the narrow card and read as the garbled 'overi')."""
    assert "Overwrite" in _OVERWRITE_MSG

    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            msg = str(app.query_one("#ow_msg", Label).content)    # the mounted prompt label's text
            assert "Overwrite" in msg
            assert "MKCREW workspace" in msg
    asyncio.run(go())


def test_wizard_browse_navigates_into_folder_then_confirm_picks(tmp_path):
    """FIX 1: 'Browse' on the Folder step opens a DirectoryTree picker.  Selecting a folder node
    NAVIGATES into it (re-roots the tree) and must NOT set the working dir; the currently-browsed dir
    is shown so the user knows what Confirm picks.  Only the 'Use this folder' button fills #folder and
    dismisses back to the Folder step."""
    sub = tmp_path / "picked_dir"
    sub.mkdir()

    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert app.query_one("#browse_btn", Button)           # Browse sits beside the folder Input
            await pilot.click("#browse_btn")                      # open the folder picker
            await pilot.pause()
            switcher = app.query_one("#steps", ContentSwitcher)
            assert switcher.current == "browse"
            tree = app.query_one("#dirtree", DirectoryTree)       # the picker IS a DirectoryTree
            folder_before = app.query_one("#folder", Input).value

            # selecting a directory node DESCENDS into it (re-roots) — it must NOT pick/dismiss.
            tree.post_message(DirectoryTree.DirectorySelected(tree.root, sub))
            for _ in range(10):                                   # let the bubbled handler + reactive settle
                await pilot.pause()
                if Path(str(tree.path)) == sub:
                    break
            assert Path(str(tree.path)) == sub                    # navigated INTO the folder
            assert app.query_one("#folder", Input).value == folder_before   # working dir UNCHANGED
            assert switcher.current == "browse"                   # still browsing (not dismissed)
            assert app._overlay == "browse"
            assert str(sub) in str(app.query_one("#br_current", Label).content)  # Confirm target visible

            # NOW confirm: 'Use this folder' picks the currently-browsed dir + returns to Folder step.
            btn = app.query_one("#br_use", Button)
            # The Confirm button must be HIT-TESTABLE: its center has to resolve to the button itself,
            # not fall through to the Screen (the bug was #br_use overflowing below the visible card,
            # so the docked-nav fix must keep it on-screen).  We click for real, never bypass it.
            hit, _region = app.screen.get_widget_at(*btn.region.center)
            assert hit is btn                                     # center resolves to the button, not Screen
            await pilot.click("#br_use")                          # REAL click on the Confirm button
            for _ in range(10):
                await pilot.pause()
                if switcher.current == "step0":
                    break
            assert app.query_one("#folder", Input).value == str(sub)   # the click SET the working dir
            assert switcher.current == "step0"                    # back on the Folder step
            assert app._overlay is None
    asyncio.run(go())


def test_wizard_browse_can_reroot_to_parent_and_drives(tmp_path):
    """Item #3: the Browse picker is NOT locked to the cwd subtree — it can climb UP to a parent, be
    re-rooted at any typed path, and offers drive/disk roots, so any folder on any disk is reachable."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)

    async def go():
        app = AddWorkspaceApp(str(deep))
        async with app.run_test(size=(95, 32)) as pilot:
            await pilot.pause()
            await pilot.click("#browse_btn")
            await pilot.pause()
            tree = app.query_one("#dirtree", DirectoryTree)
            assert Path(str(tree.path)) == deep                   # opens at the start folder
            # climb UP via the wired button -> reaches a directory ABOVE the cwd subtree
            await pilot.click("#br_up")
            await pilot.pause()
            assert Path(str(tree.path)) == tmp_path / "a"
            # re-root at any OTHER path (here the grandparent) -> proves it isn't cwd-locked
            app._browse_to(str(tmp_path))
            await pilot.pause()
            assert Path(str(tree.path)) == tmp_path
            assert Path(app.query_one("#br_path", Input).value) == tmp_path   # path box mirrors root
            # drive/disk jump targets are offered (Windows letters; '/' on POSIX) and never empty
            assert app._drive_list
            assert len(app.query("#br_drives Button")) == len(app._drive_list)
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Item #6: Confirm step renders a clean, aligned key->value config report
# ---------------------------------------------------------------------------

def test_align_rows_pads_labels_into_columns():
    """The alignment helper left-pads labels to a common width so every value starts in the SAME
    column (the tidy apt/Debian-style key->value report the Confirm step wants)."""
    rows = [("Folder", "E:/proj"), ("Agents", "2"), ("Agent 1", "Claude . high")]
    lines = _align_rows(rows).splitlines()
    assert len(lines) == len(rows)
    width = max(len(k) for k, _v in rows)                 # longest label -> shared value column
    val_col = width + 2                                   # label.ljust(width) + two-space gutter
    for (label, value), line in zip(rows, lines):
        assert line.startswith(label)                     # label present at the start of the row
        assert line[val_col:] == value                    # value begins in the shared column
        assert line[len(label):val_col].strip() == ""     # only padding sits between label and value


def test_wizard_summary_step_is_aligned_key_value_report(tmp_path):
    """Item #6: the Confirm step (4/4) renders an ALIGNED key->value report — Folder, Name, Agents, a
    per-agent CLI/model/effort line, and Template — with values sharing a column (not one cramped row)."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.query_one("#folder", Input).value = "E:/proj"   # short -> shown verbatim (not truncated)
            app.query_one("#name", Input).value = "myws"
            app.current_step = 3                          # jump to the Confirm step
            await pilot.pause()
            text = str(app.query_one("#summary", Label).content)
            for label in ("Folder", "Name", "Agents", "Agent 1", "Template"):
                assert label in text                      # every key label is present
            assert "myws" in text                         # the typed workspace name (a value)
            assert app._folder_value() in text            # the chosen folder path (a value)
            assert "Claude Opus 4.8" in text              # the default agent's model label (a value)
            assert "LEAD LEFT" in text or "main-vertical" in text   # the template (value: label or key)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            assert len(lines) >= 5                         # multi-line report, not a single cramped row
            # the single-word-label rows all start their value in one shared column (alignment proof)
            cols = {re.match(r"\S+\s+", ln).end()
                    for ln in lines if re.match(r"(Folder|Name|Agents|Template)\s", ln)}
            assert len(cols) == 1
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Backend: cmd_add per-agent comma lists + single-flag back-compat
# ---------------------------------------------------------------------------

def test_cmd_add_launches_team_in_layout(monkeypatch, tmp_path):
    """Back-compat: `mk add <dir> --agents N --provider P --template L` (single flags) still spawns N
    agents (prefixed roles, provider P) + core + files, and applies layout L."""
    from mkcrew import cli
    calls = []

    class FakeMux:
        def new_window(self, session, window, command, cwd=None):
            calls.append(("new_window", window, cwd, command)); return "%0"
        def split_window(self, target, command, vertical=True, size=None):
            calls.append(("split", command)); return "%n"
        def set_pane_title(self, pid, title): pass
        def select_layout(self, target, layout="tiled"):
            calls.append(("layout", layout))
        def window_size(self, target): return (250, 60)   # cmd_add reads size to build the layout string

    monkeypatch.setattr(cli, "PsmuxBackend", lambda: FakeMux())
    monkeypatch.setattr(cli, "_session_exists", lambda mux, session: True)
    monkeypatch.setattr(cli.layouts, "_launch",
                        lambda a, proj: ["AGENT", a["role"], a.get("provider"), a.get("effort"), a.get("model")])
    cli.cmd_add([str(tmp_path), "--agents", "3", "--provider", "codex",
                     "--template", "tiled", "--effort", "max", "--model", "gpt-5.5"])
    nw = next(c for c in calls if c[0] == "new_window")
    assert nw[1] == tmp_path.name and nw[2] == str(tmp_path)            # window = folder, cwd set
    assert nw[3] == ["AGENT", f"{tmp_path.name}.main", "codex", "max", "gpt-5.5"]
    agent_splits = [c for c in calls if c[0] == "split" and c[1][0] == "AGENT"]
    assert len(agent_splits) == 2                                       # worker1 + worker2 (3 agents total)
    assert all(s[1][2] == "codex" and s[1][3] == "max" and s[1][4] == "gpt-5.5" for s in agent_splits)
    assert all(s[1][1].startswith(f"{tmp_path.name}.") for s in agent_splits)  # roles workspace-prefixed
    assert sum(1 for c in calls if c[0] == "split") == 3               # 2 more agents + files (no core pane)
    assert ("layout", "tiled") in calls                                # chosen layout applied


def test_cmd_add_per_agent_providers_models(monkeypatch, tmp_path):
    """`mk add --providers claude,codex --models a,b --agents 2` yields a 2-agent team whose agents
    carry providers claude/codex and models a/b respectively (the wizard's comma-list path)."""
    from mkcrew import cli, teamconfig
    captured = {}
    real_build = teamconfig.build_team

    def spy(count, providers=None, models=None, efforts=None):
        team = real_build(count, providers=providers, models=models, efforts=efforts)
        captured.update(count=count, providers=providers, models=models, team=team)
        return team

    class FakeMux:
        def new_window(self, session, window, command, cwd=None): return "%0"
        def split_window(self, target, command, vertical=True, size=None): return "%n"
        def set_pane_title(self, pid, title): pass
        def select_layout(self, target, layout="tiled"): pass
        def window_size(self, target): return (250, 60)   # default template is main-vertical (item #9) -> needs size

    monkeypatch.setattr(teamconfig, "build_team", spy)
    monkeypatch.setattr(cli, "PsmuxBackend", lambda: FakeMux())
    monkeypatch.setattr(cli, "_session_exists", lambda mux, session: True)
    monkeypatch.setattr(cli.layouts, "_launch", lambda a, proj: ["AGENT", a["role"]])

    cli.cmd_add([str(tmp_path), "--agents", "2",
                     "--providers", "claude,codex", "--models", "a,b"])

    assert captured["count"] == 2
    assert captured["providers"] == ["claude", "codex"]
    assert captured["models"] == ["a", "b"]
    team = captured["team"]
    assert [t["provider"] for t in team] == ["claude", "codex"]
    assert [t.get("model") for t in team] == ["a", "b"]


# ---------------------------------------------------------------------------
# Constants / native-menu / raw-popup paths (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_addworkspace_picker_removes_gemini_and_uses_template_labels():
    assert "gemini" not in _PROVIDERS
    assert any(value.startswith("opencode-go/") for value, _label, _efforts in _MODELS["opencode"])
    assert not any("variant" in label.lower() for _value, label, _efforts in _MODELS["opencode"])
    # Requirement 1: FULL model labels (not bare "Opus"/"Sonnet").
    claude_labels = [label for _v, label, _ef in _MODELS["claude"]]
    assert "Claude Opus 4.8" in claude_labels and "Claude Sonnet 4.6" in claude_labels
    # Requirement 2: efforts are per-MODEL and differ (Opus has 'max', Sonnet does not).
    by_id = {v: ef for v, _l, ef in _MODELS["claude"]}
    assert "max" in by_id["claude-opus-4-8"] and "max" not in by_id["claude-sonnet-4-6"]
    assert _MODELS["codex"][0][2][-1] == "xhigh"                       # GPT-5.5 tops out at 'xhigh'


def test_template_picker_groups_registry_normal_and_experimental():
    """The Layout step groups the FROZEN templates registry: 'Normal' = add-capable core layouts
    (main-vertical / even-horizontal), 'Experimental' = the files-IDE layout (lead-left-ide).  No
    second 'Plain' mode, and the non-add-capable registry entry (pages) is NOT offered."""
    from mkcrew.addworkspace import _NORMAL_TEMPLATES, _EXPERIMENTAL_TEMPLATES, _DEFAULT_TEMPLATE_KEY
    assert [t.key for t in _NORMAL_TEMPLATES] == ["main-vertical", "even-horizontal"]   # two, in order
    assert [t.key for t in _EXPERIMENTAL_TEMPLATES] == ["lead-left-ide"]                # one experimental
    assert _DEFAULT_TEMPLATE_KEY == "main-vertical"                    # default = first Normal option
    offered = {t.key for t in _NORMAL_TEMPLATES + _EXPERIMENTAL_TEMPLATES}
    assert offered == {t.key for t in templates.wizard_templates()}   # exactly the add-capable set
    assert "pages" not in offered                                     # non-add-capable NOT offered


def test_codex_roster_adds_gpt54_and_mini_with_effort_lists():
    """Item #4: Codex offers GPT-5.5, GPT-5.4, and GPT-5.4 mini.  5.5 + 5.4 expose 'xhigh'; the mini
    variant caps at 'high' (low/medium/high only)."""
    labels = [label for _v, label, _ef in _MODELS["codex"]]
    assert labels == ["GPT-5.5", "GPT-5.4", "GPT-5.4 mini"]
    by_id = {v: ef for v, _l, ef in _MODELS["codex"]}
    assert by_id["gpt-5.5"] == ["low", "medium", "high", "xhigh"]
    assert by_id["gpt-5.4"] == ["low", "medium", "high", "xhigh"]
    assert by_id["gpt-5.4-mini"] == ["low", "medium", "high"]          # mini: no 'xhigh'
    assert "xhigh" not in by_id["gpt-5.4-mini"]


def test_native_menu_command_uses_prompt_and_run_shell():
    cmd = menu_command("add-workspace.cmd")
    assert cmd[0] == "display-menu"
    assert " MKCREW add workspace " in cmd
    assert "display-popup" not in cmd
    joined = "\n".join(cmd)
    assert "command-prompt" in joined
    assert "run-shell" in joined
    assert "--menu-run" in joined
    assert "gemini" not in joined.lower()


def test_menu_run_main_executes_mk_add(monkeypatch, tmp_path):
    seen = {}

    monkeypatch.setattr("mkcrew.addworkspace._mk_exe", lambda: "mk.exe")
    monkeypatch.setattr("mkcrew.addworkspace._display_message", lambda text: seen.setdefault("message", text))

    def fake_run(cmd, capture_output, encoding=None, errors=None):
        seen["cmd"] = cmd
        class Result:
            returncode = 0
            stdout = "added workspace 'demo'"
            stderr = ""
        return Result()

    monkeypatch.setattr("mkcrew.addworkspace.subprocess.run", fake_run)

    rc = _menu_run_main(["--provider", "opencode", "--agents", "2", "--template", "tiled",
                         "--model", "opencode-go/minimax-m3", "--folder", str(tmp_path)])
    assert rc == 0
    # The single menu provider/model fans out across the agents into the per-agent comma lists.
    assert seen["cmd"] == ["mk.exe", "add", str(tmp_path), "--agents", "2",
                           "--providers", "opencode,opencode",
                           "--models", "opencode-go/minimax-m3,opencode-go/minimax-m3",
                           "--template", "tiled"]
    assert "added workspace" in seen["message"]


def test_popup_flow_runs_mk_add(monkeypatch, tmp_path):
    seen = {}

    monkeypatch.setattr("mkcrew.addworkspace._run_wizard",
                        lambda start_dir=None: (str(tmp_path), "demo", "2", "opencode", "",
                                                "tiled", "opencode-go/minimax-m3"))
    monkeypatch.setattr("mkcrew.addworkspace._select", lambda *a, **k: "yes")
    monkeypatch.setattr("mkcrew.addworkspace._mk_exe", lambda: "mk.exe")

    def fake_run(cmd, capture_output, encoding, errors):
        seen["cmd"] = cmd
        class Result:
            returncode = 0
            stdout = "added workspace 'demo'"
            stderr = ""
        return Result()

    monkeypatch.setattr("mkcrew.addworkspace.subprocess.run", fake_run)
    monkeypatch.setattr("mkcrew.addworkspace.time.sleep", lambda _n: None)

    assert _popup_main() == 0
    cmd = seen["cmd"]
    assert cmd[:3] == ["mk.exe", "add", str(tmp_path)]
    assert cmd[cmd.index("--agents") + 1] == "2"
    assert cmd[cmd.index("--providers") + 1] == "opencode,opencode"
    assert "--effort" not in cmd
    assert cmd[cmd.index("--models") + 1] == "opencode-go/minimax-m3,opencode-go/minimax-m3"
    assert cmd[cmd.index("--template") + 1] == "tiled"
    assert cmd[cmd.index("--name") + 1] == "demo"


# ---------------------------------------------------------------------------
# Open-existing flow: detection, overwrite (--force) / `mk open`, `mk workspaces`
# ---------------------------------------------------------------------------

def test_is_existing_setup_detects_team_config(tmp_path):
    """Detection helper (direct FS check): True only when <folder>/.mkcrew/team.config exists."""
    assert _is_existing_setup(tmp_path) is False                 # bare dir
    assert _is_existing_setup(tmp_path / "missing") is False     # nonexistent dir
    cfg = tmp_path / ".mkcrew"
    cfg.mkdir()
    (cfg / "team.config").write_text("{}")
    assert _is_existing_setup(tmp_path) is True                  # now configured


def test_open_command_builds_open_argv(tmp_path):
    """Pure helper: the overwrite-No / list-select path builds ['open', <folder>]."""
    assert _open_command(str(tmp_path)) == ["open", str(tmp_path)]


def test_build_add_command_force_appends_flag():
    """Overwrite-Yes: the create argv appends --force; absent it by default."""
    forced = _build_add_command("F", "ws", 1, ["claude"], ["claude-opus-4-8"], ["high"],
                                "tiled", force=True)
    assert "--force" in forced
    assert _build_add_command("F", "ws", 1, ["claude"], ["claude-opus-4-8"], ["high"],
                              "tiled") == forced[:-1]            # only difference is the trailing flag


def test_parse_workspaces_json_lines_and_empty():
    """`mk workspaces` parsing: JSON array, tab/pipe lines, and empty -> [] (never raises)."""
    js = '[{"name": "alpha", "path": "C:/a"}, {"name": "beta", "path": "C:/b"}]'
    assert _parse_workspaces(js) == [{"name": "alpha", "path": "C:/a"},
                                     {"name": "beta", "path": "C:/b"}]
    lines = "alpha\tC:/a\nbeta|C:/b\n"
    assert _parse_workspaces(lines) == [{"name": "alpha", "path": "C:/a"},
                                        {"name": "beta", "path": "C:/b"}]
    assert _parse_workspaces("") == []
    assert _parse_workspaces("   \n\n  ") == []


def test_list_workspaces_missing_or_empty_is_graceful(monkeypatch):
    """A missing / empty `mk workspaces` yields [] (the wizard shows 'none', never crashes)."""
    monkeypatch.setattr("mkcrew.addworkspace._mk_exe", lambda: "mk.exe")

    class Empty:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr("mkcrew.addworkspace.subprocess.run", lambda *a, **k: Empty())
    assert _list_workspaces() == []                              # empty output

    def boom(*a, **k):
        raise FileNotFoundError("mk not built yet")
    monkeypatch.setattr("mkcrew.addworkspace.subprocess.run", boom)
    assert _list_workspaces() == []                              # missing command -> graceful empty


def test_wizard_overwrite_prompt_yes_adds_force(tmp_path):
    """Pilot: entering an already-set-up folder shows the overwrite prompt (NOT step 1); clicking Yes
    continues the wizard and the create argv now carries --force."""
    cfg = tmp_path / ".mkcrew"
    cfg.mkdir()
    (cfg / "team.config").write_text("{}")

    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert app.current_step == 0
            await pilot.press("enter")                           # advance from Folder -> detection
            await pilot.pause()
            switcher = app.query_one("#steps", ContentSwitcher)
            assert switcher.current == "overwrite"               # prompt shown instead of advancing
            assert app.current_step == 0
            await pilot.click("#ow_yes")                         # Yes -> continue, with --force
            await pilot.pause()
            assert app.current_step == 1
            assert app._force is True
            assert "--force" in app._build_cmd()
    asyncio.run(go())


def test_wizard_overwrite_prompt_no_opens_existing(tmp_path):
    """Pilot: in the overwrite prompt, clicking No fires `mk open <folder>` and exits (no re-setup)."""
    cfg = tmp_path / ".mkcrew"
    cfg.mkdir()
    (cfg / "team.config").write_text("{}")
    captured = {}

    async def go():
        with patch("mkcrew.addworkspace.subprocess.Popen",
                   lambda *a, **k: captured.setdefault("argv", a[0])):
            app = AddWorkspaceApp(str(tmp_path))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert app.query_one("#steps", ContentSwitcher).current == "overwrite"
                await pilot.click("#ow_no")                      # No -> mk open <folder>, exit
                await pilot.pause()
            captured["pending"] = app._pending_cmd

    asyncio.run(go())
    assert captured["argv"][1:] == ["open", str(tmp_path)]       # launched argv (argv[0] = mk exe)
    assert captured["pending"] == ["open", str(tmp_path)]


def test_wizard_open_existing_lists_and_opens(tmp_path):
    """Pilot: the 'Open existing' entry lists `mk workspaces`; selecting one fires `mk open <path>`."""
    captured = {}

    async def go():
        with patch("mkcrew.addworkspace._list_workspaces",
                   lambda: [{"name": "alpha", "path": "C:/a"}, {"name": "beta", "path": "C:/b"}]), \
             patch("mkcrew.addworkspace.subprocess.Popen",
                   lambda *a, **k: captured.setdefault("argv", a[0])):
            app = AddWorkspaceApp(str(tmp_path))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.click("#open_existing")              # B: open the existing-workspace list
                await pilot.pause()
                assert app.query_one("#steps", ContentSwitcher).current == "existing"
                assert app._workspaces[0]["path"] == "C:/a"
                await pilot.click("#ws_open")                    # default = first -> mk open C:/a
                await pilot.pause()

    asyncio.run(go())
    assert captured["argv"][1:] == ["open", "C:/a"]


def test_wizard_open_existing_empty_is_graceful(tmp_path):
    """Pilot: an empty `mk workspaces` shows 'no existing workspaces found' with Open disabled, and
    does NOT crash."""
    async def go():
        with patch("mkcrew.addworkspace._list_workspaces", lambda: []):
            app = AddWorkspaceApp(str(tmp_path))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.click("#open_existing")
                await pilot.pause()
                assert app.query_one("#steps", ContentSwitcher).current == "existing"
                assert app._workspaces == []
                assert app.query_one("#ws_open", Button).disabled is True
                # empty-state placeholder is a Label, not a populated RadioSet -> nothing to select
                assert isinstance(app.query_one("#ws_list"), Label)
                assert app._selected_workspace() is None
    asyncio.run(go())


# ---------------------------------------------------------------------------
# Wizard polish (observe-pass): radio spacing, path truncation, browse hint /
# dirs-only filter, step-1 footer Next hint, layout-step template preview
# ---------------------------------------------------------------------------

def test_truncate_middle_keeps_drive_root_and_leaf():
    """Item #2: long values are MIDDLE-truncated — drive root (head) + leaf (tail) kept, ellipsis
    between — and fit the budget; short values pass through unchanged."""
    long = "E:\\proj\\aaaa\\bbbb\\cccc\\dddd\\eeee\\MKCREW"
    out = _truncate_middle(long, 22)
    assert len(out) <= 22
    assert "…" in out                                    # an explicit ellipsis (not a hard border cut)
    assert out.startswith("E:")                          # drive root kept
    assert out.endswith("MKCREW")                        # leaf kept
    assert _truncate_middle("E:\\proj", 40) == "E:\\proj"   # short -> untouched
    assert _truncate_middle("", 10) == ""                # degenerate -> safe


def test_wizard_summary_middle_truncates_long_folder(tmp_path):
    """Item #2: a long folder path in the Review is middle-truncated (ellipsis, keeping drive root +
    leaf) instead of being cut dead at the card border with no ellipsis."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            longp = "C:/proj/" + "/".join(f"deep{i}" for i in range(12)) + "/MKCREW_LEAF"
            app.query_one("#folder", Input).value = longp
            app.current_step = 3
            await pilot.pause()
            text = str(app.query_one("#summary", Label).content)
            assert "…" in text                            # truncated WITH an ellipsis
            assert "MKCREW_LEAF" in text                  # leaf kept
            assert "C:" in text                           # drive root kept
            assert longp not in text                      # the over-long path is NOT shown verbatim
    asyncio.run(go())


def test_browse_tree_filters_to_directories_only(tmp_path):
    """Item #4: the Browse folder picker (a DirectoryTree subclass) lists DIRECTORIES only — files are
    filtered out of the tree so it's a clean folder picker."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".gitignore").write_text("y")
    tree = _DirOnlyTree(str(tmp_path))
    kept = list(tree.filter_paths([tmp_path / "sub", tmp_path / "a.txt", tmp_path / ".gitignore"]))
    assert sorted(p.name for p in kept) == ["sub"]        # only the directory survives


def test_wizard_browse_uses_dirs_only_tree_and_short_hint(tmp_path):
    """Items #3 + #4: opening Browse mounts a folders-only tree (_DirOnlyTree) and a SHORT hint that
    fits the picker width (the old hint clipped at the box edge as '…arrows mov')."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            await pilot.click("#browse_btn")
            for _ in range(5):                            # let the tree's path-watch reload settle
                await pilot.pause()
            assert isinstance(app.query_one("#dirtree", DirectoryTree), _DirOnlyTree)   # folders-only
            hint = str(app.query_one("#br_hint", Label).content)
            assert len(hint) <= 64                        # short enough for the ~70-col picker
            assert "Esc" in hint                          # still advertises cancel
    asyncio.run(go())


def test_wizard_step1_footer_shows_next_affordance(tmp_path):
    """Item #5: on the Folder step the focused folder Input must NOT shadow the nav — the app's PRIORITY
    bindings keep 'next' (and 'back') in the active footer bindings, so the Next affordance shows instead
    of the user having to guess to press Enter."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert app.current_step == 0
            app.query_one("#folder", Input).focus()       # the Folder step's default focus (an Input)
            await pilot.pause()
            actions = {ab.binding.action for ab in app.screen.active_bindings.values()}
            assert "next" in actions                      # Next hint present in the footer on step 1
            assert "back" in actions                      # ...and Back, like steps 2-4
    asyncio.run(go())


def test_wizard_model_and_effort_radios_are_one_per_line(tmp_path):
    """Item #1: the per-agent model + effort radiosets (longest, most-uneven labels) lay out VERTICALLY —
    one option per line — so options never collide/clip; the short agent-count radios stay on one row and
    are content-sized (not stretched to 1fr with big trailing gaps)."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.current_step = 1
            await pilot.pause()
            for rid in ("#model0", "#effort0"):
                rs = app.query_one(rid, RadioSet)
                ys = [b.region.y for b in rs.query(RadioButton)]
                assert len(set(ys)) == len(ys)            # each option on its own line (vertical stack)
            count = app.query_one("#count", RadioSet)
            cys = [b.region.y for b in count.query(RadioButton)]
            cws = [b.region.width for b in count.query(RadioButton)]
            assert len(set(cys)) == 1                      # agent-count options share one row (horizontal)
            assert max(cws) <= 8                           # content-sized "1".."4" (not stretched ~16-wide)


def test_template_preview_surfaces_sketch_and_description():
    """Item #6: the layout-step preview surfaces BOTH the registry description AND a tiny ASCII sketch,
    for EVERY offered template — main-vertical, even-horizontal AND the experimental lead-left-ide."""
    offered = [t.key for t in templates.wizard_templates()]
    assert "lead-left-ide" in offered                     # the experimental option is covered too
    for t in templates.wizard_templates():
        prev = _template_preview(t.key)
        assert t.desc in prev                             # the registry description is surfaced
        assert any(ch in prev for ch in "┌│└─")           # plus a small ASCII sketch (non-empty)
    assert _template_preview("nonsense-key") == ""        # unknown key -> empty (no crash)


def test_wizard_layout_step_shows_and_updates_template_preview(tmp_path):
    """Item #6 / Bug B: the Layout step renders the selected template's preview, and switching the
    selection — WITHIN the Normal group AND across to the Experimental group — updates the shared
    preview.  The experimental option must produce a NON-EMPTY preview (the old empty-preview bug)."""
    async def go():
        app = AddWorkspaceApp(str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.current_step = 2
            await pilot.pause()
            prev = str(app.query_one("#template_preview", Label).content)
            assert "Lead top-left" in prev                # default (main-vertical) registry description
            # switch WITHIN the Normal group -> even-horizontal
            app.query_one("#tmpl_normal", RadioSet).focus()
            await pilot.press("down", "enter")
            await pilot.pause()
            prev2 = str(app.query_one("#template_preview", Label).content)
            assert "side-by-side columns" in prev2        # even-horizontal registry description
            assert app._layout() == "even-horizontal"
            # switch ACROSS to the Experimental group -> lead-left-ide (preview must be NON-EMPTY)
            app.query_one("#tmpl_experimental", RadioSet).focus()
            await pilot.press("enter")                    # cursor starts on the single option
            await pilot.pause()
            prev3 = str(app.query_one("#template_preview", Label).content)
            assert prev3.strip()                          # NON-empty preview (empty-preview bug fixed)
            assert "files IDE" in prev3                   # lead-left-ide registry description
            assert app._layout() == "lead-left-ide"
            # the pick is global-single: choosing Experimental cleared the Normal group
            assert app.query_one("#tmpl_normal", RadioSet).pressed_index == -1
    asyncio.run(go())
