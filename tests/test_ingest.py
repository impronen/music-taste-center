import math
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from mtc import db, derive, fsutil, ingest, insights, updater
from mtc.ingest import Scrobble
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
        for kind, (key, _) in insights.INSIGHT_KINDS.items():
            # the full list starts with the card's rows and pages without gaps or repeats
            full = insights.insight_list(self.conn, kind, limit=500)["items"]
            if kind in ("binges", "staying-power", "deep-dives"):
                self.assertGreater(len(full), 12, kind)  # longer than the card, so paging is exercised
            self.assertEqual(full[:len(cards[key])], cards[key], kind)
            first = insights.insight_list(self.conn, kind, limit=3)
            rest = insights.insight_list(self.conn, kind, limit=500, offset=3)["items"]
            self.assertEqual(first["items"] + rest, full, kind)
            self.assertEqual(first["has_more"], len(full) > 3, kind)
        g = insights.graph(self.conn, n=15)
        self.assertEqual(len(g["nodes"]), 15)
        self.assertTrue(all("cluster" in n for n in g["nodes"]))
        top = insights.top(self.conn, "artist", None, None, 1)[0]
        detail = insights.artist(self.conn, top["id"])
        self.assertEqual(detail["plays"], top["plays"])
        self.assertEqual(sum(m["plays"] for m in detail["monthly"]), top["plays"])
        self.assertEqual(sum(detail["hours"]), top["plays"])


def rediscover_before_loved(conn, limit):
    """Rediscover as it was before loved tracks counted, frozen here to prove nothing changes
    while nothing is loved."""
    hi = conn.execute("SELECT MAX(ts) FROM scrobbles").fetchone()[0]
    core = dict(conn.execute(
        "SELECT artist_id, COUNT(*) FROM scrobbles WHERE ts > ? GROUP BY artist_id ORDER BY 2 DESC LIMIT 20",
        (hi - 90 * 86400,)))
    names = dict(conn.execute("SELECT id, name FROM artists"))
    stale = {a: (p, last) for a, p, last in conn.execute(
        "SELECT artist_id, plays, last_ts FROM artist_stats WHERE last_ts < ? AND plays >= 5", (hi - 365 * 86400,))}
    scores, because = defaultdict(float), defaultdict(list)
    marks = ",".join("?" * len(core))
    for a, b, score in conn.execute(
            f"SELECT a, b, score FROM artist_links WHERE a IN ({marks}) OR b IN ({marks})", (*core, *core)):
        for src, dst in ((a, b), (b, a)):
            if src in core and dst in stale:
                scores[dst] += score
                because[dst].append((score, src))
    ranked = sorted(scores, key=lambda a: (-scores[a] * math.log1p(stale[a][0]), a))[:limit]
    return [{"id": a, "name": names[a], "plays": stale[a][0], "last_ts": stale[a][1], "score": scores[a],
             "because": [{"id": s, "name": names[s]} for _, s in sorted(because[a], reverse=True)[:3]]}
            for a in ranked]


class RediscoverSyntheticTests(unittest.TestCase):
    """The full synthetic history (2 800 days) has artists that fell out of rotation."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        csv = Path(cls.tmp.name) / "export.csv"
        csv.write_text(synthetic.to_csv(synthetic.generate()), encoding="utf-8")
        cls.conn = db.connect(Path(cls.tmp.name) / "t.db")
        ingest.import_csv(cls.conn, csv)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def tearDown(self):
        updater.store_loved(self.conn, [])

    def assert_pages(self):
        full = insights.insight_list(self.conn, "rediscover", limit=500)["items"]
        self.assertGreater(len(full), 3)
        self.assertEqual(full[:15], insights.insights(self.conn)["rediscover"])
        first = insights.insight_list(self.conn, "rediscover", limit=3)
        rest = insights.insight_list(self.conn, "rediscover", limit=500, offset=3)["items"]
        self.assertEqual(first["items"] + rest, full)
        self.assertTrue(first["has_more"])
        return full

    def test_without_loved_tracks_nothing_changes(self):
        got = insights.rediscover(self.conn, 500)
        self.assertGreater(len(got), 3)
        self.assertEqual([r.pop("loved") for r in got], [0] * len(got))
        self.assertEqual(got, rediscover_before_loved(self.conn, 500))
        self.assert_pages()

    def test_loved_tracks_lift_an_artist_and_paging_still_works(self):
        items = insights.rediscover(self.conn, 500)
        last = items[-1]["name"]
        self.assertGreaterEqual(items[-1]["plays"], insights.REDISCOVER_MIN_PLAYS)  # so all four loves count
        titles = [t for (t,) in self.conn.execute(
            "SELECT t.title FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE a.name = ? ORDER BY t.id LIMIT 4",
            (last,))]
        updater.store_loved(self.conn, [(last, t, 1) for t in titles])
        full = self.assert_pages()
        names = [r["name"] for r in full]
        # the same artists, with the loved one's rank tripled (4 loves = the 3× cap)
        expected = [r["name"] for r in sorted(items, key=lambda r: (
            -r["score"] * math.log1p(r["plays"]) * (3 if r["name"] == last else 1), r["id"]))]
        self.assertEqual(names, expected)
        self.assertLess(names.index(last), len(names) - 1)
        self.assertEqual({r["name"]: r["loved"] for r in full if r["loved"]}, {last: 4})


class RediscoverLovedTests(unittest.TestCase):
    """A small history: Core is played now; X, Y and Z were played alongside it over a year ago.
    X and Y have identical histories (6 plays, 6 shared sessions); Z has 3 plays."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        t0, day = 1_600_000_000 - 1_600_000_000 % 60, 86400
        recs = []
        for d in range(6):
            ts = t0 + d * day
            recs += [Scrobble("Core", f"c{d}", ts), Scrobble("Ex", f"x{d}", ts + 180), Scrobble("Why", f"y{d}", ts + 360)]
            if d < 3:
                recs.append(Scrobble("Zed", f"z{d}", ts + 540))
        recs += [Scrobble("Core", f"c{d}", t0 + d * day) for d in range(400, 410)]
        recs.append(Scrobble("Solo", "s0", t0 + 20 * day))  # a session of its own: no links
        ingest.ingest_records(self.conn, recs, source="csv")
        derive.rebuild(self.conn)
        self.id = dict(self.conn.execute("SELECT name, id FROM artists"))
        self.track = {"Ex": "x", "Why": "y"}  # track title prefixes

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def names(self):
        return [r["name"] for r in insights.rediscover(self.conn)]

    def love(self, *pairs):
        updater.store_loved(self.conn, [(a, t, 1) for a, t in pairs])

    def test_a_loved_artist_outranks_an_otherwise_equal_one(self):
        lo, hi = sorted(["Ex", "Why"], key=self.id.get)
        self.assertEqual(self.names(), [lo, hi])  # a tie goes to the lower id; Zed has too few plays
        self.love((hi, self.track[hi] + "0"))
        items = insights.rediscover(self.conn)
        self.assertEqual([(r["name"], r["loved"]) for r in items], [(hi, 1), (lo, 0)])

    def test_a_loved_artist_with_few_plays_gets_in_when_linked(self):
        self.love(("Zed", "z0"))
        items = {r["name"]: r for r in insights.rediscover(self.conn)}
        self.assertEqual((items["Zed"]["plays"], items["Zed"]["loved"]), (3, 1))
        self.love(("Zed", "z0"), ("Solo", "s0"))  # loved and stale, but never played alongside Core
        self.assertEqual(self.names(), ["Ex", "Why", "Zed"] if self.id["Ex"] < self.id["Why"] else ["Why", "Ex", "Zed"])

    def test_a_low_play_artist_counts_one_love_at_most(self):
        # Zed (3 plays, 3 shared sessions) with every track loved would beat Ex and Why (6 shared
        # sessions each) at the full 3×; let in on a love, it counts one, and stays below them.
        self.love(("Zed", "z0"), ("Zed", "z1"), ("Zed", "z2"))
        items = insights.rediscover(self.conn)
        lo, hi = sorted(["Ex", "Why"], key=self.id.get)
        self.assertEqual([(r["name"], r["loved"]) for r in items], [(lo, 0), (hi, 0), ("Zed", 3)])
        z, x = items[2], items[0]
        full = z["score"] * math.log1p(z["plays"]) * insights.loved_boost(3)
        self.assertGreater(full, x["score"] * math.log1p(x["plays"]))  # the cap is what keeps it below

    def test_the_boost_is_bounded(self):
        self.assertEqual(insights.loved_boost(0), 1)
        self.assertEqual(insights.loved_boost(1), 1.5)
        self.assertEqual(insights.loved_boost(4), insights.loved_boost(100))
        lo, hi = sorted(["Ex", "Why"], key=self.id.get)
        self.love(*[(hi, f"{self.track[hi]}{d}") for d in range(6)], *[(lo, f"{self.track[lo]}{d}") for d in range(4)])
        items = insights.rediscover(self.conn)
        # six loves count no more than four: still a tie, which the lower id wins
        self.assertEqual([(r["name"], r["loved"]) for r in items], [(lo, 4), (hi, 6)])


if __name__ == "__main__":
    unittest.main()
