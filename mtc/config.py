"""Runtime settings, overridable with environment variables."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("MTC_DB", ROOT / "data" / "mtc.db"))
STATIC_DIR = ROOT / "static"

# last.fm timestamps are UTC; local-time views (listening clock, days, streaks) use this zone.
TZ = ZoneInfo(os.environ.get("MTC_TZ", "Europe/Helsinki"))

# Gap between two scrobbles that starts a new listening session.
SESSION_GAP_S = 30 * 60
# Artists first heard this soon after history begins were already known, not "discovered".
PREHISTORY_DAYS = 30
# Sessions with more distinct artists than this only count their most-played ones for links.
LINK_MAX_ARTISTS_PER_SESSION = 30
LINK_MIN_SHARED_SESSIONS = 3

# ---- external metadata (python -m mtc enrich) ----
# API key: env LASTFM_API_KEY, else data/settings.json {"lastfm_api_key": "..."} (python -m mtc set-key).
SETTINGS_PATH = Path(os.environ.get("MTC_SETTINGS", DB_PATH.parent / "settings.json"))
USER_AGENT = "TasteCenter/0.2 ( https://github.com/impronen/music-taste-center )"
# last.fm asks not to make "several calls per second" continuously; MusicBrainz allows 1/s.
LASTFM_MIN_INTERVAL_S = 0.5
MUSICBRAINZ_MIN_INTERVAL_S = 1.1
METADATA_TTL_DAYS = 120
