"""Turn scrobbles into rows.

`ingest_records` is the single entry point every source goes through: the CSV importer here,
and the last.fm API updater (updater.py: user.getrecenttracks with `from=<latest ts>`), which only
has to yield `Scrobble` objects. Re-importing overlapping data is safe: a scrobble is identified
by (ts, artist, track) and duplicates are ignored. Timestamps are stored at minute precision,
because lastfm-to-csv dates have no seconds; flooring every source to the minute makes the same
listen from the CSV (20:11) and the API (20:11:23) collide instead of counting twice.
"""
import csv
import io
import re
import sqlite3
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, fsutil


@dataclass(frozen=True, slots=True)
class Scrobble:
    artist: str
    track: str
    ts: int  # unix seconds, UTC
    album: str = ""
    artist_mbid: str | None = None
    track_mbid: str | None = None
    album_mbid: str | None = None


def key(text: str) -> str:
    """Identity used for de-duplicating names: NFKC, casefolded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def clean(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").split())


_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
# last.fm's own text form, e.g. "22 May 2014, 20:11" (UTC).
_LASTFM_DATE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")


def parse_date(value: str | None) -> int | None:
    """Parse last.fm text dates, unix seconds/milliseconds or ISO 8601 into unix seconds (UTC)."""
    s = (value or "").strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 100_000_000_000 else n
    m = _LASTFM_DATE.match(s)
    if m:
        day, mon, year, hh, mm, ss = m.groups()
        month = _MONTHS.get(mon.lower())
        if not month:
            return None
        dt = datetime(int(year), month, int(day), int(hh), int(mm), int(ss or 0), tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


_HEADER_ALIASES = {
    "artist": ("artist", "artist name", "artist_name", "artistname"),
    "album": ("album", "album name", "album_name", "albumname"),
    "track": ("track", "name", "title", "track name", "track_name", "song"),
    "date": ("date", "uts", "timestamp", "time", "played at", "played_at", "date#text"),
    "artist_mbid": ("artist_mbid", "artist mbid"),
    "track_mbid": ("track_mbid", "mbid", "track mbid"),
    "album_mbid": ("album_mbid", "album mbid"),
}


def _header_map(row: list[str]) -> dict[str, int] | None:
    lowered = [c.strip().lower() for c in row]
    found = {}
    for field, aliases in _HEADER_ALIASES.items():
        for i, col in enumerate(lowered):
            if col in aliases:
                found[field] = i
                break
    if {"artist", "track", "date"} <= found.keys():
        return found
    return None


def parse_csv(text: str) -> tuple[list[Scrobble], int]:
    """Parse lastfm-to-csv output (no header: artist, album, track, date) or a CSV with a header.

    Returns (scrobbles, skipped_rows). Rows without a usable date (the "now playing" track,
    blank lines) are skipped.
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], 0
    cols = _header_map(rows[0])
    if cols:
        rows = rows[1:]
    else:
        cols = {"artist": 0, "album": 1, "track": 2, "date": 3}

    def cell(row: list[str], field: str) -> str:
        i = cols.get(field)
        return row[i] if i is not None and i < len(row) else ""

    out, skipped = [], 0
    for row in rows:
        artist, track = clean(cell(row, "artist")), clean(cell(row, "track"))
        ts = parse_date(cell(row, "date"))
        if not artist or not track or ts is None:
            skipped += 1
            continue
        out.append(
            Scrobble(
                artist=artist,
                track=track,
                ts=ts,
                album=clean(cell(row, "album")),
                artist_mbid=cell(row, "artist_mbid").strip() or None,
                track_mbid=cell(row, "track_mbid").strip() or None,
                album_mbid=cell(row, "album_mbid").strip() or None,
            )
        )
    return out, skipped


class _Ids:
    """get-or-create caches for artists/tracks/albums within one import."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.artists = {r[0]: r[1] for r in conn.execute("SELECT name_key, id FROM artists")}
        # name rules from merges (maintenance.merge_artists): a known misspelling maps to its artist
        self.artists.update((r[0], r[1]) for r in conn.execute("SELECT name_key, artist_id FROM artist_aliases"))
        self.tracks = {(r[0], r[1]): r[2] for r in conn.execute("SELECT artist_id, title_key, id FROM tracks")}
        self.albums = {(r[0], r[1]): r[2] for r in conn.execute("SELECT artist_id, title_key, id FROM albums")}

    def artist(self, name: str, mbid: str | None) -> int:
        k = key(name)
        if k not in self.artists:
            cur = self.conn.execute("INSERT INTO artists(name, name_key, mbid) VALUES (?, ?, ?)", (name, k, mbid))
            self.artists[k] = cur.lastrowid
        return self.artists[k]

    def track(self, artist_id: int, title: str, mbid: str | None) -> int:
        k = (artist_id, key(title))
        if k not in self.tracks:
            cur = self.conn.execute(
                "INSERT INTO tracks(artist_id, title, title_key, mbid) VALUES (?, ?, ?, ?)", (artist_id, title, k[1], mbid)
            )
            self.tracks[k] = cur.lastrowid
        return self.tracks[k]

    def album(self, artist_id: int, title: str, mbid: str | None) -> int | None:
        if not title:
            return None
        k = (artist_id, key(title))
        if k not in self.albums:
            cur = self.conn.execute(
                "INSERT INTO albums(artist_id, title, title_key, mbid) VALUES (?, ?, ?, ?)", (artist_id, title, k[1], mbid)
            )
            self.albums[k] = cur.lastrowid
        return self.albums[k]


def minute(ts: int) -> int:
    """Floor a unix timestamp to the minute (the precision of last.fm's text dates)."""
    return ts - ts % 60


def local_parts(ts: int) -> tuple[str, int, int]:
    dt = datetime.fromtimestamp(ts, config.TZ)
    return dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday()


def ingest_records(
    conn: sqlite3.Connection,
    records: Iterable[Scrobble],
    *,
    source: str,
    label: str | None = None,
    encoding: str | None = None,
    skipped: int = 0,
) -> dict:
    """Insert scrobbles in one transaction and log the import. Does not rebuild derived tables;
    call `derive.rebuild(conn)` afterwards (import_csv does both).

    `records` is fully read *before* the write transaction starts, so a generator that pages a
    web API never holds the database lock while it waits for the network."""
    records = list(records)
    with conn:
        import_id = conn.execute(
            "INSERT INTO imports(source, label, encoding, started_at, rows_skipped) VALUES (?, ?, ?, ?, ?)",
            (source, label, encoding, int(time.time()), skipped),
        ).lastrowid
        ids = _Ids(conn)
        before = conn.total_changes
        rows, read, lo, hi = [], 0, None, None
        for r in records:
            read += 1
            a = ids.artist(r.artist, r.artist_mbid)
            t = ids.track(a, r.track, r.track_mbid)
            al = ids.album(a, r.album, r.album_mbid)
            ts = minute(r.ts)
            rows.append((ts, a, t, al, import_id, *local_parts(ts)))
            lo = ts if lo is None or ts < lo else lo
            hi = ts if hi is None or ts > hi else hi
        entity_changes = conn.total_changes - before
        conn.executemany(
            "INSERT OR IGNORE INTO scrobbles(ts, artist_id, track_id, album_id, import_id, lday, lhour, lwday)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        added = conn.total_changes - before - entity_changes
        if added:
            db.bump(conn, "scrobbles_version")
        conn.execute(
            "UPDATE imports SET rows_read = ?, rows_added = ?, min_ts = ?, max_ts = ? WHERE id = ?",
            (read, added, lo, hi, import_id),
        )
    return {
        "import_id": import_id, "source": source, "label": label, "encoding": encoding,
        "rows_read": read, "rows_added": added, "rows_skipped": skipped,
        "duplicates": read - added, "min_ts": lo, "max_ts": hi,
    }


def import_csv_text(conn: sqlite3.Connection, text: str, *, label: str, encoding: str) -> dict:
    from . import derive

    records, skipped = parse_csv(text)
    result = ingest_records(conn, records, source="csv", label=label, encoding=encoding, skipped=skipped)
    if result["rows_added"]:
        derive.rebuild(conn)
    return result


def import_csv(conn: sqlite3.Connection, path: str | Path) -> dict:
    text, encoding = fsutil.read_text(path)
    return import_csv_text(conn, text, label=Path(path).name, encoding=encoding)


def recompute_local_time(conn: sqlite3.Connection) -> None:
    """Refresh lday/lhour/lwday after changing MTC_TZ."""
    with conn:
        rows = [(*local_parts(ts), sid) for sid, ts in conn.execute("SELECT id, ts FROM scrobbles")]
        conn.executemany("UPDATE scrobbles SET lday = ?, lhour = ?, lwday = ? WHERE id = ?", rows)
        db.bump(conn, "scrobbles_version")
