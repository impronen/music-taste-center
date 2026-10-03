"""Local, gitignored settings (data/settings.json). Holds the last.fm API key."""
import json
import os

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


def lastfm_api_key() -> str | None:
    return os.environ.get("LASTFM_API_KEY") or load().get("lastfm_api_key") or None
