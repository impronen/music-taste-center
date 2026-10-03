"""last.fm read-only metadata client (https://www.last.fm/api).

Quirks handled here:
- every method answers HTTP 200 or 4xx with {"error": code, "message": ...};
- a list with one element comes back as a bare object, and an empty one as "" or missing;
- numbers are strings; album.getInfo has no release date (despite the docs) - those come
  from MusicBrainz, see musicbrainz.py.
"""
from .ingest import Scrobble, clean
from .webapi import ApiError, Fatal, JsonApi, NotFound, Transient

# https://www.last.fm/api/errorcodes
NOT_FOUND = {6}
FATAL = {4, 10, 26}  # authentication failed, invalid API key, suspended API key
TRANSIENT = {8, 11, 16, 29}  # operation failed, offline, temporarily unavailable, rate limit


def as_list(value) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def best_image(images) -> str | None:
    """Largest non-empty image URL. last.fm serves a grey star placeholder for most artists."""
    urls = [i.get("#text") for i in as_list(images) if isinstance(i, dict) and i.get("#text")]
    url = urls[-1] if urls else None
    return None if url and "2a96cbd8b46e442fc41c2b86b821562f" in url else url


def tag_list(container) -> list[tuple[str, int]]:
    """[(name, weight 0-100)] from a {"tag": [...]} block. Tags without counts (getInfo's
    top-5) get descending pseudo-weights so their order survives."""
    tags = as_list((container or {}).get("tag") if isinstance(container, dict) else None)
    out = []
    for i, t in enumerate(tags):
        if isinstance(t, dict) and t.get("name"):
            count = to_int(t.get("count"))
            out.append((t["name"], count if count is not None else max(0, 100 - 20 * i)))
    return out


class LastFm(JsonApi):
    base_url = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str, *args, **kwargs):
        if not api_key:
            raise Fatal("no last.fm API key: run `python -m mtc set-key <key>` or set LASTFM_API_KEY")
        super().__init__(*args, **kwargs)
        self.api_key = api_key

    def call(self, method: str, **params: str) -> dict:
        return self.get("", {"method": method, "format": "json", "api_key": self.api_key, "autocorrect": "1", **params})

    def _interpret(self, status: int, data: dict | None) -> dict:
        if isinstance(data, dict) and "error" in data:
            code, msg = data.get("error"), data.get("message", "")
            if code in NOT_FOUND:
                raise NotFound(msg)
            if code in FATAL:
                raise Fatal(f"last.fm error {code}: {msg}")
            if code in TRANSIENT:
                raise Transient(f"last.fm error {code}: {msg}")
            raise ApiError(f"last.fm error {code}: {msg}")
        if status >= 500 or status == 429 or data is None:
            raise Transient(f"HTTP {status}")
        if status != 200:
            raise ApiError(f"HTTP {status}")
        return data

    # ---- normalized calls ----

    def artist_info(self, artist: str) -> dict:
        a = self.call("artist.getInfo", artist=artist).get("artist") or {}
        stats = a.get("stats") or {}
        return {
            "lastfm_name": a.get("name"),
            "mbid": a.get("mbid") or None,
            "url": a.get("url"),
            "image_url": best_image(a.get("image")),
            "listeners": to_int(stats.get("listeners")),
            "playcount": to_int(stats.get("playcount")),
            "bio_summary": ((a.get("bio") or {}).get("summary") or "").strip() or None,
            "tags": tag_list(a.get("tags")),
        }

    def artist_tags(self, artist: str) -> list[tuple[str, int]]:
        return tag_list(self.call("artist.getTopTags", artist=artist).get("toptags"))

    def album_info(self, artist: str, album: str) -> dict:
        al = self.call("album.getInfo", artist=artist, album=album).get("album") or {}
        tracks = (al.get("tracks") or {}).get("track") if isinstance(al.get("tracks"), dict) else None
        return {
            "lastfm_name": al.get("name"),
            "mbid": al.get("mbid") or None,
            "url": al.get("url"),
            "image_url": best_image(al.get("image")),
            "listeners": to_int(al.get("listeners")),
            "playcount": to_int(al.get("playcount")),
            "n_tracks": len(as_list(tracks)) or None,
            "tags": tag_list(al.get("tags")),
        }

    def album_tags(self, artist: str, album: str) -> list[tuple[str, int]]:
        return tag_list(self.call("album.getTopTags", artist=artist, album=album).get("toptags"))

    def recent_tracks_page(self, user: str, since: int | None, page: int, limit: int = 200) -> tuple[list[Scrobble], int]:
        """One page of user.getRecentTracks, newest first, after `since` (unix seconds) when given.
        Returns (scrobbles, total pages). The "now playing" entry has no date and is left out."""
        params = {"user": user, "limit": str(limit), "page": str(page)}
        if since:
            params["from"] = str(since)
        rt = self.call("user.getRecentTracks", **params).get("recenttracks")
        if not isinstance(rt, dict):  # not an empty page: an odd answer must not look like "nothing new"
            raise ApiError("last.fm answered without a recenttracks block")
        out = []
        for t in as_list(rt.get("track")):
            ts = to_int((t.get("date") or {}).get("uts")) if isinstance(t, dict) else None
            artist = clean((t.get("artist") or {}).get("#text") or (t.get("artist") or {}).get("name")) if ts else ""
            title = clean(t.get("name")) if ts else ""
            if not (ts and artist and title):
                continue
            out.append(Scrobble(
                artist=artist, track=title, ts=ts, album=clean((t.get("album") or {}).get("#text")),
                artist_mbid=(t.get("artist") or {}).get("mbid") or None, track_mbid=t.get("mbid") or None,
                album_mbid=(t.get("album") or {}).get("mbid") or None))
        return out, to_int((rt.get("@attr") or {}).get("totalPages")) or 0
