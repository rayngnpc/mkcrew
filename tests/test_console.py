from mkcrew import console


def test_adaptive_font_shrinks_with_panes_and_clamps():
    """Font height falls (or holds) as panes grow and as the screen shrinks, always within the
    legible [_MIN_FONT, _MAX_FONT] range."""
    big = console.adaptive_font_height(2, size=(1920, 1080))     # few panes, big screen -> default max
    dense = console.adaptive_font_height(16, size=(1366, 768))   # many panes, laptop -> small
    assert big == console._MAX_FONT
    assert console._MIN_FONT <= dense <= console._MAX_FONT
    assert dense < big

    # monotonically non-increasing in pane count on a fixed screen (4 vs 9 agents must differ here)
    seq = [console.adaptive_font_height(p, size=(1366, 768)) for p in (1, 4, 6, 9, 16)]
    assert seq == sorted(seq, reverse=True)
    assert console.adaptive_font_height(5, (1366, 768)) > console.adaptive_font_height(10, (1366, 768))

    # never below the legible floor, even absurdly dense
    assert console.adaptive_font_height(200, size=(800, 600)) == console._MIN_FONT


def test_set_console_font_is_safe_and_returns_bool():
    """set_console_font never raises (best-effort); returns a bool (False off a real console host)."""
    assert isinstance(console.set_console_font(12), bool)


def _fake_windll(monkeypatch, active_face_fn, set_ok=True):
    calls = []

    class _K32:
        def GetStdHandle(self, n):
            return 42

        def SetCurrentConsoleFontEx(self, handle, max_win, info_ref):
            face = info_ref._obj.FaceName
            calls.append((handle, max_win, info_ref._obj.FontHeight, face))
            return bool(set_ok)

        def GetCurrentConsoleFontEx(self, handle, max_win, info_ref):
            info2 = info_ref._obj
            assert info2.cbSize > 0
            info2.FaceName = active_face_fn(calls[-1][3])
            return True

    fake = type("W", (), {"kernel32": _K32()})()
    monkeypatch.setattr(console.ctypes, "windll", fake, raising=False)
    return calls


def test_set_console_font_accepts_cascadia_on_first_try(monkeypatch):
    calls = _fake_windll(monkeypatch, lambda req: req)
    assert console.set_console_font(12) is True
    assert [c[3] for c in calls] == ["Cascadia Mono"]
    assert calls[0][2] == 12


def test_set_console_font_falls_back_to_consolas_when_cascadia_rejected(monkeypatch):
    calls = _fake_windll(monkeypatch, lambda req: "Consolas")
    assert console.set_console_font(10) is True
    assert [c[3] for c in calls] == ["Cascadia Mono", "Consolas"]


def test_set_console_font_returns_false_when_set_fails(monkeypatch):
    calls = _fake_windll(monkeypatch, lambda req: req, set_ok=False)
    assert console.set_console_font(12) is False
    assert [c[3] for c in calls] == ["Cascadia Mono", "Consolas"]


def test_set_console_font_returns_false_without_windll(monkeypatch):
    monkeypatch.delattr(console.ctypes, "windll", raising=False)
    assert console.set_console_font(12) is False
