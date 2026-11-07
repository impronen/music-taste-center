"""The year in review: dataset sections, year boundaries and the files written."""
import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from mtc import config, db, derive, ingest, rhythms, wrapped
from mtc.ingest import Scrobble
from tests import synthetic


def at(day: str, hour: int = 12, minute: int = 0) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=config.TZ).timestamp())


class Base(unittest.TestCase):
    def library(self, records: list[Scrobble]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rhythms._cache.clear()
        conn = db.connect(Path(tmp.name) / "w.db")
        self.addCleanup(conn.close)
        ingest.ingest_records(conn, records, source="test")
        derive.rebuild(conn)
        return conn


class SectionTests(Base):
    def setUp(self):
        recs = []
        # Old favourite, played in both years; "Newcomer" is first heard in 2024.
        for i in range(30):
            recs.append(Scrobble("Old Favourite", f"Song {i % 3}", at("2023-03-01") + i * 86400, "Old LP"))
        for i in range(60):
            recs.append(Scrobble("Old Favourite", f"Song {i % 3}", at("2024-01-01") + i * 86400, "Old LP"))
        for i in range(10):  # one burst on a single day, back to back: a session and a repeat day
            recs.append(Scrobble("Newcomer", "Hit", at("2024-06-10", 9) + i * 200, "New LP"))
        recs.append(Scrobble("=Ääniwalli", "Sigur Rós", at("2024-02-02", 3), "Öö LP"))  # Finnish letters, formula-like name
        recs.append(Scrobble("Late Night", "Moon", at("2024-12-31", 23, 30), "Dark LP"))
        recs.append(Scrobble("Late Night", "Moon", at("2025-01-01", 0, 10), "Dark LP"))  # next year
        self.conn = self.library(recs)
        self.data = wrapped.build(self.conn, 2024)

    def test_totals_use_local_calendar_year(self):
        t = self.data["totals"]
        self.assertEqual(t["plays"], 72)  # 60 + 10 + the 23:30 play; the 00:10 play is 2025
        self.assertEqual(t["artists"], 4)
        self.assertEqual(self.data["versus_last_year"]["plays"]["before"], 30)
        self.assertEqual(self.data["versus_last_year"]["plays"]["change"], 42)

    def test_new_artists_exclude_those_known_before(self):
        self.assertEqual(self.data["totals"]["new_artists"], 3)  # Newcomer, Ääniwalli, Late Night (not Old Favourite)
        self.assertEqual(self.data["totals"]["new_artist_plays"], 12)

    def test_tops_are_ordered_with_artist_names(self):
        self.assertEqual(self.data["top_artists"][0]["name"], "Old Favourite")
        self.assertEqual(self.data["top_tracks"][0]["artist"], "Old Favourite")
        self.assertEqual(self.data["top_albums"][0]["name"], "Old LP")

    def test_streak_and_busiest_day(self):
        self.assertEqual(self.data["streak"]["start"], "2024-01-01")
        self.assertEqual(self.data["streak"]["days"], 60)  # Jan 1 - Feb 29 (leap year)
        self.assertEqual(self.data["busiest_day"], {"day": "2024-06-10", "plays": 10})

    def test_repeat_day_and_sessions(self):
        self.assertEqual(self.data["repeat_day"]["name"], "Hit")
        self.assertEqual(self.data["repeat_day"]["plays"], 10)
        self.assertEqual(self.data["sessions"][0]["plays"], 10)

    def test_artist_of_month_has_twelve_slots(self):
        months = self.data["artist_of_month"]
        self.assertEqual(len(months), 12)
        self.assertEqual(months[0]["name"], "Old Favourite")
        self.assertIsNone(months[8])

    def test_clock_sums_to_plays(self):
        self.assertEqual(sum(map(sum, self.data["clock"])), 72)

    def test_release_years_report_coverage_when_undated(self):
        r = self.data["release_years"]
        self.assertEqual((r["dated_plays"], r["total_plays"]), (0, 72))
        self.assertIsNone(r["this_year_share"])

    def test_archetypes_state_their_evidence(self):
        keys = {a["key"] for a in self.data["archetypes"]}
        self.assertEqual(keys, {"loyalist"})  # album pairs (9) are under the minimum; night owl is under its bar
        self.assertTrue(all(a["fact"] for a in self.data["archetypes"]))

    def test_thin_year_gets_no_archetypes(self):
        self.assertEqual(wrapped.build(self.conn, 2023)["archetypes"], [])

    def test_session_is_clipped_to_the_year(self):
        # 23:30 on 31 Dec and 00:10 on 1 Jan are one session; only the 2024 play counts for 2024.
        late = [x for x in wrapped._sessions(self.conn, "2024-12-31", "2024-12-31")]
        self.assertEqual(late[0]["plays"], 1)

    def test_loved_counts_the_local_year_only(self):
        with self.conn:
            for title, day in (("Moon", "2024-12-31"), ("Moon2", "2025-01-01")):
                self.conn.execute("INSERT INTO loved_tracks(artist, artist_key, title, title_key, loved_at)"
                                  " VALUES ('Late Night', 'late night', ?, ?, ?)", (title, title.lower(), at(day, 23, 59)))
        self.assertEqual(wrapped.build(self.conn, 2024)["loved"]["count"], 1)

    def test_versus_last_year_ratio_and_new_artists(self):
        v = self.data["versus_last_year"]
        self.assertEqual(v["plays"]["ratio"], round(72 / 30, 4))
        self.assertEqual(v["new_artists"]["before"], 0)  # Old Favourite began within the pre-history window

    def test_finnish_names_and_formula_guard_survive_csv(self):
        with tempfile.TemporaryDirectory() as out:
            wrapped.write(self.data, Path(out))
            rows = list(csv.reader((Path(out) / "top_artists.csv").read_text("utf-8-sig").splitlines()))
            names = [r[1] for r in rows[1:]]
            self.assertIn("'=Ääniwalli", names)
            self.assertIn("=Ääniwalli", [a["name"] for a in json.loads((Path(out) / "wrapped.json").read_text("utf-8"))["top_artists"]])

    def test_empty_year(self):
        d = wrapped.build(self.conn, 2010)
        self.assertTrue(d["empty"])
        self.assertEqual(d["totals"]["plays"], 0)


class DemoLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "demo.db"
        synthetic.build_demo_db(path)
        cls.conn = db.connect(path)
        cls.year = int(cls.conn.execute("SELECT MAX(substr(lday, 1, 4)) FROM scrobbles").fetchone()[0]) - 1
        cls.data = wrapped.build(cls.conn, cls.year)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_all_sections_present_and_json_serialisable(self):
        for key in ("top_artists", "top_genres", "genre_months", "release_years", "loved", "highlights",
                    "archetypes", "calendar", "clock", "versus_last_year"):
            self.assertIn(key, self.data)
        json.dumps(self.data)

    def test_genre_months_cover_the_year(self):
        g = self.data["genre_months"]
        self.assertTrue(g["genres"])
        self.assertTrue(all(len(x["months"]) == 12 for x in g["genres"]))
        self.assertGreater(g["covered_plays"], 0)

    def test_release_dates_are_counted(self):
        r = self.data["release_years"]
        self.assertGreater(r["dated_plays"], 0)
        self.assertLessEqual(r["dated_plays"], r["total_plays"])

    def test_write_makes_json_and_excel_friendly_csv(self):
        with tempfile.TemporaryDirectory() as out:
            files = wrapped.write(self.data, Path(out) / "y")
            names = {f.name for f in files}
            self.assertIn("wrapped.json", names)
            self.assertIn("top_artists.csv", names)
            raw = (Path(out) / "y" / "top_artists.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
            self.assertEqual(rows[0], ["id", "name", "plays", "days"])
            self.assertEqual(json.loads((Path(out) / "y" / "wrapped.json").read_text("utf-8"))["year"], self.year)


if __name__ == "__main__":
    unittest.main()
