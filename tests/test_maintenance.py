"""Merging duplicate artists, name rules for future imports, duplicate suggestions, and loved
tracks that don't match (with loved-title rules)."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from mtc import db, derive, enrich, ingest, insights, maintenance, updater
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


class UnmatchedLovedTests(unittest.TestCase):
    """Loved tracks whose title doesn't match the library, their suggestions and loved-title rules."""
    LOVED = [("Nobody Known", "Song", T0 + 50), (GOOD, "Big Church (Remastered 2011)", T0 + 40),
             (GOOD, "Aghartha", T0 + 30), (TYPO, "Hunting and Gathering - Live", T0 + 20),
             (TYPO, "aghartha (live)", T0 + 10)]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "m.db"
        self.conn = db.connect(self.path)
        ingest.ingest_records(self.conn, history(), source="test")
        derive.rebuild(self.conn)
        updater.store_loved(self.conn, self.LOVED)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def track(self, artist, title):
        return self.conn.execute("SELECT t.id FROM tracks t JOIN artists a ON a.id = t.artist_id"
                                 " WHERE a.name_key = ? AND t.title_key = ?", (ingest.key(artist), ingest.key(title))).fetchone()[0]

    def artist_id(self, name):
        return self.conn.execute("SELECT id FROM artists WHERE name_key = ?", (ingest.key(name),)).fetchone()[0]

    def resolved(self, title):
        return self.conn.execute("SELECT track_id FROM loved WHERE title = ?", (title,)).fetchone()[0]

    def unmatched(self):
        return {u["title"]: u for u in maintenance.unmatched_loved(self.conn)}

    def test_loose_title(self):
        lt = maintenance.loose_title
        self.assertEqual(lt("Big Church (Remastered 2011)"), lt("Big Church"))
        self.assertEqual(lt("Song [Live at Tavastia]"), lt("song"))
        self.assertEqual(lt("Song - 2009 Remaster"), lt("Song"))
        self.assertEqual(lt("Song - Single Version"), lt("Song"))
        self.assertEqual(lt("Don’t Stop"), lt("Don't Stop"))
        self.assertEqual(lt("Don`t Stop"), lt("Dont Stop"))
        self.assertEqual(lt("Song (feat. Someone)"), lt("Song"))
        self.assertEqual(lt("Song feat. Someone"), lt("Song"))
        self.assertEqual(lt("Kärpäset & Mä"), lt("Karpaset and Ma"))
        self.assertNotEqual(lt("Song (Part 1)"), lt("Song (Part 2)"))  # brackets without edition words stay
        self.assertEqual(lt("Live Forever"), "liveforever")  # an edition word outside a suffix stays
        self.assertEqual(lt("!!!"), "")
        # whole words only: these brackets and dashes aren't edition suffixes
        self.assertEqual(lt("Song (Without You)"), "songwithoutyou")
        self.assertEqual(lt("Hello (Radioactive)"), "helloradioactive")
        self.assertEqual(lt("Song (Liverpool)"), "songliverpool")
        self.assertEqual(lt("Here - Take Me"), "heretakeme")
        self.assertEqual(lt("Stone - Monolith"), "stonemonolith")
        self.assertEqual(lt("Song (Remix)"), "songremix")  # a remix isn't offered as the original
        self.assertEqual(lt("Song (with Someone)"), "song")
        self.assertEqual(lt("Song - Live"), "song")
        self.assertEqual(lt("Song - Remastered"), "song")
        self.assertEqual(lt("Song (Mixed)"), "song")
        # only Latin accents are dropped: a dakuten makes a different letter
        self.assertNotEqual(lt("ハト"), lt("バト"))
        self.assertEqual(lt("バト"), "バト")
        self.assertEqual(lt("Café"), "cafe")

    def test_reasons_and_suggestions(self):
        u = self.unmatched()
        self.assertEqual(set(u), {"Song", "Big Church (Remastered 2011)", "Hunting and Gathering - Live", "aghartha (live)"})
        self.assertEqual((u["Song"]["reason"], u["Song"]["suggestions"], u["Song"]["artist_id"]),
                         (maintenance.NO_ARTIST, [], None))
        church = u["Big Church (Remastered 2011)"]
        self.assertEqual(church["reason"], maintenance.NO_TITLE)
        self.assertEqual(church["suggestions"], [{"id": self.track(GOOD, "Big Church"), "title": "Big Church", "plays": 6}])
        self.assertEqual(u["Hunting and Gathering - Live"]["suggestions"][0]["title"], "Hunting & Gathering")
        dates = [x["loved_at"] for x in maintenance.unmatched_loved(self.conn)]
        self.assertEqual(dates, sorted(dates, reverse=True))  # newest first

    def test_similarity_fallback(self):
        updater.store_loved(self.conn, [(GOOD, "Agharta", T0)])  # a letter missing: no loose match, close enough
        self.assertEqual([s["title"] for s in maintenance.unmatched_loved(self.conn)[0]["suggestions"]], ["Aghartha"])
        updater.store_loved(self.conn, [(GOOD, "Something else entirely", T0)])
        self.assertEqual(maintenance.unmatched_loved(self.conn)[0]["suggestions"], [])

    def test_titles_differing_only_in_digits_are_not_suggested(self):
        ingest.ingest_records(self.conn, [Scrobble(GOOD, "Aghartha (Part 1)", T0 + 300_000)], source="test")
        updater.store_loved(self.conn, [(GOOD, "Aghartha (Part 2)", T0)])
        self.assertNotIn("Aghartha (Part 1)", [s["title"] for s in maintenance.unmatched_loved(self.conn)[0]["suggestions"]])

    def test_artist_known_through_a_name_rule(self):
        maintenance.merge_artists(self.conn, self.artist_id(TYPO), self.artist_id(GOOD))
        u = self.unmatched()
        self.assertEqual(u["Hunting and Gathering - Live"]["reason"], maintenance.NO_TITLE)
        self.assertEqual(u["Hunting and Gathering - Live"]["artist_id"], self.artist_id(GOOD))
        self.assertEqual([s["title"] for s in u["aghartha (live)"]["suggestions"]], ["Aghartha"])

    def test_rule_links_survives_resync_and_undoes(self):
        church = self.track(GOOD, "Big Church")
        before = db.versions(self.conn)[0]
        r = maintenance.add_loved_rule(self.conn, GOOD, "big church (remastered 2011)", church)  # by key
        self.assertGreater(db.versions(self.conn)[0], before)
        self.assertEqual(self.resolved("Big Church (Remastered 2011)"), church)
        self.assertNotIn("Big Church (Remastered 2011)", self.unmatched())
        self.assertEqual(insights.track(self.conn, church)["loved_at"], T0 + 40)
        rules = maintenance.loved_rules(self.conn)
        self.assertEqual([(x["id"], x["title"], x["track"], x["still_loved"]) for x in rules],
                         [(r["id"], "Big Church (Remastered 2011)", "Big Church", 1)])
        # the updater replaces the whole list: unchanged, changed, unloved, loved again
        updater.store_loved(self.conn, self.LOVED)
        updater.store_loved(self.conn, [*self.LOVED, ("Boris", "Farewell", T0 + 60)])
        self.assertEqual(self.resolved("Big Church (Remastered 2011)"), church)
        updater.store_loved(self.conn, self.LOVED[:1])
        self.assertFalse(maintenance.loved_rules(self.conn)[0]["still_loved"])  # kept, harmless
        updater.store_loved(self.conn, self.LOVED)
        self.assertEqual(self.resolved("Big Church (Remastered 2011)"), church)
        # undo
        before = db.versions(self.conn)[0]
        self.assertTrue(maintenance.remove_loved_rule(self.conn, r["id"]))
        self.assertGreater(db.versions(self.conn)[0], before)
        self.assertFalse(maintenance.remove_loved_rule(self.conn, r["id"]))
        self.assertIsNone(self.resolved("Big Church (Remastered 2011)"))
        self.assertIn("Big Church (Remastered 2011)", self.unmatched())

    def test_rule_refusals(self):
        church = self.track(GOOD, "Big Church")
        with self.assertRaises(LookupError):
            maintenance.add_loved_rule(self.conn, GOOD, "Not loved", church)
        with self.assertRaises(LookupError):
            maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", 999_999)
        with self.assertRaises(ValueError):  # another artist's track
            maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", self.track("Boris", "Farewell"))
        with self.assertRaises(ValueError):  # unknown artist
            maintenance.add_loved_rule(self.conn, "Nobody Known", "Song", church)
        with self.assertRaises(ValueError):  # matches already
            maintenance.add_loved_rule(self.conn, GOOD, "Aghartha", church)
        self.assertEqual(maintenance.loved_rules(self.conn), [])
        # linked already: a re-link must not keep the old id (an old Undo would remove the new link)
        r = maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", church)
        with self.assertRaises(ValueError):
            maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", self.track(GOOD, "Aghartha"))
        self.assertEqual([x["id"] for x in maintenance.loved_rules(self.conn)], [r["id"]])
        maintenance.remove_loved_rule(self.conn, r["id"])
        again = maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", church)
        self.assertNotEqual(again["id"], r["id"])  # ids aren't reused

    def test_rules_survive_an_artist_merge(self):
        typo_aghartha, typo_hunting = self.track(TYPO, "aghartha"), self.track(TYPO, "Hunting & Gathering")
        maintenance.add_loved_rule(self.conn, TYPO, "aghartha (live)", typo_aghartha)  # track combined by the merge
        maintenance.add_loved_rule(self.conn, TYPO, "Hunting and Gathering - Live", typo_hunting)  # track moved
        maintenance.merge_artists(self.conn, self.artist_id(TYPO), self.artist_id(GOOD))
        self.assertEqual(self.resolved("aghartha (live)"), self.track(GOOD, "Aghartha"))
        self.assertEqual(self.resolved("Hunting and Gathering - Live"), typo_hunting)
        artists = {r[0] for r in self.conn.execute("SELECT artist_id FROM loved WHERE title IN (?, ?)",
                                                   ("aghartha (live)", "Hunting and Gathering - Live"))}
        self.assertEqual(artists, {self.artist_id(GOOD)})
        self.assertEqual(len(maintenance.loved_rules(self.conn)), 2)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_upgrading_a_database_from_before_the_rules(self):
        rules_file = next(db.MIGRATIONS_DIR.glob("*_loved_title_rules.sql"))
        number = int(rules_file.name.split("_", 1)[0])
        with tempfile.TemporaryDirectory() as old:
            for f in db.MIGRATIONS_DIR.glob("*.sql"):  # the migrations as they were before the rules
                if int(f.name.split("_", 1)[0]) < number:
                    shutil.copy(f, old)
            path = Path(old) / "old.db"
            with mock.patch.object(db, "MIGRATIONS_DIR", Path(old)):
                conn = db.connect(path)
            try:
                self.assertLess(conn.execute("PRAGMA user_version").fetchone()[0], number)
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'loved_title_rules'").fetchone())
                ingest.ingest_records(conn, history(), source="test")
                updater.store_loved(conn, self.LOVED)
                aghartha = conn.execute("SELECT track_id FROM loved WHERE title = 'Aghartha'").fetchone()[0]
                self.assertIsNotNone(aghartha)
                db.migrate(conn)  # now with every migration
                self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0], number)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM loved_tracks").fetchone()[0], len(self.LOVED))
                self.assertEqual(conn.execute("SELECT track_id FROM loved WHERE title = 'Aghartha'").fetchone()[0], aghartha)
                church = conn.execute("SELECT t.id FROM tracks t WHERE t.title = 'Big Church'").fetchone()[0]
                maintenance.add_loved_rule(conn, GOOD, "Big Church (Remastered 2011)", church)
                self.assertEqual(conn.execute("SELECT track_id FROM loved WHERE title = ?",
                                              ("Big Church (Remastered 2011)",)).fetchone()[0], church)
            finally:
                conn.close()

    def test_rerunning_migrations_keeps_rules(self):
        church = self.track(GOOD, "Big Church")
        maintenance.add_loved_rule(self.conn, GOOD, "Big Church (Remastered 2011)", church)
        with self.conn:
            self.conn.execute("PRAGMA user_version = 3")
        db.migrate(self.conn)
        self.assertEqual(self.resolved("Big Church (Remastered 2011)"), church)

    def test_api(self):
        church = self.track(GOOD, "Big Church")
        self.conn.close()
        with TestClient(create_app(self.path)) as client:
            body = client.get("/api/loved/unmatched").json()
            self.assertEqual((body["total"], len(body["items"]), body["rules"]), (5, 4, []))
            link = {"artist": GOOD, "title": "Big Church (Remastered 2011)", "track_id": church}
            evil = {"origin": "https://evil.example"}
            self.assertEqual(client.post("/api/loved/rules", json=link, headers=evil).status_code, 403)
            self.assertEqual(client.post("/api/loved/rules", json={**link, "track_id": 999_999}).status_code, 404)
            self.assertEqual(client.post("/api/loved/rules", json={**link, "title": "Aghartha"}).status_code, 400)
            self.assertEqual(client.post("/api/loved/rules", json={**link, "title": ""}).status_code, 422)
            self.assertEqual(client.post("/api/loved/rules", json={**link, "track_id": 2**70}).status_code, 422)
            self.assertEqual(client.post("/api/loved/rules", json={**link, "track_id": 0}).status_code, 422)
            self.assertEqual(client.delete(f"/api/loved/rules/{2**70}").status_code, 422)
            self.assertEqual(client.delete(f"/api/loved/rules/{2**63 - 1}").status_code, 404)
            v = client.get("/api/version").json()["version"]
            self.assertEqual(client.post("/api/loved/rules", json=link).status_code, 200)
            self.assertNotEqual(client.get("/api/version").json()["version"], v)
            body = client.get("/api/loved/unmatched").json()
            self.assertEqual((len(body["items"]), [x["track_id"] for x in body["rules"]]), (3, [church]))
            rid = body["rules"][0]["id"]
            self.assertEqual(client.delete(f"/api/loved/rules/{rid}", headers=evil).status_code, 403)
            self.assertEqual(client.delete(f"/api/loved/rules/{rid}").json(), {"removed": True})
            self.assertEqual(client.delete(f"/api/loved/rules/{rid}").status_code, 404)
            self.assertEqual(len(client.get("/api/loved/unmatched").json()["items"]), 4)


if __name__ == "__main__":
    unittest.main()
