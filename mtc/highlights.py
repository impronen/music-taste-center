"""Facts for the Overview's changing sentence, for a local-date range.

Each fact is None unless it clears its bar, so the UI can pick from whatever is worth saying:
short or quiet periods simply offer fewer of them.
"""
import sqlite3
from collections import defaultdict

from .decades import MAX_YEAR, MIN_YEAR
from .insights import DAY, _clip, _range_sql
from .rhythms import _cached

ONE_SONG_MIN_ARTISTS = 5        # artists with a single song in the period
ONE_SONG_MAX_PLAYS = 3          # ... played at most this often: a song on repeat is no brush-off
ONE_SONG_MIN_SHARE = 0.25       # ... as a share of the period's artists
DOUBLE_MIN_PLAYS = 5            # an artist's plays on "their" weekday
DOUBLE_MIN_DAYS = 2             # on this many dates: one long Monday is not a habit
DOUBLE_MIN_SHARE = 1 / 3        # share of the artist's period plays on that weekday (an even week is 1/7)
DOUBLE_MIN_WEEKDAY_SHARE = 0.15  # share of that weekday's plays going to the artist
AGE_MIN_DATED_PLAYS = 20
AGE_MIN_COVERAGE = 0.5          # share of the period's plays with a release date
AGE_MIN_YEARS = 5               # below this the average age is no story
COMEBACK_MIN_DAYS = 365         # time away
COMEBACK_MIN_PLAYS_BEFORE = 3   # one stray play years ago is no relationship
COMEBACK_MIN_PLAYS_AFTER = 3    # plays in the period, from the return on; one shuffle hit is no comeback
COMEBACK_MIN_LISTENING_DAYS = 100  # days you listened to anything during the gap: a break from scrobbling is no comeback
OBSCURE_MIN_PLAYS = 3
OBSCURE_MAX_LISTENERS = 20_000  # global last.fm listeners
OBSCURE_MIN_LISTENERS = 100     # fewer is usually a mismatched or misspelled last.fm page


def highlights(conn: sqlite3.Connection, start: str | None = None, end: str | None = None,
               birth_year: int | None = None) -> dict:
    span = _clip(conn, start, end)
    if span is None or span[0] > span[1]:
        return {"one_song": None, "double_life": None, "song_age": None, "comeback": None, "obscure": None}
    s, e = span
    return _cached(conn, ("highlights", s, e, birth_year), lambda: _highlights(conn, s, e, birth_year), tags=True)


def _highlights(conn, s: str, e: str, birth_year: int | None) -> dict:
    return {"one_song": _one_song(conn, s, e), "double_life": _double_life(conn, s, e),
            "song_age": _song_age(conn, s, e, birth_year), "comeback": _comeback(conn, s, e),
            "obscure": _obscure(conn, s, e)}


def _one_song(conn, s: str, e: str) -> dict | None:
    where, args = _range_sql(s, e)
    one, total = conn.execute(
        "SELECT COALESCE(SUM(n = 1 AND plays <= ?), 0), COUNT(*) FROM"
        f" (SELECT COUNT(DISTINCT track_id) AS n, COUNT(*) AS plays FROM scrobbles s WHERE {where} GROUP BY artist_id)",
        (ONE_SONG_MAX_PLAYS, *args)).fetchone()
    if one < ONE_SONG_MIN_ARTISTS or one / total < ONE_SONG_MIN_SHARE:
        return None
    return {"artists": one, "of": total}


def _double_life(conn, s: str, e: str) -> dict | None:
    """Two weekdays that each belong to a different artist: one who leans on that weekday and also
    takes a good share of it. The pair with the most plays is named, Monday first."""
    where, args = _range_sql(s, e)
    rows = conn.execute(
        "SELECT s.artist_id, a.name, s.lwday, COUNT(*), COUNT(DISTINCT s.lday) FROM scrobbles s"
        f" JOIN artists a ON a.id = s.artist_id WHERE {where} GROUP BY 1, 3", args).fetchall()
    by_artist: dict = defaultdict(int)
    by_weekday: dict = defaultdict(int)
    for aid, _, wd, plays, _ in rows:
        by_artist[aid] += plays
        by_weekday[wd] += plays
    picked = sorted(({"id": aid, "name": name, "weekday": wd, "plays": plays} for aid, name, wd, plays, days in rows
                     if plays >= DOUBLE_MIN_PLAYS and days >= DOUBLE_MIN_DAYS and plays / by_artist[aid] >= DOUBLE_MIN_SHARE
                     and plays / by_weekday[wd] >= DOUBLE_MIN_WEEKDAY_SHARE), key=lambda b: (-b["plays"], b["name"]))
    for i, first in enumerate(picked):
        for second in picked[i + 1:]:
            if first["id"] != second["id"] and first["weekday"] != second["weekday"]:
                return {"days": sorted((first, second), key=lambda b: b["weekday"])}
    return None


def _song_age(conn, s: str, e: str, birth_year: int | None) -> dict | None:
    """How old the music was when played, by release year; a play before its release counts as age 0."""
    where, args = _range_sql(s, e)
    total = conn.execute(f"SELECT COUNT(*) FROM scrobbles s WHERE {where}", args).fetchone()[0]
    dated, age, older = conn.execute(
        "SELECT COUNT(*), AVG(MAX(0, CAST(substr(s.lday, 1, 4) AS INTEGER) - ry)), SUM(ry < ?) FROM scrobbles s"
        " JOIN (SELECT album_id, CAST(substr(release_date, 1, 4) AS INTEGER) AS ry FROM album_info"
        " WHERE release_date IS NOT NULL) ai ON ai.album_id = s.album_id"
        f" WHERE {where} AND ry BETWEEN ? AND ?", (birth_year, *args, MIN_YEAR, MAX_YEAR)).fetchone()
    if dated < AGE_MIN_DATED_PLAYS or dated / total < AGE_MIN_COVERAGE or age < AGE_MIN_YEARS:
        return None
    return {"years": round(age), "before_birth": round(older / dated, 4) if birth_year else None}


def _comeback(conn, s: str, e: str) -> dict | None:
    """The longest absence that ended in the period: every artist's plays are compared with their
    previous one, so an artist can return in the middle of a long period too."""
    rows = conn.execute(
        "SELECT r.artist_id, a.name, r.prev, r.ts, r.lday, (SELECT COUNT(*) FROM scrobbles x"
        " WHERE x.artist_id = r.artist_id AND x.ts >= r.ts AND x.lday <= ?) AS after"
        " FROM (SELECT artist_id, ts, lday, LAG(ts) OVER w AS prev, ROW_NUMBER() OVER w - 1 AS before FROM scrobbles"
        " WINDOW w AS (PARTITION BY artist_id ORDER BY ts)) r JOIN artists a ON a.id = r.artist_id"
        " WHERE r.lday BETWEEN ? AND ? AND r.before >= ? AND r.ts - r.prev >= ? AND after >= ?"
        " ORDER BY r.ts - r.prev DESC, a.name",
        (e, s, e, COMEBACK_MIN_PLAYS_BEFORE, COMEBACK_MIN_DAYS * DAY, COMEBACK_MIN_PLAYS_AFTER)).fetchall()
    for aid, name, prev, ts, lday, after in rows:
        listening = conn.execute("SELECT COUNT(DISTINCT lday) FROM scrobbles WHERE ts > ? AND ts < ?",
                                 (prev, ts)).fetchone()[0]
        if listening >= COMEBACK_MIN_LISTENING_DAYS:
            return {"id": aid, "name": name, "gap_days": (ts - prev) // DAY, "back_on": lday, "plays": after}
    return None


def _obscure(conn, s: str, e: str) -> dict | None:
    """The artist in the period with the fewest last.fm listeners, rounded to two digits ("about")."""
    where, args = _range_sql(s, e)
    row = conn.execute(
        "SELECT a.id, a.name, ai.listeners, COUNT(*) AS plays FROM scrobbles s JOIN artists a ON a.id = s.artist_id"
        f" JOIN artist_info ai ON ai.artist_id = a.id WHERE {where} AND ai.status = 'ok' AND ai.listeners BETWEEN ? AND ?"
        " GROUP BY a.id HAVING plays >= ? ORDER BY ai.listeners, a.name LIMIT 1",
        (*args, OBSCURE_MIN_LISTENERS, OBSCURE_MAX_LISTENERS, OBSCURE_MIN_PLAYS)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "listeners": round(row[2], 2 - len(str(row[2]))), "plays": row[3]}
