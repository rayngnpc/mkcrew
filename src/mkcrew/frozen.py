# src/mkcrew/frozen.py
"""One place for 'are we a PyInstaller onefile, and where are the bundled bits'.

A frozen MKCREW.exe is a single binary that acts as the app AND as mk / mkd / core-view / done
(busybox style, dispatched by argv in _run.py). The console scripts + `python -m` don't exist inside
the bundle, so the engine asks here how to re-invoke itself.
"""
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _meipass() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def psmux_exe() -> str:
    """Bundled psmux when frozen (shipped via --add-binary), else 'psmux' from PATH."""
    p = _meipass() / "psmux.exe"
    return str(p) if is_frozen() and p.exists() else "psmux"


def daemon_cmd() -> list:
    """How to spawn the daemon: `MKCREW.exe mkd` when frozen, else `python -m mkcrew.daemon`."""
    return [sys.executable, "mkd"] if is_frozen() else [sys.executable, "-m", "mkcrew.daemon"]


def core_view_cmd(project, orient: str = "v") -> list:
    """The core-view pane command. Frozen -> re-invoke the single exe; dev -> the mk-core-view.exe."""
    if is_frozen():
        cmd = [sys.executable, "core-view", str(project)]
    else:
        cmd = [str(Path(sys.executable).parent / "mk-core-view.exe"), str(project)]
    if orient != "v":
        cmd.append(orient)
    return cmd


def files_view_cmd(project) -> list:
    """The files-view pane command (IDE file explorer). Frozen -> re-invoke the single exe; dev ->
    `python -m mkcrew.filesview` (robust even when the mk-files-view console script isn't regenerated
    on an incremental editable reinstall)."""
    if is_frozen():
        return [sys.executable, "files-view", str(project), "cockpit"]
    return [sys.executable, "-m", "mkcrew.filesview", str(project), "cockpit"]


def add_workspace_cmd() -> list:
    """The in-cockpit add-workspace picker command (run by the Ctrl-b A popup)."""
    if is_frozen():
        return [sys.executable, "add-workspace"]
    return [sys.executable, "-m", "mkcrew.addworkspace"]


def add_workspace_launcher() -> str:
    """Write a single-FILE .cmd launcher for in-cockpit add-workspace commands.

    A psmux key-bind re-tokenizes its command on keypress, so a multi-token command
    (`python -m mkcrew.addworkspace`) collapses to a bare `python` REPL — a ONE-token .cmd path
    survives.  The .cmd holds the real (quoted, backslash) command for cmd.exe."""
    from . import config
    binp = config.runtime_root() / "bin"
    binp.mkdir(parents=True, exist_ok=True)
    launcher = binp / "add-workspace.cmd"
    command = (f'@"{sys.executable}" add-workspace %*\r\n' if is_frozen()
               else f'@"{sys.executable}" -m mkcrew.addworkspace %*\r\n')
    launcher.write_text(
        command,
        encoding="utf-8")
    # BACKSLASH path: the bind runs this and tmux preserves backslashes in a single token; a forward-
    # slash path would break the PowerShell variant. No spaces, so it survives the key-bind re-tokenize.
    return str(launcher)


def hide_console() -> None:
    """Hide THIS process's console window. The frozen exe is a --console build (so the cockpit's
    core/agent panes, which are this same exe re-invoked, can render) — but the APP doesn't want a
    stray cmd window, so it hides its own. No-op unless frozen / no console / off-Windows."""
    if not is_frozen():
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


def shim_bin() -> str:
    """When frozen, write mk.cmd / mk-done.cmd shims (-> the single exe) into a bin dir and return it,
    so agent panes can still call `mk ...` and `mk-done ...`. Empty string when not frozen (the real
    mk.exe / mk-done.exe sit next to the interpreter)."""
    if not is_frozen():
        return ""
    from . import config
    b = config.runtime_root() / "bin"
    b.mkdir(parents=True, exist_ok=True)
    exe = sys.executable
    (b / "mk.cmd").write_text(f'@"{exe}" %*\r\n', encoding="utf-8")
    (b / "mk-done.cmd").write_text(f'@"{exe}" done %*\r\n', encoding="utf-8")
    return str(b)
