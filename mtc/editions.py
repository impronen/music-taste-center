"""Remaster and edition duplicates: "Song" and "Song - Remastered 2011" (Spotify adds these to
the titles of remastered albums), "Album" and "Album (Deluxe Edition)".

Only remaster-type words count as noise. Live, acoustic, demo, instrumental, remix and "Taylor's
Version" are different recordings and stay apart. Tracks are grouped per artist by their title
without the noise; the albums they were played on say how sure a pair is (the same album once
the noise is dropped is strong evidence, different albums is weaker). Albums are grouped the same
way, with the tracks they share as evidence. A group is safe to merge unattended when it has album
evidence for every track, or nothing in `hard` (many versions, an album name that sounds like
another recording, a generic title, no album information, a compilation-style or unrelated album). A merge leaves a rule behind (track_aliases,
album_aliases) so later scrobbles of the old title land on the kept one."""
import re
import sqlite3
import time
from collections import defaultdict

from . import db, derive
from .ingest import key

# Words that make a segment (a bracket's content, or what follows " - ") an edition note.
_CORE = {"remaster", "remastered", "remasters", "remastering", "reissue", "reissued", "rerelease", "rereleased",
         "deluxe", "anniversary", "expanded", "mono", "stereo", "bonus"}
# Words that may sit next to them in the same segment.
_FILLER = {"digital", "version", "mix", "edition", "the", "original", "new", "super", "special", "limited",
           "track", "tracks", "and", "from", "album", "collectors", "collector's", "release", "re"}
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_YEAR = re.compile(r"^(?:\d{4}|\d{1,3}(?:st|nd|rd|th))$")
_BRACKETED = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]")
_DASH = re.compile(r"\s[-–—]\s")


def is_noise(segment: str) -> bool:
    """Is this bracket content / dash suffix only an edition note?"""
    words = [w.casefold().replace("’", "'") for w in _WORD.findall(segment)]
    if not words or not any(w in _CORE for w in words):
        return False
    return all(w in _CORE or w in _FILLER or _YEAR.match(w) for w in words)


def strip_edition(title: str) -> str:
    """The title without remaster / edition notes, whitespace collapsed. A title that is nothing
    but a note is returned as it is."""
    s = " ".join(title.split())
    while True:
        before = s
        s = _BRACKETED.sub(lambda m: "" if is_noise(m.group(1)) else m.group(0), s).strip()
        parts = _DASH.split(s)
        if len(parts) > 1 and is_noise(parts[-1]):
            head = re.sub(r"\s[-–—]$", "", s[: len(s) - len(parts[-1])].rstrip()).strip()
            if head.count("(") == head.count(")") and head.count("[") == head.count("]"):  # not a dash inside a bracket
                s = head
        if s == before:
            break
    return s or " ".join(title.split())


def edition_key(title: str) -> str:
    return key(strip_edition(title))


# ---------------------------------------------------------------- finding groups

_CHUNK = 500
OVERLAP_MIN = 0.5  # albums: share of the smaller album's tracks that the other has too
MAX_SAFE_VARIANTS = 3

# What makes a group not safe to merge without a look. Groups with album evidence (the tracks were
# played from the same album, the albums share their tracks) are safe whatever else they have.
_OTHER_RECORDING = re.compile(
    r"live|demo|session|unplugged|acoustic|bootleg|rehears|outtake|alternat|instrumental|remix|karaoke|tribute|concert"
    r"|\bmtv\b|\bbbc\b|\bpeel\b|b-side|rarit|unreleased"
    r"|konsert|akustinen|en vivo|directo|concierto|reimagin|re-?record|stripped|orchestra|symphon|piano|soundtrack|\bost\b"
    r"|mixtape|medley|\bdub\b", re.I)
_COMPILATION = re.compile(r"greatest|best of|collection|anthology|essential|hits|compilation|very best|singles|rarities"
                          r"|archive|box|complete|selected|ultimate|definitive", re.I)
_GENERIC_TRACKS = {"intro", "outro", "interlude", "untitled", "prelude", "overture", "finale", "theme", "prologue",
                   "epilogue", "reprise", "skit", "title track", "introduction", "end credits", "main title"}


def _chunks(ids: list[int]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def _dismissed(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT group_key FROM dismissed_duplicates")}


def _collisions(rows) -> list[list]:
    """Rows (id, artist_id, title, title_key, ...) that share an artist and a stripped title with a
    differently spelled one."""
    by: defaultdict[tuple[int, str], list] = defaultdict(list)
    for r in rows:
        by[(r["artist_id"], edition_key(r["title"]))].append(r)
    return [g for g in by.values() if len({r["title_key"] for r in g}) > 1]


def track_groups(conn: sqlite3.Connection) -> list[dict]:
    """Groups of one artist's tracks that differ only by edition notes, most played first. Each
    track lists its plays and albums; `evidence` is "same-album" when two of them were played from
    the same album (ignoring edition notes in the album title), else "other-albums"."""
    dismissed = _dismissed(conn)
    groups = _collisions(conn.execute("SELECT t.id, t.artist_id, t.title, t.title_key FROM tracks t").fetchall())
    ids = [r["id"] for g in groups for r in g]
    plays: dict[int, int] = {}
    albums: defaultdict[int, list] = defaultdict(list)
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT s.track_id, s.album_id, al.title, COUNT(*) AS n FROM scrobbles s LEFT JOIN albums al ON al.id = s.album_id"
                f" WHERE s.track_id IN ({marks}) GROUP BY s.track_id, s.album_id", chunk):
            plays[r["track_id"]] = plays.get(r["track_id"], 0) + r["n"]
            if r["album_id"] is not None:
                albums[r["track_id"]].append({"id": r["album_id"], "title": r["title"], "plays": r["n"]})
    names = dict(conn.execute("SELECT id, name FROM artists"))
    out = []
    for g in groups:
        ek = edition_key(g[0]["title"])
        gkey = f"edition-track:{g[0]['artist_id']}:{ek}"
        if gkey in dismissed:
            continue
        tracks = []
        for r in g:
            alb = sorted(albums.get(r["id"], []), key=lambda x: (-x["plays"], x["id"]))
            tracks.append({"id": r["id"], "title": r["title"], "plays": plays.get(r["id"], 0), "clean": r["title_key"] == ek,
                           "albums": alb[:3], "n_albums": len(alb)})
        tracks.sort(key=lambda t: (not t["clean"], -t["plays"], t["id"]))
        seen: defaultdict[str, set] = defaultdict(set)
        for r in g:
            for a in albums.get(r["id"], []):
                seen[edition_key(a["title"] or "")].add(r["id"])
        shared = any(len(v) > 1 for k, v in seen.items() if k)
        # tracks that share an album with another one are anchored; the others are judged on their own albums
        anchored = {tid for k, v in seen.items() if k and len(v) > 1 for tid in v}
        outsiders = [r for r in g if r["id"] not in anchored]
        hard = []
        if outsiders:
            if len(g) > MAX_SAFE_VARIANTS:
                hard.append("more than 3 versions")
            if any(_OTHER_RECORDING.search(a["title"] or "") for r in outsiders for a in albums.get(r["id"], [])):
                hard.append("an album name suggests another recording")
            if ek in _GENERIC_TRACKS:
                hard.append("a very generic title")
            if any(not albums.get(r["id"]) for r in outsiders):
                hard.append("no album information")
        out.append({"key": gkey, "artist": {"id": g[0]["artist_id"], "name": names.get(g[0]["artist_id"], "")},
                    "tracks": tracks, "target_id": tracks[0]["id"], "evidence": "same-album" if shared else "other-albums",
                    "hard": hard, "plays": sum(t["plays"] for t in tracks)})
    out.sort(key=lambda x: (bool(x["hard"]), x["evidence"] != "same-album", -x["plays"], x["key"]))
    return out


def album_groups(conn: sqlite3.Connection) -> list[dict]:
    """Groups of one artist's albums that differ only by edition notes. `overlap` is the smallest
    share of the smaller album's tracks (by title, notes ignored) that another album of the group
    also has, and `evidence` is "same-tracks" from OVERLAP_MIN up."""
    dismissed = _dismissed(conn)
    groups = _collisions(conn.execute("SELECT id, artist_id, title, title_key FROM albums").fetchall())
    ids = [r["id"] for g in groups for r in g]
    plays: dict[int, int] = {}
    tracks: defaultdict[int, set] = defaultdict(set)
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT s.album_id, t.title, COUNT(*) AS n FROM scrobbles s JOIN tracks t ON t.id = s.track_id"
                              f" WHERE s.album_id IN ({marks}) GROUP BY s.album_id, s.track_id", chunk):
            plays[r["album_id"]] = plays.get(r["album_id"], 0) + r["n"]
            tracks[r["album_id"]].add(edition_key(r["title"]))
    names = dict(conn.execute("SELECT id, name FROM artists"))
    out = []
    for g in groups:
        ek = edition_key(g[0]["title"])
        gkey = f"edition-album:{g[0]['artist_id']}:{ek}"
        if gkey in dismissed:
            continue
        albums = [{"id": r["id"], "title": r["title"], "plays": plays.get(r["id"], 0), "clean": r["title_key"] == ek,
                   "n_tracks": len(tracks.get(r["id"], ()))} for r in g]
        albums.sort(key=lambda a: (not a["clean"], -a["plays"], a["id"]))
        overlap = 1.0
        for a in albums:
            others = [tracks.get(b["id"], set()) for b in albums if b["id"] != a["id"]]
            mine = tracks.get(a["id"], set())
            if not mine:
                overlap = 0.0
                continue
            best = max((len(mine & o) / min(len(mine), len(o)) for o in others if o), default=0.0)
            overlap = min(overlap, best)
        hard = []
        if overlap < OVERLAP_MIN:
            if len(albums) > MAX_SAFE_VARIANTS:
                hard.append("more than 3 versions")
            if any(_COMPILATION.search(a["title"]) for a in albums):
                hard.append("a compilation-style title")
            if overlap == 0 and min(a["n_tracks"] for a in albums) >= 3:
                hard.append("the albums share no tracks")
        out.append({"key": gkey, "artist": {"id": g[0]["artist_id"], "name": names.get(g[0]["artist_id"], "")},
                    "albums": albums, "target_id": albums[0]["id"], "overlap": round(overlap, 2),
                    "evidence": "same-tracks" if overlap >= OVERLAP_MIN else "few-shared-tracks",
                    "hard": hard, "plays": sum(a["plays"] for a in albums)})
    out.sort(key=lambda x: (bool(x["hard"]), x["evidence"] != "same-tracks", -x["plays"], x["key"]))
    return out


# ---------------------------------------------------------------- merging


def _same_artist(conn: sqlite3.Connection, table: str, source_ids: list[int], target_id: int) -> int:
    if target_id in source_ids:
        raise ValueError("the target can't also be a source")
    ids = list(dict.fromkeys([*source_ids, target_id]))
    rows = conn.execute(f"SELECT id, artist_id FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if len(rows) != len(ids):
        raise LookupError(f"{table[:-1]} not found")
    if len({r["artist_id"] for r in rows}) != 1:
        raise ValueError(f"those {table} are by different artists; merge the artists first")
    return rows[0]["artist_id"]


def merge_tracks(conn: sqlite3.Connection, source_ids: list[int], target_id: int, *, rebuild: bool = True) -> dict:
    """Move every scrobble of the source tracks (one artist's) to the target, delete them, and
    leave a rule for each old title. A play scrobbled under both at the same minute is kept once."""
    source_ids = list(dict.fromkeys(source_ids))
    artist_id = _same_artist(conn, "tracks", source_ids, target_id)
    moved = dropped = 0
    with conn:
        db.bump(conn, "scrobbles_version")
        target = conn.execute("SELECT mbid FROM tracks WHERE id = ?", (target_id,)).fetchone()
        for sid in source_ids:
            src = conn.execute("SELECT title, title_key, mbid FROM tracks WHERE id = ?", (sid,)).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM scrobbles WHERE track_id = ?", (sid,)).fetchone()[0]
            d = conn.execute("DELETE FROM scrobbles WHERE track_id = ? AND EXISTS (SELECT 1 FROM scrobbles x"
                             " WHERE x.track_id = ? AND x.artist_id = ? AND x.ts = scrobbles.ts)",
                             (sid, target_id, artist_id)).rowcount
            conn.execute("UPDATE scrobbles SET track_id = ? WHERE track_id = ?", (target_id, sid))
            conn.execute("UPDATE artist_stats SET first_track_id = ? WHERE first_track_id = ?", (target_id, sid))
            conn.execute("UPDATE loved_title_rules SET track_id = ? WHERE track_id = ?", (target_id, sid))
            conn.execute("UPDATE track_aliases SET track_id = ? WHERE track_id = ?", (target_id, sid))
            if not target["mbid"] and src["mbid"]:
                conn.execute("UPDATE tracks SET mbid = ? WHERE id = ?", (src["mbid"], target_id))
                target = {"mbid": src["mbid"]}
            conn.execute("DELETE FROM tracks WHERE id = ?", (sid,))
            conn.execute(
                "INSERT INTO track_aliases(artist_id, title_key, title, track_id, scrobbles, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(artist_id, title_key) DO UPDATE SET track_id = excluded.track_id, title = excluded.title,"
                " scrobbles = excluded.scrobbles, created_at = excluded.created_at",
                (artist_id, src["title_key"], src["title"], target_id, n - d, int(time.time())))
            moved += n - d
            dropped += d
        # a rule must never shadow a real track's own title
        conn.execute("DELETE FROM track_aliases WHERE artist_id = ? AND title_key IN"
                     " (SELECT title_key FROM tracks WHERE artist_id = ?)", (artist_id, artist_id))
    title = conn.execute("SELECT title FROM tracks WHERE id = ?", (target_id,)).fetchone()[0]
    if rebuild:
        derive.rebuild(conn)
    return {"target": title, "target_id": target_id, "merged": len(source_ids), "scrobbles_moved": moved,
            "duplicates_dropped": dropped}


def merge_albums(conn: sqlite3.Connection, source_ids: list[int], target_id: int) -> dict:
    """Move every scrobble of the source albums (one artist's) to the target album, delete them,
    and leave a rule for each old title. Fetched info and tags of a source are kept when the
    target has none yet, else dropped."""
    source_ids = list(dict.fromkeys(source_ids))
    artist_id = _same_artist(conn, "albums", source_ids, target_id)
    moved = 0
    with conn:
        db.bump(conn, "scrobbles_version", "tags_version")
        for sid in source_ids:
            src = conn.execute("SELECT title, title_key, mbid FROM albums WHERE id = ?", (sid,)).fetchone()
            moved_here = conn.execute("UPDATE scrobbles SET album_id = ? WHERE album_id = ?", (target_id, sid)).rowcount
            if conn.execute("SELECT 1 FROM album_info WHERE album_id = ?", (target_id,)).fetchone():
                conn.execute("DELETE FROM album_info WHERE album_id = ?", (sid,))
            else:
                conn.execute("UPDATE album_info SET album_id = ? WHERE album_id = ?", (target_id, sid))
            if conn.execute("SELECT 1 FROM album_tags WHERE album_id = ?", (target_id,)).fetchone():
                conn.execute("DELETE FROM album_tags WHERE album_id = ?", (sid,))
            else:
                conn.execute("UPDATE album_tags SET album_id = ? WHERE album_id = ?", (target_id, sid))
            conn.execute("UPDATE album_aliases SET album_id = ? WHERE album_id = ?", (target_id, sid))
            if src["mbid"] and not conn.execute("SELECT mbid FROM albums WHERE id = ?", (target_id,)).fetchone()[0]:
                conn.execute("UPDATE albums SET mbid = ? WHERE id = ?", (src["mbid"], target_id))
            conn.execute("DELETE FROM albums WHERE id = ?", (sid,))
            conn.execute(
                "INSERT INTO album_aliases(artist_id, title_key, title, album_id, scrobbles, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(artist_id, title_key) DO UPDATE SET album_id = excluded.album_id, title = excluded.title,"
                " scrobbles = excluded.scrobbles, created_at = excluded.created_at",
                (artist_id, src["title_key"], src["title"], target_id, moved_here, int(time.time())))
            moved += moved_here
        conn.execute("DELETE FROM album_aliases WHERE artist_id = ? AND title_key IN"
                     " (SELECT title_key FROM albums WHERE artist_id = ?)", (artist_id, artist_id))
    title = conn.execute("SELECT title FROM albums WHERE id = ?", (target_id,)).fetchone()[0]
    return {"target": title, "target_id": target_id, "merged": len(source_ids), "scrobbles_moved": moved}


def rules(conn: sqlite3.Connection) -> dict:
    """The track and album rules, newest first, with what each points at."""
    return {
        "tracks": [dict(r) for r in conn.execute(
            "SELECT x.id, x.title, x.scrobbles, x.created_at, t.id AS target_id, t.title AS target, a.id AS artist_id,"
            " a.name AS artist FROM track_aliases x JOIN tracks t ON t.id = x.track_id JOIN artists a ON a.id = x.artist_id"
            " ORDER BY x.created_at DESC, x.id DESC")],
        "albums": [dict(r) for r in conn.execute(
            "SELECT x.id, x.title, x.scrobbles, x.created_at, t.id AS target_id, t.title AS target, a.id AS artist_id,"
            " a.name AS artist FROM album_aliases x JOIN albums t ON t.id = x.album_id JOIN artists a ON a.id = x.artist_id"
            " ORDER BY x.created_at DESC, x.id DESC")],
    }


def remove_rule(conn: sqlite3.Connection, kind: str, rule_id: int) -> bool:
    """Stop applying a rule. Scrobbles merged earlier stay merged."""
    table = {"tracks": "track_aliases", "albums": "album_aliases"}[kind]
    with conn:
        removed = conn.execute(f"DELETE FROM {table} WHERE id = ?", (rule_id,)).rowcount > 0
        if removed:
            db.bump(conn, "scrobbles_version")  # loved tracks under that title stop resolving
        return removed
