# tests/test_transcript.py
import json
from mkcrew.transcript import last_assistant_reply

def test_extracts_last_assistant_text(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        '{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"PHASE0_OK"}]}}',
    ]), encoding="utf-8")
    assert last_assistant_reply(f) == "PHASE0_OK"

def test_joins_multiple_text_blocks(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"a"},{"type":"text","text":"b"}]}}', encoding="utf-8")
    assert last_assistant_reply(f) == "a\nb"

def test_missing_file_returns_empty(tmp_path):
    assert last_assistant_reply(tmp_path / "nope.jsonl") == ""

def test_skips_thinking_blocks(tmp_path):
    """thinking blocks are not text replies; should be ignored."""
    f = tmp_path / "t.jsonl"
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "internal monologue"},
            {"type": "text", "text": "REAL_REPLY"},
        ], "stop_reason": "end_turn"}}),
    ]
    f.write_text("\n".join(lines), encoding="utf-8")
    assert last_assistant_reply(f) == "REAL_REPLY"

def test_skips_tool_use_only_entry_walks_back(tmp_path):
    """Real format: last assistant entry is tool_use-only; walk back to find text."""
    f = tmp_path / "t.jsonl"
    lines = [
        # user turn
        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "go"}]}}),
        # assistant with actual text reply
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "let me think"},
            {"type": "text", "text": "Here is my answer"},
        ], "stop_reason": "end_turn"}}),
        # trailing tool_use-only assistant entry (e.g. TaskCreate) - no text
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_01", "name": "TaskCreate", "input": {}},
        ], "stop_reason": "tool_use"}}),
    ]
    f.write_text("\n".join(lines), encoding="utf-8")
    assert last_assistant_reply(f) == "Here is my answer"

def test_ai_title_only_file_returns_empty(tmp_path):
    """project/<uuid>.jsonl in Claude Code 2.1.173 is ai-title-only; must return empty."""
    f = tmp_path / "session.jsonl"
    f.write_text(
        json.dumps({"type": "ai-title", "aiTitle": "some title", "sessionId": "abc-123"}) + "\n",
        encoding="utf-8",
    )
    assert last_assistant_reply(f) == ""
