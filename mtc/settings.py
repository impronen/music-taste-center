"""Local, gitignored settings (data/settings.json): the last.fm API key and username, and an optional birth year."""
import hashlib
import json
import os
import re
import threading
from datetime import date

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


_lock = threading.Lock()


def update(**changes) -> dict:
    """Read-modify-write under a lock (the API and the background fetch both write settings).
    A value of None removes the setting."""
    with _lock:
        values = load()
        for k, v in changes.items():
            if v is None:
                values.pop(k, None)
            else:
                values[k] = v
        save(values)
        return values


def key_fingerprint(key: str | None) -> str | None:
    """Identifies which key was verified without storing it twice."""
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else None


def mark_key_works(key: str | None) -> None:
    update(lastfm_key_ok=key_fingerprint(key), lastfm_key_ok_at=None)


def key_works() -> bool:
    key = lastfm_api_key()
    return bool(key) and load().get("lastfm_key_ok") == key_fingerprint(key)


def lastfm_api_key() -> str | None:
    return os.environ.get("LASTFM_API_KEY") or load().get("lastfm_api_key") or None


def lastfm_username() -> str | None:
    """The account whose scrobbles a live updater fetches (user.getRecentTracks)."""
    return os.environ.get("LASTFM_USER") or load().get("lastfm_username") or None


def set_lastfm_username(name: str) -> str:
    name = name.strip()
    if not USERNAME_RE.match(name):
        raise ValueError(f"not a valid last.fm username: {name!r}")
    update(lastfm_username=name)
    return name


MIN_BIRTH_YEAR = 1900


def birth_year() -> int | None:
    """Optional: lets the Decades page tell records from before you were born from your own years."""
    value = load().get("birth_year")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def set_birth_year(year: int | None) -> int | None:
    """None clears it."""
    if year is not None and not (isinstance(year, int) and MIN_BIRTH_YEAR <= year <= date.today().year):
        raise ValueError(f"not a plausible birth year: {year!r}")
    update(birth_year=year)
    return year
