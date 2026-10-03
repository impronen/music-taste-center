"""Fetch tags and metadata for your artists and albums, most-played first.

Three phases, each resumable (every item is committed on its own, so Ctrl+C loses nothing):
  artists   last.fm artist.getInfo + artist.getTopTags    (2 calls each)
  albums    last.fm album.getInfo + album.getTopTags      (2 calls each)
  releases  MusicBrainz release-group date for albums     (1-2 calls each, 1/s)
Items are refetched after config.METADATA_TTL_DAYS; failed ones on the next run.
"""
import sqlite3
import threading
import time
from collections.abc import Callable

from . import config
from . import tags as tagmod
from .ingest import key
from .lastfm import LastFm
from .musicbrainz import MusicBrainz
from .webapi import ApiError, Fatal, NotFound

ALBUM_MIN_PLAYS = 3  # skip albums you've barely touched
MAX_TAGS = 25  # per artist/album; keeps storage far below last.fm's 100 MB cap

Log = Callable[[str], None]
Progress = Callable[[str, int, int, str, str | None], None]


def _now() -> int:
    return int(time.time())


def _cutoff(refresh_days: float) -> int:
    return _now() - int(refresh_days * 86400)


def _limit(sql: str, limit: int | None) -> str:
    return sql + (f" LIMIT {int(limit):d}" if limit is not None else "")


# ---------------------------------------------------------------- work lists


def pending_artists(conn, limit=None, refresh_days=config.METADATA_TTL_DAYS) -> list[tuple[int, str]]:
    return conn.execute(_limit(
        "SELECT a.id, a.name FROM artists a JOIN artist_stats st ON st.artist_id = a.id"
        " LEFT JOIN artist_info i ON i.artist_id = a.id"
        " WHERE i.artist_id IS NULL OR i.status = 'error' OR i.fetched_at < ?"
        " ORDER BY st.plays DESC, a.id", limit), (_cutoff(refresh_days),)).fetchall()


def pending_albums(conn, limit=None, refresh_days=config.METADATA_TTL_DAYS) -> list[tuple[int, str, str]]:
    return conn.execute(_limit(
        "SELECT al.id, a.name, al.title FROM albums al JOIN artists a ON a.id = al.artist_id"
        " JOIN (SELECT album_id, COUNT(*) AS plays FROM scrobbles WHERE album_id IS NOT NULL GROUP BY album_id) p"
        "  ON p.album_id = al.id"
        " LEFT JOIN album_info i ON i.album_id = al.id"
        " WHERE p.plays >= ? AND (i.album_id IS NULL OR i.status = 'error' OR i.fetched_at < ?)"
        " ORDER BY p.plays DESC, al.id", limit), (ALBUM_MIN_PLAYS, _cutoff(refresh_days))).fetchall()


def pending_releases(conn, limit=None, refresh_days=config.METADATA_TTL_DAYS) -> list[tuple]:
    """Albums last.fm knows that have no MusicBrainz answer yet (or a stale/failed one)."""
    return conn.execute(_limit(
        "SELECT al.id, a.name, al.title, i.mbid FROM album_info i JOIN albums al ON al.id = i.album_id"
        " JOIN artists a ON a.id = al.artist_id"
        " LEFT JOIN (SELECT album_id, COUNT(*) AS plays FROM scrobbles GROUP BY album_id) p ON p.album_id = al.id"
        " WHERE i.status = 'ok' AND (i.mb_fetched_at IS NULL OR i.mb_status = 'error' OR i.mb_fetched_at < ?)"
        " ORDER BY p.plays DESC, al.id", limit), (_cutoff(refresh_days),)).fetchall()


# ---------------------------------------------------------------- storage helpers


def _tag_weights(conn, tags: list[tuple[str, int]], skip: set[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for name, weight in tags[:MAX_TAGS]:
        n = tagmod.normalize(name)
        if not n or n in skip:
            continue
        # "post-rock", "post rock" and "postrock" are one tag (first spelling seen wins)
        row = conn.execute("SELECT id FROM tags WHERE replace(replace(name, ' ', ''), '-', '') = ? ORDER BY id LIMIT 1",
                           (n.replace(" ", "").replace("-", ""),)).fetchone()
        tid = row[0] if row else conn.execute(
            "INSERT INTO tags(name, kind) VALUES (?, ?)", (n, tagmod.classify(n))).lastrowid
        out[tid] = max(out.get(tid, 0), int(weight))
    return out


def _fetch(fn: Callable[[], tuple]) -> tuple[str, tuple | None, str | None]:
    """Run a fetch; map outcomes to (status, payload, error). Fatal propagates."""
    try:
        return "ok", fn(), None
    except NotFound as exc:
        return "not_found", None, str(exc)
    except Fatal:
        raise
    except ApiError as exc:
        return "error", None, str(exc)


def enrich_artist(conn: sqlite3.Connection, lf: LastFm, artist_id: int, name: str) -> tuple[str, int]:
    def fetch():
        info = lf.artist_info(name)
        return info, lf.artist_tags(name) or info["tags"]

    status, payload, error = _fetch(fetch)
    now = _now()
    with conn:
        if status != "ok":
            # Never let a failed refresh wipe good data; a 'not_found' replaces it though.
            conn.execute(
                "INSERT INTO artist_info(artist_id, status, error, fetched_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(artist_id) DO UPDATE SET status = excluded.status, error = excluded.error,"
                " fetched_at = excluded.fetched_at WHERE artist_info.status != 'ok' OR excluded.status = 'not_found'",
                (artist_id, status, error, now))
            return status, 0
        info, top = payload
        conn.execute(
            "INSERT INTO artist_info(artist_id, status, lastfm_name, mbid, url, image_url, listeners, playcount,"
            " bio_summary, error, fetched_at, tags_fetched_at) VALUES (?, 'ok', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)"
            " ON CONFLICT(artist_id) DO UPDATE SET status = 'ok', lastfm_name = excluded.lastfm_name,"
            " mbid = excluded.mbid, url = excluded.url, image_url = excluded.image_url, listeners = excluded.listeners,"
            " playcount = excluded.playcount, bio_summary = excluded.bio_summary, error = NULL,"
            " fetched_at = excluded.fetched_at, tags_fetched_at = excluded.tags_fetched_at",
            (artist_id, info["lastfm_name"], info["mbid"], info["url"], info["image_url"], info["listeners"],
             info["playcount"], info["bio_summary"], now, now))
        weights = _tag_weights(conn, top, {key(name), key(info["lastfm_name"] or "")})
        conn.execute("DELETE FROM artist_tags WHERE artist_id = ?", (artist_id,))
        conn.executemany("INSERT INTO artist_tags(artist_id, tag_id, weight) VALUES (?, ?, ?)",
                         [(artist_id, t, w) for t, w in weights.items()])
    return status, len(weights)


def enrich_album(conn: sqlite3.Connection, lf: LastFm, album_id: int, artist: str, title: str) -> tuple[str, int]:
    def fetch():
        info = lf.album_info(artist, title)
        return info, lf.album_tags(artist, title) or info["tags"]

    status, payload, error = _fetch(fetch)
    now = _now()
    with conn:
        if status != "ok":
            conn.execute(
                "INSERT INTO album_info(album_id, status, error, fetched_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(album_id) DO UPDATE SET status = excluded.status, error = excluded.error,"
                " fetched_at = excluded.fetched_at WHERE album_info.status != 'ok' OR excluded.status = 'not_found'",
                (album_id, status, error, now))
            return status, 0
        info, top = payload
        conn.execute(
            "INSERT INTO album_info(album_id, status, lastfm_name, mbid, url, image_url, listeners, playcount, n_tracks,"
            " error, fetched_at, tags_fetched_at) VALUES (?, 'ok', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)"
            " ON CONFLICT(album_id) DO UPDATE SET status = 'ok', lastfm_name = excluded.lastfm_name,"
            " mbid = excluded.mbid, url = excluded.url, image_url = excluded.image_url, listeners = excluded.listeners,"
            " playcount = excluded.playcount, n_tracks = excluded.n_tracks, error = NULL,"
            " fetched_at = excluded.fetched_at, tags_fetched_at = excluded.tags_fetched_at",
            (album_id, info["lastfm_name"], info["mbid"], info["url"], info["image_url"], info["listeners"],
             info["playcount"], info["n_tracks"], now, now))
        weights = _tag_weights(conn, top, {key(artist), key(title)})
        conn.execute("DELETE FROM album_tags WHERE album_id = ?", (album_id,))
        conn.executemany("INSERT INTO album_tags(album_id, tag_id, weight) VALUES (?, ?, ?)",
                         [(album_id, t, w) for t, w in weights.items()])
        _apply_tag_year(conn, album_id)
    return status, len(weights)


def _apply_tag_year(conn, album_id: int) -> None:
    """Use the strongest year tag as the release date unless MusicBrainz already gave one."""
    row = conn.execute(
        "SELECT t.name FROM album_tags x JOIN tags t ON t.id = x.tag_id WHERE x.album_id = ? AND t.kind = 'year'"
        " ORDER BY x.weight DESC, t.name LIMIT 1", (album_id,)).fetchone()
    conn.execute(
        "UPDATE album_info SET release_date = ?, release_date_source = CASE WHEN ? IS NULL THEN NULL ELSE 'tag' END"
        " WHERE album_id = ? AND (release_date_source IS NULL OR release_date_source = 'tag')",
        (row[0] if row else None, row[0] if row else None, album_id))


def enrich_release(conn: sqlite3.Connection, mb: MusicBrainz, album_id: int, artist: str, title: str,
                   mbid: str | None) -> str:
    def fetch():
        if mbid:
            try:
                return (mb.release_group_of_release(mbid),)
            except NotFound:
                pass  # last.fm's MBID can be stale; fall back to a search
        return (mb.search_release_group(artist, title),)

    status, payload, error = _fetch(fetch)
    now = _now()
    with conn:
        if status == "ok" and payload[0]["release_date"]:
            g = payload[0]
            conn.execute(
                "UPDATE album_info SET release_date = ?, release_date_source = 'musicbrainz', release_group_mbid = ?,"
                " release_type = ?, mb_status = 'ok', mb_fetched_at = ? WHERE album_id = ?",
                (g["release_date"], g["release_group_mbid"], g["release_type"], now, album_id))
        else:
            if status == "ok":  # matched, but MusicBrainz has no date
                conn.execute("UPDATE album_info SET release_group_mbid = ?, release_type = ? WHERE album_id = ?",
                             (payload[0]["release_group_mbid"], payload[0]["release_type"], album_id))
            conn.execute("UPDATE album_info SET mb_status = ?, mb_fetched_at = ? WHERE album_id = ?",
                         ("not_found" if status == "ok" else status, now, album_id))
    return status


# ---------------------------------------------------------------- orchestration


def run(
    conn: sqlite3.Connection,
    *,
    lastfm: LastFm | None = None,
    musicbrainz: MusicBrainz | None = None,
    artists: int | None = None,
    albums: int | None = None,
    releases: int | None = None,
    refresh_days: float = config.METADATA_TTL_DAYS,
    log: Log = print,
    progress: Progress | None = None,
    stop: threading.Event | None = None,
) -> dict:
    """Limits: None = everything pending, 0 = skip the phase.

    progress(phase, done, total, detail, status) is called when a phase starts (done=0) and
    after every item. Setting `stop` ends the run between items; the summary then has
    "stopped": True.
    """
    summary: dict = {}

    def phase(name, items, per_item_s, work):
        counts: dict[str, int] = {}
        summary[name] = counts
        if progress:
            progress(name, 0, len(items), "", None)
        if not items:
            log(f"{name}: nothing to fetch")
            return
        log(f"{name}: {len(items)} to fetch, about {_eta(len(items) * per_item_s)}")
        for i, item in enumerate(items, 1):
            if stop is not None and stop.is_set():
                raise _Stopped
            status, detail = work(item)
            counts[status] = counts.get(status, 0) + 1
            log(f"  [{i}/{len(items)}] {detail} — {status}")
            if progress:
                progress(name, i, len(items), detail, status)

    try:
        _phases(conn, phase, lastfm, musicbrainz, artists, albums, releases, refresh_days)
    except _Stopped:
        log("stopped; everything fetched so far is saved")
        summary["stopped"] = True
    return summary


class _Stopped(Exception):
    pass


def _phases(conn, phase, lastfm, musicbrainz, artists, albums, releases, refresh_days) -> None:
    lf_interval = lastfm.min_interval if lastfm else config.LASTFM_MIN_INTERVAL_S
    if artists != 0:
        items = pending_artists(conn, artists, refresh_days)
        if items:
            lastfm = lastfm or LastFm(_key(), min_interval=config.LASTFM_MIN_INTERVAL_S)

        def do_artist(it):
            if _gone(conn, "artists", it[0]):
                return "merged", it[1]
            status, n = enrich_artist(conn, lastfm, it[0], it[1])
            return status, f"{it[1]}" + (f" · {n} tags" if status == "ok" else "")
        phase("artists", items, 2 * lf_interval, do_artist)
    if albums != 0:
        items = pending_albums(conn, albums, refresh_days)
        if items:
            lastfm = lastfm or LastFm(_key(), min_interval=config.LASTFM_MIN_INTERVAL_S)

        def do_album(it):
            if _gone(conn, "albums", it[0]):
                return "merged", f"{it[1]} – {it[2]}"
            status, n = enrich_album(conn, lastfm, it[0], it[1], it[2])
            return status, f"{it[1]} – {it[2]}" + (f" · {n} tags" if status == "ok" else "")
        phase("albums", items, 2 * lf_interval, do_album)
    if releases != 0:
        items = pending_releases(conn, releases, refresh_days)
        if items:
            musicbrainz = musicbrainz or MusicBrainz(min_interval=config.MUSICBRAINZ_MIN_INTERVAL_S)

        def do_release(it):
            if _gone(conn, "albums", it[0]):
                return "merged", f"{it[1]} – {it[2]}"
            status = enrich_release(conn, musicbrainz, *it)
            date = conn.execute("SELECT release_date FROM album_info WHERE album_id = ?", (it[0],)).fetchone()[0]
            return status, f"{it[1]} – {it[2]}" + (f" · {date}" if date else "")
        phase("releases", items, 1.5 * config.MUSICBRAINZ_MIN_INTERVAL_S, do_release)


def _gone(conn, table: str, item_id: int) -> bool:
    """True when the item was merged away (maintenance) after the work list was made."""
    return conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (item_id,)).fetchone() is None


def _key() -> str:
    from . import settings

    k = settings.lastfm_api_key()
    if not k:
        raise Fatal("no last.fm API key: get one at https://www.last.fm/api/account/create, then run"
                    " `python -m mtc set-key <key>`")
    return k


def _eta(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def status(conn: sqlite3.Connection) -> dict:
    """Coverage numbers for the UI."""
    def one(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    return {
        "artists": one("SELECT COUNT(*) FROM artist_stats"),
        "artists_done": one("SELECT COUNT(*) FROM artist_info WHERE status IN ('ok', 'not_found')"),
        "artists_tagged": one("SELECT COUNT(DISTINCT artist_id) FROM artist_tags"),
        "albums_eligible": one("SELECT COUNT(*) FROM (SELECT album_id FROM scrobbles WHERE album_id IS NOT NULL"
                               " GROUP BY album_id HAVING COUNT(*) >= ?)", ALBUM_MIN_PLAYS),
        "albums_done": one("SELECT COUNT(*) FROM album_info WHERE status IN ('ok', 'not_found')"),
        "albums_dated": one("SELECT COUNT(*) FROM album_info WHERE release_date IS NOT NULL"),
        "albums_with_cover": one("SELECT COUNT(*) FROM album_info WHERE image_url IS NOT NULL"),
        "plays_covered": one("SELECT COUNT(*) FROM scrobbles WHERE artist_id IN (SELECT artist_id FROM artist_tags)"),
        "plays": one("SELECT COUNT(*) FROM scrobbles"),
        "last_fetch": one("SELECT MAX(fetched_at) FROM artist_info"),
    }
