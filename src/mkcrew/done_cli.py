# src/mkcrew/done_cli.py
import sys, json, time, os
from . import config

def run(argv) -> int:
    if len(argv) < 1:
        sys.stderr.write('usage: mk-done <job_id> <reply...>\n')
        return 2
    job_id = argv[0]
    reply = " ".join(argv[1:]).strip()
    actor = os.environ.get("MK_ACTOR", "unknown")
    artifact = {"job_id": job_id, "actor": actor, "reply": reply, "ts": time.time()}
    dest = config.agent_finish_dir(actor) / f"done-{job_id}-{int(time.time()*1000)}.json"
    try:
        dest.write_text(json.dumps(artifact), encoding="utf-8")
    except Exception:
        import traceback; traceback.print_exc(); return 1
    print(f"reported completion for {job_id}")
    return 0

def main() -> int:
    return run(sys.argv[1:])

if __name__ == "__main__":
    raise SystemExit(main())
