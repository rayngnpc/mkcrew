# src/mkcrew/console.py
"""Adaptive cockpit console font (Windows). The cockpit is ONE console window that psmux splits into
panes; its font is shared by every pane, so shrinking the font gives every pane more character cells.
We size the font to the DENSEST cockpit window (most panes) on the REAL display, so agent panes stay
readable: more agents (or a smaller screen) -> smaller font -> more content per pane.

There is a HARD physical limit -- many panes on a small screen can't all show full-width content at a
legible glyph size. Past that, a paged layout (hub = 1/tab, pages = ~6/tab) is the readability fix, not
a smaller font. Best-effort: returns False (no-op) off-Windows or if the console host (some Windows
Terminal setups) ignores SetCurrentConsoleFontEx.
"""
import ctypes
import math

# Cells each pane should keep to stay usable (a readability target the font shrinks to satisfy).
# Deliberately generous so the font scales DOWN noticeably as panes grow, even on a roomy 1080p screen
# (a 9-pane tiled grid lands ~10px instead of staying at the old flat 12px).
_MIN_COLS, _MIN_ROWS = 90, 28
# px font height: below ~8 glyphs get illegible; 16 ~ the Windows console default (few panes).
_MIN_FONT, _MAX_FONT = 8, 16
_CELL_ASPECT = 0.5          # Consolas cell width ~= half its height (for the columns estimate)
_FACE = "Consolas"


def screen_size():
    """(width, height) of the primary display in px; (1920, 1080) fallback off-Windows."""
    try:
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))   # SM_CXSCREEN, SM_CYSCREEN
    except Exception:
        return 1920, 1080


def adaptive_font_height(panes, size=None):
    """Largest font height (px) such that a ~square grid of `panes` panes still gives each pane at
    least _MIN_COLS x _MIN_ROWS cells on this display. Monotonically non-increasing in `panes`;
    clamped to a legible range. `size` overrides the measured screen (for tests)."""
    panes = max(1, int(panes))
    w, h = size or screen_size()
    h = h * 0.90                                     # leave room for title bar / taskbar / status line
    cols = max(1, math.ceil(math.sqrt(panes)))       # pane grid is roughly square
    rows = max(1, math.ceil(panes / cols))
    by_rows = h / (rows * _MIN_ROWS)
    by_cols = (2 * w) / (cols * _MIN_COLS)           # cell_w = _CELL_ASPECT*fh -> cols_total = 2w/fh
    return max(_MIN_FONT, min(_MAX_FONT, int(min(by_rows, by_cols))))


def set_console_font(height):
    """Set THIS console's font height via Win32 SetCurrentConsoleFontEx. True on success; never raises
    (no-op off-Windows / unsupported host -> the launch .cmd's fixed shrink stays as the fallback)."""
    try:
        class _FONTINFOEX(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("nFont", ctypes.c_uint),
                        ("FontWidth", ctypes.c_short), ("FontHeight", ctypes.c_short),
                        ("FontFamily", ctypes.c_uint), ("FontWeight", ctypes.c_uint),
                        ("FaceName", ctypes.c_wchar * 32)]
        k = ctypes.windll.kernel32
        info = _FONTINFOEX()
        info.cbSize = ctypes.sizeof(_FONTINFOEX)
        info.FontWidth = 0                            # 0 -> Windows derives the width for the TT face
        info.FontHeight = int(height)
        info.FontFamily = 0
        info.FontWeight = 400
        info.FaceName = _FACE
        return bool(k.SetCurrentConsoleFontEx(k.GetStdHandle(-11), False, ctypes.byref(info)))
    except Exception:
        return False


if __name__ == "__main__":   # quick self-check: density rises with panes, clamped & legible
    for scr in [(1920, 1080), (1366, 768)]:
        seq = [adaptive_font_height(p, scr) for p in (1, 4, 6, 9, 16, 100)]
        assert seq == sorted(seq, reverse=True), seq            # more panes -> smaller-or-equal
        assert all(_MIN_FONT <= f <= _MAX_FONT for f in seq)    # always legible, never huge
        print(scr, "panes 1/4/6/9/16/100 ->", seq)
