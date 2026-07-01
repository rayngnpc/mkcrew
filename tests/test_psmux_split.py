import subprocess
import pytest
from mkcrew.psmux import PsmuxBackend

def test_split_window_returns_new_pane_id(monkeypatch):
    mux = PsmuxBackend()
    monkeypatch.setattr(mux, "_run",
        lambda *a: subprocess.CompletedProcess(a, 0, stdout="%7\n", stderr=""))
    assert mux.split_window("mkcrew:0", ["powershell"]) == "%7"

def test_split_window_raises_on_failure(monkeypatch):
    mux = PsmuxBackend()
    monkeypatch.setattr(mux, "_run",
        lambda *a: subprocess.CompletedProcess(a, 1, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError):
        mux.split_window("mkcrew:0", ["powershell"])

def test_split_window_raises_on_empty_pane(monkeypatch):
    mux = PsmuxBackend()
    monkeypatch.setattr(mux, "_run",
        lambda *a: subprocess.CompletedProcess(a, 0, stdout="\n", stderr=""))
    with pytest.raises(RuntimeError):
        mux.split_window("mkcrew:0", ["powershell"])

def test_select_layout_raises_on_failure(monkeypatch):
    mux = PsmuxBackend()
    monkeypatch.setattr(mux, "_run",
        lambda *a: subprocess.CompletedProcess(a, 1, stdout="", stderr="bad"))
    with pytest.raises(RuntimeError):
        mux.select_layout("mkcrew", "tiled")

def test_select_layout_ok(monkeypatch):
    mux = PsmuxBackend()
    calls = []
    def rec(*a):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")
    monkeypatch.setattr(mux, "_run", rec)
    mux.select_layout("mkcrew", "tiled")
    assert calls[0] == ("select-layout", "-t", "mkcrew", "tiled")
