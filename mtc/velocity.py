"""Artist velocity: cumulative plays over time, per artist, and the pace figures behind the curve.

A steeper line means a faster pace. The curve is sampled weekly (cumulative plays at the end of each
week, weeks run Monday to Sunday over local days), the same for every artist so that the UI can draw
them on one axis, either by calendar date or aligned at each artist's first play.

An artist already in rotation when the history began (`artist_stats.prehistory`) has plays from before
tracking that aren't counted, so its early pace is not a discovery pace; the series says so.
"""
from datetime import date, timedelta

from . import config
from .rhythms import _cached

MAX_ARTISTS = 6
THRESHOLDS = (100, 500, 1000)   # "days from the first play to the Nth play"
WINDOW_DAYS = 30                # the fastest stretch is the most plays in any window this long
DAYS_PER_MONTH = 30.4375


def _daily(conn, artist_id: int) -> list[tuple[int, int]]:
    """[(day ordinal, plays)] oldest first, for one artist (scrobble-only, so cached until the next import)."""
    def compute():
        return [(date.fromisoformat(day).toordinal(), n) for day, n in conn.execute(
            "SELECT lday, COUNT(*) FROM scrobbles WHERE artist_id = ? GROUP BY lday ORDER BY lday", (artist_id,))]
    return _cached(conn, ("velocity", artist_id), compute, tags=False)


def _days_to(daily: list[tuple[int, int]], thresholds=THRESHOLDS) -> dict[str, int | None]:
    """Days from the first play to the day the Nth play happened (None if it never got there)."""
    out: dict[str, int | None] = {str(t): None for t in thresholds}
    total = 0
    first = daily[0][0]
    for day, n in daily:
        total += n
        for t in thresholds:
            if out[str(t)] is None and total >= t:
                out[str(t)] = day - first
    return out


def _fastest(daily: list[tuple[int, int]], window: int = WINDOW_DAYS) -> dict:
    """The most plays in any `window` consecutive days, and the day that stretch began."""
    best, best_from, total, lo = 0, daily[0][0], 0, 0
    for hi, (day, n) in enumerate(daily):
        total += n
        while daily[lo][0] <= day - window:
            total -= daily[lo][1]
            lo += 1
        if total > best:
            best, best_from = total, daily[lo][0]
    return {"plays": best, "from": date.fromordinal(best_from).isoformat(), "days": window}


def _pace(daily: list[tuple[int, int]], end: int) -> dict:
    """Plays per month over the artist's whole span so far and over the last year (None when the
    artist is younger than that: a short span would only measure a burst)."""
    first = daily[0][0]
    plays = sum(n for _, n in daily)
    span = end - first + 1
    lifetime = plays / (span / DAYS_PER_MONTH) if span >= WINDOW_DAYS else None
    recent = None
    if span >= 365:
        recent = sum(n for day, n in daily if day > end - 365) / (365 / DAYS_PER_MONTH)
    return {"lifetime_per_month": None if lifetime is None else round(lifetime, 2),
            "recent_per_month": None if recent is None else round(recent, 2)}


def velocity(conn, artist_ids: list[int]) -> dict:
    """Weekly cumulative plays for up to MAX_ARTISTS artists (unknown ids are ignored)."""
    lo, hi = conn.execute("SELECT MIN(lday), MAX(lday) FROM scrobbles").fetchone()
    if lo is None:
        return {"start": None, "end": None, "weeks": 0, "series": []}
    first_day = date.fromisoformat(lo)
    start = first_day - timedelta(days=first_day.weekday())     # the Monday of the first week
    end = date.fromisoformat(hi)
    weeks = (end - start).days // 7 + 1
    ids = list(dict.fromkeys(artist_ids))[:MAX_ARTISTS]
    series = []
    for artist_id in ids:
        row = conn.execute(
            "SELECT a.name, COALESCE(st.prehistory, 0) FROM artists a LEFT JOIN artist_stats st ON st.artist_id = a.id"
            " WHERE a.id = ?", (artist_id,)).fetchone()
        daily = _daily(conn, artist_id) if row else []
        if not daily:
            continue
        per_week = [0] * weeks
        for day, n in daily:
            per_week[(day - start.toordinal()) // 7] += n
        cum, running = [], 0
        for n in per_week:
            running += n
            cum.append(running)
        first = daily[0][0]
        known = bool(row[1])
        series.append({
            "id": artist_id, "name": row[0], "plays": running,
            "first_day": date.fromordinal(first).isoformat(), "last_day": date.fromordinal(daily[-1][0]).isoformat(),
            "first_week": (first - start.toordinal()) // 7, "cum": cum,
            "known_before": known,
            "known_until": date.fromordinal(first + config.PREHISTORY_DAYS).isoformat() if known else None,
            "facts": {"days_to": _days_to(daily), "fastest": _fastest(daily), **_pace(daily, end.toordinal())},
        })
    return {"start": start.isoformat(), "end": end.isoformat(), "weeks": weeks, "series": series}
