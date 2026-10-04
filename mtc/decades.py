"""Which release decades you listen to, and how that moves over the years.

Release dates come from `album_info.release_date` (MusicBrainz, or a year tag as fallback), so
only plays of albums with a date count; `coverage` says how many that is.

- lift for a decade in a listening year = observed / expected, where expected is that year's
  plays times the decade's share of all dated plays. Small cells are shrunk towards 1, as in
  rhythms.py, so a quiet year can't paint a decade red or green on a handful of plays.
- "In vogue" compares the trailing 12 months with everything before them, the same way.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import rhythms
from .rhythms import PRIOR, _cached, _cell

MIN_YEAR = 1900         # release years outside this range are data errors, not music
MAX_YEAR = 2100
MIN_SHARE = 0.02        # a decade needs this share of dated plays to get a row in the heatmap
MAX_ROWS = 8
MIN_RECENT_PLAYS = 50   # dated plays in the last 12 months before "in vogue" says anything
VOGUE_LIFT = 1.1


def _label(decade: int) -> str:
    return f"{decade}s"


def _query(conn) -> tuple[Counter, Counter, int, int, str | None]:
    """plays[(listening_year, release_year, recent)], albums per decade, dated plays, all plays, newest day."""
    newest = conn.execute("SELECT MAX(lday) FROM scrobbles").fetchone()[0]
    if newest is None:
        return Counter(), Counter(), 0, 0, None
    cutoff = (date.fromisoformat(newest) - timedelta(days=365)).isoformat()
    plays: Counter = Counter()
    for ly, ry, recent, n in conn.execute(
            "SELECT CAST(substr(s.lday, 1, 4) AS INTEGER), CAST(substr(ai.release_date, 1, 4) AS INTEGER), s.lday > ?, COUNT(*)"
            " FROM scrobbles s JOIN album_info ai ON ai.album_id = s.album_id"
            " WHERE ai.release_date IS NOT NULL GROUP BY 1, 2, 3", (cutoff,)):
        if MIN_YEAR <= ry <= MAX_YEAR:
            plays[ly, ry, bool(recent)] += n
    albums: Counter = Counter()
    for ry, n in conn.execute(
            "SELECT CAST(substr(release_date, 1, 4) AS INTEGER), COUNT(*) FROM album_info"
            " WHERE release_date IS NOT NULL AND album_id IN (SELECT DISTINCT album_id FROM scrobbles) GROUP BY 1"):
        if MIN_YEAR <= ry <= MAX_YEAR:
            albums[ry // 10 * 10] += n
    total = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    return plays, albums, sum(plays.values()), total, newest


def overview(conn: sqlite3.Connection) -> dict:
    return _cached(conn, ("decades",), lambda: _overview(conn), tags=True)


def _overview(conn) -> dict:
    plays, albums, covered, total, newest = _query(conn)
    if not covered:
        return {"covered": 0, "total": total, "coverage": 0.0}

    by_decade: Counter = Counter()
    by_year: Counter = Counter()          # release year -> plays
    by_ly_decade: defaultdict = defaultdict(Counter)  # listening year -> decade -> plays
    for (ly, ry, _), n in plays.items():
        by_decade[ry // 10 * 10] += n
        by_year[ry] += n
        by_ly_decade[ly][ry // 10 * 10] += n

    decades = [{"decade": d, "label": _label(d), "plays": by_decade[d], "share": round(by_decade[d] / covered, 4),
                "albums": albums.get(d, 0)} for d in sorted(by_decade)]
    years = [{"year": y, "plays": by_year[y]} for y in range(min(by_year), max(by_year) + 1)]
    ranked = [d for d, _ in by_decade.most_common()]

    top = sorted(ranked[:5])
    drift_years = []
    for ly in sorted(by_ly_decade):
        year_total = sum(by_ly_decade[ly].values())
        shares = [by_ly_decade[ly].get(d, 0) / year_total for d in top]
        drift_years.append({"year": str(ly), "plays": year_total, "shares": [round(x, 4) for x in shares],
                            "other": round(max(0.0, 1 - sum(shares)), 4)})

    rows_for = [d for d in sorted(by_decade) if by_decade[d] / covered >= MIN_SHARE]
    rows_for = sorted(sorted(rows_for, key=lambda d: -by_decade[d])[:MAX_ROWS])
    cols = sorted(by_ly_decade)
    matrix = {"cols": [str(c) for c in cols], "rows": [
        {"decade": d, "name": _label(d), "cells": [_cell(_year_cell(by_ly_decade[c], d, by_decade, covered)) for c in cols]}
        for d in rows_for]}

    return {"covered": covered, "total": total, "coverage": round(covered / total, 4), "newest": newest,
            "decades": decades, "years": years, "peak": _label(ranked[0]),
            "drift": {"series": [{"decade": d, "name": _label(d)} for d in top], "years": drift_years},
            "matrix": matrix, "vogue": _vogue(plays, by_decade, covered)}


def _year_cell(year_counts: Counter, decade: int, by_decade: Counter, covered: int) -> dict | None:
    year_total = sum(year_counts.values())
    expected = year_total * by_decade[decade] / covered
    observed = year_counts.get(decade, 0)
    return {"lift": (observed + PRIOR) / (expected + PRIOR), "observed": observed, "expected": expected,
            "up": 0, "years": 0}


def _vogue(plays: Counter, by_decade: Counter, covered: int) -> dict:
    """Decades over- and under-represented in the last 12 months compared with the plays before."""
    recent: Counter = Counter()
    before: Counter = Counter()
    for (_, ry, is_recent), n in plays.items():
        (recent if is_recent else before)[ry // 10 * 10] += n
    r_total, b_total = sum(recent.values()), sum(before.values())
    if r_total < MIN_RECENT_PLAYS or not b_total:
        return {"enough": False, "plays": r_total, "min_plays": MIN_RECENT_PLAYS, "decades": []}
    out = []
    for d in sorted(set(recent) | set(before)):
        expected = r_total * before.get(d, 0) / b_total
        observed = recent.get(d, 0)
        if expected + observed < rhythms.MIN_EXPECTED / 3:
            continue
        out.append({"decade": d, "label": _label(d), "lift": round((observed + PRIOR) / (expected + PRIOR), 3),
                    "recent_share": round(observed / r_total, 4), "before_share": round(before.get(d, 0) / b_total, 4),
                    "plays": observed})
    out.sort(key=lambda x: -x["lift"])
    return {"enough": True, "plays": r_total, "min_plays": MIN_RECENT_PLAYS, "decades": out,
            "in_vogue": [x for x in out if x["lift"] >= VOGUE_LIFT][:3]}


# ---------------------------------------------------------------- album age at play

# Upper bounds in days: the fewest days an exact 1, 5 or 20 year anniversary can be (leap days included),
# so a play on the anniversary is already in the older bucket.
AGE_BUCKETS = [("Under 1 year", 365), ("1–5 years", 1826), ("5–20 years", 7305), ("20+ years", None)]
MIN_AGE_YEAR_PLAYS = 100  # dated plays for a listening year to get a median
DAYS_PER_YEAR = 365.25

# A year-only date counts as 1 July and a year-month as the 15th: the middle of what it could mean.
_RELEASE_DAY = ("CASE length(ai.release_date) WHEN 4 THEN ai.release_date || '-07-01'"
                " WHEN 7 THEN ai.release_date || '-15' ELSE ai.release_date END")


def album_age(conn: sqlite3.Connection) -> dict:
    return _cached(conn, ("album_age",), lambda: _album_age(conn), tags=True)


def _album_age(conn) -> dict:
    """How old the albums were when you played them, per listening year."""
    # plays per (listening year, whole days of age); a play before its release date counts as age 0
    hist: defaultdict = defaultdict(Counter)
    approx = 0
    for ly, days, imprecise, n in conn.execute(
            f"SELECT CAST(substr(s.lday, 1, 4) AS INTEGER),"
            f" MAX(0, CAST(julianday(s.lday) - julianday({_RELEASE_DAY}) AS INTEGER)),"
            f" length(ai.release_date) < 10, COUNT(*)"
            f" FROM scrobbles s JOIN album_info ai ON ai.album_id = s.album_id"
            f" WHERE ai.release_date IS NOT NULL AND CAST(substr(ai.release_date, 1, 4) AS INTEGER) BETWEEN ? AND ?"
            f" AND julianday({_RELEASE_DAY}) IS NOT NULL GROUP BY 1, 2, 3", (MIN_YEAR, MAX_YEAR)):
        hist[ly][days] += n
        approx += n if imprecise else 0
    covered = sum(sum(h.values()) for h in hist.values())
    total = conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
    if not covered:
        return {"covered": 0, "total": total, "coverage": 0.0}

    edges = [e for _, e in AGE_BUCKETS]
    years, overall = [], Counter()
    for ly in sorted(hist):
        h = hist[ly]
        n = sum(h.values())
        overall.update(h)
        counts = [0] * len(AGE_BUCKETS)
        for days, c in h.items():
            counts[next(i for i, e in enumerate(edges) if e is None or days < e)] += c
        years.append({"year": str(ly), "plays": n, "shares": [round(c / n, 4) for c in counts],
                      "median_years": round(_median(h) / DAYS_PER_YEAR, 2) if n >= MIN_AGE_YEAR_PLAYS else None})
    return {"covered": covered, "total": total, "coverage": round(covered / total, 4),
            "approximate_share": round(approx / covered, 4), "min_year_plays": MIN_AGE_YEAR_PLAYS,
            "buckets": [{"name": name} for name, _ in AGE_BUCKETS],
            "years": years, "median_years": round(_median(overall) / DAYS_PER_YEAR, 2),
            "new_share": round(sum(c for d, c in overall.items() if d < AGE_BUCKETS[0][1]) / covered, 4)}


def _median(hist: Counter) -> int:
    """Median of a {value: count} histogram."""
    half = sum(hist.values()) / 2
    run = 0
    for value in sorted(hist):
        run += hist[value]
        if run >= half:
            return value
    return 0
