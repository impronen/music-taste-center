"""The Overview's facts: each appears only when it clears its bar."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, highlights, ingest, rhythms
from mtc.api import create_app
from mtc.ingest import Scrobble


def noon(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, 12).timestamp())


def dates(start: str, n: int, step: int = 1) -> list[date]:
    first = date.fromisoformat(start)
    return [first + timedelta(days=i * step) for i in range(n)]


def plays(artist: str, days: list[date], tracks: int = 1, album: str = "") -> list[Scrobble]:
    return [Scrobble(artist, f"{artist} song {i % tracks}", noon(d) + (i % 143) * 60, album or f"{artist} album")
            for i, d in enumerate(days)]


class Base(unittest.TestCase):
    def library(self, records: list[Scrobble]):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rhythms._cache.clear()
        conn = db.connect(Path(tmp.name) / "h.db")
        self.addCleanup(conn.close)
        ingest.ingest_records(conn, records, source="test")
        return conn


class OneSongTests(Base):
    def test_counts_artists_with_a_single_song(self):
        rows = [r for n in range(6) for r in plays(f"Single {n}", dates("2024-03-01", 2))]
        rows += plays("Deep", dates("2024-03-01", 6), tracks=3)
        conn = self.library(rows)
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31")["one_song"], {"artists": 6, "of": 7})

    def test_quiet_when_too_few(self):
        rows = [r for n in range(4) for r in plays(f"Single {n}", dates("2024-03-01", 2))]
        self.assertIsNone(highlights.highlights(self.library(rows), "2024-03-01", "2024-03-31")["one_song"])

    def test_quiet_when_a_small_share(self):
        rows = [r for n in range(5) for r in plays(f"Single {n}", dates("2024-03-01", 2))]
        rows += [r for n in range(20) for r in plays(f"Deep {n}", dates("2024-03-01", 4), tracks=2)]
        self.assertIsNone(highlights.highlights(self.library(rows), "2024-03-01", "2024-03-31")["one_song"])


    def test_a_song_on_repeat_is_no_brush_off(self):
        rows = [r for n in range(6) for r in plays(f"Single {n}", dates("2024-03-01", 20))]
        self.assertIsNone(highlights.highlights(self.library(rows), "2024-03-01", "2024-03-31")["one_song"])


class DoubleLifeTests(Base):
    MONDAYS, FRIDAYS = dates("2024-03-04", 4, 7), dates("2024-03-08", 4, 7)

    def test_names_two_weekdays_monday_first(self):
        conn = self.library(plays("Fri Band", self.FRIDAYS * 3) + plays("Mon Band", self.MONDAYS * 2))
        days = highlights.highlights(conn, "2024-03-01", "2024-03-31")["double_life"]["days"]
        self.assertEqual([(d["name"], d["weekday"]) for d in days], [("Mon Band", 0), ("Fri Band", 4)])

    def test_one_long_day_is_not_a_habit(self):
        conn = self.library(plays("Fri Band", self.FRIDAYS * 3) + plays("Mon Band", self.MONDAYS[:1] * 9))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["double_life"])

    def test_a_small_artist_does_not_own_a_busy_weekday(self):
        everyone = plays("Everyone", (self.MONDAYS + self.FRIDAYS) * 20)
        conn = self.library(everyone + plays("Mon Band", self.MONDAYS * 2) + plays("Fri Band", self.FRIDAYS * 2))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["double_life"])

    def test_an_artist_leaning_on_two_weekdays_does_not_hide_another(self):
        conn = self.library(plays("Twice", (self.MONDAYS + self.FRIDAYS) * 4) + plays("Fri Band", self.FRIDAYS * 2))
        days = highlights.highlights(conn, "2024-03-01", "2024-03-31")["double_life"]["days"]
        self.assertEqual([d["name"] for d in days], ["Twice", "Fri Band"])

    def test_needs_two_different_artists(self):
        conn = self.library(plays("Both", (self.MONDAYS + self.FRIDAYS) * 3))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["double_life"])


class SongAgeTests(Base):
    def dated(self, conn, album: str, release: str):
        with conn:
            conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date)"
                         " SELECT id, 'ok', 0, ? FROM albums WHERE title = ?", (release, album))
            db.bump(conn, "tags_version")

    def test_average_age_at_play_and_before_birth(self):
        conn = self.library(plays("Old", dates("2024-03-01", 30)) + plays("New", dates("2024-03-01", 10)))
        self.dated(conn, "Old album", "1964-05")
        self.dated(conn, "New album", "2004")
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31", 1980)["song_age"],
                         {"years": 50, "before_birth": 0.75})
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["song_age"]["before_birth"])

    def test_a_new_birth_year_is_not_served_from_the_cache(self):
        conn = self.library(plays("Old", dates("2024-03-01", 25)))
        self.dated(conn, "Old album", "1964")
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31", 1980)["song_age"]["before_birth"], 1)
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31", 1950)["song_age"]["before_birth"], 0)

    def test_a_play_before_release_counts_as_age_zero(self):
        conn = self.library(plays("Old", dates("2024-03-01", 15)) + plays("Soon", dates("2024-03-01", 15)))
        self.dated(conn, "Old album", "2004")
        self.dated(conn, "Soon album", "2030")
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31")["song_age"]["years"], 10)

    def test_quiet_when_mostly_undated_or_new(self):
        conn = self.library(plays("Old", dates("2024-03-01", 20)) + plays("Mystery", dates("2024-03-01", 25)))
        self.dated(conn, "Old album", "1964")
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["song_age"])
        conn = self.library(plays("New", dates("2024-03-01", 25)))
        self.dated(conn, "New album", "2023")
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["song_age"])


class ComebackTests(Base):
    BEFORE = dates("2020-01-01", 5)
    GAP = dates("2020-02-01", 150, 3)  # other listening meanwhile
    BACK = dates("2024-03-01", 4)

    def test_finds_the_return(self):
        conn = self.library(plays("Lost", self.BEFORE + self.BACK) + plays("Steady", self.GAP))
        found = highlights.highlights(conn, "2024-03-01", "2024-03-31")["comeback"]
        self.assertEqual((found["name"], found["back_on"], found["plays"]), ("Lost", "2024-03-01", 4))
        self.assertEqual(found["gap_days"], (self.BACK[0] - self.BEFORE[-1]).days)

    def test_a_return_inside_a_longer_period_counts(self):
        conn = self.library(plays("Lost", self.BEFORE + self.BACK) + plays("Steady", self.GAP))
        self.assertEqual(highlights.highlights(conn)["comeback"]["name"], "Lost")

    def test_one_shuffle_hit_is_no_comeback(self):
        conn = self.library(plays("Lost", self.BEFORE + self.BACK[:2]) + plays("Steady", self.GAP))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["comeback"])

    def test_stray_returns_with_longer_gaps_do_not_crowd_it_out(self):
        strays = [r for n in range(25) for r in plays(f"Stray {n}", dates("2019-01-01", 4) + [date(2024, 3, 2)])]
        conn = self.library(strays + plays("Lost", self.BEFORE + self.BACK) + plays("Steady", self.GAP))
        self.assertEqual(highlights.highlights(conn, "2024-03-01", "2024-03-31")["comeback"]["name"], "Lost")

    def test_a_break_from_scrobbling_is_no_comeback(self):
        conn = self.library(plays("Lost", self.BEFORE + self.BACK))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["comeback"])

    def test_a_single_old_play_is_no_relationship(self):
        conn = self.library(plays("Lost", self.BEFORE[:1] + self.BACK) + plays("Steady", self.GAP))
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["comeback"])


class ObscureTests(Base):
    def listeners(self, conn, name: str, n: int):
        with conn:
            conn.execute("INSERT INTO artist_info(artist_id, status, listeners, fetched_at)"
                         " SELECT id, 'ok', ?, 0 FROM artists WHERE name = ?", (n, name))
            db.bump(conn, "tags_version")

    def test_picks_the_smallest_and_rounds(self):
        conn = self.library(plays("Tiny", dates("2024-03-01", 3)) + plays("Small", dates("2024-03-01", 3))
                            + plays("Huge", dates("2024-03-01", 3)))
        self.listeners(conn, "Tiny", 4321)
        self.listeners(conn, "Small", 9000)
        self.listeners(conn, "Huge", 2_000_000)
        found = highlights.highlights(conn, "2024-03-01", "2024-03-31")["obscure"]
        self.assertEqual((found["name"], found["listeners"]), ("Tiny", 4300))

    def test_skips_failed_lookups(self):
        conn = self.library(plays("Tiny", dates("2024-03-01", 3)))
        self.listeners(conn, "Tiny", 4321)
        with conn:
            conn.execute("UPDATE artist_info SET status = 'error'")
            db.bump(conn, "tags_version")
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["obscure"])

    def test_skips_suspect_counts_and_passing_plays(self):
        conn = self.library(plays("Typo", dates("2024-03-01", 3)) + plays("Once", dates("2024-03-01", 2)))
        self.listeners(conn, "Typo", 12)
        self.listeners(conn, "Once", 500)
        self.assertIsNone(highlights.highlights(conn, "2024-03-01", "2024-03-31")["obscure"])


class EmptyAndApiTests(Base):
    def test_nothing_to_say_about_an_empty_library_or_period(self):
        everything_quiet = {"one_song": None, "double_life": None, "song_age": None, "comeback": None, "obscure": None}
        self.assertEqual(highlights.highlights(self.library([])), everything_quiet)
        conn = self.library(plays("Band", dates("2024-03-01", 3)))
        self.assertEqual(highlights.highlights(conn, "2099-01-01", "2099-01-31"), everything_quiet)

    def test_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(Path(tmp) / "a.db"))
            self.assertIn("comeback", client.get("/api/highlights").json())
            self.assertEqual(client.get("/api/highlights", params={"start": "2024-1-1"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
