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
