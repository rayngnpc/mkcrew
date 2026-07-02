# src/mkcrew/_run.py
"""The single entry frozen into MKCREW.exe. argv[1] decides which MKCREW tool we are (busybox style):
no args -> the desktop app; a daemon/core-view/done token -> that helper; an mk subcommand -> the CLI.
So one .exe covers app + browser + CLI + every internal command the cockpit spawns."""
import sys

# mk subcommands route to the CLI dispatcher via cli.COMMANDS itself — a hand-maintained mirror
# list went stale (doctor/open/workspaces missing -> "MKCREW.exe doctor" opened the app window).


def main():
    a = sys.argv
    sub = a[1] if len(a) > 1 else ""
    if sub == "mkd":
        from mkcrew import daemon; daemon.main()
    elif sub == "core-view":
        sys.argv = [a[0], *a[2:]]
        from mkcrew import coreview; coreview.coreview_main()
    elif sub == "files-view":
        sys.argv = [a[0], *a[2:]]
        from mkcrew import filesview; filesview.filesview_main()
    elif sub == "add-workspace":
        sys.argv = [a[0], *a[2:]]
        from mkcrew import addworkspace; addworkspace.addworkspace_main()
    elif sub == "done":
        sys.argv = [a[0], *a[2:]]
        from mkcrew import done_cli; done_cli.main()
    elif sub == "finish-hook":
        sys.argv = [a[0], *a[2:]]
        from mkcrew import finish_hook; finish_hook.main()
    elif sub == "--check":
        from mkcrew import app, studio, frozen  # noqa: F401 — import smoke for the frozen build
        try:
            import webview  # noqa: F401
            gui = "webview OK"
        except Exception as e:
            gui = f"webview MISSING ({type(e).__name__})"
        print(f"MKCREW.exe ok | frozen={frozen.is_frozen()} | psmux={frozen.psmux_exe()} | {gui}")
    else:
        if sub:
            from mkcrew import cli
            if sub in cli.COMMANDS:
                cli.main()                                 # argv[1] is the subcommand it expects
                return
        from mkcrew import frozen; frozen.hide_console()   # no stray cmd window behind the app
        from mkcrew import app; app.main()                 # no/unknown arg -> the desktop window


if __name__ == "__main__":
    main()
