import sys

from mkcrew import app


def test_app_show_falls_back_to_browser_without_pywebview(monkeypatch):
    """pywebview is an optional dep — when it's absent, _show opens the default browser and reports
    the fallback (returns False) so main() keeps the server alive. This is the app↔browser bridge."""
    opened = {}
    monkeypatch.setattr(app.webbrowser, "open", lambda u: opened.update(url=u))
    monkeypatch.setitem(sys.modules, "webview", None)      # `import webview` -> ImportError, deterministically
    assert app._show("http://127.0.0.1:9999") is False
    assert opened["url"] == "http://127.0.0.1:9999"


def test_app_show_falls_back_when_webview2_runtime_missing(monkeypatch):
    """pywebview installed but NO WebView2 runtime (stripped Windows / Sandbox): we force the
    edgechromium renderer — never MSHTML/IE, which mangles the Studio UI — so start() raises and
    _show falls back to the default browser, which renders correctly."""
    opened, calls = {}, {}

    class FakeWebview:
        @staticmethod
        def create_window(*a, **k):
            calls["window"] = True

        @staticmethod
        def start(gui=None):
            calls["gui"] = gui
            raise RuntimeError("WebView2 runtime not found")

    monkeypatch.setitem(sys.modules, "webview", FakeWebview)
    monkeypatch.setattr(app.webbrowser, "open", lambda u: opened.update(url=u))
    assert app._show("http://127.0.0.1:9999") is False
    assert calls["gui"] == "edgechromium"                  # modern renderer demanded, MSHTML never allowed
    assert opened["url"] == "http://127.0.0.1:9999"
