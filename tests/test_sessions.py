import json
from mkcrew import sessions


def test_ensure_creates_and_persists_new_uuid(tmp_path):
    sid, is_new = sessions.ensure(tmp_path, "opus1")
    assert is_new is True
    assert sid                                   # non-empty
    data = json.loads((tmp_path / ".mkcrew" / "sessions.json").read_text(encoding="utf-8"))
    assert data["opus1"] == sid


def test_ensure_returns_same_uuid_second_time(tmp_path):
    sid1, new1 = sessions.ensure(tmp_path, "opus1")
    sid2, new2 = sessions.ensure(tmp_path, "opus1")
    assert new1 is True and new2 is False
    assert sid1 == sid2


def test_ensure_distinct_uuids_per_role(tmp_path):
    a, _ = sessions.ensure(tmp_path, "opus1")
    b, _ = sessions.ensure(tmp_path, "sonnet4")
    assert a != b


def test_ensure_writes_gitignore(tmp_path):
    sessions.ensure(tmp_path, "opus1")
    gi = tmp_path / ".mkcrew" / ".gitignore"
    assert gi.exists() and "sessions.json" in gi.read_text(encoding="utf-8")


def test_clear_removes_store_and_next_ensure_is_new(tmp_path):
    sessions.ensure(tmp_path, "opus1")
    sessions.clear(tmp_path)
    assert not (tmp_path / ".mkcrew" / "sessions.json").exists()
    _, is_new = sessions.ensure(tmp_path, "opus1")
    assert is_new is True


def test_corrupt_store_treated_as_empty(tmp_path):
    d = tmp_path / ".mkcrew"; d.mkdir()
    (d / "sessions.json").write_text("{not json", encoding="utf-8")
    sid, is_new = sessions.ensure(tmp_path, "opus1")
    assert is_new is True and sid


def test_is_resumable_true_only_when_claude_saved_the_session(tmp_path, monkeypatch):
    """is_resumable is True only when claude has a transcript for that session id.
    Claude saves to ~/.claude/projects/<cwd-with-:\\/-as-dashes>/<session-id>.jsonl, and a
    launched-but-never-used session is NOT saved (resuming it fails 'No conversation found')."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Path.home() -> tmp on Windows
    project = r"E:\Proj\app"
    sid = "abc-123"
    cdir = tmp_path / ".claude" / "projects" / "E--Proj-app"   # E:\Proj\app -> E--Proj-app
    cdir.mkdir(parents=True)
    assert sessions.is_resumable(project, sid) is False         # no transcript -> not resumable
    (cdir / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.is_resumable(project, sid) is True          # claude saved it -> resumable


def test_is_resumable_claude_default_provider_unchanged(tmp_path, monkeypatch):
    """The default provider is still 'claude' -> the transcript-file rule is byte-identical whether
    provider is omitted or passed explicitly."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    project = r"E:\Proj\app"
    sid = "abc-123"
    cdir = tmp_path / ".claude" / "projects" / "E--Proj-app"
    cdir.mkdir(parents=True)
    assert sessions.is_resumable(project, sid) is False
    assert sessions.is_resumable(project, sid, "claude") is False
    (cdir / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.is_resumable(project, sid) is True
    assert sessions.is_resumable(project, sid, "claude") is True


def test_is_resumable_continue_last_providers_resume_without_transcript(tmp_path):
    """codex/opencode/antigravity/gemini are continue-last (or preset-id) CLIs: is_resumable is True
    for them regardless of any claude transcript (the caller already gated on 'launched before')."""
    for prov in ("codex", "opencode", "antigravity", "gemini"):
        assert sessions.is_resumable(tmp_path, "any-id", prov) is True


def test_is_resumable_unknown_or_custom_provider_never_resumable(tmp_path):
    """An unknown / `custom` provider is never resumable -> always relaunched fresh."""
    assert sessions.is_resumable(tmp_path, "any-id", "custom") is False
    assert sessions.is_resumable(tmp_path, "any-id", "llama") is False


def test_resume_flag_shared_continue_last_provider_launches_fresh(tmp_path):
    """THE FIX: a continue-last provider (codex/opencode/antigravity) shared by 2+ agents can't
    co-resume -- `resume --last` / `--continue` would reopen the ONE shared 'last' session for every
    pane (corrupted history + cross-talk). resume_flag returns False for them when shared, so each
    launches FRESH into its OWN new session; a SOLE agent of the provider still resumes."""
    for prov in ("codex", "opencode", "antigravity"):
        assert sessions.resume_flag(tmp_path, "id", prov, shared_provider=True) is False   # 2+  -> fresh
        assert sessions.resume_flag(tmp_path, "id", prov, shared_provider=False) is True    # sole -> resume


def test_resume_flag_gemini_resumes_even_when_shared(tmp_path):
    """gemini PRE-SETS a per-role uuid (`--session-id`/`--resume <uuid>`), so two geminis never target
    the same session -> it is NOT a continue-last collision provider and resumes even when shared."""
    assert sessions.resume_flag(tmp_path, "id", "gemini", shared_provider=True) is True
    assert sessions.resume_flag(tmp_path, "id", "gemini", shared_provider=False) is True


def test_resume_flag_claude_keeps_transcript_rule_regardless_of_shared(tmp_path, monkeypatch):
    """claude keys resume off its per-role-uuid transcript no matter how many claude agents there are
    (each has a distinct uuid, so they never collide) -- shared_provider must NOT force claude fresh."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))            # Path.home() -> tmp on Windows
    project = r"E:\Proj\app"
    sid = "abc-123"
    cdir = tmp_path / ".claude" / "projects" / "E--Proj-app"
    cdir.mkdir(parents=True)
    assert sessions.resume_flag(project, sid, "claude", shared_provider=True) is False   # no transcript yet
    (cdir / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.resume_flag(project, sid, "claude", shared_provider=True) is True     # saved -> resumes


def test_resume_flag_unknown_or_custom_never_resumes(tmp_path):
    """custom/unknown providers are never resumable, shared or not (delegates to is_resumable)."""
    assert sessions.resume_flag(tmp_path, "id", "custom", shared_provider=False) is False
    assert sessions.resume_flag(tmp_path, "id", "llama", shared_provider=True) is False


def test_ensure_mints_id_regardless_of_provider(tmp_path):
    """ensure is provider-agnostic: it mints + persists ONE per-role id (no provider arg), so a
    codex/opencode/agy main gets a tracked id whose is_new flips to False on the next start."""
    sid, new1 = sessions.ensure(tmp_path, "main")
    sid2, new2 = sessions.ensure(tmp_path, "main")
    assert new1 is True and new2 is False and sid == sid2


def test_is_resumable_checks_account_wrapper_config_dir(tmp_path, monkeypatch):
    """Per-account resume (Windows flavor): a claude launched via an account wrapper (.cmd setting
    CLAUDE_CONFIG_DIR) stores its transcripts under THAT dir -- is_resumable must stat there, not
    ~/.claude, or a session saved under one account is wrongly --resumed under another and the pane
    crash-loops ('No conversation found')."""
    import re as _re
    from mkcrew import sessions
    proj = tmp_path / "proj"; proj.mkdir()
    work_dir = tmp_path / "claude-work"
    wrapper = tmp_path / "claudew.cmd"
    wrapper.write_text('@echo off\r\nsetlocal\r\nset "CLAUDE_CONFIG_DIR='
                       + str(work_dir) + '"\r\nclaude %*\r\n', encoding="utf-8")
    assert sessions._claude_config_dir(str(wrapper)) == work_dir      # grep + expandvars works on .cmd
    assert sessions._claude_config_dir(None) == (sessions.Path.home() / ".claude")
    sid = "11111111-2222-3333-4444-555555555555"
    enc = _re.sub(r"[:\\/]", "-", str(proj))
    # transcript exists ONLY under the wrapper's dir -> resumable with bin, NOT bare
    tdir = work_dir / "projects" / enc; tdir.mkdir(parents=True)
    (tdir / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.is_resumable(proj, sid, "claude", bin=str(wrapper)) is True
    monkeypatch.setattr(sessions.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    assert sessions.is_resumable(proj, sid, "claude") is False        # bare claude: different dir



def test_is_resumable_matches_claude_encoding_for_spaced_paths(tmp_path, monkeypatch):
    """LIVE INCIDENT (D:/helping friend/Dat/Bus 338/GroupWork): claude stores transcripts under the
    path with every char outside [A-Za-z0-9_-] dashed (spaces INCLUDED) -- the old [:/\] rule kept
    spaces, so the stat always missed for spaced paths, and the relaunch re-ran --session-id on an
    id claude already knew: "Session ID already in use" + an infinite pane crash-loop. Underscores
    stay (real "-_archive-" store entries)."""
    import re as _re
    from mkcrew import sessions
    monkeypatch.setattr(sessions.Path, "home", staticmethod(lambda: tmp_path))
    proj = r"D:\helping friend\Dat\Bus 338\GroupWork"
    sid = "a996affc-f4b0-43f2-ae4a-4ee99def62ff"
    store = tmp_path / ".claude" / "projects" / "D--helping-friend-Dat-Bus-338-GroupWork"
    store.mkdir(parents=True)
    (store / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.is_resumable(proj, sid, "claude") is True      # spaces dash-encoded like claude
    under = tmp_path / ".claude" / "projects" / "E--My_Project-App"
    under.mkdir(parents=True)
    (under / "x.jsonl").write_text("{}", encoding="utf-8")
    assert sessions.is_resumable(r"E:\My_Project\App", "x", "claude") is True   # underscore KEPT


def test_rotate_mints_fresh_uuid_and_persists(tmp_path):
    """rotate() = the crash-loop net: a fresh RE-launch never reuses a possibly-registered id."""
    from mkcrew import sessions
    proj = tmp_path / "p"; proj.mkdir()
    old, is_new = sessions.ensure(proj, "main")
    assert is_new
    new = sessions.rotate(proj, "main")
    assert new != old and len(new) == 36
    again, is_new2 = sessions.ensure(proj, "main")
    assert again == new and is_new2 is False               # persisted as the role's id
