"""Metadata layer tests. All HTTP goes through a fake transport shaped like the real
last.fm / MusicBrainz JSON (captured 2026-10), so nothing touches the network."""
import json
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, enrich, ingest, insights, tags
from mtc.api import create_app
from mtc.lastfm import LastFm, tag_list
from mtc.musicbrainz import MusicBrainz, lucene_quote
from mtc.webapi import ApiError, Fatal, NotFound
from tests import synthetic

GENRES = {"suomirock": "finnish rock", "electronic": "electronic", "jazz": "jazz", "metal": "black metal",
          "indie": "indie pop"}
CLUSTER = {name: c for c, names in synthetic.CLUSTERS.items() for name in names}


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


def lastfm_transport(overrides=None, calls=None):
    """Answer last.fm methods for the synthetic library. overrides: {(method, artist): (status, body)}."""
    overrides = overrides or {}

    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert headers["User-Agent"].startswith("TasteCenter/")
        method, artist, album = q["method"], q.get("artist", ""), q.get("album")
        if calls is not None:
            calls.append((method, artist, album))
        hit = overrides.get((method, artist))
        if hit:
            hit = hit.pop(0) if isinstance(hit, list) else hit
            return hit[0], json.dumps(hit[1]).encode()
        if artist not in CLUSTER:
            return 200, json.dumps({"error": 6, "message": "The artist you supplied could not be found"}).encode()
        genre = GENRES[CLUSTER[artist]]
        if method == "artist.getInfo":
            body = {"artist": {"name": artist, "mbid": "", "url": f"https://www.last.fm/music/{artist}",
                               "stats": {"listeners": "1234", "playcount": "56789"},
                               "tags": {"tag": [{"name": genre, "url": "x"}]},
                               "bio": {"summary": f"{artist} is a band."}}}
        elif method == "artist.getTopTags":
            body = {"toptags": {"tag": [
                {"count": 100, "name": genre}, {"count": 40, "name": "Finnish" if genre == "finnish rock" else "rock"},
                {"count": 30, "name": "seen live"}, {"count": 20, "name": artist},  # own name: dropped
            ], "@attr": {"artist": artist}}}
        elif method == "album.getInfo":
            body = {"album": {"name": album, "artist": artist, "mbid": "rel-" + album if "Ranta" in album else "",
                              "url": "u", "listeners": "10", "playcount": "20",
                              "image": [{"size": "small", "#text": ""}, {"size": "large", "#text": "https://img/x.png"}],
                              "tracks": {"track": {"name": "only one", "duration": 200}},  # single track: bare object
                              "tags": ""}}
        elif method == "album.getTopTags":
            body = {"toptags": {"tag": {"count": 100, "name": "2019"}}}  # single tag: bare object
        else:
            return 200, json.dumps({"error": 3, "message": "Invalid Method"}).encode()
        return 200, json.dumps(body).encode()

    return transport


def mb_transport(calls=None):
    def transport(url, headers, timeout):
        parts = urllib.parse.urlsplit(url)
        if calls is not None:
            calls.append(parts.path)
        if parts.path.startswith("/ws/2/release/"):
            return 404, b'{"error": "Not Found"}'  # stale MBID from last.fm -> search fallback
        q = dict(urllib.parse.parse_qsl(parts.query))
        # undo lucene_quote's escaping, as MusicBrainz's search does
        field = lambda f: re.sub(r"\\(.)", r"\1", re.search(f + r':"((?:\\.|[^"\\])*)"', q["query"]).group(1))
        title, artist = field("releasegroup"), field("artist")
        body = {"release-groups": [{"id": "rg-1", "score": 100, "title": title, "first-release-date": "2018-03-09",
                                    "primary-type": "Album", "artist-credit": [{"name": artist}]}]}
        return 200, json.dumps(body).encode()

    return transport


class ParsingTests(unittest.TestCase):
    def test_tag_list_quirks(self):
        self.assertEqual(tag_list({"tag": {"name": "jazz", "count": 7}}), [("jazz", 7)])
        self.assertEqual(tag_list(""), [])
        self.assertEqual(tag_list({"tag": [{"name": "a"}, {"name": "b"}]}), [("a", 100), ("b", 80)])

    def test_classify(self):
        self.assertEqual(tags.classify("1997"), "year")
        self.assertEqual(tags.classify("80s"), "decade")
        self.assertEqual(tags.classify("Finnish"), "place")
        self.assertEqual(tags.classify("Seen Live"), "other")
        self.assertEqual(tags.classify("post-rock"), "genre")

    def test_tag_spelling_variants_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            a = enrich._tag_weights(conn, [("post-rock", 100), ("Post Rock", 40), ("postrock", 10)], set())
            self.assertEqual(list(a.values()), [100])
            self.assertEqual(conn.execute("SELECT name FROM tags").fetchall()[0][0], "post-rock")
            conn.close()

    def test_lucene_quote(self):
        self.assertEqual(lucene_quote('Say "hi" (live)'), '"Say \\"hi\\" \\(live\\)"')


class ClientTests(unittest.TestCase):
    def test_throttle_and_user_agent(self):
        clk = FakeClock()
        lf = LastFm("k", lastfm_transport(), min_interval=0.5, sleep=clk.sleep, clock=clk.clock)
        lf.artist_tags("Häkä")
        lf.artist_tags("Häkä")
        self.assertEqual(clk.sleeps, [0.5])

    def test_rate_limit_is_retried(self):
        clk = FakeClock()
        busy = (200, {"error": 29, "message": "Rate Limit Exceeded"})
        ok = (200, {"toptags": {"tag": [{"name": "jazz", "count": 100}]}})
        lf = LastFm("k", lastfm_transport({("artist.getTopTags", "X"): [busy, busy, ok]}),
                    min_interval=0, sleep=clk.sleep, clock=clk.clock)
        self.assertEqual(lf.artist_tags("X"), [("jazz", 100)])
        self.assertEqual(lf.requests, 3)
        self.assertIn(2.0, clk.sleeps)
        self.assertIn(4.0, clk.sleeps)

    def test_errors(self):
        clk = FakeClock()
        lf = LastFm("k", lastfm_transport({("artist.getInfo", "Bad"): (403, {"error": 10, "message": "Invalid API key"}),
                                           ("artist.getInfo", "Down"): (503, None)}),
                    min_interval=0, sleep=clk.sleep, clock=clk.clock)
        with self.assertRaises(NotFound):
            lf.artist_info("Nobody At All")
        with self.assertRaises(Fatal):
            lf.artist_info("Bad")
        with self.assertRaises(ApiError):
            lf.artist_info("Down")
        with self.assertRaises(Fatal):
            LastFm("")

    def test_musicbrainz_search_requires_exact_title(self):
        clk = FakeClock()

        def transport(url, headers, timeout):
            body = {"release-groups": [{"id": "x", "score": 95, "title": "Something Else", "artist-credit": [{"name": "A"}]}]}
            return 200, json.dumps(body).encode()
        mb = MusicBrainz(transport, min_interval=0, sleep=clk.sleep, clock=clk.clock)
        with self.assertRaises(NotFound):
            mb.search_release_group("A", "Title")


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "e.db")
        rows = synthetic.generate(days=400)
        ingest.import_csv_text(self.conn, synthetic.to_csv(rows), label="t.csv", encoding="utf-8")
        self.clk = FakeClock()
        self.calls = []
        self.lf = LastFm("k", lastfm_transport(calls=self.calls), min_interval=0, sleep=self.clk.sleep, clock=self.clk.clock)
        self.mb = MusicBrainz(mb_transport(), min_interval=0, sleep=self.clk.sleep, clock=self.clk.clock)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_all(self, **kw):
        return enrich.run(self.conn, lastfm=self.lf, musicbrainz=self.mb, log=lambda *_: None, **kw)

    def test_full_run_and_resume(self):
        summary = self.run_all()
        n_artists = self.conn.execute("SELECT COUNT(*) FROM artist_stats").fetchone()[0]
        self.assertEqual(sum(summary["artists"].values()), n_artists)
        self.assertGreater(summary["artists"].get("not_found", 0), 0)  # "...Project" one-hit artists are unknown
        # own name and "seen live" are not genres; "Finnish" is a place
        names = {r[0] for r in self.conn.execute("SELECT t.name FROM tags t JOIN artist_tags x ON x.tag_id = t.id")}
        self.assertNotIn("kärpäset", names)
        kinds = dict(self.conn.execute("SELECT name, kind FROM tags"))
        self.assertEqual(kinds["finnish"], "place")
        self.assertEqual(kinds["seen live"], "other")
        # second run: nothing pending, no requests
        before = len(self.calls)
        again = self.run_all()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(again["artists"], {})

    def test_genre_profile_follows_clusters(self):
        self.run_all(albums=0, releases=0)
        g = insights.genres(self.conn)
        self.assertEqual({x["name"] for x in g["items"]} >= set(GENRES.values()), True)
        self.assertAlmostEqual(sum(x["share"] for x in g["items"]), 1.0, places=6)
        self.assertGreater(g["coverage"], 0.8)
        jazz = next(x for x in g["items"] if x["name"] == "jazz")
        self.assertTrue(all(CLUSTER.get(a["name"]) == "jazz" for a in jazz["artists"]))
        t = insights.tag(self.conn, jazz["id"])
        self.assertTrue(t["artists"])
        self.assertAlmostEqual(sum(m["plays"] for m in t["monthly"]), jazz["plays"], delta=1)

    def test_failed_refresh_keeps_good_data(self):
        self.run_all(albums=0, releases=0)
        aid, name = self.conn.execute(
            "SELECT a.id, a.name FROM artists a JOIN artist_info i ON i.artist_id = a.id WHERE i.status = 'ok' LIMIT 1").fetchone()
        n_tags = self.conn.execute("SELECT COUNT(*) FROM artist_tags WHERE artist_id = ?", (aid,)).fetchone()[0]
        down = LastFm("k", lastfm_transport({("artist.getInfo", name): (503, None)}),
                      min_interval=0, sleep=self.clk.sleep, clock=self.clk.clock)
        self.assertEqual(enrich.enrich_artist(self.conn, down, aid, name)[0], "error")
        row = self.conn.execute("SELECT status FROM artist_info WHERE artist_id = ?", (aid,)).fetchone()
        self.assertEqual(row[0], "ok")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artist_tags WHERE artist_id = ?", (aid,)).fetchone()[0], n_tags)

    def test_albums_and_release_dates(self):
        self.run_all(artists=0, releases=0)
        # tag year is the provisional release date
        row = self.conn.execute("SELECT release_date, release_date_source, n_tracks, image_url FROM album_info"
                                " WHERE status = 'ok' LIMIT 1").fetchone()
        self.assertEqual(tuple(row), ("2019", "tag", 1, "https://img/x.png"))
        self.run_all(artists=0, albums=0)
        srcs = dict(self.conn.execute("SELECT release_date_source, COUNT(*) FROM album_info WHERE status = 'ok' GROUP BY 1"))
        self.assertEqual(set(srcs), {"musicbrainz"})  # including the artist with quotes in its name
        self.assertEqual(self.conn.execute("SELECT DISTINCT release_date FROM album_info WHERE status = 'ok'").fetchall()[0][0], "2018-03-09")
        # re-fetching the album from last.fm must not downgrade a MusicBrainz date back to the tag year
        al = self.conn.execute("SELECT al.id, a.name, al.title FROM albums al JOIN artists a ON a.id = al.artist_id"
                               " JOIN album_info i ON i.album_id = al.id LIMIT 1").fetchone()
        enrich.enrich_album(self.conn, self.lf, *al)
        self.assertEqual(self.conn.execute("SELECT release_date_source FROM album_info WHERE album_id = ?",
                                           (al[0],)).fetchone()[0], "musicbrainz")

    def test_missing_key_is_fatal(self):
        import os
        from unittest import mock

        from mtc import config
        with mock.patch.dict(os.environ, {"LASTFM_API_KEY": ""}), \
                mock.patch.object(config, "SETTINGS_PATH", Path(self.tmp.name) / "none.json"):
            with self.assertRaises(Fatal):
                enrich.run(self.conn, log=lambda *_: None)

    def test_api_endpoints(self):
        self.run_all(releases=0)
        self.conn.commit()
        client = TestClient(create_app(Path(self.tmp.name) / "e.db"))
        self.assertGreater(client.get("/api/metadata/status").json()["artists_tagged"], 0)
        g = client.get("/api/genres", params={"start": "2019-01-01", "end": "2019-12-31"}).json()
        self.assertTrue(g["items"])
        self.assertEqual(client.get(f"/api/tags/{g['items'][0]['id']}").status_code, 200)
        self.assertEqual(client.get("/api/tags/999999").status_code, 404)
        a = client.get("/api/top/artist?limit=1").json()[0]
        detail = client.get(f"/api/artists/{a['id']}").json()
        self.assertEqual(detail["meta"]["status"], "ok")
        self.assertTrue(detail["tags"])
        self.assertTrue(client.get("/api/eras").json()[0]["genres"])


if __name__ == "__main__":
    unittest.main()
