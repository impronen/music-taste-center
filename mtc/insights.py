"""Read-side queries. Every function takes a connection and returns JSON-ready data.

"Now" is the newest scrobble, not the wall clock, so an older export still yields
meaningful "recent" / "forgotten" answers.
"""
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import db

DAY = 86400


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


def _span(conn) -> tuple[int | None, int | None]:
    lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM scrobbles").fetchone()
    return lo, hi


def _range_sql(start: str | None, end: str | None, col: str = "s.lday") -> tuple[str, list]:
    """WHERE fragment for an inclusive local-date range (YYYY-MM-DD)."""
    parts, args = [], []
    if start:
        parts.append(f"{col} >= ?")
        args.append(start)
    if end:
        parts.append(f"{col} <= ?")
        args.append(end)
    return (" AND ".join(parts) or "1=1"), args


# ---------------------------------------------------------------- overview


def _streaks(days: list[str]) -> tuple[int, int, str | None]:
    """(longest, latest, longest_end) runs of consecutive listening days."""
    longest = run = 0
    longest_end = None
    prev = None
    for d in days:
        cur = date.fromisoformat(d)
        run = run + 1 if prev is not None and cur - prev == timedelta(days=1) else 1
        if run > longest:
            longest, longest_end = run, d
        prev = cur
    return longest, run, longest_end


def overview(conn: sqlite3.Connection) -> dict:
    lo, hi = _span(conn)
    if lo is None:
        return {"empty": True}
    total = conn.execute(
        "SELECT COUNT(*) AS plays, COUNT(DISTINCT artist_id) AS artists, COUNT(DISTINCT track_id) AS tracks,"
        " COUNT(DISTINCT album_id) AS albums, COUNT(DISTINCT lday) AS days FROM scrobbles"
    ).fetchone()
    days = [r[0] for r in conn.execute("SELECT DISTINCT lday FROM scrobbles ORDER BY lday")]
    longest, latest, longest_end = _streaks(days)
    calendar_days = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1
    top10 = conn.execute("SELECT SUM(plays) FROM (SELECT plays FROM artist_stats ORDER BY plays DESC LIMIT 10)").fetchone()[0]
    recent_start = hi - 30 * DAY
    recent = _rows(
        conn.execute(
            "SELECT a.id, a.name, COUNT(*) AS plays FROM scrobbles s JOIN artists a ON a.id = s.artist_id"
            " WHERE s.ts > ? GROUP BY a.id ORDER BY plays DESC LIMIT 5",
            (recent_start,),
        )
    )
    new_recent = conn.execute(
        "SELECT COUNT(*) FROM artist_stats WHERE first_ts > ? AND prehistory = 0", (recent_start,)
    ).fetchone()[0]
    return {
        "empty": False,
        "plays": total["plays"], "artists": total["artists"], "tracks": total["tracks"], "albums": total["albums"],
        "listening_days": total["days"], "calendar_days": calendar_days,
        "first_ts": lo, "last_ts": hi,
        "per_day": total["plays"] / calendar_days,
        "per_listening_day": total["plays"] / total["days"],
        "longest_streak": longest, "longest_streak_end": longest_end,
        "current_streak": latest,  # run ending on the newest listening day
        "top10_share": (top10 or 0) / total["plays"],
        "recent_top": recent, "new_artists_30d": new_recent,
        "derived_at": db.get_meta(conn, "derived_at"),
    }


# ---------------------------------------------------------------- timeline


def timeline(conn: sqlite3.Connection) -> list[dict]:
    """Per month: plays, new artists discovered, and the share of plays going to artists
    first heard within the preceding 12 months ("novelty")."""
    plays = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT substr(s.lday, 1, 7) AS m, COUNT(*),"
            " SUM(CASE WHEN st.prehistory = 0 AND s.ts - st.first_ts < 365 * 86400 THEN 1 ELSE 0 END)"
            " FROM scrobbles s JOIN artist_stats st ON st.artist_id = s.artist_id GROUP BY m"
        )
    }
    new = dict(
        conn.execute("SELECT substr(first_lday, 1, 7), COUNT(*) FROM artist_stats WHERE prehistory = 0 GROUP BY 1")
    )
    top = {}
    for m, name, aid, n in conn.execute(
        "SELECT m, name, artist_id, n FROM ("
        " SELECT substr(s.lday, 1, 7) AS m, s.artist_id, COUNT(*) AS n,"
        "  ROW_NUMBER() OVER (PARTITION BY substr(s.lday, 1, 7) ORDER BY COUNT(*) DESC) AS rk"
        " FROM scrobbles s GROUP BY m, s.artist_id) t JOIN artists a ON a.id = t.artist_id WHERE rk = 1"
    ):
        top[m] = {"id": aid, "name": name, "plays": n}
    if not plays:
        return []
    out = []
    for m in _months(min(plays), max(plays)):
        p, novel = plays.get(m, (0, 0))
        out.append({"month": m, "plays": p, "new_artists": new.get(m, 0), "novelty": (novel / p) if p else None,
                    "top": top.get(m)})
    return out


def _months(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------------------------------------------------------------- any period

DAILY_MAX_DAYS = 120  # activity is per day up to this span, per month beyond


def _clip(conn, start: str | None, end: str | None) -> tuple[str, str] | None:
    """The requested local-date range clipped to the data; None when there is no data."""
    lo, hi = conn.execute("SELECT MIN(lday), MAX(lday) FROM scrobbles").fetchone()
    if lo is None:
        return None
    return max(start or lo, lo), min(end or hi, hi)


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days + 1


def _period_numbers(conn, start: str, end: str) -> dict:
    where, args = _range_sql(start, end)
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT artist_id), COUNT(DISTINCT track_id), COUNT(DISTINCT album_id),"
        f" COUNT(DISTINCT lday) FROM scrobbles s WHERE {where}", args).fetchone()
    new = conn.execute("SELECT COUNT(*) FROM artist_stats WHERE prehistory = 0 AND first_lday BETWEEN ? AND ?",
                       (start, end)).fetchone()[0]
    return {"plays": row[0], "artists": row[1], "tracks": row[2], "albums": row[3], "listening_days": row[4],
            "new_artists": new}


def _previous(s: str, e: str) -> tuple[str, str]:
    """The period just before s..e: the same calendar months for whole-month ranges (so 2024
    compares with 2023, March with February), otherwise the same number of days."""
    ds, de = date.fromisoformat(s), date.fromisoformat(e)
    if ds.day == 1 and (de + timedelta(days=1)).day == 1:
        months = (de.year - ds.year) * 12 + de.month - ds.month + 1
        y, m = divmod(ds.year * 12 + ds.month - 1 - months, 12)
        return date(y, m + 1, 1).isoformat(), (ds - timedelta(days=1)).isoformat()
    return (ds - (de - ds) - timedelta(days=1)).isoformat(), (ds - timedelta(days=1)).isoformat()


def summary(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict:
    """Headline numbers for a local-date range (default: everything), with the previous
    period of the same length for comparison and the artists first heard in the range."""
    span = _clip(conn, start, end)
    if span is None:
        return {"empty": True}
    s, e = span
    if s > e:
        return {"empty": False, "start": s, "end": e, "plays": 0, "artists": 0, "tracks": 0, "albums": 0,
                "listening_days": 0, "new_artists": 0, "calendar_days": 0, "per_day": 0, "top10_share": 0,
                "longest_streak": 0, "longest_streak_end": None, "previous": None, "discoveries": []}
    out = {"empty": False, "start": s, "end": e, **_period_numbers(conn, s, e)}
    out["calendar_days"] = _days_between(s, e)
    out["per_day"] = out["plays"] / out["calendar_days"]
    where, args = _range_sql(s, e)
    top10 = conn.execute(f"SELECT SUM(n) FROM (SELECT COUNT(*) AS n FROM scrobbles s WHERE {where}"
                         " GROUP BY artist_id ORDER BY n DESC LIMIT 10)", args).fetchone()[0]
    out["top10_share"] = (top10 or 0) / out["plays"] if out["plays"] else 0
    days = [r[0] for r in conn.execute(f"SELECT DISTINCT lday FROM scrobbles s WHERE {where} ORDER BY lday", args)]
    out["longest_streak"], _, out["longest_streak_end"] = _streaks(days) if days else (0, 0, None)
    out["previous"] = None
    lo = conn.execute("SELECT MIN(lday) FROM scrobbles").fetchone()[0]
    if start and s > lo:  # an explicit range with history before it
        ps, pe = _previous(s, e)
        out["previous"] = {"start": ps, "end": pe, "partial": ps < lo, **_period_numbers(conn, ps, pe)}
    out["discoveries"] = _rows(conn.execute(
        "SELECT a.id, a.name, COUNT(*) AS plays, st.first_ts, g.id AS gateway_id, g.name AS gateway_name"
        " FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " JOIN scrobbles s ON s.artist_id = st.artist_id AND s.lday BETWEEN ? AND ?"
        " LEFT JOIN artists g ON g.id = st.gateway_id"
        " WHERE st.prehistory = 0 AND st.first_lday BETWEEN ? AND ?"
        " GROUP BY a.id ORDER BY plays DESC, a.name LIMIT 10", (s, e, s, e)))
    return out


def activity(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict:
    """Plays per day (spans up to DAILY_MAX_DAYS) or per month, zero-filled, with each
    bucket's top artist and number of new artists."""
    span = _clip(conn, start, end)
    if span is None or span[0] > span[1]:
        return {"unit": "day", "items": []}
    s, e = span
    unit = "day" if _days_between(s, e) <= DAILY_MAX_DAYS else "month"
    b = "s.lday" if unit == "day" else "substr(s.lday, 1, 7)"
    where, args = _range_sql(s, e)
    plays = dict(conn.execute(f"SELECT {b}, COUNT(*) FROM scrobbles s WHERE {where} GROUP BY 1", args))
    nb = "first_lday" if unit == "day" else "substr(first_lday, 1, 7)"
    new = dict(conn.execute(f"SELECT {nb}, COUNT(*) FROM artist_stats WHERE prehistory = 0"
                            " AND first_lday BETWEEN ? AND ? GROUP BY 1", (s, e)))
    top = {k: {"id": aid, "name": name, "plays": n} for k, aid, name, n in conn.execute(
        f"SELECT k, artist_id, name, n FROM (SELECT {b} AS k, s.artist_id, COUNT(*) AS n,"
        f" ROW_NUMBER() OVER (PARTITION BY {b} ORDER BY COUNT(*) DESC) AS rk FROM scrobbles s WHERE {where}"
        " GROUP BY k, s.artist_id) t JOIN artists a ON a.id = t.artist_id WHERE rk = 1", args)}
    if unit == "day":
        d0 = date.fromisoformat(s)
        keys = [(d0 + timedelta(days=i)).isoformat() for i in range(_days_between(s, e))]
    else:
        keys = _months(s[:7], e[:7])
    return {"unit": unit, "items": [{"key": k, "plays": plays.get(k, 0), "new_artists": new.get(k, 0), "top": top.get(k)}
                                    for k in keys]}


# ---------------------------------------------------------------- top lists & clock


def top(conn: sqlite3.Connection, kind: str, start: str | None, end: str | None, limit: int = 25) -> list[dict]:
    where, args = _range_sql(start, end)
    if kind == "artist":
        sql = (
            "SELECT a.id, a.name, COUNT(*) AS plays FROM scrobbles s JOIN artists a ON a.id = s.artist_id"
            f" WHERE {where} GROUP BY a.id ORDER BY plays DESC, a.name LIMIT ?"
        )
    elif kind == "track":
        sql = (
            "SELECT t.id, t.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays FROM scrobbles s"
            " JOIN tracks t ON t.id = s.track_id JOIN artists a ON a.id = s.artist_id"
            f" WHERE {where} GROUP BY t.id ORDER BY plays DESC, t.title LIMIT ?"
        )
    elif kind == "album":
        sql = (
            "SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays, i.image_url"
            " FROM scrobbles s JOIN albums al ON al.id = s.album_id JOIN artists a ON a.id = s.artist_id"
            " LEFT JOIN album_info i ON i.album_id = al.id"
            f" WHERE {where} GROUP BY al.id ORDER BY plays DESC, al.title LIMIT ?"
        )
    else:
        raise ValueError(f"unknown kind: {kind}")
    return _rows(conn.execute(sql, (*args, limit)))


def clock(conn: sqlite3.Connection, start: str | None = None, end: str | None = None, artist_id: int | None = None) -> list[list[int]]:
    """7 x 24 matrix of plays: [weekday Monday=0][local hour]."""
    where, args = _range_sql(start, end)
    if artist_id is not None:
        where += " AND s.artist_id = ?"
        args.append(artist_id)
    grid = [[0] * 24 for _ in range(7)]
    for wd, hr, n in conn.execute(f"SELECT lwday, lhour, COUNT(*) FROM scrobbles s WHERE {where} GROUP BY 1, 2", args):
        grid[wd][hr] = n
    return grid


# ---------------------------------------------------------------- browsing


_ARTIST_SORTS = {
    "plays": "st.plays DESC",
    "recent": "st.last_ts DESC",
    "discovered": "st.first_ts DESC",
    "oldest": "st.first_ts ASC",
    "name": "a.name_key ASC",
    "tracks": "st.n_tracks DESC",
    "years": "st.n_years DESC, st.plays DESC",
}


def artists(conn: sqlite3.Connection, q: str = "", sort: str = "plays", limit: int = 50, offset: int = 0) -> dict:
    order = _ARTIST_SORTS.get(sort, _ARTIST_SORTS["plays"])
    where, args = "1=1", []
    if q:
        where = "a.name_key LIKE ? ESCAPE '\\'"
        args.append("%" + _like(q.casefold()) + "%")
    total = conn.execute(f"SELECT COUNT(*) FROM artists a JOIN artist_stats st ON st.artist_id = a.id WHERE {where}", args).fetchone()[0]
    rows = _rows(
        conn.execute(
            "SELECT a.id, a.name, st.plays, st.first_ts, st.last_ts, st.n_tracks, st.n_years, st.n_days"
            f" FROM artists a JOIN artist_stats st ON st.artist_id = a.id WHERE {where}"
            f" ORDER BY {order}, a.id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
    )
    return {"total": total, "items": rows}


# One table for every kind and period (Library). Sort keys map to SQL per kind; "desc" is the
# natural direction (most plays, newest first, A–Z for names).
_LIB_SORTS = {
    "artist": {"plays": "plays DESC", "name": "a.name_key ASC", "tracks": "tracks DESC", "years": "st.n_years DESC, plays DESC",
               "first": "st.first_ts ASC", "last": "last_ts DESC"},
    "track": {"plays": "plays DESC", "name": "t.title_key ASC", "artist": "a.name_key ASC, plays DESC",
              "first": "first_ts ASC", "last": "last_ts DESC"},
    "album": {"plays": "plays DESC", "name": "al.title_key ASC", "artist": "a.name_key ASC, plays DESC",
              "tracks": "tracks DESC", "released": "i.release_date DESC", "last": "last_ts DESC"},
}


def library(conn: sqlite3.Connection, kind: str = "artist", start: str | None = None, end: str | None = None,
            q: str = "", sort: str = "plays", limit: int = 50, offset: int = 0) -> dict:
    """Artists, tracks, albums or genres for any period, filtered by name, sorted and paged."""
    if kind == "genre":
        g = genres(conn, start, end, limit=500)
        items = [x for x in g["items"] if not q or q.casefold() in x["name"].casefold()]
        if sort == "name":
            items.sort(key=lambda x: x["name"])
        return {"kind": kind, "total": len(items), "items": items[offset:offset + limit], "coverage": g["coverage"]}
    if kind not in _LIB_SORTS:
        raise ValueError(f"unknown kind: {kind}")
    order = _LIB_SORTS[kind].get(sort, _LIB_SORTS[kind]["plays"])
    where, args = _range_sql(start, end)
    name_col = {"artist": "a.name_key", "track": "t.title_key", "album": "al.title_key"}[kind]
    if q:
        pat = "%" + _like(q.casefold()) + "%"
        # tracks and albums also match on the artist's name
        where += f" AND ({name_col} LIKE ? ESCAPE '\\'" + (" OR a.name_key LIKE ? ESCAPE '\\')" if kind != "artist" else ")")
        args += [pat] + ([pat] if kind != "artist" else [])
    if kind == "artist":
        base = ("SELECT a.id, a.name, COUNT(*) AS plays, COUNT(DISTINCT s.track_id) AS tracks, st.n_years AS years,"
                " st.first_ts, MAX(s.ts) AS last_ts FROM scrobbles s JOIN artists a ON a.id = s.artist_id"
                f" JOIN artist_stats st ON st.artist_id = a.id WHERE {where} GROUP BY a.id")
    elif kind == "track":
        base = ("SELECT t.id, t.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays,"
                " MIN(s.ts) AS first_ts, MAX(s.ts) AS last_ts FROM scrobbles s JOIN tracks t ON t.id = s.track_id"
                f" JOIN artists a ON a.id = s.artist_id WHERE {where} GROUP BY t.id")
    else:
        base = ("SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays,"
                " COUNT(DISTINCT s.track_id) AS tracks, MAX(s.ts) AS last_ts, i.image_url, i.release_date"
                " FROM scrobbles s JOIN albums al ON al.id = s.album_id JOIN artists a ON a.id = s.artist_id"
                f" LEFT JOIN album_info i ON i.album_id = al.id WHERE {where} GROUP BY al.id")
    total = conn.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
    items = _rows(conn.execute(f"{base} ORDER BY {order}, 1 LIMIT ? OFFSET ?", (*args, limit, offset)))
    return {"kind": kind, "total": total, "items": items}


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(conn: sqlite3.Connection, q: str, limit: int = 8) -> dict:
    pat = "%" + _like(q.casefold()) + "%"
    return {
        "artists": _rows(conn.execute(
            "SELECT a.id, a.name, st.plays FROM artists a JOIN artist_stats st ON st.artist_id = a.id"
            " WHERE a.name_key LIKE ? ESCAPE '\\' ORDER BY st.plays DESC LIMIT ?", (pat, limit))),
        "tracks": _rows(conn.execute(
            "SELECT t.id, t.title AS name, a.id AS artist_id, a.name AS artist, COUNT(s.id) AS plays FROM tracks t"
            " JOIN artists a ON a.id = t.artist_id JOIN scrobbles s ON s.track_id = t.id"
            " WHERE t.title_key LIKE ? ESCAPE '\\' GROUP BY t.id ORDER BY plays DESC LIMIT ?", (pat, limit))),
        "albums": _rows(conn.execute(
            "SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist, COUNT(s.id) AS plays FROM albums al"
            " JOIN artists a ON a.id = al.artist_id JOIN scrobbles s ON s.album_id = al.id"
            " WHERE al.title_key LIKE ? ESCAPE '\\' GROUP BY al.id ORDER BY plays DESC LIMIT ?", (pat, limit))),
    }


def related(conn: sqlite3.Connection, artist_id: int, limit: int = 15) -> list[dict]:
    return _rows(conn.execute(
        "SELECT a.id, a.name, l.shared, l.score, st.plays FROM ("
        " SELECT b AS other, shared, score FROM artist_links WHERE a = ?"
        " UNION ALL SELECT a AS other, shared, score FROM artist_links WHERE b = ?) l"
        " JOIN artists a ON a.id = l.other JOIN artist_stats st ON st.artist_id = a.id"
        " ORDER BY l.score DESC LIMIT ?",
        (artist_id, artist_id, limit),
    ))


def artist(conn: sqlite3.Connection, artist_id: int) -> dict | None:
    row = conn.execute(
        "SELECT a.id, a.name, a.mbid, st.*, g.name AS gateway_name, ft.title AS first_track"
        " FROM artists a JOIN artist_stats st ON st.artist_id = a.id"
        " LEFT JOIN artists g ON g.id = st.gateway_id LEFT JOIN tracks ft ON ft.id = st.first_track_id"
        " WHERE a.id = ?",
        (artist_id,),
    ).fetchone()
    if row is None:
        return None
    info = dict(row)
    total = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    info["share"] = info["plays"] / total
    info["rank"] = conn.execute("SELECT COUNT(*) + 1 FROM artist_stats WHERE plays > ?", (info["plays"],)).fetchone()[0]
    monthly = dict(conn.execute(
        "SELECT substr(lday, 1, 7), COUNT(*) FROM scrobbles WHERE artist_id = ? GROUP BY 1", (artist_id,)))
    lo, hi = conn.execute("SELECT MIN(lday), MAX(lday) FROM scrobbles").fetchone()
    info["monthly"] = [{"month": m, "plays": monthly.get(m, 0)} for m in _months(lo[:7], hi[:7])]
    peak = max(info["monthly"], key=lambda r: r["plays"])
    info["peak_month"] = peak
    info["tracks"] = _rows(conn.execute(
        "SELECT t.id, t.title AS name, COUNT(*) AS plays, MIN(s.ts) AS first_ts, MAX(s.ts) AS last_ts"
        " FROM scrobbles s JOIN tracks t ON t.id = s.track_id WHERE s.artist_id = ?"
        " GROUP BY t.id ORDER BY plays DESC, t.title LIMIT 50", (artist_id,)))
    info["albums"] = _rows(conn.execute(
        "SELECT al.id, al.title AS name, COUNT(*) AS plays, COUNT(DISTINCT s.track_id) AS n_tracks,"
        " MIN(s.ts) AS first_ts, i.release_date, i.image_url FROM scrobbles s JOIN albums al ON al.id = s.album_id"
        " LEFT JOIN album_info i ON i.album_id = al.id WHERE s.artist_id = ?"
        " GROUP BY al.id ORDER BY plays DESC LIMIT 30", (artist_id,)))
    info["led_to"] = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.first_ts FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " WHERE st.gateway_id = ? ORDER BY st.plays DESC LIMIT 20", (artist_id,)))
    info["related"] = related(conn, artist_id)
    info["hours"] = [sum(day[h] for day in clock(conn, artist_id=artist_id)) for h in range(24)]
    meta = conn.execute("SELECT * FROM artist_info WHERE artist_id = ?", (artist_id,)).fetchone()
    info["meta"] = dict(meta) if meta else None
    # last.fm no longer serves artist photos (only a placeholder), so fall back to the cover
    # of the artist's most-played album.
    info["image_url"] = (info["meta"] or {}).get("image_url") or next(
        (al["image_url"] for al in info["albums"] if al["image_url"]), None)
    info["tags"] = _tags_of(conn, "artist_tags", "artist_id", artist_id)
    return info


def album(conn: sqlite3.Connection, album_id: int) -> dict | None:
    row = conn.execute(
        "SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist FROM albums al"
        " JOIN artists a ON a.id = al.artist_id WHERE al.id = ?", (album_id,)).fetchone()
    if row is None:
        return None
    info = dict(row)
    info["tracks"] = _rows(conn.execute(
        "SELECT t.id, t.title AS name, COUNT(*) AS plays, MIN(s.ts) AS first_ts, MAX(s.ts) AS last_ts"
        " FROM scrobbles s JOIN tracks t ON t.id = s.track_id WHERE s.album_id = ?"
        " GROUP BY t.id ORDER BY plays DESC", (album_id,)))
    info["plays"] = sum(t["plays"] for t in info["tracks"])
    meta = conn.execute("SELECT * FROM album_info WHERE album_id = ?", (album_id,)).fetchone()
    info["meta"] = dict(meta) if meta else None
    info["tags"] = _tags_of(conn, "album_tags", "album_id", album_id)
    return info


def track(conn: sqlite3.Connection, track_id: int) -> dict | None:
    row = conn.execute(
        "SELECT t.id, t.title AS name, a.id AS artist_id, a.name AS artist FROM tracks t"
        " JOIN artists a ON a.id = t.artist_id WHERE t.id = ?", (track_id,)).fetchone()
    if row is None:
        return None
    info = dict(row)
    agg = conn.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT lday) FROM scrobbles WHERE track_id = ?", (track_id,)).fetchone()
    info.update(plays=agg[0], first_ts=agg[1], last_ts=agg[2], n_days=agg[3])
    info["yearly"] = _rows(conn.execute(
        "SELECT substr(lday, 1, 4) AS year, COUNT(*) AS plays FROM scrobbles WHERE track_id = ? GROUP BY 1", (track_id,)))
    info["best_day"] = dict(conn.execute(
        "SELECT lday AS day, COUNT(*) AS plays FROM scrobbles WHERE track_id = ? GROUP BY lday ORDER BY plays DESC LIMIT 1",
        (track_id,)).fetchone() or {})
    return info


def recent(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return _rows(conn.execute(
        "SELECT s.ts, a.id AS artist_id, a.name AS artist, t.id AS track_id, t.title AS track, al.title AS album"
        " FROM scrobbles s JOIN artists a ON a.id = s.artist_id JOIN tracks t ON t.id = s.track_id"
        " LEFT JOIN albums al ON al.id = s.album_id ORDER BY s.ts DESC LIMIT ?", (limit,)))


# ---------------------------------------------------------------- eras


def eras(conn: sqlite3.Connection) -> list[dict]:
    """Per year: plays, top artists, the year's signature artist (most over-represented vs.
    all-time) and its biggest new discovery."""
    total = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    if not total:
        return []
    alltime = dict(conn.execute("SELECT artist_id, plays FROM artist_stats"))
    names = dict(conn.execute("SELECT id, name FROM artists"))
    by_year: defaultdict[str, Counter] = defaultdict(Counter)
    for y, a, n in conn.execute("SELECT substr(lday, 1, 4), artist_id, COUNT(*) FROM scrobbles GROUP BY 1, 2"):
        by_year[y][a] = n
    first_year = {a: y for a, y in conn.execute("SELECT artist_id, substr(first_lday, 1, 4) FROM artist_stats WHERE prehistory = 0")}
    has_tags = conn.execute("SELECT 1 FROM artist_tags LIMIT 1").fetchone() is not None
    out = []
    for y in sorted(by_year):
        c = by_year[y]
        year_total = sum(c.values())
        floor = max(10, year_total * 0.005)
        lift = {a: (n / year_total) / (alltime[a] / total) for a, n in c.items() if n >= floor}
        signature = max(lift, key=lambda a: (lift[a], c[a]), default=None)
        new = [a for a in c if first_year.get(a) == y]
        best_new = max(new, key=lambda a: c[a], default=None)
        g = genres(conn, f"{y}-01-01", f"{y}-12-31", limit=5) if has_tags else None
        out.append({
            "year": y, "plays": year_total, "artists": len(c), "new_artists": len(new),
            "genres": [{"id": x["id"], "name": x["name"], "share": x["share"]} for x in g["items"]] if g else [],
            "top": [{"id": a, "name": names[a], "plays": n} for a, n in c.most_common(8)],
            "signature": {"id": signature, "name": names[signature], "plays": c[signature],
                          "lift": lift[signature]} if signature else None,
            "best_new": {"id": best_new, "name": names[best_new], "plays": c[best_new]} if best_new else None,
        })
    return out


# ---------------------------------------------------------------- insight cards


def insights(conn: sqlite3.Connection) -> dict:
    lo, hi = _span(conn)
    if hi is None:
        return {}
    year_ago, q_ago = hi - 365 * DAY, hi - 90 * DAY

    forgotten = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.last_ts FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " WHERE st.last_ts < ? AND st.plays >= 20 ORDER BY st.plays DESC LIMIT 12", (year_ago,)))

    rising = _rows(conn.execute(
        "SELECT a.id, a.name, r.recent, r.before, (r.recent + 1.0) / (r.before * 90.0 / 365 + 1) AS growth FROM ("
        " SELECT artist_id, SUM(ts > ?) AS recent, SUM(ts <= ? AND ts > ?) AS before"
        " FROM scrobbles WHERE ts > ? GROUP BY artist_id) r JOIN artists a ON a.id = r.artist_id"
        " WHERE r.recent >= 8 AND growth >= 1.25 ORDER BY growth DESC LIMIT 12",
        (q_ago, q_ago, q_ago - 365 * DAY, q_ago - 365 * DAY)))

    obsessions = _rows(conn.execute(
        "SELECT t.month, a.id, a.name, t.n AS plays, t.n * 1.0 / t.month_total AS share FROM ("
        " SELECT substr(lday, 1, 7) AS month, artist_id, COUNT(*) AS n,"
        "  SUM(COUNT(*)) OVER (PARTITION BY substr(lday, 1, 7)) AS month_total,"
        "  ROW_NUMBER() OVER (PARTITION BY substr(lday, 1, 7) ORDER BY COUNT(*) DESC) AS rk"
        " FROM scrobbles GROUP BY 1, 2) t JOIN artists a ON a.id = t.artist_id"
        " WHERE t.rk = 1 AND t.month_total >= 50 ORDER BY share DESC LIMIT 12"))

    staying = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.n_years, st.first_ts FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " WHERE st.n_years >= 2 ORDER BY st.n_years DESC, st.plays DESC LIMIT 12"))

    one_track = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, t.title AS track, x.n * 1.0 / st.plays AS share FROM ("
        " SELECT artist_id, track_id, COUNT(*) AS n,"
        "  ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY COUNT(*) DESC) AS rk"
        " FROM scrobbles GROUP BY artist_id, track_id) x"
        " JOIN artist_stats st ON st.artist_id = x.artist_id JOIN artists a ON a.id = x.artist_id"
        " JOIN tracks t ON t.id = x.track_id"
        " WHERE x.rk = 1 AND st.plays >= 15 AND x.n * 1.0 / st.plays >= 0.75 ORDER BY st.plays DESC LIMIT 12"))

    binges = _rows(conn.execute(
        "SELECT s.lday AS day, t.id, t.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays"
        " FROM scrobbles s JOIN tracks t ON t.id = s.track_id JOIN artists a ON a.id = s.artist_id"
        " GROUP BY s.lday, s.track_id HAVING plays >= 5 ORDER BY plays DESC LIMIT 12"))

    deep_divers = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.n_tracks FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " ORDER BY st.n_tracks DESC LIMIT 12"))

    gateways = _rows(conn.execute(
        "SELECT a.id, a.name, COUNT(*) AS led_to, SUM(st.plays) AS downstream_plays"
        " FROM artist_stats st JOIN artists a ON a.id = st.gateway_id"
        " GROUP BY st.gateway_id ORDER BY downstream_plays DESC LIMIT 12"))

    return {
        "reference_ts": hi,
        "rediscover": rediscover(conn),
        "forgotten": forgotten, "rising": rising, "obsessions": obsessions, "staying_power": staying,
        "one_track": one_track, "binges": binges, "deep_dives": deep_divers, "gateways": gateways,
    }


def rediscover(conn: sqlite3.Connection, limit: int = 15) -> list[dict]:
    """Recommendations from your own library: artists strongly linked (co-listened) with what
    you play now, but not played in the past year."""
    lo, hi = _span(conn)
    if hi is None:
        return []
    core = dict(conn.execute(
        "SELECT artist_id, COUNT(*) FROM scrobbles WHERE ts > ? GROUP BY artist_id ORDER BY 2 DESC LIMIT 20",
        (hi - 90 * DAY,)))
    if not core:
        return []
    names = dict(conn.execute("SELECT id, name FROM artists"))
    stale = {a: (p, last) for a, p, last in conn.execute(
        "SELECT artist_id, plays, last_ts FROM artist_stats WHERE last_ts < ? AND plays >= 5", (hi - 365 * DAY,))}
    scores: defaultdict[int, float] = defaultdict(float)
    because: defaultdict[int, list] = defaultdict(list)
    marks = ",".join("?" * len(core))
    for a, b, score in conn.execute(
        f"SELECT a, b, score FROM artist_links WHERE a IN ({marks}) OR b IN ({marks})", (*core, *core)
    ):
        for src, dst in ((a, b), (b, a)):
            if src in core and dst in stale:
                scores[dst] += score
                because[dst].append((score, src))
    ranked = sorted(scores, key=lambda a: scores[a] * math.log1p(stale[a][0]), reverse=True)[:limit]
    return [
        {"id": a, "name": names[a], "plays": stale[a][0], "last_ts": stale[a][1], "score": scores[a],
         "because": [{"id": s, "name": names[s]} for _, s in sorted(because[a], reverse=True)[:3]]}
        for a in ranked
    ]


# ---------------------------------------------------------------- taste graph


def graph(conn: sqlite3.Connection, n: int = 120, per_node: int = 6) -> dict:
    """Top-n artists with their strongest co-listening links, clustered into taste
    communities with weighted label propagation."""
    nodes = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.first_ts FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " ORDER BY st.plays DESC LIMIT ?", (n,)))
    ids = {r["id"] for r in nodes}
    if not ids:
        return {"nodes": [], "edges": [], "clusters": []}
    marks = ",".join("?" * len(ids))
    links = conn.execute(
        f"SELECT a, b, shared, score FROM artist_links WHERE a IN ({marks}) AND b IN ({marks}) ORDER BY score DESC",
        (*ids, *ids)).fetchall()
    # Keep each node's strongest few links so the picture stays readable.
    kept, degree = [], Counter()
    for a, b, shared, score in links:
        if degree[a] < per_node or degree[b] < per_node:
            kept.append({"source": a, "target": b, "shared": shared, "score": score})
            degree[a] += 1
            degree[b] += 1

    cluster = _label_propagation([r["id"] for r in nodes], kept)
    plays = {r["id"]: r["plays"] for r in nodes}
    members: defaultdict[int, list] = defaultdict(list)
    for node, c in cluster.items():
        members[c].append(node)
    ordered = sorted(members.values(), key=lambda m: -sum(plays[x] for x in m))
    names = {r["id"]: r["name"] for r in nodes}
    clusters = []
    for i, m in enumerate(ordered):
        m.sort(key=lambda x: -plays[x])
        for x in m:
            cluster[x] = i
        clusters.append({"id": i, "size": len(m), "plays": sum(plays[x] for x in m),
                         "members": [{"id": x, "name": names[x], "plays": plays[x]} for x in m]})
    for r in nodes:
        r["cluster"] = cluster[r["id"]]
    return {"nodes": nodes, "edges": kept, "clusters": clusters}


def _label_propagation(node_ids: list[int], edges: list[dict], rounds: int = 30) -> dict[int, int]:
    nbrs: defaultdict[int, list] = defaultdict(list)
    for e in edges:
        nbrs[e["source"]].append((e["target"], e["score"]))
        nbrs[e["target"]].append((e["source"], e["score"]))
    label = {n: n for n in node_ids}
    for _ in range(rounds):
        changed = False
        for n in node_ids:  # deterministic order: by plays, biggest first
            if not nbrs[n]:
                continue
            weight: defaultdict[int, float] = defaultdict(float)
            for m, w in nbrs[n]:
                weight[label[m]] += w
            best = max(weight, key=lambda lab: (weight[lab], -lab))
            if best != label[n]:
                label[n], changed = best, True
        if not changed:
            break
    return label


def imports(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 50"))


# ---------------------------------------------------------------- genres (needs `mtc enrich`)

GENRE_TAGS_PER_ARTIST = 5


def _artist_genre_shares(conn: sqlite3.Connection, kind: str = "genre") -> dict[int, list[tuple[int, float]]]:
    """Each artist's top tags of one kind (genre or place) as shares summing to 1 (by last.fm tag weight)."""
    out: defaultdict[int, list] = defaultdict(list)
    for artist_id, tag_id, weight in conn.execute(
        "SELECT x.artist_id, x.tag_id, x.weight FROM artist_tags x JOIN tags t ON t.id = x.tag_id"
        " WHERE t.kind = ? AND x.weight > 0 ORDER BY x.artist_id, x.weight DESC", (kind,)
    ):
        if len(out[artist_id]) < GENRE_TAGS_PER_ARTIST:
            out[artist_id].append((tag_id, weight))
    return {a: [(t, w / sum(x for _, x in tw)) for t, w in tw] for a, tw in out.items()}


def genres(conn: sqlite3.Connection, start: str | None = None, end: str | None = None, limit: int = 40) -> dict:
    """Play-weighted genre profile: an artist's plays are split across its top genre tags."""
    where, args = _range_sql(start, end)
    plays = dict(conn.execute(f"SELECT s.artist_id, COUNT(*) FROM scrobbles s WHERE {where} GROUP BY 1", args))
    shares = _artist_genre_shares(conn)
    score: Counter = Counter()
    contrib: defaultdict[int, Counter] = defaultdict(Counter)
    covered = 0
    for artist_id, n in plays.items():
        if artist_id not in shares:
            continue
        covered += n
        for tag_id, share in shares[artist_id]:
            score[tag_id] += n * share
            contrib[tag_id][artist_id] += n * share
    names = dict(conn.execute("SELECT id, name FROM tags WHERE kind = 'genre'"))
    artist_names = dict(conn.execute("SELECT id, name FROM artists"))
    total = sum(plays.values())
    items = [
        {"id": t, "name": names[t], "plays": round(s, 1), "share": s / covered if covered else 0,
         "artists": [{"id": a, "name": artist_names[a]} for a, _ in contrib[t].most_common(3)]}
        for t, s in score.most_common(limit)
    ]
    return {"items": items, "plays": total, "covered": covered, "coverage": covered / total if total else 0}


def tag(conn: sqlite3.Connection, tag_id: int) -> dict | None:
    row = conn.execute("SELECT id, name, kind FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if row is None:
        return None
    info = dict(row)
    info["artists"] = _rows(conn.execute(
        "SELECT a.id, a.name, x.weight, st.plays FROM artist_tags x JOIN artists a ON a.id = x.artist_id"
        " JOIN artist_stats st ON st.artist_id = a.id WHERE x.tag_id = ? ORDER BY st.plays DESC LIMIT 100",
        (tag_id,)))
    info["albums"] = _rows(conn.execute(
        "SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist, x.weight, i.release_date,"
        " (SELECT COUNT(*) FROM scrobbles s WHERE s.album_id = al.id) AS plays"
        " FROM album_tags x JOIN albums al ON al.id = x.album_id JOIN artists a ON a.id = al.artist_id"
        " LEFT JOIN album_info i ON i.album_id = al.id WHERE x.tag_id = ? ORDER BY plays DESC LIMIT 50",
        (tag_id,)))
    # Monthly plays attributed to this genre (same split as `genres`).
    shares = {a: dict(s).get(tag_id) for a, s in _artist_genre_shares(conn).items()}
    shares = {a: s for a, s in shares.items() if s}
    monthly: Counter = Counter()
    if shares:
        marks = ",".join("?" * len(shares))
        for m, a, n in conn.execute(
            f"SELECT substr(lday, 1, 7), artist_id, COUNT(*) FROM scrobbles WHERE artist_id IN ({marks}) GROUP BY 1, 2",
            tuple(shares)):
            monthly[m] += n * shares[a]
    lo, hi = conn.execute("SELECT MIN(lday), MAX(lday) FROM scrobbles").fetchone()
    info["monthly"] = [{"month": m, "plays": round(monthly.get(m, 0), 1)} for m in _months(lo[:7], hi[:7])] if lo else []
    return info


def _tags_of(conn, table: str, id_col: str, item_id: int, limit: int = 12) -> list[dict]:
    return _rows(conn.execute(
        f"SELECT t.id, t.name, t.kind, x.weight FROM {table} x JOIN tags t ON t.id = x.tag_id"
        f" WHERE x.{id_col} = ? AND t.kind != 'other' ORDER BY x.weight DESC, t.name LIMIT ?", (item_id, limit)))
