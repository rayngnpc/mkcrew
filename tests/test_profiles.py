from mkcrew import profiles


def test_save_then_list_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    profiles.save("web crew", {"count": 4, "layout": "hub", "providers": ["claude", "codex"]})
    names = [p["name"] for p in profiles.list_profiles()]
    assert "web crew" in names
    data = profiles.load("web crew")
    assert data["count"] == 4 and data["layout"] == "hub"


def test_delete_removes_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    profiles.save("temp", {"count": 2, "layout": "tiled", "providers": []})
    profiles.delete("temp")
    assert "temp" not in [p["name"] for p in profiles.list_profiles()]
