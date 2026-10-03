"""Cyclical patterns on synthetic history with known habits (synthetic.generate(rhythms=True)):
metal in winter, jazz in the morning, indie at weekends, and a December-only artist."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, derive, ingest, rhythms
from mtc.api import create_app
from mtc.ingest import Scrobble
from tests import synthetic

GENRES = {"suomirock": "finnish rock", "electronic": "electronic", "jazz": "jazz", "metal": "black metal",
          "indie": "indie pop"}


def add_tags(conn, genre_of: dict[str, str], place_of: dict[str, str] | None = None) -> None:
    """Tag artists by name, as a tag fetch would (artist_info marks them looked up)."""
    place_of = place_of or {}
    with conn:
        ids = {}
        for name, kind in [(g, "genre") for g in set(genre_of.values())] + [(p, "place") for p in set(place_of.values())]:
            ids[name] = conn.execute("INSERT INTO tags(name, kind) VALUES (?, ?)", (name, kind)).lastrowid
        for aid, name in conn.execute("SELECT id, name FROM artists").fetchall():
            if name in genre_of:
                conn.execute("INSERT INTO artist_tags VALUES (?, ?, 100)", (aid, ids[genre_of[name]]))
            if name in place_of:
                conn.execute("INSERT INTO artist_tags VALUES (?, ?, 50)", (aid, ids[place_of[name]]))
            conn.execute("INSERT INTO artist_info(artist_id, status, fetched_at, tags_fetched_at)"
                         " VALUES (?, 'ok', 0, 1)", (aid,))


def lift(matrix: dict, name: str, col: str) -> float:
    row = next(r for r in matrix["rows"] if r["name"] == name)
    return row["cells"][matrix["cols"].index(col)]["lift"]


class RhythmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "r.db"
        cls.conn = db.connect(cls.path)
        ingest.import_csv_text(cls.conn, synthetic.to_csv(synthetic.generate(rhythms=True)), label="t", encoding="utf-8")
        cluster = {a: c for c, names in synthetic.CLUSTERS.items() for a in names}
        genre_of = {a: GENRES[c] for a, c in cluster.items()} | {synthetic.SEASONAL_ARTIST: "christmas"}
        place_of = {a: "finnish" for a, c in cluster.items() if c in ("suomirock", "metal")}
        add_tags(cls.conn, genre_of, place_of)
        cls.o = rhythms.overview(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_winter_genre_recurs(self):
        winter = next(s for s in self.o["seasons"] if s["key"] == "winter")
        metal = next(g for g in winter["genres"] if g["name"] == "black metal")
        self.assertGreater(metal["lift"], 1.3)
        self.assertGreaterEqual(metal["up"], metal["years"] - 1)
        self.assertGreater(lift(self.o["months"], "black metal", "Jan"), 1.3)
        self.assertLess(lift(self.o["months"], "black metal", "Jul"), 1)
        summer = next(s for s in self.o["seasons"] if s["key"] == "summer")
        self.assertNotIn("black metal", [g["name"] for g in summer["genres"]])
        self.assertIn(synthetic.SEASONAL_ARTIST, [a["name"] for a in winter["artists"]])

    def test_time_of_day_and_week(self):
        self.assertGreater(lift(self.o["dayparts"], "jazz", "Morning"), 1.3)
        self.assertGreater(lift(self.o["weekparts"], "indie pop", "Weekend"), 1.2)
        self.assertLess(lift(self.o["weekparts"], "indie pop", "Weekdays"), 1)

    def test_drift_and_diversity(self):
        for y in self.o["drift"]["years"]:
            self.assertAlmostEqual(sum(y["shares"]) + y["other"], 1, places=2)
        self.assertEqual(len(self.o["drift"]["series"]), len(set(GENRES.values())) + 1)  # + christmas
        d = self.o["diversity"]
        self.assertTrue(all(1 <= m["effective"] <= 6 for m in d["monthly"]))
        self.assertEqual(len(d["by_month"]), 12)
        self.assertGreater(self.o["coverage"], 0.9)  # the one-hit "... Project" artists have no tags

    def test_seasonal_artist_and_coming_up(self):
        r = rhythms.seasonal_artists(self.conn, today=date(2025, 11, 20))
        tonttu = next(a for a in r["artists"] if a["name"] == synthetic.SEASONAL_ARTIST)
        self.assertEqual(tonttu["peak"][:2], "12")
        self.assertLess(tonttu["start"], tonttu["peak"])  # starts a little before the peak
        self.assertEqual(tonttu["years_agree"], tonttu["years"])
        self.assertEqual(tonttu["status"], "soon")
        self.assertIn(tonttu["id"], [a["id"] for a in r["coming_up"]])
        self.assertEqual(next(a for a in rhythms.seasonal_artists(self.conn, today=date(2025, 12, 12))["artists"]
                              if a["id"] == tonttu["id"])["status"], "now")
        summer = rhythms.seasonal_artists(self.conn, today=date(2025, 7, 1))
        self.assertNotIn(tonttu["id"], [a["id"] for a in summer["coming_up"]])
        # everyday artists are not seasonal
        self.assertNotIn("Kärpäset", [a["name"] for a in r["artists"]])

    def test_places_compare_with_everything(self):
        place = rhythms.overview(self.conn, "place")
        self.assertEqual([r["name"] for r in place["months"]["rows"]], ["finnish"])
        lifts = [c["lift"] for c in place["months"]["rows"][0]["cells"]]
        self.assertGreater(max(lifts) - min(lifts), 0.05)  # metal's winter lifts "finnish" in winter
        self.assertIsNone(place["diversity"])

    def test_api(self):
        with TestClient(create_app(self.path)) as client:
            self.assertEqual(client.get("/api/rhythms").json()["kind"], "genre")
            self.assertEqual(client.get("/api/rhythms", params={"kind": "place"}).json()["kind"], "place")
            self.assertEqual(client.get("/api/rhythms", params={"kind": "year"}).status_code, 422)
            self.assertIn("coming_up", client.get("/api/rhythms/artists").json())
            tag_id = self.conn.execute("SELECT id FROM tags WHERE name = 'black metal'").fetchone()[0]
            months = client.get(f"/api/tags/{tag_id}").json()["months"]
            self.assertEqual(len(months), 12)
            self.assertGreater(months[0]["lift"], 1.3)


class MethodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_growth_over_the_years_is_not_seasonal(self):
        # "New" grows every year and history starts in March and ends in October, so the
        # months Mar–Oct contain the latest, biggest year and Nov–Feb don't. Pooled shares
        # would call that a summer peak; per-year expectations must not.
        rows, day = [], date(2019, 3, 1)
        while day <= date(2026, 10, 31):
            t = int(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp())
            rows += [Scrobble("Old", f"o{i}", t + i * 60) for i in range(4)]
            rows += [Scrobble("New", f"n{i}", t + 600 + i * 60) for i in range(day.year - 2018)]
            day += timedelta(days=1)
        ingest.ingest_records(self.conn, rows, source="test")
        derive.rebuild(self.conn)
        add_tags(self.conn, {"Old": "old genre", "New": "new genre"})
        months = rhythms.overview(self.conn)["months"]
        for name in ("old genre", "new genre"):
            lifts = [c["lift"] for c in next(r for r in months["rows"] if r["name"] == name)["cells"]]
            self.assertTrue(all(0.97 < x < 1.03 for x in lifts), (name, lifts))

    def test_without_tags_and_cache_refresh(self):
        ingest.ingest_records(self.conn, [Scrobble("A", "x", 1_700_000_000 + i * 3600) for i in range(50)], source="test")
        derive.rebuild(self.conn)
        empty = rhythms.overview(self.conn)
        self.assertEqual((empty["coverage"], empty["seasons"], empty["months"]["rows"]), (0, [], []))
        self.assertEqual(rhythms.seasonal_artists(self.conn)["artists"], [])
        add_tags(self.conn, {"A": "ambient"})  # a tag fetch must invalidate the cache
        self.assertEqual(rhythms.overview(self.conn)["coverage"], 1)
        year = self.conn.execute("INSERT INTO tags(name, kind) VALUES ('2019', 'year')").lastrowid
        self.assertIsNone(rhythms.tag_months(self.conn, year))


if __name__ == "__main__":
    unittest.main()
