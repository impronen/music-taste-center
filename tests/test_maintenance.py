"""Merging duplicate artists, name rules for future imports, and duplicate suggestions."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, derive, enrich, ingest, insights, maintenance
from mtc.api import create_app
from mtc.ingest import Scrobble
from mtc.lastfm import LastFm
from tests.test_enrich import FakeClock, lastfm_transport

T0 = 1_700_000_000 - 1_700_000_000 % 60
GOOD, TYPO = "Sunn O)))", "Sunn 0)))"


def history() -> list[Scrobble]:
    rows = []
    for i in range(6):
        rows.append(Scrobble(GOOD, "Aghartha", T0 + i * 3600, "Monoliths & Dimensions"))
        rows.append(Scrobble(GOOD, "Big Church", T0 + i * 3600 + 600, "Monoliths & Dimensions"))
    for i in range(4):
        rows.append(Scrobble(TYPO, "aghartha", T0 + 90_000 + i * 3600, "Monoliths and Dimensions"))
        rows.append(Scrobble(TYPO, "Hunting & Gathering", T0 + 90_000 + i * 3600 + 600, "Monoliths & Dimensions"))
    # the same play scrobbled under both spellings (e.g. two scrobblers) must end up once
    rows.append(Scrobble(TYPO, "Aghartha", T0, "Monoliths & Dimensions"))
    rows.append(Scrobble("Boris", "Farewell", T0 + 200_000, "Pink"))
    return rows


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "m.db"
        self.conn = db.connect(self.path)
        ingest.ingest_records(self.conn, history(), source="test")
        derive.rebuild(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def artist_id(self, name):
        row = self.conn.execute("SELECT id FROM artists WHERE name_key = ?", (ingest.key(name),)).fetchone()
        return row[0] if row else None

    def test_loose_key(self):
        lk = maintenance.loose_key
        self.assertEqual(lk(TYPO), lk(GOOD))
        self.assertEqual(lk("The Cure"), lk("Cure"))
        self.assertEqual(lk("Mötley Crüe"), lk("Motley Crue"))
        self.assertEqual(lk("Simon & Garfunkel"), lk("Simon and Garfunkel"))
        self.assertNotEqual(lk("The The"), "")
        self.assertEqual(lk("!!!"), "")  # no letters: never grouped

    def test_merge_preview_matches_the_merge(self):
        good, typo = self.artist_id(GOOD), self.artist_id(TYPO)
        before = self.conn.total_changes
        p = maintenance.merge_preview(self.conn, [typo], good)
        self.assertEqual(self.conn.total_changes, before)  # nothing written
        self.assertEqual((p["scrobbles"], p["duplicates"], p["tracks_combined"], p["albums_combined"]), (8, 1, 1, 1))
        r = maintenance.merge_artists(self.conn, typo, good)
        self.assertEqual((r["scrobbles_moved"], r["duplicates_dropped"]), (p["scrobbles"], p["duplicates"]))
        with self.assertRaises(ValueError):
            maintenance.merge_preview(self.conn, [good], good)
        with self.assertRaises(LookupError):
            maintenance.merge_preview(self.conn, [999], good)

    def test_merge_combines_everything(self):
        good, typo = self.artist_id(GOOD), self.artist_id(TYPO)
        r = maintenance.merge_artists(self.conn, typo, good)
        self.assertEqual((r["scrobbles_moved"], r["duplicates_dropped"]), (8, 1))
        self.assertIsNone(self.artist_id(TYPO))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE artist_id = ?", (good,)).fetchone()[0], 20)
        tracks = {r[0]: r[1] for r in self.conn.execute(
            "SELECT t.title, COUNT(*) FROM scrobbles s JOIN tracks t ON t.id = s.track_id WHERE s.artist_id = ?"
            " GROUP BY t.id", (good,))}
        self.assertEqual(tracks, {"Aghartha": 10, "Big Church": 6, "Hunting & Gathering": 4})
        # albums with the same normalized title are one; a differently written one stays separate
        albums = dict(self.conn.execute(
            "SELECT al.title, COUNT(*) FROM scrobbles s JOIN albums al ON al.id = s.album_id WHERE s.artist_id = ?"
            " GROUP BY al.id", (good,)).fetchall())
        self.assertEqual(albums, {"Monoliths & Dimensions": 16, "Monoliths and Dimensions": 4})
        self.assertEqual(insights.artist(self.conn, good)["plays"], 20)  # derived tables rebuilt
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual([a["name"] for a in maintenance.aliases(self.conn)], [TYPO])

    def test_rule_applies_to_future_imports(self):
        maintenance.merge_artists(self.conn, self.artist_id(TYPO), self.artist_id(GOOD))
        # re-importing the full history adds nothing, new plays of the misspelling land on the right artist
        self.assertEqual(ingest.ingest_records(self.conn, history(), source="test")["rows_added"], 0)
        new = ingest.ingest_records(self.conn, [Scrobble("SUNN 0)))", "Aghartha", T0 + 500_000)], source="test")
        self.assertEqual(new["rows_added"], 1)
        self.assertIsNone(self.artist_id(TYPO))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE artist_id = ?",
                                           (self.artist_id(GOOD),)).fetchone()[0], 21)
        # removing the rule stops it (the merged scrobbles stay merged)
        maintenance.remove_alias(self.conn, maintenance.aliases(self.conn)[0]["id"])
        ingest.ingest_records(self.conn, [Scrobble(TYPO, "Aghartha", T0 + 600_000)], source="test")
        self.assertIsNotNone(self.artist_id(TYPO))

    def test_rules_follow_chained_merges_and_manual_rules(self):
        boris = self.artist_id("Boris")
        maintenance.merge_artists(self.conn, self.artist_id(TYPO), self.artist_id(GOOD))
        maintenance.merge_artists(self.conn, self.artist_id(GOOD), boris)  # silly, but rules must follow
        self.assertEqual({a["name"]: a["artist"] for a in maintenance.aliases(self.conn)}, {TYPO: "Boris", GOOD: "Boris"})
        maintenance.add_alias(self.conn, "Borris", boris)  # a spelling not seen yet
        ingest.ingest_records(self.conn, [Scrobble("borris", "Farewell", T0 + 700_000)], source="test")
        self.assertIsNone(self.artist_id("Borris"))
        with self.assertRaises(ValueError):
            maintenance.add_alias(self.conn, "BORIS", boris)
        with self.assertRaises(ValueError):
            maintenance.merge_artists(self.conn, boris, boris)
        # merging back: the target's own name never stays a rule
        ingest.ingest_records(self.conn, [Scrobble("Other", "x", T0 + 800_000)], source="test")
        maintenance.merge_artists(self.conn, boris, self.artist_id("Other"))
        maintenance.add_alias(self.conn, "Boris", self.artist_id("Other"))
        self.assertNotIn("Other", [a["name"] for a in maintenance.aliases(self.conn)])

    def test_duplicate_suggestions(self):
        groups = maintenance.duplicate_candidates(self.conn)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual({a["name"] for a in g["artists"]}, {GOOD, TYPO})
        self.assertEqual(g["target_id"], self.artist_id(GOOD))  # most played
        # last.fm's autocorrected name links artists the loose key can't, and picks the target
        ingest.ingest_records(self.conn, [Scrobble("Boris with Sunn", "x", T0 + 900_000)], source="test")
        derive.rebuild(self.conn)
        with self.conn:
            for name in (GOOD, "Boris with Sunn"):
                self.conn.execute("INSERT INTO artist_info(artist_id, status, lastfm_name, fetched_at) VALUES (?, 'ok', ?, 0)",
                                  (self.artist_id(name), "Sunn O)))"))
        g = maintenance.duplicate_candidates(self.conn)[0]
        self.assertEqual(len(g["artists"]), 3)
        self.assertEqual(g["target_id"], self.artist_id(GOOD))
        maintenance.dismiss(self.conn, g["key"])
        self.assertEqual(maintenance.duplicate_candidates(self.conn), [])

    def test_running_enrichment_skips_merged_artists(self):
        typo, good = self.artist_id(TYPO), self.artist_id(GOOD)
        inner = lastfm_transport()
        merged = []

        def transport(url, headers, timeout):
            if not merged:  # the user merges while the fetch is running
                merged.append(maintenance.merge_artists(self.conn, typo, good))
            return inner(url, headers, timeout)
        clk = FakeClock()
        lf = LastFm("k", transport, min_interval=0, sleep=clk.sleep, clock=clk.clock)
        summary = enrich.run(self.conn, lastfm=lf, artists=None, albums=0, releases=0, log=lambda *_: None)
        self.assertEqual(summary["artists"].get("merged"), 1)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_api(self):
        self.conn.close()
        with TestClient(create_app(self.path)) as client:
            g = client.get("/api/maintenance/duplicates").json()[0]
            sources = [a["id"] for a in g["artists"] if a["id"] != g["target_id"]]
            self.assertEqual(client.post("/api/maintenance/merge", json={"source_ids": sources, "target_id": sources[0]}).status_code, 400)
            self.assertEqual(client.post("/api/maintenance/merge", json={"source_ids": sources, "target_id": g["target_id"]},
                                         headers={"origin": "https://evil.example"}).status_code, 403)
            preview = client.post("/api/maintenance/merge/preview", json={"source_ids": sources, "target_id": g["target_id"]}).json()
            self.assertEqual(preview["scrobbles"], 8)
            r = client.post("/api/maintenance/merge", json={"source_ids": sources, "target_id": g["target_id"]}).json()
            self.assertEqual(r["merged"][0]["scrobbles_moved"], 8)
            self.assertEqual(client.get(f"/api/artists/{g['target_id']}").json()["plays"], 20)
            self.assertEqual(client.get(f"/api/artists/{sources[0]}").status_code, 404)
            self.assertEqual(client.get("/api/maintenance/duplicates").json(), [])
            rules = client.get("/api/maintenance/aliases").json()
            self.assertEqual([a["name"] for a in rules], [TYPO])
            self.assertEqual(client.post("/api/maintenance/merge", json={"source_ids": [999], "target_id": g["target_id"]}).status_code, 404)
            self.assertEqual(client.post("/api/maintenance/aliases", json={"name": "Sun O)))", "target_id": g["target_id"]}).status_code, 200)
            self.assertEqual(client.delete(f"/api/maintenance/aliases/{rules[0]['id']}").json(), {"removed": True})
            self.assertEqual(client.delete(f"/api/maintenance/aliases/{rules[0]['id']}").status_code, 404)
            self.assertEqual([a["name"] for a in client.get("/api/maintenance/aliases").json()], ["Sun O)))"])


if __name__ == "__main__":
    unittest.main()
