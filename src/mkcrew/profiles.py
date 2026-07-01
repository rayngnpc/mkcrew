# src/mkcrew/profiles.py
"""Saved Studio configs (profiles). One JSON file per profile under the runtime profiles dir.
A profile stores {name, count, layout, providers}; recency = file mtime."""
import json
import re
from . import config


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "profile"


def save(name: str, data: dict) -> None:
    p = config.profiles_dir() / f"{_slug(name)}.json"
    p.write_text(json.dumps({"name": name, **data}, indent=2), encoding="utf-8")


def load(name: str) -> dict:
    p = config.profiles_dir() / f"{_slug(name)}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def list_profiles() -> list:
    """All profiles, most-recently-saved first (by file mtime)."""
    files = sorted(config.profiles_dir().glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def delete(name: str) -> None:
    (config.profiles_dir() / f"{_slug(name)}.json").unlink(missing_ok=True)
