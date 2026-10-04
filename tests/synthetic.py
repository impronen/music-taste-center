"""Deterministic, fictional listening history in lastfm-to-csv format, for tests and demos.

Artists live in genre clusters; sessions mostly stay inside one cluster, artists unlock over
time (discoveries), and some months have an obsession. All names are invented.

`python -m tests.synthetic` prints a CSV; `python -m tests.synthetic --demo-db PATH` writes a complete
fictional library (scrobbles, genre tags and release dates) for trying the app and for screenshots.
"""
import csv
import io
import random
from datetime import datetime, timedelta, timezone

CLUSTERS = {
    "suomirock": ["Kärpäset", "Pöllö ja Yö", "Routa-Akustiikka", "Häkä", "Mustikkasuo", "Jäänsärkijät",
                  "Lumikko", "Kivikasvo", "Särö", "Pakkasyö"],
    "electronic": ["Neon Cartography", "Vapor Index", "Lumen Drift", "Subgrid", "Kaiku Systems",
                   "Polar Modular", "Glasswave", "Halcyon Static"],
    "jazz": ["Åkerlund Trio", "The Blue Meridian", "Saari Quartet", "Velvet Arithmetic", "Kosmos Ensemble",
             "Midnight Lattice"],
    "metal": ["Hautausmaa", "Iron Fjord", "Ruoste", "Grimtide", "Kalmankarhu", "Obsidian Choir", "Myrskyn Silmä"],
    "indie": ["Paper Lanterns, Inc.", "The Quiet Arcade", "Sunday Botany", "Hölmö", "Little Comets Club",
              "Window Seat", "Marigold \"Mari\" Lane", "Teal Atlas"],
}
ADJ = ["Kylmä", "Silver", "Hidden", "Viimeinen", "Electric", "Hollow", "Öinen", "Golden", "Broken", "Ääretön"]
NOUN = ["Ranta", "Signal", "Harbour", "Tähdet", "Garden", "Machine", "Sydän", "River", "Lumi", "Echo"]


def _catalog(rng: random.Random) -> dict[str, dict]:
    catalog = {}
    for cluster, artists in CLUSTERS.items():
        for i, name in enumerate(artists):
            albums = [f"{rng.choice(ADJ)} {rng.choice(NOUN)}" for _ in range(rng.randint(1, 4))]
            tracks = []
            for album in albums:
                tracks += [(f"{rng.choice(ADJ)} {rng.choice(NOUN)} {n}", album) for n in range(1, rng.randint(5, 11))]
            unlock = 0 if i < 3 else rng.randint(60, 2400)  # days after start
            catalog[name] = {
                "cluster": cluster,
                "weight": 1.0 / (i + 1) ** 0.8,
                "unlock": unlock,
                # some artists fall out of rotation: fodder for "forgotten" and "rediscover"
                "retire": unlock + rng.randint(200, 900) if i >= 1 and rng.random() < 0.4 else 10**9,
                "tracks": tracks,
                "favourites": rng.sample(range(len(tracks)), min(3, len(tracks))),
            }
        one_hit = f"{rng.choice(ADJ)} {cluster.title()} Project"
        catalog[one_hit] = {"cluster": cluster, "weight": 0.25, "unlock": rng.randint(30, 900), "retire": 10**9,
                            "tracks": [(f"{rng.choice(NOUN)} Anthem", "Single")], "favourites": [0]}
    return catalog


SEASONAL_ARTIST = "Tonttu Orchestra"  # only ever played in December


def generate(start: str = "2019-01-01", days: int = 2800, seed: int = 7,
             rhythms: bool = False) -> list[tuple[str, str, str, int]]:
    """Return (artist, album, track, unix_ts) tuples, oldest first.

    rhythms=True layers cyclical habits on top of the same base history (separate RNG):
    metal in winter, jazz in the morning, indie at weekends, and a December-only artist."""
    rng = random.Random(seed)
    catalog = _catalog(rng)
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    clusters = list(CLUSTERS)
    out = []
    obsession = None
    for d in range(days):
        day = t0 + timedelta(days=d)
        if d % 30 == 0:
            obsession = rng.choice(list(catalog)) if rng.random() < 0.4 else None
            if obsession and not catalog[obsession]["unlock"] <= d < catalog[obsession]["retire"]:
                obsession = None
        # Taste drifts: cluster preference rotates slowly over the years.
        prefs = [1 + 0.9 * ((d // 365 + i) % len(clusters) == 0) + rng.random() * 0.3 for i in range(len(clusters))]
        for _ in range(rng.choices([0, 1, 2, 3], [0.15, 0.45, 0.3, 0.1])[0]):
            ts = day + timedelta(hours=rng.choice([7, 8, 12, 17, 18, 20, 21, 22, 23]), minutes=rng.randint(0, 59))
            cluster = rng.choices(clusters, prefs)[0]
            pool = [a for a, v in catalog.items() if v["cluster"] == cluster and v["unlock"] <= d < v["retire"]]
            weights = [catalog[a]["weight"] for a in pool]
            for _ in range(rng.randint(4, 22)):
                if obsession and rng.random() < 0.35:
                    artist = obsession
                elif rng.random() < 0.06:  # cross-genre stray
                    artist = rng.choice([a for a, v in catalog.items() if v["unlock"] <= d < v["retire"]])
                else:
                    artist = rng.choices(pool, weights)[0]
                info = catalog[artist]
                idx = rng.choice(info["favourites"]) if rng.random() < 0.4 else rng.randrange(len(info["tracks"]))
                track, album = info["tracks"][idx]
                out.append((artist, album, track, int(ts.timestamp())))
                ts += timedelta(seconds=rng.randint(150, 330))
    if rhythms:
        out = sorted(out + _rhythms(out, t0, days, seed), key=lambda r: r[3])
    return out


def _rhythms(base: list, t0: datetime, days: int, seed: int) -> list:
    rng = random.Random(seed * 1000 + 1)
    seen: dict[str, list] = {}
    for artist, album, track, _ in base:
        seen.setdefault(artist, []).append((album, track))
    pick = {c: [a for a in CLUSTERS[c][:3] if a in seen] for c in ("metal", "jazz", "indie")}
    extra = []

    def session(day, hour_utc, cluster, n):
        ts = day + timedelta(hours=hour_utc, minutes=rng.randint(0, 50))
        for _ in range(n):
            artist = rng.choice(pick[cluster])
            album, track = rng.choice(seen[artist])
            extra.append((artist, album, track, int(ts.timestamp())))
            ts += timedelta(seconds=rng.randint(150, 330))

    xmas = [(f"{w} {n}", "Joulun Kaiku") for w in ("Kuusen", "Lumen", "Tähden") for n in ("Laulu", "Valssi", "Kello")]
    for d in range(days):
        day = t0 + timedelta(days=d)
        if day.month in (12, 1, 2) and rng.random() < 0.55:
            session(day, 19, "metal", rng.randint(6, 14))       # dark evenings
        if rng.random() < 0.3:
            session(day, 5, "jazz", rng.randint(4, 9))          # 07-08 local time
        if day.weekday() >= 5 and rng.random() < 0.6:
            session(day, 11, "indie", rng.randint(6, 14))       # weekend middays
        if day.month == 12 and day.day <= 26 and rng.random() < 0.7:
            ts = day + timedelta(hours=16, minutes=rng.randint(0, 50))
            for _ in range(rng.randint(2, 6)):
                album_track = rng.choice(xmas)
                extra.append((SEASONAL_ARTIST, album_track[1], album_track[0], int(ts.timestamp())))
                ts += timedelta(seconds=rng.randint(150, 330))
    return extra


def to_csv(rows: list[tuple[str, str, str, int]], now_playing: bool = True) -> str:
    """Render like lastfm-to-csv: BOM, no header, newest first, last.fm text dates."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    ordered = sorted(rows, key=lambda r: -r[3])
    if now_playing and ordered:
        w.writerow([ordered[0][0], ordered[0][1], ordered[0][2], ""])
    for artist, album, track, ts in ordered:
        w.writerow([artist, album, track, datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y, %H:%M")])
    return "﻿" + buf.getvalue()


# Fictional metadata for the demo library: per cluster a main genre, some sub-genres, and a span of release years.
DEMO_GENRES = {
    "suomirock": ("finnish rock", ["rock", "pop rock", "alternative"]),
    "electronic": ("electronic", ["ambient", "idm", "synthpop"]),
    "jazz": ("jazz", ["instrumental", "bebop"]),
    "metal": ("black metal", ["metal", "doom metal"]),
    "indie": ("indie pop", ["indie", "dream pop", "lo-fi"]),
}
DEMO_YEARS = {"suomirock": (1978, 1996), "electronic": (1991, 2009), "jazz": (1955, 1973),
              "metal": (1996, 2016), "indie": (2007, 2025)}


def build_demo_db(path, seed: int = 7) -> None:
    """Write a fresh database with a fictional history plus genre tags and release dates, as a
    fetch from last.fm and MusicBrainz would leave it. Refuses to touch an existing file."""
    import os
    import time

    from mtc import db, ingest

    if os.path.exists(path):
        raise FileExistsError(f"{path} already exists; pick a new file so no real data is touched")
    conn = db.connect(path)
    try:
        ingest.import_csv_text(conn, to_csv(generate(seed=seed, rhythms=True)), label="demo", encoding="utf-8")
        cluster_of = {a: c for c, names in CLUSTERS.items() for a in names}
        now = int(time.time())
        with conn:
            tag_ids = {}

            def tag(name, kind):
                if name not in tag_ids:
                    tag_ids[name] = conn.execute("INSERT INTO tags(name, kind) VALUES (?, ?)", (name, kind)).lastrowid
                return tag_ids[name]

            for artist_id, name in conn.execute("SELECT id, name FROM artists").fetchall():
                cluster = cluster_of.get(name) or next((c for c in CLUSTERS if name.endswith(f"{c.title()} Project")), None)
                if name == SEASONAL_ARTIST:
                    genres = [("christmas", 100)]
                elif cluster:
                    main, subs = DEMO_GENRES[cluster]
                    genres = [(main, 100), (subs[artist_id % len(subs)], 30 + artist_id * 7 % 25)]
                else:
                    continue
                for genre, weight in genres:
                    conn.execute("INSERT INTO artist_tags VALUES (?, ?, ?)", (artist_id, tag(genre, "genre"), weight))
                if cluster in ("suomirock", "metal"):
                    conn.execute("INSERT INTO artist_tags VALUES (?, ?, 60)", (artist_id, tag("finnish", "place")))
                conn.execute("INSERT INTO artist_info(artist_id, status, fetched_at, tags_fetched_at) VALUES (?, 'ok', ?, ?)",
                             (artist_id, now, now))
            for album_id, artist in conn.execute(
                    "SELECT al.id, ar.name FROM albums al JOIN artists ar ON ar.id = al.artist_id").fetchall():
                cluster = cluster_of.get(artist) or next((c for c in CLUSTERS if artist.endswith(f"{c.title()} Project")), None)
                rng = random.Random(seed * 7919 + album_id)
                if rng.random() < 0.06 or cluster is None:  # the odd album MusicBrainz doesn't know
                    conn.execute("INSERT INTO album_info(album_id, status, fetched_at, mb_status, mb_fetched_at)"
                                 " VALUES (?, 'ok', ?, 'not_found', ?)", (album_id, now, now))
                    continue
                year = rng.randint(*DEMO_YEARS[cluster])
                precision = rng.random()
                if precision < 0.45:
                    date, source = f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", "musicbrainz"
                elif precision < 0.75:
                    date, source = f"{year}-{rng.randint(1, 12):02d}", "musicbrainz"
                else:
                    date, source = str(year), "tag"  # a year tag was all there was
                conn.execute(
                    "INSERT INTO album_info(album_id, status, fetched_at, release_date, release_date_source, release_type,"
                    " mb_status, mb_fetched_at) VALUES (?, 'ok', ?, ?, ?, 'Album', ?, ?)",
                    (album_id, now, date, source, "ok" if source == "musicbrainz" else "not_found", now))
            db.bump(conn, "tags_version")
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    if "--demo-db" in sys.argv:
        target = sys.argv[sys.argv.index("--demo-db") + 1:][:1]
        if not target:
            sys.exit("usage: python -m tests.synthetic --demo-db PATH")
        try:
            build_demo_db(target[0])
        except FileExistsError as exc:
            sys.exit(str(exc))
        print(f"Wrote {target[0]}")
    else:
        sys.stdout.write(to_csv(generate(rhythms="--rhythms" in sys.argv)))
