"""Release decades on synthetic history: each genre cluster's albums get a fixed decade, and the
cluster preference rotates yearly (synthetic.generate), so which decade is in vogue is known."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, decades, ingest
from mtc.api import create_app
from tests import synthetic

DECADE_OF = {"suomirock": "1985", "electronic": "1994-05", "jazz": "1962-03-09", "metal": "2003", "indie": "2013"}


def add_release_dates(conn, undated_cluster: str | None = None) -> None:
    """Date every album by its artist's cluster, as a release fetch would (album_info rows, tags_version bump)."""
    cluster = {a: c for c, names in synthetic.CLUSTERS.items() for a in names}
    with conn:
        for album_id, artist in conn.execute("SELECT al.id, ar.name FROM albums al JOIN artists ar ON ar.id = al.artist_id").fetchall():
            c = cluster.get(artist) or next((c for c in synthetic.CLUSTERS if artist.endswith(f"{c.title()} Project")), None)
            if c is None or c == undated_cluster:
                continue
            conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date, release_date_source, mb_status)"
                         " VALUES (?, 'ok', 0, ?, 'musicbrainz', 'ok')", (album_id, DECADE_OF[c]))
        db.bump(conn, "tags_version")


def cell(m: dict, decade: str, year: str) -> float | None:
    row = next(r for r in m["rows"] if r["name"] == decade)
    return row["cells"][m["cols"].index(year)]["lift"]


class DecadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "d.db"
        cls.conn = db.connect(cls.path)
        ingest.import_csv_text(cls.conn, synthetic.to_csv(synthetic.generate()), label="t", encoding="utf-8")
        add_release_dates(cls.conn, undated_cluster="jazz")
        cls.o = decades.overview(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_coverage_counts_only_plays_of_dated_albums(self):
        o = self.o
        total = self.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
        self.assertEqual(o["total"], total)
        self.assertEqual(o["covered"], sum(d["plays"] for d in o["decades"]))
        self.assertTrue(0.5 < o["coverage"] < 0.95)  # jazz is undated
        self.assertNotIn("1960s", [d["label"] for d in o["decades"]])

    def test_decades_and_release_years_use_the_year_part_of_any_date_precision(self):
        self.assertEqual({d["label"] for d in self.o["decades"]}, {"1980s", "1990s", "2000s", "2010s"})
        by_year = {y["year"]: y["plays"] for y in self.o["years"]}
        self.assertGreater(by_year[1985], 0)   # 'YYYY'
        self.assertGreater(by_year[1994], 0)   # 'YYYY-MM'
        self.assertEqual(by_year[1990], 0)     # the range between first and last year is filled in
        self.assertEqual(sum(by_year.values()), self.o["covered"])
        self.assertGreater(min(d["albums"] for d in self.o["decades"]), 0)

    def test_the_decade_of_the_boosted_cluster_is_above_usual_that_year(self):
        # generate() boosts the cluster i where (year offset + i) % 5 == 0: 2019 = suomirock (1980s), 2020 = indie (2010s)
        m = self.o["matrix"]
        self.assertGreater(cell(m, "1980s", "2019"), 1.1)
        self.assertLess(cell(m, "1980s", "2020"), 1.0)
        self.assertGreater(cell(m, "2010s", "2020"), 1.1)

    def test_decade_share_per_listening_year_sums_to_at_most_one(self):
        d = self.o["drift"]
        self.assertEqual(len(d["series"]), 4)
        for y in d["years"]:
            self.assertAlmostEqual(sum(y["shares"]) + y["other"], 1.0, places=2)

    def test_vogue_compares_the_last_twelve_months_with_everything_before(self):
        v = self.o["vogue"]
        self.assertTrue(v["enough"])
        lifts = {x["label"]: x["lift"] for x in v["decades"]}
        self.assertEqual(max(lifts, key=lifts.get), v["decades"][0]["label"])
        self.assertTrue(all(x["lift"] >= decades.VOGUE_LIFT for x in v["in_vogue"]))

    def test_no_dates_at_all_is_an_empty_answer_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "e.db")
            ingest.import_csv_text(conn, synthetic.to_csv(synthetic.generate(days=30)), label="t", encoding="utf-8")
            o = decades.overview(conn)
            conn.close()
        self.assertEqual((o["covered"], o["coverage"]), (0, 0.0))

    def test_a_new_release_fetch_refreshes_the_cached_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "c.db")
            ingest.import_csv_text(conn, synthetic.to_csv(synthetic.generate(days=60)), label="t", encoding="utf-8")
            self.assertEqual(decades.overview(conn)["covered"], 0)
            add_release_dates(conn)
            self.assertGreater(decades.overview(conn)["covered"], 0)
            conn.close()

    def test_nonsense_release_years_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "b.db")
            ingest.import_csv_text(conn, synthetic.to_csv(synthetic.generate(days=30)), label="t", encoding="utf-8")
            with conn:
                for (album_id,) in conn.execute("SELECT id FROM albums").fetchall():
                    conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date) VALUES (?, 'ok', 0, '0001')", (album_id,))
                db.bump(conn, "tags_version")
            self.assertEqual(decades.overview(conn)["covered"], 0)
            conn.close()

    def test_api_serves_the_overview(self):
        with TestClient(create_app(self.path)) as c:
            r = c.get("/api/decades")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["peak"], self.o["peak"])


if __name__ == "__main__":
    unittest.main()
