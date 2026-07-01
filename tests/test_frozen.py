from mkcrew import frozen


def test_frozen_helpers_default_to_dev_paths():
    """Not frozen (tests, uv install): psmux from PATH, daemon via `python -m`, core-view via the
    mk-core-view.exe, no shims. The frozen branches only fire inside a PyInstaller MKCREW.exe."""
    assert frozen.is_frozen() is False
    assert frozen.psmux_exe() == "psmux"
    assert frozen.daemon_cmd()[-2:] == ["-m", "mkcrew.daemon"]
    cv = frozen.core_view_cmd("P", "h")
    assert cv[0].endswith("mk-core-view.exe") and cv[1] == "P" and cv[-1] == "h"
    assert frozen.core_view_cmd("P")[-1] == "P"          # 'v' default appends no orient token
    assert frozen.shim_bin() == ""


def test_add_workspace_launcher_dispatches_addworkspace(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    launcher = frozen.add_workspace_launcher()
    text = (tmp_path / "mkcrew" / "bin" / "add-workspace.cmd").read_text(encoding="utf-8")
    assert launcher.endswith("add-workspace.cmd")
    assert "mkcrew.addworkspace %*" in text


def test_frozen_add_workspace_launcher_uses_busybox_token(monkeypatch, tmp_path):
    import sys
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\x\MKCREW.exe", raising=False)
    frozen.add_workspace_launcher()
    text = (tmp_path / "mkcrew" / "bin" / "add-workspace.cmd").read_text(encoding="utf-8")
    assert r'@"C:\x\MKCREW.exe" add-workspace %*' in text


def test_frozen_branches_reinvoke_the_single_exe(monkeypatch):
    """When frozen, every internal launch command re-invokes the single MKCREW.exe (sys.executable)
    instead of a separate mk.exe / `python -m` — _run.py's argv dispatch handles the token. This is
    the bug that made the .exe's Launch fail with 'mk.exe is not recognized'."""
    import sys
    from mkcrew import frozen, studio, agent
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\x\MKCREW.exe", raising=False)
    assert frozen.is_frozen() is True
    assert studio._mk_exe() == r"C:\x\MKCREW.exe"                       # `mk` IS the exe
    assert frozen.daemon_cmd() == [r"C:\x\MKCREW.exe", "mkd"]
    assert frozen.core_view_cmd("P", "h") == [r"C:\x\MKCREW.exe", "core-view", "P", "h"]
    assert agent._hook_command()["args"] == ["finish-hook"]            # not "-m mkcrew.finish_hook"
    assert agent._is_mkcrew_finish_hook({"hooks": [{"args": ["finish-hook"]}]})
    assert agent._is_mkcrew_finish_hook({"hooks": [{"args": ["-m", "mkcrew.finish_hook"]}]})
