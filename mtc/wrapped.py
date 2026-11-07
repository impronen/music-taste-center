"""The year in review ("Wrapped"): one JSON-ready dataset per calendar year, plus CSV tables.

`build` only reads the database. Every section carries its own coverage (plays with tags or a
release date out of all plays) so a card can say what it is based on. The data is the same for
every renderer: `python -m mtc wrapped YEAR` writes it to data/wrapped/YEAR/.
"""
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, highlights
from .decades import MAX_YEAR, MIN_YEAR
from .insights import _artist_genre_shares, genres

TOP_N = 10
GENRES_SHOWN = 8
LOVED_SHOWN = 10
SESSIONS_SHOWN = 3
# Archetype bars: a label appears only when the year clears its bar.
EXPLORER_MIN_NEW_SHARE = 0.30       # plays going to artists first heard this year
LOYALIST_MIN_TOP5_SHARE = 0.50      # plays going to the five most-played artists
NIGHT_OWL_MIN_SHARE = 0.15          # plays between 00:00 and 05:59
EARLY_BIRD_MIN_SHARE = 0.15         # plays between 05:00 and 08:59 (overlaps the night owl hours at 05)
ALBUM_MIN_SHARE = 0.50              # plays following a play of the same album in the same session
MIN_PLAYS = 50                      # below this a year is too thin for labels


def build(conn: sqlite3.Connection, year: int, birth_year: int | None = None) -> dict:
    """The dataset for one calendar year (local dates), with changes against the year before."""
    data = _year(conn, year, birth_year)
    last = _totals(conn, f"{year - 1}-01-01", f"{year - 1}-12-31")
    data["versus_last_year"] = {k: _delta(data["totals"][k], last[k]) for k in ("plays", "artists", "tracks", "active_days")}
    data["versus_last_year"]["new_artists"] = _delta(data["totals"]["new_artists"], last["new_artists"])
    return data


def _delta(now: int, before: int) -> dict:
    return {"before": before, "change": now - before,
            "ratio": round(now / before, 4) if before else None}


def _year(conn, year: int, birth_year: int | None) -> dict:
    s, e = f"{year}-01-01", f"{year}-12-31"
    totals = _totals(conn, s, e)
    data = {"year": year, "range": [s, e], "totals": totals}
    if not totals["plays"]:
        return data | {"empty": True}
    data |= {
        "top_artists": _top(conn, s, e, "artist"),
        "top_tracks": _top(conn, s, e, "track"),
        "top_albums": _top(conn, s, e, "album"),
        "top_genres": _top_genres(conn, s, e),
        "artist_of_month": _artist_of_month(conn, year),
        "clock": _clock(conn, s, e),
        "calendar": dict(conn.execute("SELECT lday, COUNT(*) FROM scrobbles WHERE lday BETWEEN ? AND ? GROUP BY 1", (s, e))),
        "streak": _streak(conn, s, e),
        "sessions": _sessions(conn, s, e),
        "repeat_day": _repeat_day(conn, s, e),
        "genre_months": _genre_months(conn, year),
        "release_years": _release_years(conn, s, e, year),
        "loved": _loved(conn, year),
        "highlights": highlights.highlights(conn, s, e, birth_year),
    }
    cal = data["calendar"]
    day = max(cal, key=lambda d: (cal[d], d))
    data["busiest_day"] = {"day": day, "plays": cal[day]}
    data["archetypes"] = _archetypes(conn, s, e, data)
    return data


def _totals(conn, s: str, e: str) -> dict:
    plays, artists, tracks, albums, days = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT artist_id), COUNT(DISTINCT track_id), COUNT(DISTINCT album_id),"
        " COUNT(DISTINCT lday) FROM scrobbles WHERE lday BETWEEN ? AND ?", (s, e)).fetchone()
    # "New": first heard in the range and not already in rotation when tracking began.
    new_artists, new_plays = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(p), 0) FROM artist_stats st JOIN"
        " (SELECT artist_id, COUNT(*) AS p FROM scrobbles WHERE lday BETWEEN ? AND ? GROUP BY 1) x"
        " ON x.artist_id = st.artist_id WHERE st.first_lday BETWEEN ? AND ? AND st.prehistory = 0",
        (s, e, s, e)).fetchone()
    return {"plays": plays, "artists": artists, "tracks": tracks, "albums": albums, "active_days": days,
            "new_artists": new_artists, "new_artist_plays": new_plays,
            "new_artist_share": round(new_plays / plays, 4) if plays else None}


_TOP_SQL = {
    "artist": ("a.id, a.name, NULL", "FROM scrobbles s JOIN artists a ON a.id = s.artist_id", "a.id"),
    "track": ("t.id, t.title, a.name", "FROM scrobbles s JOIN tracks t ON t.id = s.track_id"
              " JOIN artists a ON a.id = t.artist_id", "t.id"),
    "album": ("b.id, b.title, a.name", "FROM scrobbles s JOIN albums b ON b.id = s.album_id"
              " JOIN artists a ON a.id = b.artist_id", "b.id"),
}


def _top(conn, s: str, e: str, kind: str, n: int = TOP_N) -> list[dict]:
    cols, source, group = _TOP_SQL[kind]
    rows = conn.execute(f"SELECT {cols}, COUNT(*) AS plays, COUNT(DISTINCT s.lday) {source}"
                        f" WHERE s.lday BETWEEN ? AND ? GROUP BY {group} ORDER BY plays DESC, 2 LIMIT ?", (s, e, n))
    return [{"id": i, "name": name, "artist": artist, "plays": plays, "days": days}
            for i, name, artist, plays, days in rows]


def _top_genres(conn, s: str, e: str) -> dict:
    g = genres(conn, s, e, limit=GENRES_SHOWN)
    return {"items": [{"id": i["id"], "name": i["name"], "plays": i["plays"], "share": round(i["share"], 4),
                       "artists": [a["name"] for a in i["artists"]]} for i in g["items"]],
            "covered_plays": g["covered"], "total_plays": g["plays"], "coverage": round(g["coverage"], 4)}


def _artist_of_month(conn, year: int) -> list[dict | None]:
    best: dict[int, tuple] = {}
    for month, aid, name, plays in conn.execute(
            "SELECT CAST(substr(s.lday, 6, 2) AS INTEGER), a.id, a.name, COUNT(*) FROM scrobbles s"
            " JOIN artists a ON a.id = s.artist_id WHERE s.lday BETWEEN ? AND ? GROUP BY 1, 2",
            (f"{year}-01-01", f"{year}-12-31")):
        if month not in best or (plays, name) > (best[month][2], best[month][1]):
            best[month] = (aid, name, plays)
    return [{"month": m, "id": best[m][0], "name": best[m][1], "plays": best[m][2]} if m in best else None
            for m in range(1, 13)]


def _clock(conn, s: str, e: str) -> list[list[int]]:
    """Plays by weekday (Monday first) and hour."""
    grid = [[0] * 24 for _ in range(7)]
    for wd, hour, n in conn.execute(
            "SELECT lwday, lhour, COUNT(*) FROM scrobbles WHERE lday BETWEEN ? AND ? GROUP BY 1, 2", (s, e)):
        grid[wd][hour] = n
    return grid


def _streak(conn, s: str, e: str) -> dict:
    """The longest run of consecutive listening days inside the range."""
    days = [date.fromisoformat(d) for (d,) in conn.execute(
        "SELECT DISTINCT lday FROM scrobbles WHERE lday BETWEEN ? AND ? ORDER BY 1", (s, e))]
    best = (0, None, None)
    run_start, prev = None, None
    for d in days:
        if prev is None or d - prev != timedelta(days=1):
            run_start = d
        prev = d
        length = (d - run_start).days + 1
        if length > best[0]:
            best = (length, run_start, d)
    return {"days": best[0], "start": best[1].isoformat(), "end": best[2].isoformat()} if best[1] else {"days": 0}


def _sessions(conn, s: str, e: str) -> list[dict]:
    """The longest listening sessions (by plays) that started in the range."""
    rows = conn.execute(
        "SELECT MIN(lday), MIN(ts), MAX(ts), COUNT(*), COUNT(DISTINCT artist_id) FROM scrobbles WHERE session_id IN"
        " (SELECT DISTINCT session_id FROM scrobbles WHERE lday BETWEEN ? AND ?) GROUP BY session_id"
        " HAVING MIN(lday) BETWEEN ? AND ? ORDER BY COUNT(*) DESC, MIN(ts) LIMIT ?", (s, e, s, e, SESSIONS_SHOWN))
    return [{"day": day, "plays": plays, "artists": artists, "minutes": round((last - first) / 60)}
            for day, first, last, plays, artists in rows]


def _repeat_day(conn, s: str, e: str) -> dict | None:
    """The track played most often in a single day."""
    row = conn.execute(
        "SELECT t.id, t.title, a.name, s.lday, COUNT(*) AS n FROM scrobbles s JOIN tracks t ON t.id = s.track_id"
        " JOIN artists a ON a.id = t.artist_id WHERE s.lday BETWEEN ? AND ? GROUP BY t.id, s.lday"
        " ORDER BY n DESC, s.lday LIMIT 1", (s, e)).fetchone()
    return {"id": row[0], "name": row[1], "artist": row[2], "day": row[3], "plays": row[4]} if row and row[4] > 1 else None


def _genre_months(conn, year: int) -> dict:
    """Plays per month for the year's biggest genres (an artist's plays split by its genre tags)."""
    shares = _artist_genre_shares(conn)
    names = dict(conn.execute("SELECT id, name FROM tags WHERE kind = 'genre'"))
    by_genre: defaultdict[int, list[float]] = defaultdict(lambda: [0.0] * 12)
    covered = total = 0
    for aid, month, n in conn.execute(
            "SELECT artist_id, CAST(substr(lday, 6, 2) AS INTEGER), COUNT(*) FROM scrobbles"
            " WHERE lday BETWEEN ? AND ? GROUP BY 1, 2", (f"{year}-01-01", f"{year}-12-31")):
        total += n
        if aid in shares:
            covered += n
            for tag, share in shares[aid]:
                by_genre[tag][month - 1] += n * share
    top = sorted(by_genre, key=lambda t: (-sum(by_genre[t]), names[t]))[:GENRES_SHOWN]
    return {"genres": [{"id": t, "name": names[t], "months": [round(x, 1) for x in by_genre[t]]} for t in top],
            "covered_plays": covered, "total_plays": total}


def _release_years(conn, s: str, e: str, year: int) -> dict:
    hist: Counter = Counter()
    for ry, n in conn.execute(
            "SELECT CAST(substr(ai.release_date, 1, 4) AS INTEGER), COUNT(*) FROM scrobbles s"
            " JOIN album_info ai ON ai.album_id = s.album_id WHERE s.lday BETWEEN ? AND ? AND ai.release_date IS NOT NULL"
            " GROUP BY 1", (s, e)):
        if MIN_YEAR <= ry <= MAX_YEAR:
            hist[ry] = n
    dated = sum(hist.values())
    total = conn.execute("SELECT COUNT(*) FROM scrobbles WHERE lday BETWEEN ? AND ?", (s, e)).fetchone()[0]
    return {"histogram": dict(sorted(hist.items())), "dated_plays": dated, "total_plays": total,
            "this_year_share": round(hist[year] / dated, 4) if dated else None,
            "median": _median(hist)}


def _median(hist: Counter) -> int | None:
    half, seen = sum(hist.values()) / 2, 0
    for y in sorted(hist):
        seen += hist[y]
        if seen >= half:
            return y
    return None


def _loved(conn, year: int) -> dict:
    lo = int(datetime(year, 1, 1, tzinfo=config.TZ).timestamp())
    hi = int(datetime(year + 1, 1, 1, tzinfo=config.TZ).timestamp())
    rows = conn.execute("SELECT artist, title, loved_at FROM loved WHERE loved_at >= ? AND loved_at < ?"
                        " ORDER BY loved_at DESC", (lo, hi)).fetchall()
    return {"count": len(rows),
            "recent": [{"artist": a, "title": t, "day": datetime.fromtimestamp(ts, config.TZ).date().isoformat()}
                       for a, t, ts in rows[:LOVED_SHOWN]]}


def _archetypes(conn, s: str, e: str, data: dict) -> list[dict]:
    """Rule-of-thumb labels. Each states the number behind it, and none shows for a very thin year."""
    plays = data["totals"]["plays"]
    if plays < MIN_PLAYS:
        return []
    out = []

    def add(key: str, label: str, share: float, bar: float, fact: str):
        if share >= bar:
            out.append({"key": key, "label": label, "share": round(share, 4), "fact": fact})

    new_share = data["totals"]["new_artist_share"] or 0
    add("explorer", "Explorer", new_share, EXPLORER_MIN_NEW_SHARE,
        f"{new_share:.0%} of plays went to artists you first heard this year")
    top5 = sum(a["plays"] for a in data["top_artists"][:5]) / plays
    add("loyalist", "Loyalist", top5, LOYALIST_MIN_TOP5_SHARE, f"{top5:.0%} of plays went to five artists")
    night = sum(sum(row[:6]) for row in data["clock"]) / plays
    add("night_owl", "Night owl", night, NIGHT_OWL_MIN_SHARE, f"{night:.0%} of plays were between midnight and 6")
    early = sum(sum(row[5:9]) for row in data["clock"]) / plays
    add("early_bird", "Early bird", early, EARLY_BIRD_MIN_SHARE, f"{early:.0%} of plays were between 5 and 9")
    same, following = conn.execute(
        "SELECT SUM(album_id IS NOT NULL AND album_id = prev), SUM(prev IS NOT NULL) FROM"
        " (SELECT album_id, LAG(album_id) OVER (PARTITION BY session_id ORDER BY ts) AS prev, lday FROM scrobbles)"
        " WHERE lday BETWEEN ? AND ?", (s, e)).fetchone()
    if following:
        add("album_listener", "Album listener", same / following, ALBUM_MIN_SHARE,
            f"{same / following:.0%} of plays continued the same album")
    return out


# ---------------------------------------------------------------- files

_TABLES = {
    "top_artists": ("id", "name", "plays", "days"),
    "top_tracks": ("id", "name", "artist", "plays", "days"),
    "top_albums": ("id", "name", "artist", "plays", "days"),
}


def write(data: dict, out_dir: Path) -> list[Path]:
    """wrapped.json plus one CSV per table (utf-8 with BOM, so Excel reads ä/ö/å)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "wrapped.json"]
    written[0].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if data.get("empty"):
        return written
    tables = {name: [list(cols)] + [[row[c] for c in cols] for row in data[name]] for name, cols in _TABLES.items()}
    tables["artist_of_month"] = [["month", "artist", "plays"]] + [
        [m["month"], m["name"], m["plays"]] for m in data["artist_of_month"] if m]
    tables["calendar"] = [["day", "plays"]] + sorted(data["calendar"].items())
    tables["clock"] = [["weekday", *range(24)]] + [[d, *row] for d, row in enumerate(data["clock"])]
    tables["genre_months"] = [["genre", *range(1, 13)]] + [[g["name"], *g["months"]] for g in data["genre_months"]["genres"]]
    tables["release_years"] = [["release_year", "plays"]] + sorted(data["release_years"]["histogram"].items())
    for name, rows in tables.items():
        path = out_dir / f"{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
        written.append(path)
    return written
