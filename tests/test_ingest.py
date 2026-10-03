import tempfile
import unittest
from pathlib import Path

from mtc import db, derive, fsutil, ingest, insights
from tests import synthetic


class ParseTests(unittest.TestCase):
    def test_lastfm_text_date_is_utc(self):
        self.assertEqual(ingest.parse_date("22 May 2014, 20:11"), 1400789460)

    def test_other_date_forms(self):
        self.assertEqual(ingest.parse_date("1400789483"), 1400789483)
        self.assertEqual(ingest.parse_date("1400789483000"), 1400789483)
        self.assertEqual(ingest.parse_date("2014-05-22T20:11:00Z"), 1400789460)
        self.assertIsNone(ingest.parse_date(""))
        self.assertIsNone(ingest.parse_date("yesterday"))

    def test_headerless_export_with_quotes_and_now_playing(self):
        text = '﻿Kärpäset,Öinen,"Song, with comma",\n"Paper Lanterns, Inc.",,"Say ""hi""","22 May 2014, 20:11"\n'
        rows, skipped = ingest.parse_csv(text)
        self.assertEqual(skipped, 1)  # now-playing row has no date
        self.assertEqual(rows[0].artist, "Paper Lanterns, Inc.")
        self.assertEqual(rows[0].track, 'Say "hi"')
        self.assertEqual(rows[0].album, "")

    def test_header_with_uts_column(self):
        rows, _ = ingest.parse_csv("uts,artist,album,track\n1400789483,Häkä,Lumi,Ranta\n")
        self.assertEqual((rows[0].artist, rows[0].track, rows[0].ts), ("Häkä", "Ranta", 1400789483))

    def test_decoding_fallbacks_keep_finnish_letters(self):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            text, used = fsutil.decode("Pöllö ja Yö,Åkerlund,ä".encode(enc))
            self.assertEqual(text, "Pöllö ja Yö,Åkerlund,ä", enc)
        self.assertEqual(fsutil.decode(b"\x81\xe4")[1], "latin-1")  # 0x81 is undefined in cp1252

    def test_name_key_merges_spelling_variants(self):
        self.assertEqual(ingest.key("  The  Quiet ARCADE "), ingest.key("the quiet arcade"))


class MinuteTests(unittest.TestCase):
    def test_minute_floors(self):
        self.assertEqual(ingest.minute(1400789483), 1400789460)
        self.assertEqual(ingest.minute(1400789460), 1400789460)


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rows = synthetic.generate(days=500)
        cls.csv = Path(cls.tmp.name) / "export.csv"
        cls.csv.write_text(synthetic.to_csv(cls.rows), encoding="utf-8")
        cls.conn = db.connect(Path(cls.tmp.name) / "t.db")
        cls.first = ingest.import_csv(cls.conn, cls.csv)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def expected_unique(self):
        return len({(a, t, ts // 60) for a, _, t, ts in self.rows})

    def csv_rows(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM scrobbles s JOIN imports i ON i.id = s.import_id WHERE i.source = 'csv'").fetchone()[0]

    def test_import_counts(self):
        self.assertEqual(self.first["rows_skipped"], 1)
        self.assertEqual(self.first["rows_added"], self.expected_unique())
        self.assertEqual(self.csv_rows(), self.expected_unique())

    def test_reimport_is_idempotent(self):
        again = ingest.import_csv(self.conn, self.csv)
        self.assertEqual(again["rows_added"], 0)
        self.assertEqual(self.csv_rows(), self.expected_unique())

    def test_api_style_records_merge_with_csv(self):
        newest = max(r[3] for r in self.rows)
        recs = [ingest.Scrobble(artist="kärpäset", track="Uusi biisi", album="", ts=newest + 3600)]
        r = ingest.ingest_records(self.conn, recs, source="lastfm-api", label="test")
        derive.rebuild(self.conn)
        self.assertEqual(r["rows_added"], 1)
        # casefolded name resolves to the existing artist
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artists WHERE name_key = 'kärpäset'").fetchone()[0], 1)

    def test_api_seconds_match_csv_minutes(self):
        # The CSV only has "dd Mon yyyy, HH:MM"; the API gives exact seconds for the same listen.
        artist, album, track, ts = self.rows[len(self.rows) // 2]
        same_listen = ingest.Scrobble(artist=artist, track=track, album=album, ts=ts)
        self.assertNotEqual(ts % 60, 0, "fixture should carry seconds")
        r = ingest.ingest_records(self.conn, [same_listen], source="lastfm-api", label="test")
        self.assertEqual(r["rows_added"], 0)
        self.assertEqual(r["min_ts"] % 60, 0)

    def test_stored_timestamps_are_whole_minutes(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE ts % 60 != 0").fetchone()[0], 0)

    def test_derived_tables(self):
        plays = self.conn.execute("SELECT SUM(plays) FROM artist_stats").fetchone()[0]
        total = self.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
        self.assertEqual(plays, total)
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM artist_links").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles WHERE session_id IS NULL").fetchone()[0], 0)
        # artists unlocked later than the prehistory window are discoveries with a gateway
        self.assertGreater(
            self.conn.execute("SELECT COUNT(*) FROM artist_stats WHERE prehistory = 0 AND gateway_id IS NOT NULL").fetchone()[0], 0)

    def test_links_reflect_genre_clusters(self):
        # Artists of the same synthetic genre should link more strongly than across genres.
        cluster_of = {name: c for c, names in synthetic.CLUSTERS.items() for name in names}
        names = dict(self.conn.execute("SELECT id, name FROM artists"))
        same, cross = [], []
        for a, b, score in self.conn.execute("SELECT a, b, score FROM artist_links"):
            if names[a] not in cluster_of or names[b] not in cluster_of:
                continue  # one-hit "Project" artists sit outside the named clusters
            (same if cluster_of[names[a]] == cluster_of[names[b]] else cross).append(score)
        self.assertGreater(sum(same) / len(same), 2 * (sum(cross) / max(len(cross), 1)))

    def test_insight_queries_run(self):
        o = insights.overview(self.conn)
        self.assertFalse(o["empty"])
        self.assertGreater(o["longest_streak"], 1)
        self.assertTrue(insights.timeline(self.conn))
        self.assertTrue(insights.eras(self.conn))
        cards = insights.insights(self.conn)
        self.assertIn("rediscover", cards)
        g = insights.graph(self.conn, n=15)
        self.assertEqual(len(g["nodes"]), 15)
        self.assertTrue(all("cluster" in n for n in g["nodes"]))
        top = insights.top(self.conn, "artist", None, None, 1)[0]
        detail = insights.artist(self.conn, top["id"])
        self.assertEqual(detail["plays"], top["plays"])
        self.assertEqual(sum(m["plays"] for m in detail["monthly"]), top["plays"])
        self.assertEqual(sum(detail["hours"]), top["plays"])


if __name__ == "__main__":
    unittest.main()
