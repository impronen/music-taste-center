"""Cyclical patterns: how genres and artists move through the year, the week and the day.

Method (README "Rhythms"):
- Plays are spread over each artist's top genre (or place) tags, as in insights.genres.
- lift = observed / expected for a bucket (a month, a season, a part of the day). Expected
  comes from *the same year's* genre mix, so a genre that simply grew over the years doesn't
  look seasonal, and partial first/last years are fine. Small buckets are shrunk towards 1.
- Recurrence: in how many years the bucket was above that year's own expectation.
- Seasonal artists: circular statistics on the day of the year, every year weighted equally.
"""
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import db
from .insights import _artist_genre_shares

PRIOR = 20             # pseudo-plays pulling every lift towards 1
MIN_EXPECTED = 30      # show a bucket only when this many plays were expected
MIN_YEAR_PLAYS = 200   # covered plays for a year to count towards "k of n years"
MIN_YEAR_EXPECTED = 3  # ...and this many expected in the bucket that year
SEASON_LIFT = 1.2      # a season card lists genres at least this over-represented
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SEASONS = {"winter": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11)}
DAYPARTS = [("night", "Night", 0, 6), ("morning", "Morning", 6, 12), ("afternoon", "Afternoon", 12, 18),
            ("evening", "Evening", 18, 24)]

_SEASON_OF = {m: name for name, months in SEASONS.items() for m in months}

# ---------------------------------------------------------------- cache

_cache: dict = {}


def _signature(conn) -> tuple:
    """Changes after an import, rebuild, tag fetch or merge."""
    return (db.get_meta(conn, "derived_at"),
            *conn.execute("SELECT MAX(tags_fetched_at), (SELECT COUNT(*) FROM artist_tags),"
                          " (SELECT COUNT(*) FROM scrobbles) FROM artist_info").fetchone())


def _cached(conn, key: tuple, compute):
    sig = _signature(conn)
    hit = _cache.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    value = compute()
    _cache[key] = (sig, value)
    return value


# ---------------------------------------------------------------- lift


def _counts(conn) -> dict[str, Counter]:
    """Plays per (artist, year, bucket) for every dimension, from one pass over the scrobbles."""
    return _cached(conn, ("counts",), lambda: _collect(conn))


def _collect(conn) -> dict[str, Counter]:
    dims = {d: Counter() for d in ("month", "season", "daypart", "weekpart", "yearmonth")}
    weekend: dict[str, bool] = {}
    for a, day, part, n in conn.execute(
            "SELECT artist_id, lday, lhour / 6, COUNT(*) FROM scrobbles GROUP BY 1, 2, 3"):
        y, m = int(day[:4]), int(day[5:7])
        if day not in weekend:
            weekend[day] = date.fromisoformat(day).weekday() >= 5
        dims["month"][a, y, m] += n
        # Winter belongs to the year it ends in: December 2023 is part of winter 2024.
        dims["season"][a, y + (m == 12), _SEASON_OF[m]] += n
        dims["daypart"][a, y, DAYPARTS[part][0]] += n
        dims["weekpart"][a, y, "weekend" if weekend[day] else "weekday"] += n
        dims["yearmonth"][a, day[:7], 0] += n
    return dims


def _spread(conn, dim: str, shares: dict) -> tuple[dict, dict, int, int]:
    """observed[year][bucket][key] (plays spread over each artist's shares), covered[year][bucket],
    plus covered and total plays."""
    observed: defaultdict = defaultdict(lambda: defaultdict(Counter))
    covered: defaultdict = defaultdict(Counter)
    total = cov = 0
    for (artist_id, year, bucket), n in _counts(conn)[dim].items():
        total += n
        if artist_id not in shares:
            continue
        cov += n
        covered[year][bucket] += n
        for key, share in shares[artist_id]:
            observed[year][bucket][key] += n * share
    return observed, covered, cov, total


def _lift(observed, covered) -> dict:
    """{bucket: {key: {lift, observed, expected, up, years}}} with expectations from each year's own mix."""
    acc: defaultdict = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0, 0]))  # O, E, years up, years
    for year, buckets in observed.items():
        year_total = sum(covered[year].values())
        if not year_total:
            continue
        mix = Counter()
        for counts in buckets.values():
            mix.update(counts)
        for bucket, plays in covered[year].items():
            for key, n_key in mix.items():
                e = plays * n_key / year_total
                o = buckets[bucket].get(key, 0.0)
                a = acc[bucket][key]
                a[0] += o
                a[1] += e
                if year_total >= MIN_YEAR_PLAYS and e >= MIN_YEAR_EXPECTED:
                    a[3] += 1
                    a[2] += o > e
    return {b: {k: {"lift": (o + PRIOR) / (e + PRIOR), "observed": o, "expected": e, "up": up, "years": n}
                for k, (o, e, up, n) in keys.items()} for b, keys in acc.items()}


def _cell(x: dict | None) -> dict:
    if not x or x["expected"] < MIN_EXPECTED:
        return {"lift": None}
    return {"lift": round(x["lift"], 3), "plays": round(x["observed"]), "expected": round(x["expected"]),
            "up": x["up"], "years": x["years"]}


def _tag_names(conn) -> dict[int, str]:
    return dict(conn.execute("SELECT id, name FROM tags"))


def _matrix(lifts: dict, buckets: list[str], keys: list[int], names: dict) -> list[dict]:
    return [{"id": k, "name": names.get(k, str(k)), "cells": [_cell(lifts.get(b, {}).get(k)) for b in buckets]}
            for k in keys]


def _top_keys(observed, n: int) -> list:
    totals = Counter()
    for buckets in observed.values():
        for counts in buckets.values():
            totals.update(counts)
    return [k for k, _ in totals.most_common() if k is not None][:n]


def _shares(conn, kind: str) -> dict:
    shares = _artist_genre_shares(conn, kind)
    if kind == "place":
        # A place is compared with everything looked up: artists without a place tag count as
        # "elsewhere" (key None), otherwise "finnish" would be 100 % of itself.
        for (a,) in conn.execute("SELECT artist_id FROM artist_info WHERE status IN ('ok', 'not_found')"):
            shares.setdefault(a, [(None, 1.0)])
    return shares


# ---------------------------------------------------------------- genres through the year, day and week


def overview(conn: sqlite3.Connection, kind: str = "genre") -> dict:
    """Everything the Rhythms page shows about genres (or places)."""
    return _cached(conn, ("overview", kind), lambda: _overview(conn, kind))


def _overview(conn, kind: str) -> dict:
    shares = _shares(conn, kind)
    names = _tag_names(conn)
    month_obs, month_cov, covered, total = _spread(conn, "month", shares)
    months = _lift(month_obs, month_cov)
    rows = _top_keys(month_obs, 12)
    out = {
        "kind": kind, "plays": total, "covered": covered, "coverage": covered / total if total else 0,
        "months": {"cols": MONTHS, "rows": _matrix(months, list(range(1, 13)), rows, names)},
    }
    if not covered:
        return {**out, "seasons": [], "dayparts": None, "weekparts": None, "drift": None, "diversity": None}

    season_obs, season_cov, _, _ = _spread(conn, "season", shares)
    season_lift = _lift(season_obs, season_cov)
    out["seasons"] = [_season_card(conn, key, season_lift.get(key, {}), names) for key in SEASONS]

    rows10 = rows[:10]
    day_obs, day_cov, _, _ = _spread(conn, "daypart", shares)
    out["dayparts"] = {"cols": [d[1] for d in DAYPARTS],
                       "rows": _matrix(_lift(day_obs, day_cov), [d[0] for d in DAYPARTS], rows10, names)}
    week_obs, week_cov, _, _ = _spread(conn, "weekpart", shares)
    out["weekparts"] = {"cols": ["Weekdays", "Weekend"],
                        "rows": _matrix(_lift(week_obs, week_cov), ["weekday", "weekend"], rows10, names)}
    out["drift"] = _drift(month_obs, month_cov, rows, names)
    out["diversity"] = _diversity(conn, shares) if kind == "genre" else None
    return out


def _season_card(conn, season: str, lifts: dict, names: dict) -> dict:
    genres = sorted(
        ({"id": k, "name": names.get(k, str(k)), **_cell(x)} for k, x in lifts.items()
         if k is not None and x["expected"] >= MIN_EXPECTED and x["lift"] >= SEASON_LIFT and x["years"] and x["up"] * 2 >= x["years"]),
        key=lambda g: -g["lift"])[:4]
    return {"key": season, "months": [MONTHS[m - 1] for m in SEASONS[season]], "genres": genres,
            "artists": _season_artists(conn, season)}


def _season_artists(conn, season: str, limit: int = 5) -> list[dict]:
    """Artists most over-represented in a season (vs their share of the same season-years)."""
    def compute():
        shares = {a: [(a, 1.0)] for (a,) in conn.execute("SELECT artist_id FROM artist_stats WHERE plays >= 30")}
        obs, cov, _, _ = _spread(conn, "season", shares)
        return _lift(obs, cov)
    lifts = _cached(conn, ("artist_seasons",), compute).get(season, {})
    best = sorted(((k, x) for k, x in lifts.items() if x["observed"] >= 30 and x["lift"] >= 1.3
                   and x["years"] and x["up"] * 2 >= x["years"]), key=lambda kx: -kx[1]["lift"])[:limit]
    names = dict(conn.execute(
        f"SELECT id, name FROM artists WHERE id IN ({','.join('?' * len(best))})", [k for k, _ in best])) if best else {}
    return [{"id": k, "name": names.get(k), **_cell(x)} for k, x in best]


def _drift(observed, covered, keys: list, names: dict, n: int = 5) -> dict:
    """Per year: share of the top genres (fixed order, one per categorical colour) and everything else."""
    top = keys[:n]
    years = []
    for year in sorted(observed):
        total = sum(covered[year].values())
        if not total:
            continue
        mix = Counter()
        for counts in observed[year].values():
            mix.update(counts)
        shares = [mix.get(k, 0.0) / total for k in top]
        years.append({"year": str(year), "plays": total, "shares": [round(x, 4) for x in shares],
                      "other": round(max(0.0, 1 - sum(shares)), 4)})
    return {"series": [{"id": k, "name": names.get(k, str(k))} for k in top], "years": years}


def _diversity(conn, shares: dict) -> dict:
    """Effective number of genres per month: exp(Shannon entropy) of the play-weighted mix."""
    mixes: defaultdict = defaultdict(Counter)
    for (artist_id, month, _), n in _counts(conn)["yearmonth"].items():
        for key, share in shares.get(artist_id, ()):
            mixes[month][key] += n * share
    monthly = []
    for month in sorted(mixes):
        mix = mixes[month]
        total = sum(mix.values())
        if total < 50:
            continue
        h = -sum((v / total) * math.log(v / total) for v in mix.values() if v > 0)
        monthly.append({"month": month, "effective": round(math.exp(h), 2), "plays": round(total)})
    by_month = defaultdict(list)
    for m in monthly:
        by_month[int(m["month"][5:])].append(m["effective"])
    yearly_avg = [{"month": MONTHS[i - 1], "effective": round(sum(v) / len(v), 2) if (v := by_month.get(i)) else None}
                  for i in range(1, 13)]
    ranked = [m for m in yearly_avg if m["effective"] is not None]
    return {"monthly": monthly, "by_month": yearly_avg,
            "most": max(ranked, key=lambda m: m["effective"])["month"] if ranked else None,
            "least": min(ranked, key=lambda m: m["effective"])["month"] if ranked else None}


def tag_months(conn: sqlite3.Connection, tag_id: int) -> list[dict] | None:
    """The 12-month lift strip for one tag (genre or place)."""
    row = conn.execute("SELECT kind FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if row is None or row[0] not in ("genre", "place"):
        return None

    def compute():
        obs, cov, _, _ = _spread(conn, "month", _shares(conn, row[0]))
        return _lift(obs, cov)
    lifts = _cached(conn, ("month_lifts", row[0]), compute)
    cells = [{"month": MONTHS[m - 1], **_cell(lifts.get(m, {}).get(tag_id))} for m in range(1, 13)]
    return cells if any(c["lift"] is not None for c in cells) else None


# ---------------------------------------------------------------- seasonal artists

YEAR_DAYS = 365.25
MIN_ARTIST_YEARS = 3
MIN_ARTIST_PLAYS = 30
MIN_R = 0.5            # mean resultant length: 0 = spread over the year, 1 = one day
PEAK_TOLERANCE = 30    # days; a year "agrees" when its own peak is this close
MIN_AGREE = 0.6
COMING_UP_DAYS = 28


def _angle(day: date) -> float:
    return 2 * math.pi * (day.timetuple().tm_yday - 1) / YEAR_DAYS


def _day_diff(a: float, b: float) -> float:
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d) * YEAR_DAYS / (2 * math.pi)


def seasonal_artists(conn: sqlite3.Connection, today: date | None = None, limit: int = 20) -> dict:
    """Artists that come back around the same time every year, and which of them are due."""
    found = _cached(conn, ("seasonal_artists",), lambda: _seasonal(conn))
    if today is None:
        last = conn.execute("SELECT MAX(lday) FROM scrobbles").fetchone()[0]
        today = date.fromisoformat(last) if last else date.today()
    t = _angle(today)
    out = []
    for a in found:
        to_start = ((a["peak_angle"] - t) * YEAR_DAYS / (2 * math.pi) - a["spread_days"]) % YEAR_DAYS
        in_season = _day_diff(a["peak_angle"], t) <= a["spread_days"]
        status = "now" if in_season else "soon" if to_start <= COMING_UP_DAYS else None
        out.append({k: v for k, v in a.items() if k != "peak_angle"} | {"status": status, "starts_in_days": round(to_start)})
    order = {"now": 0, "soon": 1, None: 2}
    out.sort(key=lambda a: (order[a["status"]], a["starts_in_days"] if a["status"] == "soon" else 0, -a["strength"]))
    return {"reference": today.isoformat(), "artists": out[:limit],
            "coming_up": [a for a in out if a["status"] in ("now", "soon")][:8]}


def _seasonal(conn) -> list[dict]:
    per: defaultdict = defaultdict(lambda: defaultdict(list))
    for artist_id, day, n in conn.execute(
            "SELECT s.artist_id, s.lday, COUNT(*) FROM scrobbles s JOIN artist_stats st ON st.artist_id = s.artist_id"
            " WHERE st.plays >= ? AND st.n_years >= ? GROUP BY 1, 2", (MIN_ARTIST_PLAYS, MIN_ARTIST_YEARS)):
        d = date.fromisoformat(day)
        per[artist_id][d.year].append((_angle(d), n))
    found = []
    for artist_id, years in per.items():
        vecs = []
        for y, pts in years.items():
            n = sum(c for _, c in pts)
            if n < 3:
                continue
            vecs.append((sum(c * math.cos(a) for a, c in pts) / n, sum(c * math.sin(a) for a, c in pts) / n, n))
        if len(vecs) < MIN_ARTIST_YEARS:
            continue
        c = sum(v[0] for v in vecs) / len(vecs)
        s = sum(v[1] for v in vecs) / len(vecs)
        r = math.hypot(c, s)
        if r < MIN_R:
            continue
        peak = math.atan2(s, c) % (2 * math.pi)
        agree = sum(_day_diff(math.atan2(v[1], v[0]) % (2 * math.pi), peak) <= PEAK_TOLERANCE for v in vecs)
        if agree < MIN_AGREE * len(vecs):
            continue
        spread = round(max(7.0, min(math.sqrt(-2 * math.log(r)) * YEAR_DAYS / (2 * math.pi), 60.0)))  # circular SD
        peak_day = date(2001, 1, 1) + timedelta(days=round(peak * YEAR_DAYS / (2 * math.pi)) % 365)
        found.append({"id": artist_id, "peak": peak_day.strftime("%m-%d"), "peak_angle": peak,
                      "start": (peak_day - timedelta(days=spread)).strftime("%m-%d"),
                      "spread_days": spread, "strength": round(r, 3),
                      "years_agree": agree, "years": len(vecs), "plays": sum(v[2] for v in vecs)})
    if found:
        names = dict(conn.execute(
            f"SELECT id, name FROM artists WHERE id IN ({','.join('?' * len(found))})", [a["id"] for a in found]))
        for a in found:
            a["name"] = names.get(a["id"])
    return found
