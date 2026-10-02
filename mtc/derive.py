"""Derived tables, rebuilt from scratch after each import: sessions, artist stats with
discovery "gateways", and co-listening links between artists."""
import math
import sqlite3
import time
from collections import Counter, defaultdict
from itertools import combinations

from . import config, db


def rebuild(conn: sqlite3.Connection) -> dict:
    started = time.perf_counter()
    with conn:
        n_sessions = _sessions_and_stats(conn)
        n_links = _links(conn)
        db.set_meta(conn, "derived_at", str(int(time.time())))
    return {"sessions": n_sessions, "links": n_links, "seconds": round(time.perf_counter() - started, 2)}


def _sessions_and_stats(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id, ts, artist_id, track_id, lday FROM scrobbles ORDER BY ts, id").fetchall()
    if not rows:
        conn.execute("DELETE FROM artist_stats")
        return 0

    history_start = rows[0][1]
    prehistory_until = history_start + config.PREHISTORY_DAYS * 86400

    session_updates = []
    stats: dict[int, dict] = {}
    days: defaultdict[int, set] = defaultdict(set)
    tracks: defaultdict[int, set] = defaultdict(set)
    sessions_of: defaultdict[int, set] = defaultdict(set)

    session, prev_ts, prev_artist = 0, None, None
    for sid, ts, artist, track, lday in rows:
        if prev_ts is None or ts - prev_ts > config.SESSION_GAP_S:
            session += 1
            prev_artist = None  # a new session has no "previous artist" to credit
        session_updates.append((session, sid))

        st = stats.get(artist)
        if st is None:
            gateway = prev_artist if ts >= prehistory_until and prev_artist != artist else None
            st = stats[artist] = {
                "plays": 0, "first_ts": ts, "first_lday": lday, "first_track": track,
                "prehistory": int(ts < prehistory_until), "gateway": gateway,
            }
        st["plays"] += 1
        st["last_ts"] = ts
        days[artist].add(lday)
        tracks[artist].add(track)
        sessions_of[artist].add(session)
        prev_ts, prev_artist = ts, artist

    conn.executemany("UPDATE scrobbles SET session_id = ? WHERE id = ?", session_updates)
    conn.execute("DELETE FROM artist_stats")
    conn.executemany(
        "INSERT INTO artist_stats(artist_id, plays, first_ts, last_ts, first_lday, n_tracks, n_days, n_years,"
        " n_sessions, prehistory, gateway_id, first_track_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a, s["plays"], s["first_ts"], s["last_ts"], s["first_lday"], len(tracks[a]), len(days[a]),
                len({d[:4] for d in days[a]}), len(sessions_of[a]), s["prehistory"], s["gateway"], s["first_track"],
            )
            for a, s in stats.items()
        ],
    )
    return session


def _links(conn: sqlite3.Connection) -> int:
    """Ochiai similarity between artists over shared listening sessions.

    Very long shuffle sessions would link everything to everything, so a session only contributes
    its LINK_MAX_ARTISTS_PER_SESSION most-played artists.
    """
    per_session: defaultdict[int, Counter] = defaultdict(Counter)
    for session, artist, n in conn.execute(
        "SELECT session_id, artist_id, COUNT(*) FROM scrobbles GROUP BY session_id, artist_id"
    ):
        per_session[session][artist] = n

    pair_counts: Counter = Counter()
    artist_sessions: Counter = Counter()
    for counts in per_session.values():
        artists = sorted(a for a, _ in counts.most_common(config.LINK_MAX_ARTISTS_PER_SESSION))
        artist_sessions.update(artists)
        pair_counts.update(combinations(artists, 2))

    links = [
        (a, b, shared, shared / math.sqrt(artist_sessions[a] * artist_sessions[b]))
        for (a, b), shared in pair_counts.items()
        if shared >= config.LINK_MIN_SHARED_SESSIONS
    ]
    conn.execute("DELETE FROM artist_links")
    conn.executemany("INSERT INTO artist_links(a, b, shared, score) VALUES (?, ?, ?, ?)", links)
    return len(links)
