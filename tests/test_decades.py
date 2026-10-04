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
    """Tracking starts 2020-01-05 (the anchor's play). The wait only counts albums released since then."""
    ANCHOR = [("Anchor", "2020-01-05", "Veteran")]  # the first scrobbles ever: Veteran was already in rotation

    def lag(self, rows, dates, birth_year=None):
        tmp, conn = library(self.ANCHOR + rows, {"Anchor": "1999-01-01"} | dates)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        return decades.discovery_lag(conn, birth_year)

    def eras(self, a):
        return {e["key"]: (e["albums"], e["plays"]) for e in a["eras"]}

    def test_old_records_have_no_wait_however_late_they_were_found(self):
        a = self.lag([("Classic", "2022-06-01", "Newcomer")] * 5 + [("Fresh", "2022-06-01", "Newcomer")] * 5,
                     {"Classic": "1965", "Fresh": "2022-03-01"})
        self.assertEqual(a["covered"], 1)  # only Fresh: released while tracking
        self.assertEqual([x["name"] for x in a["late"]], ["Fresh"])
        self.assertEqual(a["late"][0]["lag_days"], 92)

    def test_without_a_birth_year_everything_before_tracking_is_one_era(self):
        a = self.lag([("Classic", "2022-06-01", "Newcomer"), ("Fresh", "2022-06-01", "Newcomer")], {"Classic": "1965", "Fresh": "2022-03-01"})
        self.assertEqual([e["label"] for e in a["eras"]], ["Before tracking", "Released while tracking"])
        self.assertEqual(self.eras(a), {"pre_tracking": (2, 2), "tracked": (1, 1)})  # Anchor and Classic
        self.assertAlmostEqual(sum(e["share"] for e in a["eras"]), 1.0, places=3)

    def test_a_birth_year_splits_old_records_in_before_you_were_born_and_your_years(self):
        a = self.lag([("Classic", "2022-06-01", "Newcomer"), ("Teen", "2022-06-01", "Newcomer"), ("Fresh", "2022-06-01", "Newcomer")],
                     {"Classic": "1970", "Teen": "1995-05", "Fresh": "2022-03-01"}, birth_year=1985)
        self.assertEqual([e["label"] for e in a["eras"]], ["Before you were born", "Your years, before tracking", "Released while tracking"])
        self.assertEqual(self.eras(a), {"before_birth": (1, 1), "pre_tracking": (2, 2), "tracked": (1, 1)})  # Anchor is 1999
        self.assertEqual(a["birth_year"], 1985)

    def test_the_birth_year_is_part_of_the_cache_key(self):
        tmp, conn = library(self.ANCHOR + [("Classic", "2022-06-01", "Newcomer")], {"Anchor": "1999-01-01", "Classic": "1970"})
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        self.assertEqual(len(decades.discovery_lag(conn, 1985)["eras"]), 2)
        self.assertEqual(len(decades.discovery_lag(conn, None)["eras"]), 1)
        self.assertEqual(len(decades.discovery_lag(conn, 1985)["eras"]), 2)

    def test_an_album_released_after_tracking_began_counts_even_for_a_veteran_artist(self):
        rows = [("Fresh", "2021-03-05", "Veteran"), ("Backlist", "2021-03-05", "Veteran")]
        a = self.lag(rows, {"Fresh": "2021-03-01", "Backlist": "2005-01-01"})
        self.assertEqual(a["covered"], 1)
        self.assertEqual(a["buckets"][0]["albums"], 1)

    def test_buckets_and_median(self):
        rows = [(t, d, "Newcomer") for t, d in (("A", "2022-06-01"), ("B", "2022-06-01"), ("C", "2024-06-01"), ("D", "2032-06-01"))]
        a = self.lag(rows, {"A": "2022-04-01", "B": "2020-06-01", "C": "2020-06-01", "D": "2020-02-01"})
        self.assertEqual([b["albums"] for b in a["buckets"]], [1, 1, 1, 1])
        self.assertEqual([b["name"] for b in a["buckets"]][-1], "10+ years")
        self.assertEqual((a["first_year_share"], a["late_count"], a["late_label"]), (0.25, 1, "10+ years"))
        self.assertAlmostEqual(a["median_years"], 4.0, delta=0.1)  # lags 0.2, 2, 4 and 12 years: the upper middle one

    def test_a_play_before_the_release_date_counts_as_zero_lag(self):
        a = self.lag([("B", "2022-01-01", "Newcomer")] * 5, {"B": "2022-03-01"})
        self.assertEqual(a["on_release"][0]["lag_days"], 0)

    def test_there_on_release_needs_a_full_date_and_enough_plays(self):
        rows = ([("Exact", "2022-06-01", "Newcomer")] * 5 + [("YearOnly", "2022-06-01", "Newcomer")] * 5
                + [("Rare", "2022-06-01", "Newcomer")] * 2 + [("Late", "2022-06-01", "Newcomer")] * 5)
        a = self.lag(rows, {"Exact": "2022-05-25", "YearOnly": "2022", "Rare": "2022-05-25", "Late": "2022-01-01"})
        self.assertEqual([x["name"] for x in a["on_release"]], ["Exact"])
        self.assertEqual(a["covered"], 4)  # the lists are stricter than the statistics

    def test_found_late_lists_the_longest_waits_first_with_enough_plays(self):
        rows = ([("Old", "2030-06-01", "Newcomer")] * 5 + [("Older", "2030-06-01", "Newcomer")] * 5
                + [("Oldest", "2030-06-01", "Newcomer")] * 2)
        a = self.lag(rows, {"Old": "2020-06-01", "Older": "2020-02-01", "Oldest": "2020-01-10"})
        self.assertEqual([x["name"] for x in a["late"]], ["Older", "Old"])  # Oldest has too few plays

    def test_dug_up_counts_old_albums_by_first_play_year_and_skips_artists_already_in_rotation(self):
        rows = [("Classic", "2022-06-01", "Newcomer"), ("Teen", "2022-08-01", "Newcomer"), ("Deep", "2024-02-01", "Newcomer"),
                ("Backlist", "2021-03-05", "Veteran")]  # Veteran's first scrobble is no discovery
        a = self.lag(rows, {"Classic": "1970", "Teen": "1995", "Deep": "1960", "Backlist": "2005"}, birth_year=1985)
        years = {y["year"]: y for y in a["dug_up"]["years"]}
        self.assertEqual(sorted(years), [2022, 2023, 2024])  # gaps are filled for the chart
        self.assertEqual((years[2022]["albums"], years[2023]["albums"], years[2024]["albums"]), (2, 0, 1))
        self.assertEqual([(e["label"], e["albums"]) for e in years[2022]["by_era"]],
                         [("Before you were born", 1), ("Your years, before tracking", 1)])

    def test_only_old_albums_still_answer_about_the_eras_but_have_no_wait(self):
        a = self.lag([("Classic", "2022-06-01", "Newcomer")], {"Classic": "1970"})
        self.assertEqual(a["covered"], 0)
        self.assertNotIn("median_years", a)
        self.assertEqual(self.eras(a), {"pre_tracking": (2, 2)})
        self.assertEqual(a["dug_up"]["years"][0]["albums"], 1)

    def test_no_dated_albums_is_an_empty_answer(self):
        tmp, conn = library([("A", "2022-06-01")], {})
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        a = decades.discovery_lag(conn, 1985)
        self.assertEqual((a["covered"], a["eras"], a["dug_up"]), (0, [], None))

    def test_api_serves_it_with_the_saved_birth_year(self):
        from unittest import mock
        tmp, conn = library(self.ANCHOR + [("B", "2022-06-01", "Newcomer")], {"B": "1970", "Anchor": "1999-01-01"})
        conn.close()
        with mock.patch("mtc.settings.birth_year", return_value=1985), TestClient(create_app(Path(tmp.name) / "a.db")) as c:
            r = c.get("/api/decades/lag")
        tmp.cleanup()
        self.assertEqual((r.status_code, r.json()["birth_year"]), (200, 1985))
        self.assertEqual([e["key"] for e in r.json()["eras"]], ["before_birth", "pre_tracking"])


class DecadeRhythmTests(unittest.TestCase):
    """Synthetic habits (rhythms=True): metal in winter, jazz in the morning, indie at weekends.
    Each cluster's albums share one release decade, so the habits show up as decade lifts."""

    @classmethod
    def setUpClass(cls):
        from tests.test_rhythms import GENRES, add_tags
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "r.db"
        cls.conn = db.connect(cls.path)
        ingest.import_csv_text(cls.conn, synthetic.to_csv(synthetic.generate(rhythms=True)), label="t", encoding="utf-8")
        add_release_dates(cls.conn)
        cluster = {a: c for c, names in synthetic.CLUSTERS.items() for a in names}
        add_tags(cls.conn, {a: GENRES[c] for a, c in cluster.items()})
        cls.o = decades.rhythm_overview(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    @staticmethod
    def lift(m: dict, decade: str, col: str):
        row = next(r for r in m["rows"] if r["name"] == decade)
        return row["cells"][m["cols"].index(col)]["lift"]

    def test_a_decade_of_winter_music_is_above_usual_in_winter_and_below_in_summer(self):
        m = self.o["seasons"]
        self.assertEqual(m["cols"], ["Winter", "Spring", "Summer", "Autumn"])
        self.assertGreater(self.lift(m, "2000s", "Winter"), 1.1)  # metal
        self.assertLess(self.lift(m, "2000s", "Summer"), 1.0)

    def test_a_decade_of_morning_music_leads_the_morning_hours(self):
        self.assertGreater(self.lift(self.o["dayparts"], "1960s", "Morning"), 1.1)  # jazz at 07-08 local

    def test_a_decade_of_weekend_music_leads_the_weekend(self):
        w = self.o["weekparts"]
        self.assertGreater(self.lift(w, "2010s", "Weekend"), 1.05)  # indie
        self.assertLess(self.lift(w, "2010s", "Weekdays"), 1.0)

    def test_every_matrix_has_the_same_decade_rows(self):
        names = [r["name"] for r in self.o["seasons"]["rows"]]
        self.assertEqual(names, self.o["decades"])
        self.assertEqual([r["name"] for r in self.o["dayparts"]["rows"]], names)
        self.assertEqual([r["name"] for r in self.o["weekparts"]["rows"]], names)

    def test_each_decade_is_dominated_by_its_own_genre(self):
        g = self.o["genres"]
        self.assertGreater(g["coverage"], 0.9)
        self.assertGreater(self.lift(g, "2000s", "black metal"), 1.5)
        self.assertLess(self.lift(g, "2000s", "jazz"), 1.0)
        sig = {x["label"]: [y["name"] for y in x["genres"]] for x in g["signature"]}
        self.assertEqual(sig["2000s"][0], "black metal")
        self.assertEqual(sig["1980s"][0], "finnish rock")

    def test_without_genre_tags_only_the_genre_part_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "n.db")
            ingest.import_csv_text(conn, synthetic.to_csv(synthetic.generate(days=400)), label="t", encoding="utf-8")
            add_release_dates(conn)
            o = decades.rhythm_overview(conn)
            conn.close()
        self.assertIsNone(o["genres"])
        self.assertGreater(o["covered"], 0)

    def test_no_release_dates_is_an_empty_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "e.db")
            ingest.import_csv_text(conn, synthetic.to_csv(synthetic.generate(days=30)), label="t", encoding="utf-8")
            o = decades.rhythm_overview(conn)
            conn.close()
        self.assertEqual(o, {"covered": 0})

    def test_api_serves_it(self):
        with TestClient(create_app(self.path)) as c:
            r = c.get("/api/decades/rhythms")
        self.assertEqual((r.status_code, r.json()["decades"]), (200, self.o["decades"]))


if __name__ == "__main__":
    unittest.main()
