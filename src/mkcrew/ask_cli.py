# src/mkcrew/ask_cli.py
import argparse, json, sys, urllib.request, urllib.error
from . import config

def parse_args(argv):
    p = argparse.ArgumentParser(prog="mk ask")
    p.add_argument("--callback", action="store_true")
    p.add_argument("--silence", action="store_true")
    p.add_argument("role")
    p.add_argument("message")
    return p.parse_args(argv)

def _post(path: str, payload: dict) -> dict:
    try:
        text = config.port_file().read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("port file is empty")
        port = int(text)
    except (FileNotFoundError, ValueError, OSError) as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Socket cap = dead-daemon FAILSAFE, not the ask ceiling: the daemon owns the timeout and
        # always replies first (1800s, or 5400s in thorough mode) — this only has to outlast 5400.
        with urllib.request.urlopen(req, timeout=5430) as r:   # blocking: waits for the worker's reply
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionRefusedError) as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")

def main(argv=None) -> int:
    ns = parse_args(argv if argv is not None else sys.argv[1:])
    import os
    frm = os.environ.get("MK_ACTOR", "main")
    resp = _post("/ask", {"from": frm, "to": ns.role, "text": ns.message})
    if "error" in resp:
        print(resp["error"])
    else:
        print(resp.get("reply", ""))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
