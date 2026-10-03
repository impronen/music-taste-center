"""Data maintenance: merge duplicate artists, keep name rules for future imports, and find
likely duplicates (spelling variants such as "Sunn 0)))" vs "Sunn O)))")."""
import sqlite3
import time
import unicodedata

from . import derive
from .ingest import key

# ---------------------------------------------------------------- merging


def merge_artists(conn: sqlite3.Connection, source_id: int, target_id: int, *, rebuild: bool = True) -> dict:
    """Move everything of `source` onto `target`, delete `source`, and add a name rule so that
    imports of the source spelling (and its earlier rules) resolve to `target` from now on.

    Tracks and albums with the same normalized title are combined; a scrobble that would then
    exist twice (same minute, same track) is kept once."""
    if source_id == target_id:
        raise ValueError("can't merge an artist into itself")
    src = conn.execute("SELECT id, name, name_key, mbid FROM artists WHERE id = ?", (source_id,)).fetchone()
    dst = conn.execute("SELECT id, name, name_key, mbid FROM artists WHERE id = ?", (target_id,)).fetchone()
    if src is None or dst is None:
        raise LookupError("artist not found")
    with conn:
        moved = conn.execute("SELECT COUNT(*) FROM scrobbles WHERE artist_id = ?", (source_id,)).fetchone()[0]
        # derived rows of the source go first (they reference its tracks); rebuilt below
        conn.execute("DELETE FROM artist_links WHERE a = ? OR b = ?", (source_id, source_id))
        conn.execute("DELETE FROM artist_stats WHERE artist_id = ?", (source_id,))
        conn.execute("UPDATE artist_stats SET gateway_id = ? WHERE gateway_id = ?", (target_id, source_id))
        dropped = 0
        for tid, tkey in conn.execute("SELECT id, title_key FROM tracks WHERE artist_id = ?", (source_id,)).fetchall():
            same = conn.execute("SELECT id FROM tracks WHERE artist_id = ? AND title_key = ?", (target_id, tkey)).fetchone()
            if same is None:
                conn.execute("UPDATE tracks SET artist_id = ? WHERE id = ?", (target_id, tid))
                continue
            dropped += conn.execute(
                "DELETE FROM scrobbles WHERE track_id = ? AND EXISTS (SELECT 1 FROM scrobbles d"
                " WHERE d.track_id = ? AND d.artist_id = ? AND d.ts = scrobbles.ts)", (tid, same[0], target_id)).rowcount
            conn.execute("UPDATE scrobbles SET track_id = ?, artist_id = ? WHERE track_id = ?", (same[0], target_id, tid))
            conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
        conn.execute("UPDATE scrobbles SET artist_id = ? WHERE artist_id = ?", (target_id, source_id))
        for aid, akey in conn.execute("SELECT id, title_key FROM albums WHERE artist_id = ?", (source_id,)).fetchall():
            same = conn.execute("SELECT id FROM albums WHERE artist_id = ? AND title_key = ?", (target_id, akey)).fetchone()
            if same is None:
                conn.execute("UPDATE albums SET artist_id = ? WHERE id = ?", (target_id, aid))
                continue
            conn.execute("UPDATE scrobbles SET album_id = ? WHERE album_id = ?", (same[0], aid))
            conn.execute("DELETE FROM album_tags WHERE album_id = ?", (aid,))
            conn.execute("DELETE FROM album_info WHERE album_id = ?", (aid,))
            conn.execute("DELETE FROM albums WHERE id = ?", (aid,))
        # metadata of the source
        conn.execute("DELETE FROM artist_tags WHERE artist_id = ?", (source_id,))
        conn.execute("DELETE FROM artist_info WHERE artist_id = ?", (source_id,))
        if not dst["mbid"] and src["mbid"]:
            conn.execute("UPDATE artists SET mbid = ? WHERE id = ?", (src["mbid"], target_id))
        # name rules: earlier rules for the source now point at the target, plus one for the source itself
        conn.execute("UPDATE artist_aliases SET artist_id = ? WHERE artist_id = ?", (target_id, source_id))
        conn.execute("DELETE FROM artists WHERE id = ?", (source_id,))
        conn.execute(
            "INSERT INTO artist_aliases(name_key, name, artist_id, scrobbles, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(name_key) DO UPDATE SET artist_id = excluded.artist_id, scrobbles = excluded.scrobbles,"
            " created_at = excluded.created_at",
            (src["name_key"], src["name"], target_id, moved - dropped, int(time.time())))
        # a rule must never shadow the target's own name
        conn.execute("DELETE FROM artist_aliases WHERE name_key = ?", (dst["name_key"],))
    result = {"source": src["name"], "target": dst["name"], "target_id": target_id,
              "scrobbles_moved": moved - dropped, "duplicates_dropped": dropped}
    if rebuild:
        derive.rebuild(conn)
    return result


def merge_preview(conn: sqlite3.Connection, source_ids: list[int], target_id: int) -> dict:
    """What merge_artists would do, without changing anything (for the confirmation dialog)."""
    if target_id in source_ids:
        raise ValueError("the target can't also be a source")
    target = conn.execute("SELECT name FROM artists WHERE id = ?", (target_id,)).fetchone()
    if target is None:
        raise LookupError("artist not found")
    out = {"target": target[0], "sources": [], "scrobbles": 0, "duplicates": 0, "tracks_combined": 0, "albums_combined": 0}
    for sid in dict.fromkeys(source_ids):
        row = conn.execute("SELECT name FROM artists WHERE id = ?", (sid,)).fetchone()
        if row is None:
            raise LookupError("artist not found")
        n = conn.execute("SELECT COUNT(*) FROM scrobbles WHERE artist_id = ?", (sid,)).fetchone()[0]
        dup = conn.execute(
            "SELECT COUNT(*) FROM scrobbles s JOIN tracks t ON t.id = s.track_id"
            " JOIN tracks tt ON tt.artist_id = ? AND tt.title_key = t.title_key"
            " WHERE s.artist_id = ? AND EXISTS (SELECT 1 FROM scrobbles d WHERE d.track_id = tt.id AND d.ts = s.ts)",
            (target_id, sid)).fetchone()[0]
        tracks = conn.execute("SELECT COUNT(*) FROM tracks t WHERE t.artist_id = ? AND EXISTS (SELECT 1 FROM tracks"
                              " x WHERE x.artist_id = ? AND x.title_key = t.title_key)", (sid, target_id)).fetchone()[0]
        albums = conn.execute("SELECT COUNT(*) FROM albums al WHERE al.artist_id = ? AND EXISTS (SELECT 1 FROM albums"
                              " x WHERE x.artist_id = ? AND x.title_key = al.title_key)", (sid, target_id)).fetchone()[0]
        out["sources"].append({"id": sid, "name": row[0], "scrobbles": n})
        out["scrobbles"] += n - dup
        out["duplicates"] += dup
        out["tracks_combined"] += tracks
        out["albums_combined"] += albums
    return out


def aliases(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT x.id, x.name, x.name_key, x.artist_id, a.name AS artist, x.scrobbles, x.created_at"
        " FROM artist_aliases x JOIN artists a ON a.id = x.artist_id ORDER BY x.created_at DESC, x.id DESC")]


def add_alias(conn: sqlite3.Connection, name: str, target_id: int) -> dict:
    """A rule for a spelling that isn't in the database yet. If it is, merge instead."""
    k = key(name)
    if not k:
        raise ValueError("empty name")
    existing = conn.execute("SELECT id FROM artists WHERE name_key = ?", (k,)).fetchone()
    if existing:
        if existing[0] == target_id:
            raise ValueError("that is the artist's own name")
        return merge_artists(conn, existing[0], target_id)
    if conn.execute("SELECT 1 FROM artists WHERE id = ?", (target_id,)).fetchone() is None:
        raise LookupError("artist not found")
    with conn:
        conn.execute(
            "INSERT INTO artist_aliases(name_key, name, artist_id, scrobbles, created_at) VALUES (?, ?, ?, 0, ?)"
            " ON CONFLICT(name_key) DO UPDATE SET artist_id = excluded.artist_id",
            (k, " ".join(name.split()), target_id, int(time.time())))
    return {"source": name, "target_id": target_id, "scrobbles_moved": 0, "duplicates_dropped": 0}


def remove_alias(conn: sqlite3.Connection, alias_id: int) -> bool:
    """Stop applying a rule. Scrobbles merged earlier stay merged."""
    with conn:
        return conn.execute("DELETE FROM artist_aliases WHERE id = ?", (alias_id,)).rowcount > 0


# ---------------------------------------------------------------- finding duplicates


def loose_key(name: str) -> str:
    """Aggressive spelling-insensitive key, only for *suggesting* duplicates: accents dropped,
    0 read as o, & as "and", a leading "the" ignored, and everything but letters and digits removed."""
    s = unicodedata.normalize("NFKD", name).casefold()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ").replace("0", "o")
    words = s.split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return "".join(c for c in "".join(words) if c.isalnum())


def duplicate_candidates(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """Groups of artists that are probably one: the same loose key, or last.fm autocorrects
    them to the same name. Most-played groups first; dismissed groups are left out."""
    rows = conn.execute(
        "SELECT a.id, a.name, a.name_key, COALESCE(st.plays, 0) AS plays, i.lastfm_name FROM artists a"
        " LEFT JOIN artist_stats st ON st.artist_id = a.id"
        " LEFT JOIN artist_info i ON i.artist_id = a.id AND i.status = 'ok'").fetchall()
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first: dict[str, int] = {}
    for r in rows:
        for k in (("loose", loose_key(r["name"])), ("lastfm", key(r["lastfm_name"] or ""))):
            if not k[1]:
                continue
            if k in first:
                parent[find(r["id"])] = find(first[k])
            else:
                first[k] = r["id"]
    groups: dict[int, list] = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(r)
    dismissed = {r[0] for r in conn.execute("SELECT group_key FROM dismissed_duplicates")}
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        gkey = group_key(m["name_key"] for m in members)
        if gkey in dismissed:
            continue
        members.sort(key=lambda m: -m["plays"])
        # prefer the spelling last.fm itself uses, else the most played
        target = next((m for m in members if m["lastfm_name"] and key(m["lastfm_name"]) == m["name_key"]), members[0])
        out.append({
            "key": gkey,
            "target_id": target["id"],
            "plays": sum(m["plays"] for m in members),
            "artists": [{"id": m["id"], "name": m["name"], "plays": m["plays"], "lastfm_name": m["lastfm_name"]}
                        for m in members],
        })
    out.sort(key=lambda g: -g["plays"])
    return out[:limit]


def group_key(name_keys) -> str:
    return "\n".join(sorted(name_keys))


def dismiss(conn: sqlite3.Connection, gkey: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO dismissed_duplicates(group_key, created_at) VALUES (?, ?)",
                     (gkey, int(time.time())))
