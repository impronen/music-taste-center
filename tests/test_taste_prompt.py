"""The taste prompt: recent listening weighted more, genres before artists, and the task texts
(fictional names throughout)."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, derive, ingest, taste_prompt, updater
from mtc.api import create_app
from mtc.ingest import Scrobble
from tests.test_rhythms import add_tags

LATEST = 1_790_000_000
DAY = 86400
TODAY = date(2026, 10, 7)


def build(path):
    """Old Guard: rock, 600 plays three to four years ago. Steady Hands: folk, 4 plays a month for
    four years. New Wave Kid: shoegaze, 120 plays in the last two months (a discovery), and a
    Finnish scene tag. Comma, Inc.: jazz, 40 plays a year ago."""
    out = []
    out += [Scrobble("Old Guard", f"Riff {i % 20}", LATEST - 4 * 365 * DAY + i * 3600) for i in range(600)]
    out += [Scrobble("Steady Hands", f"Song {i % 10}", LATEST - 4 * 365 * DAY + i * 7 * DAY) for i in range(200)]
    out += [Scrobble("New Wave Kid", f"Haze {i % 8}", LATEST - 60 * DAY + i * 12 * 3600) for i in range(120)]
    out += [Scrobble("Comma, Inc.", f"Tune {i % 5}", LATEST - 365 * DAY + i * 3600) for i in range(40)]
    out.append(Scrobble("New Wave Kid", "Haze 0", LATEST))
    conn = db.connect(path)
    ingest.ingest_records(conn, out, source="csv")
    derive.rebuild(conn)
    add_tags(conn, {"Old Guard": "rock", "Steady Hands": "folk", "New Wave Kid": "shoegaze", "Comma, Inc.": "jazz"},
             {"New Wave Kid": "finland"})
    updater.store_loved(conn, [("New Wave Kid", "Haze 3", LATEST)])
    return conn


class TastePromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = build(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_recent_listening_leads(self):
        p = taste_prompt.profile(self.conn)
        g = p["genres"]
        self.assertEqual(g["now"][0]["name"], "shoegaze")          # 121 recent plays beat 600 old ones
        self.assertEqual(g["core"][0]["name"], "rock")             # but all time, rock is the core
        self.assertEqual([x["name"] for x in g["rising"]][0], "shoegaze")
        self.assertIn("rock", [x["name"] for x in g["fading"]])
        self.assertEqual(p["artists"]["now"][0], "New Wave Kid")
        self.assertEqual(p["artists"]["staples"][0], "Old Guard")
        self.assertEqual(p["artists"]["discoveries"], ["New Wave Kid"])
        self.assertEqual(p["loved"], ["New Wave Kid – Haze 3"])
        # "finland" counts as the finnish scene, as a share of all recent listening
        self.assertEqual([x["name"] for x in p["places"]], ["finnish"])
        self.assertLess(p["places"][0]["now"], 1)

    def test_texts(self):
        releases_text = taste_prompt.build(self.conn, "releases", today=TODAY)["text"]
        self.assertIn("Genres I'm into now (share of my recent listening): shoegaze", releases_text)
        # names can hold commas, so they're joined with ";", and "Inc." doesn't get a second full stop
        self.assertIn("All-time staples: Old Guard; Steady Hands; New Wave Kid; Comma, Inc.\n", releases_text)
        self.assertIn("Friday 9 October 2026 and Friday 16 October 2026", releases_text)
        self.assertIn("including Finnish music media", releases_text)
        self.assertIn("genres matter more than the artist names", releases_text)
        discover = taste_prompt.build(self.conn, "discover")["text"]
        self.assertIn("Recommend 10 artists", discover)
        profile = taste_prompt.build(self.conn, "profile")["text"]
        self.assertNotIn("# Task", profile)

    def test_known_releases_are_listed(self):
        with self.conn:
            self.conn.execute("INSERT INTO upcoming_releases(source, release_date, artist, title, release_type)"
                              " VALUES ('listenbrainz', '2026-10-16', 'New Wave Kid', 'Fog Machine', 'Album')")
        text = taste_prompt.build(self.conn, "releases", today=TODAY)["text"]
        self.assertIn("Already on my list, no need to find these (but say if one deserves special attention): "
                      "New Wave Kid – Fog Machine (2026-10-16).", text)

    def test_saturday_asks_for_the_next_fridays(self):
        text = taste_prompt.build(self.conn, "releases", today=date(2026, 10, 10))["text"]
        self.assertIn("Friday 16 October 2026 and Friday 23 October 2026", text)

    def test_cache_follows_loved_tracks(self):
        self.assertEqual(taste_prompt.profile(self.conn)["loved"], ["New Wave Kid – Haze 3"])
        updater.store_loved(self.conn, [("Old Guard", "Riff 1", LATEST)])
        self.assertEqual(taste_prompt.profile(self.conn)["loved"], ["Old Guard – Riff 1"])


class CoverageTest(unittest.TestCase):
    """Shares are of all recent listening, and trends need enough tagged listening."""

    def library(self, path, genre_of, place_of=None):
        out = [Scrobble("Old Guard", f"Riff {i % 20}", LATEST - 400 * DAY + i * 3600) for i in range(200)]
        out += [Scrobble("Untagged Star", f"Hit {i % 9}", LATEST - 30 * DAY + i * 3600) for i in range(300)]
        out += [Scrobble("Tiny Jazz", "Solo", LATEST - i * DAY) for i in range(5)]
        conn = db.connect(path)
        ingest.ingest_records(conn, out, source="csv")
        derive.rebuild(conn)
        add_tags(conn, genre_of, place_of)
        return conn

    def test_partial_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.library(Path(tmp) / "c.db", {"Old Guard": "rock", "Tiny Jazz": "jazz"}, {"Untagged Star": "finland"})
            p = taste_prompt.profile(conn)
            self.assertLess(p["genre_coverage"], taste_prompt.MIN_COVERAGE)
            self.assertLess(sum(g["now"] for g in p["genres"]["now"]) + p["places"][0]["now"], 1.01)  # shares of one whole
            self.assertEqual((p["genres"]["rising"], p["genres"]["fading"]), ([], []))
            text = taste_prompt.build(conn, "profile")["text"]
            self.assertIn("too much to tell what's rising or fading", text)
            conn.close()

    def test_no_genre_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.library(Path(tmp) / "n.db", {}, {"Untagged Star": "finland"})
            text = taste_prompt.build(conn, "profile")["text"]
            self.assertIn("No genre tags fetched yet", text)
            self.assertIn("Scenes that matter: finnish", text)
            conn.close()

    def test_tiny_genres_are_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.library(Path(tmp) / "t.db", {"Old Guard": "rock", "Untagged Star": "pop", "Tiny Jazz": "jazz"})
            names = [g["name"] for g in taste_prompt.profile(conn)["genres"]["now"]]
            self.assertIn("pop", names)
            self.assertTrue(all(g["now"] >= taste_prompt.MIN_TOP_SHARE for g in taste_prompt.profile(conn)["genres"]["now"]))
            conn.close()

    def test_empty_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "e.db")
            self.assertEqual(taste_prompt.build(conn, "releases")["text"], "")
            conn.close()

    def test_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "a.db").close()
            client = TestClient(create_app(Path(tmp) / "a.db"))
            r = client.get("/api/taste-prompt", params={"task": "discover"}).json()
            self.assertTrue(r["text"].startswith("# My music taste"))
            self.assertEqual(client.get("/api/taste-prompt", params={"task": "nope"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
