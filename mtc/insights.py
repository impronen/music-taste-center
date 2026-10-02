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
            "SELECT al.id, al.title AS name, a.id AS artist_id, a.name AS artist, COUNT(*) AS plays FROM scrobbles s"
            " JOIN albums al ON al.id = s.album_id JOIN artists a ON a.id = s.artist_id"
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
        " MIN(s.ts) AS first_ts FROM scrobbles s JOIN albums al ON al.id = s.album_id WHERE s.artist_id = ?"
        " GROUP BY al.id ORDER BY plays DESC LIMIT 30", (artist_id,)))
    info["led_to"] = _rows(conn.execute(
        "SELECT a.id, a.name, st.plays, st.first_ts FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
        " WHERE st.gateway_id = ? ORDER BY st.plays DESC LIMIT 20", (artist_id,)))
    info["related"] = related(conn, artist_id)
    info["hours"] = [sum(day[h] for day in clock(conn, artist_id=artist_id)) for h in range(24)]
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
    out = []
    for y in sorted(by_year):
        c = by_year[y]
        year_total = sum(c.values())
        floor = max(10, year_total * 0.005)
        lift = {a: (n / year_total) / (alltime[a] / total) for a, n in c.items() if n >= floor}
        signature = max(lift, key=lambda a: (lift[a], c[a]), default=None)
        new = [a for a in c if first_year.get(a) == y]
        best_new = max(new, key=lambda a: c[a], default=None)
        out.append({
            "year": y, "plays": year_total, "artists": len(c), "new_artists": len(new),
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
