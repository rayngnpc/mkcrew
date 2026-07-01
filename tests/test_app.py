from mkcrew import app


def test_app_show_falls_back_to_browser_without_pywebview(monkeypatch):
    """pywebview is an optional dep — when it's absent, _show opens the default browser and reports
    the fallback (returns False) so main() keeps the server alive. This is the app↔browser bridge."""
    opened = {}
    monkeypatch.setattr(app.webbrowser, "open", lambda u: opened.update(url=u))
    # pywebview isn't installed in the test venv, so the import fails -> browser path
    assert app._show("http://127.0.0.1:9999") is False
    assert opened["url"] == "http://127.0.0.1:9999"
