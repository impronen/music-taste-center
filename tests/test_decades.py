"""Release decades on synthetic history: each genre cluster's albums get a fixed decade, and the
cluster preference rotates yearly (synthetic.generate), so which decade is in vogue is known."""
import tempfile
import unittest
from datetime import datetime
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
        self.assertEqual(v["min_plays"], decades.MIN_RECENT_PLAYS)
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



def noon(day: str) -> int:
    return int(datetime.fromisoformat(day + "T12:00:00+00:00").timestamp())


def library(rows: list[tuple], dates: dict[str, str]):
    """Plays as (album, day) or (album, day, artist) and release dates by album title: one track per play, as a temp DB."""
    tmp = tempfile.TemporaryDirectory()
    conn = db.connect(Path(tmp.name) / "a.db")
    plays = [(r[2] if len(r) > 2 else "Artist", r[0], f"t{i}", noon(r[1])) for i, r in enumerate(rows)]
    ingest.import_csv_text(conn, synthetic.to_csv(plays, now_playing=False), label="t", encoding="utf-8")
    with conn:
        for album_id, title in conn.execute("SELECT id, title FROM albums").fetchall():
            if title in dates:
                conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date) VALUES (?, 'ok', 0, ?)",
                             (album_id, dates[title]))
        db.bump(conn, "tags_version")
    return tmp, conn


class AlbumAgeTests(unittest.TestCase):
    def age(self, rows, dates):
        tmp, conn = library(rows, dates)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        return decades.album_age(conn)

    def shares(self, a, year="2022"):
        return next(y for y in a["years"] if y["year"] == year)["shares"]

    def test_plays_land_in_the_age_buckets_by_whole_months(self):
        a = self.age([("New", "2022-06-01"), ("Recent", "2022-06-01"), ("Old", "2022-06-01"), ("Ancient", "2022-06-01")],
                     {"New": "2022-03-01", "Recent": "2019-06-01", "Old": "2012-06-01", "Ancient": "1985-06-01"})
        self.assertEqual(self.shares(a), [0.25, 0.25, 0.25, 0.25])
        self.assertEqual(a["covered"], 4)
        self.assertEqual([b["name"] for b in a["buckets"]], [b[0] for b in decades.AGE_BUCKETS])

    def test_a_bucket_boundary_belongs_to_the_older_bucket(self):
        # exactly one year (across a leap day), a day short of it, and exactly five and twenty years
        a = self.age([("A", "2021-06-01"), ("B", "2021-06-01"), ("C", "2022-06-01"), ("D", "2022-06-01")],
                     {"A": "2020-06-01", "B": "2020-06-02", "C": "2017-06-01", "D": "2002-06-01"})
        self.assertEqual(self.shares(a, "2021"), [0.5, 0.5, 0.0, 0.0])
        self.assertEqual(self.shares(a, "2022"), [0.0, 0.0, 0.5, 0.5])

    def test_year_only_dates_count_as_the_first_of_july(self):
        a = self.age([("Y", "2022-06-15"), ("Y", "2022-08-01")], {"Y": "2021"})  # 349 and 396 days after 1 July 2021
        self.assertEqual(self.shares(a), [0.5, 0.5, 0.0, 0.0])
        self.assertEqual(a["approximate_share"], 1.0)

    def test_a_play_before_the_release_date_counts_as_age_zero(self):
        a = self.age([("Future", "2022-01-10")], {"Future": "2022-03-01"})
        self.assertEqual(self.shares(a), [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(a["new_share"], 1.0)

    def test_full_dates_are_not_approximate(self):
        a = self.age([("Exact", "2022-06-01")], {"Exact": "2020-01-01"})
        self.assertEqual(a["approximate_share"], 0.0)

    def test_median_age_needs_enough_plays_that_year(self):
        few = self.age([("A", "2022-06-01")] * 5, {"A": "2012-06-01"})
        self.assertIsNone(few["years"][0]["median_years"])
        self.assertEqual(few["min_year_plays"], decades.MIN_AGE_YEAR_PLAYS)
        n = decades.MIN_AGE_YEAR_PLAYS
        many = self.age([("A", "2022-06-01")] * n, {"A": "2012-06-01"})
        self.assertAlmostEqual(many["years"][0]["median_years"], 10.0, delta=0.1)
        self.assertAlmostEqual(many["median_years"], 10.0, delta=0.1)

    def test_each_listening_year_is_separate(self):
        a = self.age([("A", "2021-06-01"), ("A", "2023-06-01")], {"A": "2020-06-01"})
        self.assertEqual([y["year"] for y in a["years"]], ["2021", "2023"])
        self.assertEqual((self.shares(a, "2021"), self.shares(a, "2023")), ([0.0, 1.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]))

    def test_undated_albums_and_nonsense_dates_are_ignored(self):
        a = self.age([("A", "2022-06-01"), ("B", "2022-06-01"), ("C", "2022-06-01")], {"A": "2020-01-01", "B": "0001", "C": "20xx"})
        self.assertEqual((a["covered"], a["total"]), (1, 3))

    def test_no_dates_is_an_empty_answer(self):
        a = self.age([("A", "2022-06-01")], {})
        self.assertEqual((a["covered"], a["coverage"]), (0, 0.0))

    def test_api_serves_it(self):
        tmp, conn = library([("A", "2022-06-01")], {"A": "2020-01-01"})
        conn.close()
        with TestClient(create_app(Path(tmp.name) / "a.db")) as c:
            r = c.get("/api/decades/age")
        tmp.cleanup()
        self.assertEqual((r.status_code, r.json()["covered"]), (200, 1))


class DiscoveryLagTests(unittest.TestCase):
    ANCHOR = [("Anchor", "2020-01-05", "Veteran")]  # the first scrobbles ever: Veteran was already in rotation

    def lag(self, rows, dates):
        tmp, conn = library(self.ANCHOR + rows, {"Anchor": "1999-01-01"} | dates)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        return decades.discovery_lag(conn)

    def test_artists_already_in_rotation_when_tracking_began_are_left_out(self):
        a = self.lag([("B", "2022-06-01", "Newcomer")], {"B": "2022-05-20"})
        self.assertEqual((a["covered"], a["excluded"]), (1, 1))

    def test_an_album_released_after_tracking_began_counts_even_for_a_veteran_artist(self):
        rows = [("Fresh", "2021-03-05", "Veteran"), ("Backlist", "2021-03-05", "Veteran")]
        a = self.lag(rows, {"Fresh": "2021-03-01", "Backlist": "2005-01-01"})  # tracking began 2020-01-05
        self.assertEqual((a["covered"], a["excluded"]), (1, 2))  # Anchor and Backlist are left out
        self.assertEqual(a["buckets"][0]["albums"], 1)

    def test_lag_is_whole_days_from_release_to_the_first_play(self):
        a = self.lag([("B", "2022-06-10", "Newcomer"), ("B", "2022-06-01", "Newcomer")] * 3, {"B": "2022-05-20"})
        self.assertEqual(len(a["on_release"]), 1)
        self.assertEqual((a["on_release"][0]["lag_days"], a["on_release"][0]["plays"]), (12, 6))  # first play counts, not the last

    def test_buckets_and_median(self):
        rows = [(t, "2022-06-01", "Newcomer") for t in "ABCD"]
        a = self.lag(rows, {"A": "2022-04-01", "B": "2020-06-01", "C": "2010-06-01", "D": "1990-06-01"})
        self.assertEqual([b["albums"] for b in a["buckets"]], [1, 1, 1, 1])
        self.assertEqual((a["first_year_share"], a["late_count"]), (0.25, 1))
        self.assertAlmostEqual(a["median_years"], 12.0, delta=0.1)  # lags 0.2, 2, 12 and 32 years: the upper middle one

    def test_a_play_before_the_release_date_counts_as_zero_lag(self):
        a = self.lag([("B", "2022-01-01", "Newcomer")] * 5, {"B": "2022-03-01"})
        self.assertEqual(a["on_release"][0]["lag_days"], 0)

    def test_there_on_release_needs_a_full_date_and_enough_plays(self):
        rows = ([("Exact", "2022-06-01", "Newcomer")] * 5 + [("YearOnly", "2022-06-01", "Newcomer")] * 5
                + [("Rare", "2022-06-01", "Newcomer")] * 2 + [("Late", "2022-06-01", "Newcomer")] * 5)
        a = self.lag(rows, {"Exact": "2022-05-25", "YearOnly": "2022", "Rare": "2022-05-25", "Late": "2022-01-01"})
        self.assertEqual([x["name"] for x in a["on_release"]], ["Exact"])
        self.assertEqual(a["covered"], 4)  # the lists are stricter than the statistics

    def test_found_late_lists_the_longest_lags_first_with_enough_plays(self):
        rows = ([("Old", "2022-06-01", "Newcomer")] * 5 + [("Older", "2022-06-01", "Newcomer")] * 5
                + [("Oldest", "2022-06-01", "Newcomer")] * 2)
        a = self.lag(rows, {"Old": "1990-01-01", "Older": "1970-01-01", "Oldest": "1950-01-01"})
        self.assertEqual([x["name"] for x in a["late"]], ["Older", "Old"])  # Oldest has too few plays

    def test_nothing_to_measure_is_an_empty_answer(self):
        a = self.lag([], {})
        self.assertEqual((a["covered"], a["excluded"]), (0, 1))

    def test_api_serves_it(self):
        tmp, conn = library(self.ANCHOR + [("B", "2022-06-01", "Newcomer")], {"B": "2022-05-20"})
        conn.close()
        with TestClient(create_app(Path(tmp.name) / "a.db")) as c:
            r = c.get("/api/decades/lag")
        tmp.cleanup()
        self.assertEqual((r.status_code, r.json()["covered"]), (200, 1))



if __name__ == "__main__":
    unittest.main()
