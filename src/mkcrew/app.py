# src/mkcrew/app.py
"""MKCREW desktop app — option #1 of three ways to run MKCREW (app / browser / CLI).

It hosts the EXACT Studio web UI in a native OS window (pywebview, which uses the platform webview —
Edge WebView2 on Windows) instead of a browser tab. With pywebview absent it degrades to the default
browser, so this one entry point covers both. (Browser = `mk studio`; CLI = the `mk` command.)
There is no separate app UI to maintain — the engine and the screen are shared across all three.
"""
import threading
import webbrowser

from . import studio


def _show(url: str) -> bool:
    """Open `url` in a native window via pywebview; fall back to the default browser if it isn't
    installed. Returns True if the webview ran (it blocks until the window closes), False on fallback."""
    try:
        import webview   # pywebview (optional dep: pip install mkcrew[app])
    except ImportError:
        webbrowser.open(url)
        return False
    try:
        webview.create_window("MKCREW", url, width=1200, height=820)
        # edgechromium ONLY: without it pywebview silently falls back to MSHTML (IE11), which
        # mangles the Studio UI (seen on stripped Windows/Sandbox where WebView2 isn't installed).
        # No WebView2 -> raise -> the default browser renders it correctly instead.
        webview.start(gui="edgechromium")
        return True
    except Exception:
        webbrowser.open(url)
        return False


def main():
    httpd = studio.make_server(port=0)                       # Studio's own server on an ephemeral port
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if not _show(url):                                       # browser fallback -> keep the server alive
        print(f"MKCREW Studio: {url}  (Ctrl-C to stop)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
