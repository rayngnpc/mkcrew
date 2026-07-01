# src/mkcrew/transcript.py
import json
from pathlib import Path

def last_assistant_reply(transcript_path) -> str:
    """Return text from the last assistant message that contains text blocks.

    Skips entries that are tool-use-only or thinking-only (no text blocks).
    Walks backward from the end so a trailing tool_use-only assistant entry
    does not shadow the most recent text reply.
    """
    p = Path(transcript_path)
    if not p.exists():
        return ""
    candidates = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        content = obj.get("message", {}).get("content", [])
        texts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if texts:
            candidates.append("\n".join(texts))
    return candidates[-1] if candidates else ""
