# src/mkcrew/psmux.py
import os, subprocess, time

# Wait briefly after typing a line before pressing Enter, so the Enter lands as a deliberate SUBMIT
# and not a newline swallowed by the same input burst as the text. Direct Codex doorbell delivery now
# uses this path, so keep it short; raise it only if submits start getting swallowed again.
_SUBMIT_SETTLE = 0.35

# Codex is a little more finicky on wake: give the paste burst more time to settle, then press Enter
# twice with a short gap so a swallowed first Enter still leaves a second chance to submit.
_WAKE_SETTLE = 1.5
_WAKE_ENTER_GAP = 0.15

class PsmuxBackend:
    def __init__(self, exe: str = None):
        from . import frozen
        self.exe = exe or frozen.psmux_exe()   # bundled psmux.exe when frozen, else 'psmux' on PATH
        # MK_PSMUX_SOCKET=<name>: target an ISOLATED psmux server (`psmux -L <name> ...`) instead of
        # the default one — lets tests / dev cockpits run without ever touching the user's live
        # cockpit. Unset (the default) leaves every invocation byte-identical to before.
        self.socket = os.environ.get("MK_PSMUX_SOCKET") or None

    def _base(self) -> list:
        return [self.exe, "-L", self.socket] if self.socket else [self.exe]

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([*self._base(), *args], capture_output=True, encoding="utf-8", errors="replace")

    def kill_server(self) -> None:
        self._run("kill-server")

    @staticmethod
    def _quote_cmd(command) -> list:
        """Pane-spawn commands are handed to psmux as argv tokens after `--`, but psmux re-JOINS
        them with spaces into one command line before spawning (join-then-parse). A token that
        itself contains a space therefore SPLITS into several words in the pane process -- live
        incident: a spaced project dir handed to mk-core-view arrived as project='D:/helping',
        so the tower rendered an empty roster and lost its orientation flag. Pre-quote spaced
        tokens so the re-join round-trips; everything else passes through untouched."""
        return [f'"{c}"' if " " in str(c) and not str(c).startswith('"') else str(c)
                for c in command]

    def new_session(self, session: str, window: str, command: list[str]) -> str:
        # -x/-y: build at a generous size so many-pane layouts (tiled/pages/dashboard) have room
        # to split BEFORE the client attaches; tmux scales the window down to the client on attach.
        result = self._run("new-session", "-d", "-s", session, "-n", window,
                           "-x", "250", "-y", "60", "--", *self._quote_cmd(command))
        if result.returncode != 0:
            raise RuntimeError(f"psmux new-session failed: {result.stderr!r}")
        pane_id = self.pane_id(f"{session}:0")
        if not pane_id or not pane_id.startswith("%"):
            raise RuntimeError(f"psmux new-session failed: empty pane id (stderr={result.stderr!r})")
        return pane_id

    def new_window(self, session: str, window: str, command: list[str], cwd: str | None = None) -> str:
        args = ["new-window", "-t", session, "-d", "-n", window]
        if cwd:
            args += ["-c", cwd]
        result = self._run(*args, "--", *self._quote_cmd(command))
        if result.returncode != 0:
            raise RuntimeError(f"psmux new-window failed: {result.stderr!r}")
        pane_id = self.pane_id(f"{session}:{window}")
        if not pane_id or not pane_id.startswith("%"):
            raise RuntimeError(f"psmux new-window failed: empty pane id (stderr={result.stderr!r})")
        return pane_id

    def split_window(self, target: str, command: list[str], vertical: bool = True,
                     size: int | None = None) -> str:
        flag = "-v" if vertical else "-h"
        args = ["split-window", flag, "-t", target, "-d"]
        if size is not None:
            args += ["-p", str(size)]   # psmux: new pane takes `size` percent
        args += ["-P", "-F", "#{pane_id}", "--", *self._quote_cmd(command)]
        result = self._run(*args)
        if result.returncode != 0:
            raise RuntimeError(f"psmux split-window failed: {result.stderr!r}")
        pane_id = result.stdout.strip()
        if not pane_id.startswith("%"):
            raise RuntimeError(f"psmux split-window failed: empty pane id (stderr={result.stderr!r})")
        return pane_id

    def select_layout(self, target: str, layout: str = "tiled") -> None:
        result = self._run("select-layout", "-t", target, layout)
        if result.returncode != 0:
            raise RuntimeError(f"psmux select-layout failed: {result.stderr!r}")

    def window_size(self, target: str) -> tuple:
        """(cols, rows) of the target's window; falls back to the 250x60 build size if unreadable."""
        out = self._run("display-message", "-t", target, "-p", "#{window_width} #{window_height}").stdout.split()
        try:
            return int(out[0]), int(out[1])
        except (IndexError, ValueError):
            return 250, 60

    def set_option(self, name: str, value: str) -> None:
        """Best-effort `set -g <name> <value>`; cosmetic, never raises."""
        self._run("set", "-g", name, value)

    def rename_window(self, target: str, name: str) -> None:
        """Best-effort: name the window's tab after the workspace, and PIN it (automatic-rename off for
        this window) so psmux won't overwrite the name with the running process. Cosmetic; never raises."""
        self._run("set-window-option", "-t", target, "automatic-rename", "off")
        self._run("rename-window", "-t", target, name)

    def set_pane_title(self, target: str, title: str) -> None:
        """Best-effort pane-border title; cosmetic, never raises."""
        self._run("select-pane", "-t", target, "-T", title)

    def select_pane(self, target: str) -> None:
        """Best-effort focus a pane (so the user lands here on attach); cosmetic, never raises."""
        self._run("select-pane", "-t", target)

    def bind_key(self, key: str, *command: str) -> None:
        """Best-effort prefix key binding (Ctrl-b <key> -> command); cosmetic, never raises."""
        self._run("bind-key", key, *command)

    def window_names(self, session: str) -> list[str]:
        """The session's window (tab) names, in index order. psmux resolves window targets BY NAME to
        the FIRST match, so callers use this to refuse creating a duplicate name (a second window with
        the same name would silently receive none of its splits). Best-effort: [] on any failure."""
        result = self._run("list-windows", "-t", session, "-F", "#{window_name}")
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def pane_id(self, target: str) -> str:
        return self._run("display-message", "-t", target, "-p", "#{pane_id}").stdout.strip()

    def send_text(self, pane_id: str, text: str) -> None:
        self._run("send-keys", "-t", pane_id, "-l", text)

    def send_enter(self, pane_id: str) -> None:
        self._run("send-keys", "-t", pane_id, "Enter")

    def cancel_copy_mode(self, pane_id: str) -> None:
        """Exit copy-mode/scroll before a delivery — psmux routes send-keys to copy-mode navigation
        while a pane is scrolled, silently swallowing the keys. Best-effort; no-op when not in a mode.

        This un-sticks a WORKER pane before we deliver to it. The USER's own panes are hardened
        separately in layouts.apply_chrome: `scroll-enter-copy-mode off` stops the mouse wheel from ever
        dropping a pane into copy-mode (the wheel scrolls scrollback directly instead), and a Ctrl-b Esc
        bind runs this same `send-keys -X cancel` so a trapped user can instantly get back to typing."""
        self._run("send-keys", "-t", pane_id, "-X", "cancel")

    def send_line(self, pane_id: str, text: str) -> None:
        self.cancel_copy_mode(pane_id)   # else copy-mode/scroll can silently swallow the delivery
        self.send_text(pane_id, text)
        time.sleep(_SUBMIT_SETTLE)   # let the input settle so Enter is a deliberate submit (codex)
        self.send_enter(pane_id)

    def send_wake_submit(self, pane_id: str, text: str) -> None:
        self.cancel_copy_mode(pane_id)   # else copy-mode/scroll can silently swallow the delivery
        self.send_text(pane_id, text)
        time.sleep(_WAKE_SETTLE)
        self.send_enter(pane_id)
        time.sleep(_WAKE_ENTER_GAP)
        self.send_enter(pane_id)

    def capture(self, pane_id: str) -> str:
        return self._run("capture-pane", "-t", pane_id, "-p").stdout

    def attach_command(self, session: str) -> list[str]:
        return [*self._base(), "attach", "-t", session]
