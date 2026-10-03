"""Local, gitignored settings (data/settings.json): the last.fm API key and username."""
import json
import os
import re

from . import config


def load() -> dict:
    try:
        return json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(values: dict) -> None:
    config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(config.SETTINGS_PATH)


# last.fm usernames: 2-15 characters, starting with a letter; letters, digits, "_" and "-".
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,14}$")


def lastfm_api_key() -> str | None:
    return os.environ.get("LASTFM_API_KEY") or load().get("lastfm_api_key") or None


def lastfm_username() -> str | None:
    """The account whose scrobbles a live updater fetches (user.getRecentTracks)."""
    return os.environ.get("LASTFM_USER") or load().get("lastfm_username") or None


def set_lastfm_username(name: str) -> str:
    name = name.strip()
    if not USERNAME_RE.match(name):
        raise ValueError(f"not a valid last.fm username: {name!r}")
    save({**load(), "lastfm_username": name})
    return name
