"""Remaster / edition duplicates: stripping, grouping with album evidence, merging, and the rules
that keep later scrobbles, loved tracks and artist merges consistent."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, editions, ingest, maintenance, updater
from mtc.api import create_app
from mtc.ingest import Scrobble

T0 = 1_700_000_000 - 1_700_000_000 % 60
BAND = "The Fictionals"


def S(track, i, album="Midnight", artist=BAND):
    return Scrobble(artist, track, T0 + i * 3600, album)


def library() -> list[Scrobble]:
    rows = []
    for i in range(5):  # the original album and its Spotify remaster
        rows += [S("Harbour Lights", i, "Midnight"), S("Glass Houses", i + 10, "Midnight")]
    for i in range(3):
        rows += [S("Harbour Lights - Remastered 2011", i + 20, "Midnight (Remastered 2011)"),
                 S("Glass Houses - 2011 Remaster", i + 30, "Midnight (Remastered 2011)")]
    rows += [S("Harbour Lights (Live)", 40, "Live in Oslo"), S("Harbour Lights", 41, "Live in Oslo")]
    # same title on unrelated albums: weaker evidence
    rows += [S("Echo", 50, "Singles"), S("Echo - Remastered", 51, "Greatest Hits")]
    rows += [S("Other Song", 60, "Elsewhere", artist="Someone Else"),
             S("Other Song (Remastered 2009)", 61, "Elsewhere (Deluxe Edition)", artist="Someone Else")]
    rows += [S("Track A", 70, "Compilation", artist="Third One"), S("Track B", 71, "Compilation (Deluxe Edition)", artist="Third One")]
    return rows


class StripTests(unittest.TestCase):
    def test_only_remaster_type_notes_are_removed(self):
        for title, want in [
            ("Come Together - Remastered 2009", "Come Together"), ("Heroes (2017 Remaster)", "Heroes"),
            ("Song - 2011 Remaster", "Song"), ("Song [Remastered]", "Song"), ("Song - Mono Version", "Song"),
            ("Song – Digital Remaster", "Song"), ("Song (Deluxe Edition)", "Song"), ("Song (Bonus Track)", "Song"),
            ("Album (40th Anniversary Edition)", "Album"), ("Album [Expanded Edition]", "Album"),
            ("Song (Live) [Remastered 2011]", "Song (Live)"), ("Song - Live - Remastered 2011", "Song - Live"),
            ("Song (feat. X) - Remastered 2011", "Song (feat. X)"),
            # different recordings stay as they are
            ("Song (Live)", "Song (Live)"), ("Song - Live at Wembley", "Song - Live at Wembley"),
            ("Shake It Off (Taylor's Version)", "Shake It Off (Taylor's Version)"), ("Song - Single Version", "Song - Single Version"),
            ("Without You - Remix", "Without You - Remix"), ("Radioactive", "Radioactive"),
            ("Song - Remastered 2011 (Live)", "Song - Remastered 2011 (Live)"),
            # nothing but a note: kept
            ("Remastered", "Remastered"), ("(Remastered)", "(Remastered)"),
        ]:
            self.assertEqual(editions.strip_edition(title), want, title)


class EditionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "e.db"
        self.conn = db.connect(self.path)
        ingest.ingest_records(self.conn, library(), source="test")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def track(self, title, artist=BAND):
        return self.conn.execute("SELECT t.id FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE t.title = ? AND a.name = ?",
                                 (title, artist)).fetchone()[0]

    def album(self, title):
        return self.conn.execute("SELECT id FROM albums WHERE title = ?", (title,)).fetchone()[0]

    def plays(self, track_id):
        return self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE track_id = ?", (track_id,)).fetchone()[0]

    def test_track_groups_use_album_evidence_and_keep_live_apart(self):
        groups = {g["key"].split(":", 2)[2]: g for g in editions.track_groups(self.conn)}
        self.assertEqual(set(groups), {"harbour lights", "glass houses", "echo", "other song"})  # not "track a"/"track b"
        hl = groups["harbour lights"]
        self.assertEqual(hl["evidence"], "same-album")  # "Midnight" and "Midnight (Remastered 2011)" are one album
        self.assertEqual([t["title"] for t in hl["tracks"]], ["Harbour Lights", "Harbour Lights - Remastered 2011"])
        self.assertEqual(hl["target_id"], self.track("Harbour Lights"))
        self.assertNotIn("Harbour Lights (Live)", [t["title"] for t in hl["tracks"]])
        self.assertEqual(groups["echo"]["evidence"], "other-albums")
        self.assertEqual(groups["other song"]["artist"]["name"], "Someone Else")
        self.assertEqual([g["evidence"] for g in editions.track_groups(self.conn)][-1], "other-albums")  # weakest last

    def test_album_groups_show_shared_tracks(self):
        g = next(x for x in editions.album_groups(self.conn) if x["artist"]["name"] == BAND)
        self.assertEqual([a["title"] for a in g["albums"]], ["Midnight", "Midnight (Remastered 2011)"])
        self.assertEqual((g["evidence"], g["overlap"]), ("same-tracks", 1.0))
        same = next(x for x in editions.album_groups(self.conn) if x["artist"]["name"] == "Someone Else")
        self.assertEqual(same["evidence"], "same-tracks")  # "Other Song" and "Other Song (Remastered 2009)"
        other = next(x for x in editions.album_groups(self.conn) if x["artist"]["name"] == "Third One")
        self.assertEqual((other["evidence"], other["overlap"]), ("few-shared-tracks", 0.0))
        self.assertEqual(editions.album_groups(self.conn)[-1]["artist"]["name"], "Third One")  # weakest last

    def test_merging_tracks_moves_plays_and_leaves_a_rule(self):
        keep, old = self.track("Harbour Lights"), self.track("Harbour Lights - Remastered 2011")
        before = self.plays(keep) + self.plays(old)
        r = editions.merge_tracks(self.conn, [old], keep)
        self.assertEqual((r["target"], r["scrobbles_moved"], r["duplicates_dropped"]), ("Harbour Lights", 3, 0))
        self.assertEqual(self.plays(keep), before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tracks WHERE id = ?", (old,)).fetchone())
        self.assertEqual([x["title"] for x in editions.rules(self.conn)["tracks"]], ["Harbour Lights - Remastered 2011"])
        self.assertNotIn("harbour lights", [g["key"].split(":", 2)[2] for g in editions.track_groups(self.conn)])
        # a later import of the old title lands on the kept track, and no track is created
        n = self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        ingest.ingest_records(self.conn, [S("Harbour Lights - Remastered 2011", 99, "Midnight (Remastered 2011)")], source="t")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], n)
        self.assertEqual(self.plays(keep), before + 1)

    def test_validation(self):
        keep, old = self.track("Harbour Lights"), self.track("Harbour Lights - Remastered 2011")
        for bad, exc in [(([old], old), ValueError), (([old], self.track("Other Song", "Someone Else")), ValueError),
                         (([10**9], keep), LookupError)]:
            with self.assertRaises(exc):
                editions.merge_tracks(self.conn, *bad)
        with self.assertRaises(ValueError):
            editions.merge_albums(self.conn, [self.album("Elsewhere")], self.album("Midnight"))

    def test_a_play_scrobbled_under_both_titles_is_kept_once(self):
        keep, old = self.track("Glass Houses"), self.track("Glass Houses - 2011 Remaster")
        ingest.ingest_records(self.conn, [S("Glass Houses - 2011 Remaster", 10, "Midnight (Remastered 2011)")], source="t")  # same minute as one of keep's
        r = editions.merge_tracks(self.conn, [old], keep)
        self.assertEqual(r["duplicates_dropped"], 1)

    def test_loved_tracks_keep_matching_after_a_merge(self):
        keep, old = self.track("Harbour Lights"), self.track("Harbour Lights - Remastered 2011")
        updater.store_loved(self.conn, [(BAND, "Harbour Lights - Remastered 2011", T0)])
        self.assertEqual(self.conn.execute("SELECT track_id FROM loved").fetchone()[0], old)
        editions.merge_tracks(self.conn, [old], keep)
        self.assertEqual(self.conn.execute("SELECT track_id FROM loved").fetchone()[0], keep)
        self.assertEqual(maintenance.unmatched_loved(self.conn), [])

    def test_merging_albums(self):
        keep, old = self.album("Midnight"), self.album("Midnight (Remastered 2011)")
        r = editions.merge_albums(self.conn, [old], keep)
        self.assertEqual(r["scrobbles_moved"], 6)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE album_id = ?", (keep,)).fetchone()[0], 16)
        ingest.ingest_records(self.conn, [S("Glass Houses", 98, "Midnight (Remastered 2011)")], source="t")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM albums WHERE title = 'Midnight (Remastered 2011)'").fetchone())
        self.assertEqual([x["title"] for x in editions.rules(self.conn)["albums"]], ["Midnight (Remastered 2011)"])

    def test_an_artist_merge_carries_the_rules(self):
        keep, old = self.track("Harbour Lights"), self.track("Harbour Lights - Remastered 2011")
        editions.merge_tracks(self.conn, [old], keep)
        editions.merge_albums(self.conn, [self.album("Midnight (Remastered 2011)")], self.album("Midnight"))
        ingest.ingest_records(self.conn, [S("x", 1, "x", artist="Fictionals, The")], source="t")
        src = self.conn.execute("SELECT id FROM artists WHERE name = ?", (BAND,)).fetchone()[0]
        dst = self.conn.execute("SELECT id FROM artists WHERE name = 'Fictionals, The'").fetchone()[0]
        maintenance.merge_artists(self.conn, src, dst)
        for table in ("track_aliases", "album_aliases"):
            self.assertEqual([r[0] for r in self.conn.execute(f"SELECT artist_id FROM {table}")], [dst])
        n = self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        ingest.ingest_records(self.conn, [S("Harbour Lights - Remastered 2011", 97, "Midnight (Remastered 2011)")], source="t")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], n)

    def test_endpoints(self):
        keep, old = self.track("Harbour Lights"), self.track("Harbour Lights - Remastered 2011")
        evil = {"Origin": "http://evil.example"}
        client = TestClient(create_app(self.path))
        body = client.get("/api/maintenance/editions").json()
        self.assertEqual(sorted(body), ["albums", "rules", "tracks"])
        merge = {"merges": [{"source_ids": [old], "target_id": keep}]}
        self.assertEqual(client.post("/api/maintenance/editions/tracks", json=merge, headers=evil).status_code, 403)
        self.assertEqual(client.post("/api/maintenance/editions/tracks", json={"merges": []}).status_code, 422)
        v = client.get("/api/version").json()["version"]
        r = client.post("/api/maintenance/editions/tracks", json=merge)
        self.assertEqual((r.status_code, r.json()["merged"][0]["scrobbles_moved"]), (200, 3))
        self.assertNotEqual(client.get("/api/version").json()["version"], v)
        self.assertEqual(client.post("/api/maintenance/editions/tracks", json=merge).status_code, 404)  # already gone
        # a failure part-way keeps what merged before it, and the derived tables are rebuilt
        glass = {"source_ids": [self.track("Glass Houses - 2011 Remaster")], "target_id": self.track("Glass Houses")}
        r = client.post("/api/maintenance/editions/tracks", json={"merges": [glass, {"source_ids": [10**9], "target_id": keep}]})
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tracks WHERE title = 'Glass Houses - 2011 Remaster'").fetchone())
        album = {"merges": [{"source_ids": [self.album("Midnight (Remastered 2011)")], "target_id": self.album("Midnight")}]}
        self.assertEqual(client.post("/api/maintenance/editions/albums", json=album).status_code, 200)
        rules = client.get("/api/maintenance/editions").json()["rules"]
        self.assertEqual((len(rules["tracks"]), len(rules["albums"])), (2, 1))
        rid = rules["tracks"][0]["id"]
        self.assertEqual(client.delete(f"/api/maintenance/editions/tracks/rules/{rid}", headers=evil).status_code, 403)
        self.assertEqual(client.delete(f"/api/maintenance/editions/tracks/rules/{rid}").json(), {"removed": True})
        self.assertEqual(client.delete(f"/api/maintenance/editions/tracks/rules/{rid}").status_code, 404)
        self.assertEqual(client.delete("/api/maintenance/editions/nope/rules/1").status_code, 422)

    def test_dismissed_groups_are_left_out(self):
        key = next(g["key"] for g in editions.track_groups(self.conn) if g["key"].endswith(":echo"))
        maintenance.dismiss(self.conn, key)
        self.assertNotIn(key, [g["key"] for g in editions.track_groups(self.conn)])


if __name__ == "__main__":
    unittest.main()
