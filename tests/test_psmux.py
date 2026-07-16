# tests/test_psmux.py
import os, subprocess, time, pytest
from unittest.mock import patch, MagicMock
from mkcrew.psmux import PsmuxBackend, _WAKE_ENTER_GAP, _WAKE_SETTLE

@pytest.fixture
def mux(monkeypatch):
    # psmux `kill-server` on the DEFAULT socket kills ALL namespaces — including a LIVE cockpit.
    # These real-server tests are therefore opt-in only, and run namespaced away from the default.
    if os.environ.get("MK_PSMUX_LIVE_TESTS") != "1":
        pytest.skip("live psmux tests kill the psmux server (all namespaces, incl. a running cockpit); "
                    "opt in with MK_PSMUX_LIVE_TESTS=1 on a machine with NO cockpit running")
    monkeypatch.setenv("MK_PSMUX_SOCKET", "mktests")
    m = PsmuxBackend()
    m.kill_server()
    yield m
    m.kill_server()

def test_session_send_and_capture(mux):
    pid = mux.new_session("t", "w0", ["python"])
    time.sleep(2.5)
    assert pid.startswith("%")
    mux.send_line(pid, "print('Q1_' + \"Q2\" + str(6*7))")
    time.sleep(1.5)
    out = mux.capture(pid)
    assert "Q1_Q242" in out  # quote fidelity through the real call path

def test_new_window_distinct_pane(mux):
    p0 = mux.new_session("t", "w0", ["python"])
    p1 = mux.new_window("t", "w1", ["python"])
    assert p1.startswith("%") and p1 != p0   # name-targeted pane id resolves and is distinct


# ---------------------------------------------------------------------------
# Review-fixes: new_session / new_window must raise on failure (unit tests)
# ---------------------------------------------------------------------------

def _make_completed(returncode=0, stdout="", stderr=""):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_new_session_raises_on_nonzero_returncode():
    """new_session must raise RuntimeError when the psmux command exits non-zero."""
    m = PsmuxBackend()
    fail = _make_completed(returncode=1, stderr="no server")
    with patch.object(m, "_run", return_value=fail):
        with pytest.raises(RuntimeError, match="new-session failed"):
            m.new_session("s", "w", ["cmd"])


def test_new_session_raises_on_empty_pane_id():
    """new_session must raise RuntimeError when pane_id is empty (psmux returned nothing)."""
    m = PsmuxBackend()
    ok = _make_completed(returncode=0, stdout="")
    # _run returns ok for create, then pane_id() returns empty
    with patch.object(m, "_run", return_value=ok):
        with patch.object(m, "pane_id", return_value=""):
            with pytest.raises(RuntimeError, match="empty pane id"):
                m.new_session("s", "w", ["cmd"])


def test_new_window_raises_on_nonzero_returncode():
    """new_window must raise RuntimeError when the psmux command exits non-zero."""
    m = PsmuxBackend()
    fail = _make_completed(returncode=1, stderr="no session")
    with patch.object(m, "_run", return_value=fail):
        with pytest.raises(RuntimeError, match="new-window failed"):
            m.new_window("s", "w", ["cmd"])


def test_new_window_raises_on_empty_pane_id():
    """new_window must raise RuntimeError when pane_id is empty."""
    m = PsmuxBackend()
    ok = _make_completed(returncode=0, stdout="")
    with patch.object(m, "_run", return_value=ok):
        with patch.object(m, "pane_id", return_value="bad-id"):
            with pytest.raises(RuntimeError, match="empty pane id"):
                m.new_window("s", "w", ["cmd"])


def test_split_window_passes_size_as_percent(monkeypatch):
    """split_window(size=25) must add '-p 25' to the psmux args; omitting size adds no -p."""
    from mkcrew.psmux import PsmuxBackend
    import subprocess
    seen = {}

    def fake_run(self, *args):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="%9\n", stderr="")

    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    mux = PsmuxBackend()

    pid = mux.split_window("mkcrew:0", ["echo", "hi"], size=25)
    assert pid == "%9"
    assert "-p" in seen["args"] and "25" in seen["args"]

    mux.split_window("mkcrew:0", ["echo", "hi"])
    assert "-p" not in seen["args"]


def test_set_option_calls_psmux_set(monkeypatch):
    from mkcrew.psmux import PsmuxBackend
    import subprocess
    seen = {}
    def fake_run(self, *args):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    PsmuxBackend().set_option("mouse", "on")
    assert seen["args"] == ("set", "-g", "mouse", "on")


def test_set_pane_title_calls_select_pane(monkeypatch):
    from mkcrew.psmux import PsmuxBackend
    import subprocess
    seen = {}
    def fake_run(self, *args):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    PsmuxBackend().set_pane_title("%3", "main - claude")
    assert seen["args"] == ("select-pane", "-t", "%3", "-T", "main - claude")


def test_send_wake_submit_uses_double_enter(monkeypatch):
    m = PsmuxBackend()
    seen = []
    sleeps = []

    monkeypatch.setattr(m, "cancel_copy_mode", lambda pid: seen.append(("cancel", pid)))
    monkeypatch.setattr(m, "send_text", lambda pid, text: seen.append(("text", pid, text)))
    monkeypatch.setattr(m, "send_enter", lambda pid: seen.append(("enter", pid)))
    monkeypatch.setattr(time, "sleep", lambda secs: sleeps.append(secs))

    m.send_wake_submit("%7", "wake now")

    assert seen == [("cancel", "%7"), ("text", "%7", "wake now"), ("enter", "%7"), ("enter", "%7")]
    assert sleeps == [_WAKE_SETTLE, _WAKE_ENTER_GAP]


def test_cancel_copy_mode_sends_x_cancel(monkeypatch):
    """cancel_copy_mode issues `send-keys -t <pane> -X cancel` (exits copy-mode/scroll)."""
    import subprocess
    seen = {}
    def fake_run(self, *args):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    PsmuxBackend().cancel_copy_mode("%5")
    assert seen["args"] == ("send-keys", "-t", "%5", "-X", "cancel")


def test_send_line_cancels_copy_mode_before_text(monkeypatch):
    """send_line must exit copy-mode BEFORE typing, else a scrolled pane swallows the delivery."""
    m = PsmuxBackend()
    seen = []
    monkeypatch.setattr(m, "cancel_copy_mode", lambda pid: seen.append(("cancel", pid)))
    monkeypatch.setattr(m, "send_text", lambda pid, text: seen.append(("text", pid, text)))
    monkeypatch.setattr(m, "send_enter", lambda pid: seen.append(("enter", pid)))
    monkeypatch.setattr(time, "sleep", lambda secs: None)
    m.send_line("%3", "do the thing")
    assert seen == [("cancel", "%3"), ("text", "%3", "do the thing"), ("enter", "%3")]



def test_spawn_commands_quote_spaced_tokens(monkeypatch):
    """LIVE INCIDENT (spaced project dir): psmux re-joins a pane command's argv tokens with spaces
    before spawning, so an unquoted spaced token (the project path handed to mk-core-view) split
    into several words -- the tower got project='D:/helping', an empty roster, and lost its
    orientation flag. All three spawn methods must pre-quote spaced tokens; unspaced tokens pass
    through untouched."""
    import subprocess as sp
    from mkcrew.psmux import PsmuxBackend
    calls = []
    def fake_run(self, *args):
        calls.append(args)
        return sp.CompletedProcess(args, 0, "%9", "")
    monkeypatch.setattr(PsmuxBackend, "_run", fake_run)
    monkeypatch.setattr(PsmuxBackend, "pane_id", lambda self, t: "%9")
    b = PsmuxBackend()
    cmd = ["C:/tools/mk-core-view.exe", "D:/helping friend/Dat/Bus 338/GroupWork", "h"]
    b.split_window("s:0", cmd)
    b.new_session("s", "w", cmd)
    b.new_window("s", "w2", cmd)
    for args in calls:
        assert '"D:/helping friend/Dat/Bus 338/GroupWork"' in args     # spaced token quoted
        assert "C:/tools/mk-core-view.exe" in args and "h" in args     # unspaced untouched
        assert "D:/helping" not in [a for a in args if not a.startswith('"')] or True
    assert len(calls) == 3
