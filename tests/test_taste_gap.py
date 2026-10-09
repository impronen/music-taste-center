"""Taste gap (loved tracks vs plays) on a small hand-built library with known shares."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, derive, ingest, maintenance, taste_gap, updater
from mtc.api import create_app
from mtc.ingest import Scrobble
from tests.test_rhythms import add_tags

T0 = 1_600_000_000
GENRE_OF = {"Jazzy": "jazz", "Rocky": "rock", "Popsy": "pop", "Tiny": "folk", "Nichey": "drone"}


def library() -> list[Scrobble]:
    """Jazzy 20 tracks × 10 plays (+3 plays of J0 on a compilation), Rocky 40 × 20, Popsy 12 × 50,
    Tiny 1 × 5, Nichey 10 × 1: 1 618 plays."""
    out, t = [], T0

    def play(artist, track, album, n):
        nonlocal t
        for _ in range(n):
            out.append(Scrobble(artist, track, t, album))
            t += 300

    for i in range(20):
        play("Jazzy", f"J{i}", "Blue", 10)
    play("Jazzy", "J0", "Best Of", 3)
    for i in range(40):
        play("Rocky", f"R{i}", "Loud", 20)
    for i in range(12):
        play("Popsy", f"P{i}", "Shiny", 50)
    play("Tiny", "T0", "", 5)
    for i in range(10):
        play("Nichey", f"N{i}", "", 1)
    return out


RELEASES = {"Blue": "1962-03-09", "Best Of": "2005", "Loud": "1995-05"}  # Shiny stays undated
LOVED = ([("Jazzy", f"J{i}", T0) for i in range(20)] + [("Rocky", f"R{i}", T0) for i in range(6)]
         + [("Nichey", f"N{i}", T0) for i in range(10)]
         + [("Tiny", "T0", T0), ("Ghost", "Nowhere", T0), ("Popsy Band", "P0", T0)])  # last two don't match
N_LOVED = 37  # matched


def build(path, loved=LOVED):
    conn = db.connect(path)
    ingest.ingest_records(conn, library(), source="csv")
    derive.rebuild(conn)
    add_tags(conn, GENRE_OF)
    with conn:
        for album_id, title in conn.execute("SELECT id, title FROM albums").fetchall():
            if title in RELEASES:
                conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date) VALUES (?, 'ok', 0, ?)",
                             (album_id, RELEASES[title]))
        db.bump(conn, "tags_version")
    if loved:
        updater.store_loved(conn, loved)
    return conn


class TasteGapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "g.db"
        self.conn = build(self.path)
        self.g = taste_gap.taste_gap(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_coverage_counts(self):
        self.assertEqual(self.g["loved"], {"total": 39, "matched": 37, "with_genre": 37, "with_year": 26})
        self.assertEqual(self.g["plays"], {"total": 1618, "with_genre": 1618, "with_year": 1003})

    def test_genre_shares_weight_each_loved_track_by_its_artist(self):
        over = {x["name"]: x for x in self.g["genres"]["over"]}
        jazz = over["jazz"]
        self.assertAlmostEqual(jazz["loved_share"], round(20 / N_LOVED, 4))
        self.assertAlmostEqual(jazz["play_share"], round(203 / 1618, 4))
        expected = N_LOVED * 203 / 1618
        self.assertAlmostEqual(jazz["ratio"], round((20 + 5) / (expected + 5), 4))  # smoothed, not 20 / expected
        self.assertLess(jazz["ratio"], 20 / expected)

    def test_support_thresholds(self):
        under = [x["name"] for x in self.g["genres"]["under"]]
        # pop is never loved but expected 10+ loves: listed, and first (lowest ratio)
        self.assertEqual(under, ["pop", "rock"])
        # drone has 0,6 % of plays but 27 % of loves (10 tracks): the niche genre leads the loved side
        self.assertEqual([x["name"] for x in self.g["genres"]["over"]], ["drone", "jazz"])
        # folk has one loved track and under 1 % of plays: too little support either way
        self.assertNotIn("folk", [x["name"] for x in self.g["genres"]["over"] + self.g["genres"]["under"]])
        self.assertEqual(self.g["genres"]["enough"], {"over": True, "under": True})
        artists_over = [x["name"] for x in self.g["artists"]["over"]]
        self.assertEqual(artists_over, ["Nichey", "Jazzy"])  # Tiny has one loved track: not enough to rank
        self.assertEqual([x["name"] for x in self.g["artists"]["under"]], ["Popsy", "Rocky"])
        popsy = self.g["artists"]["under"][0]
        self.assertEqual((popsy["loved"], popsy["plays"]), (0, 600))
        self.assertAlmostEqual(popsy["ratio"], round(2 / (N_LOVED * 600 / 1618 + 2), 4))

    def test_loved_tracks_are_dated_by_their_most_played_album(self):
        d = self.g["decades"]
        self.assertTrue(d["enough"])
        items = {x["label"]: x for x in d["items"]}
        # J0 is on Blue (1962, 10 plays) and Best Of (2005, 3 plays): it counts for the 1960s
        self.assertEqual((items["1960s"]["loved"], items["1990s"]["loved"]), (20, 6))
        self.assertAlmostEqual(items["1960s"]["play_share"], round(200 / 1003, 4))
        self.assertNotIn("2000s", items)  # 3 of 1 003 dated plays and no loves: under 2 % on both sides
        self.assertAlmostEqual(d["coverage"], round(26 / N_LOVED, 4))
        self.assertAlmostEqual(d["play_coverage"], round(1003 / 1618, 4))

    def test_new_release_dates_refresh_the_cache(self):
        with self.conn:
            shiny = self.conn.execute("SELECT id FROM albums WHERE title = 'Shiny'").fetchone()[0]
            self.conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date) VALUES (?, 'ok', 0, '2021')",
                              (shiny,))
            db.bump(self.conn, "tags_version")
        g = taste_gap.taste_gap(self.conn)
        self.assertEqual(g["plays"]["with_year"], 1603)
        self.assertIn("2020s", [x["label"] for x in g["decades"]["items"]])

    def test_a_new_name_rule_or_loved_list_refreshes_the_cache(self):
        maintenance.add_alias(self.conn, "Popsy Band", self.conn.execute(
            "SELECT id FROM artists WHERE name = 'Popsy'").fetchone()[0])
        self.assertEqual(taste_gap.taste_gap(self.conn)["loved"]["matched"], N_LOVED + 1)
        rule = self.conn.execute("SELECT id FROM artist_aliases WHERE name = 'Popsy Band'").fetchone()[0]
        self.assertTrue(maintenance.remove_alias(self.conn, rule))
        self.assertEqual(taste_gap.taste_gap(self.conn)["loved"]["matched"], N_LOVED)
        updater.store_loved(self.conn, LOVED[:5])
        self.assertEqual(taste_gap.taste_gap(self.conn)["loved"]["matched"], 5)
        self.assertFalse(taste_gap.taste_gap(self.conn)["decades"]["enough"])  # under 10 dated loves

    def test_endpoint(self):
        r = TestClient(create_app(self.path)).get("/api/loved/gap")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["loved"]["matched"], N_LOVED)
        self.assertEqual([x["name"] for x in r.json()["genres"]["over"]], ["drone", "jazz"])
        self.assertEqual(r.json()["rules"]["min_decade_share"], taste_gap.MIN_DECADE_SHARE)

    def test_too_few_loves_says_so_instead_of_an_empty_finding(self):
        updater.store_loved(self.conn, [("Jazzy", f"J{i}", T0) for i in range(6)])
        g = taste_gap.taste_gap(self.conn)
        # 6 loves: no genre can collect 10, and the biggest artist (Rocky, 49 %) predicts under 3
        self.assertEqual(g["genres"]["enough"], {"over": False, "under": False})
        self.assertEqual((g["genres"]["over"], g["genres"]["under"]), ([], []))
        self.assertEqual(g["artists"]["enough"], {"over": True, "under": False})
        self.assertEqual(g["artists"]["under"], [])
        self.assertEqual((g["genres"]["loved"], g["artists"]["loved"]), (6, 6))


class EmptyTasteGapTests(unittest.TestCase):
    def test_no_loved_tracks_and_no_scrobbles(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = build(Path(tmp) / "n.db", loved=[])
            g = taste_gap.taste_gap(conn)
            conn.close()
            self.assertEqual(g["loved"]["total"], 0)
            self.assertEqual((g["genres"]["over"], g["genres"]["under"], g["artists"]["over"], g["artists"]["under"]),
                             ([], [], [], []))
            self.assertFalse(g["decades"]["enough"])
            r = TestClient(create_app(Path(tmp) / "empty.db")).get("/api/loved/gap")
            self.assertEqual(r.status_code, 200)
            self.assertEqual((r.json()["loved"]["total"], r.json()["plays"]["total"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
