"""Deterministic, fictional listening history in lastfm-to-csv format, for tests and demos.

Artists live in genre clusters; sessions mostly stay inside one cluster, artists unlock over
time (discoveries), and some months have an obsession. All names are invented.
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


def generate(start: str = "2019-01-01", days: int = 2800, seed: int = 7) -> list[tuple[str, str, str, int]]:
    """Return (artist, album, track, unix_ts) tuples, oldest first."""
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
    return out


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


if __name__ == "__main__":
    import sys

    sys.stdout.write(to_csv(generate()))
