# tests/test_pickfolder.py
"""studio.pick_folder() upgraded from the OLD FolderBrowserDialog tree to the MODERN Windows Common
Item Dialog (IFileOpenDialog + FOS_PICKFOLDERS) — the resizable "Select Folder" with an address bar +
a path box you can type/paste into. A live dialog needs a desktop, so these tests run headless: we
assert the PowerShell carries the modern markers, and mock subprocess.run so no GUI opens."""
from mkcrew import studio


class _Fake:
    """Stand-in for the subprocess.run CompletedProcess (only .stdout is read by pick_folder)."""
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def _capture(monkeypatch):
    """Replace studio's subprocess.run: record the argv, hand back a canned stdout (seen['stdout'])."""
    seen = {}
    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return _Fake(seen.get("stdout", ""))
    monkeypatch.setattr(studio.subprocess, "run", fake_run)
    return seen


def test_ps_uses_modern_common_item_dialog(monkeypatch):
    seen = _capture(monkeypatch)
    studio.pick_folder()
    cmd = seen["cmd"]
    ps = cmd[-1]
    assert "-STA" in cmd                              # COM needs a single-threaded apartment
    assert "IFileOpenDialog" in ps                    # the modern Common Item Dialog
    assert ("FOS_PICKFOLDERS" in ps) or ("0x20" in ps)   # ...told to pick a FOLDER
    assert "GetResult" in ps and "0x80058000" in ps   # SIGDN_FILESYSPATH -> real path
    assert "FolderBrowserDialog" in ps                # graceful fallback so it never hard-fails
    # the old dialog must no longer be the PRIMARY path (it's now only inside the catch block)
    assert ps.index("IFileOpenDialog") < ps.index("FolderBrowserDialog")


def test_successful_pick_sets_project(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    seen["stdout"] = str(tmp_path) + "\r\n"           # dialog prints the chosen path
    res = studio.pick_folder()
    assert res["ok"] is True
    assert res["path"] == str(tmp_path)
    assert studio.get_project() == str(tmp_path)      # set_project actually ran


def test_cancel_returns_not_ok(monkeypatch):
    seen = _capture(monkeypatch)
    seen["stdout"] = "   \r\n"                         # blank stdout == cancelled / nothing picked
    assert studio.pick_folder()["ok"] is False


def test_bad_path_returns_not_ok(monkeypatch):
    seen = _capture(monkeypatch)
    seen["stdout"] = "Z:\\nope\\does\\not\\exist\n"   # set_project rejects a non-directory
    assert studio.pick_folder()["ok"] is False


def test_subprocess_failure_returns_not_ok(monkeypatch):
    def boom(*a, **k):
        raise OSError("powershell not found")
    monkeypatch.setattr(studio.subprocess, "run", boom)
    assert studio.pick_folder()["ok"] is False
