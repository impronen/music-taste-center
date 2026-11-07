"""Cover downloads for the year in review: sources and their order, caching, misses, retries, safety."""
import http.server
import json
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from mtc import db, derive, ingest, rhythms, wrapped, wrapped_art, wrapped_render
from mtc.ingest import Scrobble
from tests.test_wrapped import at


def png(size: int = 24, seed: int = 0) -> bytes:
    """A small valid PNG (diagonal gradient) without any image library."""
    rows = b"".join(b"\x00" + bytes(((x * 9 + seed * 40) % 256, (y * 9) % 256, ((x + y) * 5) % 256)[i] for x in range(size) for i in range(3))
                    for y in range(size))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Fake:
    """A transport answering by URL; records every request."""
    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        a = self.answers.get(url, (404, b""))
        return a(url) if callable(a) else a


def client_run(conn, data, out, transport):
    return wrapped_art.fetch(conn, data, out, transport, sleep=lambda s: None)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rhythms._cache.clear()
        self.conn = db.connect(Path(self.tmp.name) / "a.db")
        self.addCleanup(self.conn.close)
        recs = [Scrobble("Alpha", f"s{i}", at("2024-03-01") + i * 86400, "Alpha LP") for i in range(8)]
        recs += [Scrobble("Beta", f"t{i}", at("2024-04-01") + i * 86400, "Beta LP") for i in range(5)]
        recs += [Scrobble("Gamma", f"u{i}", at("2024-05-01") + i * 86400, "") for i in range(3)]  # no album
        ingest.ingest_records(self.conn, recs, source="test")
        derive.rebuild(self.conn)
        self.ids = dict(self.conn.execute("SELECT title, id FROM albums"))
        with self.conn:
            self.conn.execute("INSERT INTO album_info(album_id, status, release_group_mbid, mbid, image_url, fetched_at)"
                              " VALUES (?, 'ok', 'rg-alpha', 'rel-alpha', NULL, 0)", (self.ids["Alpha LP"],))
            self.conn.execute("INSERT INTO album_info(album_id, status, image_url, fetched_at) VALUES (?, 'ok', ?, 0)",
                              (self.ids["Beta LP"], "https://lastfm.freetls.fastly.net/i/u/300x300/abc.png"))
        self.data = wrapped.build(self.conn, 2024)
        self.out = Path(self.tmp.name) / "out"

    def manifest(self):
        return json.loads((self.out / "art" / "manifest.json").read_text())

    def test_targets_cover_top_albums_and_stand_ins_for_artists(self):
        albums, stands_for = wrapped_art.targets(self.conn, self.data)
        self.assertIn(self.ids["Alpha LP"], albums)
        artist = self.conn.execute("SELECT id FROM artists WHERE name = 'Alpha'").fetchone()[0]
        self.assertEqual(stands_for[artist], self.ids["Alpha LP"])
        self.assertEqual(len(albums), len(set(albums)))

    def test_release_group_first_and_files_written(self):
        t = Fake({"https://coverartarchive.org/release-group/rg-alpha/front-500": (200, png()),
                  "https://lastfm.freetls.fastly.net/i/u/300x300/abc.png": (200, png(seed=1))})
        r = client_run(self.conn, self.data, self.out, t)
        self.assertEqual(r["with_cover"], 2)
        m = self.manifest()
        self.assertEqual(m["albums"][str(self.ids["Alpha LP"])], f"album-{self.ids['Alpha LP']}.png")
        self.assertTrue((self.out / "art" / f"album-{self.ids['Alpha LP']}.png").read_bytes().startswith(b"\x89PNG"))
        self.assertNotIn("https://coverartarchive.org/release/rel-alpha/front-500", t.urls)  # first source answered
        self.assertEqual(m["names"][str(self.ids["Alpha LP"])], {"title": "Alpha LP", "artist": "Alpha"})  # cards print it
        self.assertEqual(wrapped_render._art(self.out)["names"], m["names"])

    def test_falls_back_to_release_then_lastfm(self):
        t = Fake({"https://coverartarchive.org/release/rel-alpha/front-500": (200, png())})
        client_run(self.conn, self.data, self.out, t)
        self.assertTrue(self.manifest()["albums"][str(self.ids["Alpha LP"])])
        self.assertEqual(t.urls[0], "https://coverartarchive.org/release-group/rg-alpha/front-500")

    def test_misses_are_remembered_and_not_asked_again(self):
        client_run(self.conn, self.data, self.out, Fake({}))
        self.assertIsNone(self.manifest()["albums"][str(self.ids["Alpha LP"])])
        again = Fake({})
        client_run(self.conn, self.data, self.out, again)
        self.assertEqual(again.urls, [])

    def test_transient_failure_is_retried_on_the_next_run_not_recorded_as_a_miss(self):
        down = Fake({u: (503, b"") for u in ("https://coverartarchive.org/release-group/rg-alpha/front-500",
                                              "https://coverartarchive.org/release/rel-alpha/front-500")})
        client_run(self.conn, self.data, self.out, down)
        self.assertNotIn(str(self.ids["Alpha LP"]), self.manifest()["albums"])
        up = Fake({"https://coverartarchive.org/release-group/rg-alpha/front-500": (200, png())})
        client_run(self.conn, self.data, self.out, up)
        self.assertTrue(self.manifest()["albums"][str(self.ids["Alpha LP"])])

    def test_placeholder_not_images_and_foreign_hosts_are_refused(self):
        with self.conn:
            self.conn.execute("UPDATE album_info SET image_url = ? WHERE album_id = ?",
                              (f"https://lastfm.freetls.fastly.net/i/u/300x300/{wrapped_art.PLACEHOLDER}.png", self.ids["Beta LP"]))
        t = Fake({})
        client_run(self.conn, self.data, self.out, t)
        self.assertFalse(any(wrapped_art.PLACEHOLDER in u for u in t.urls))
        client = wrapped_art.CoverArt(Fake({"http://evil.example/x.png": (200, png())}), sleep=lambda s: None)
        self.assertIsNone(client.image("http://evil.example/x.png"))
        self.assertIsNone(client.image("https://evil.example/x.png"))
        self.assertIsNone(client.image("https://coverartarchive.org.evil.example/x.png"))
        html_body = Fake({"https://coverartarchive.org/a": (200, b"<html>not an image</html>")})
        self.assertIsNone(wrapped_art.CoverArt(html_body, sleep=lambda s: None).image("https://coverartarchive.org/a"))

    def test_redirects_are_followed_only_to_allowed_hosts(self):
        base = "https://coverartarchive.org/release-group/rg-alpha/front-500"
        ok = Fake({base: (307, b"https://ia800000.us.archive.org/1/x.png"), "https://ia800000.us.archive.org/1/x.png": (200, png())})
        self.assertIsNotNone(wrapped_art.CoverArt(ok, sleep=lambda s: None).image(base))
        bad = Fake({base: (307, b"https://evil.example/x.png"), "https://evil.example/x.png": (200, png())})
        self.assertIsNone(wrapped_art.CoverArt(bad, sleep=lambda s: None).image(base))
        self.assertEqual(bad.urls, [base])  # the second hop was never requested
        plain = Fake({base: (302, b"http://archive.org/x.png")})
        self.assertIsNone(wrapped_art.CoverArt(plain, sleep=lambda s: None).image(base))
        loop = Fake({base: (302, base.encode())})
        self.assertIsNone(wrapped_art.CoverArt(loop, sleep=lambda s: None).image(base))  # hop limit

    def test_odd_urls_status_codes_size_and_retries(self):
        c = lambda t: wrapped_art.CoverArt(t, sleep=lambda s: None)
        self.assertIsNone(c(Fake({})).image("https:///x.png"))  # no host
        u = "https://coverartarchive.org/a"
        for status in (400, 403, 404):
            self.assertIsNone(c(Fake({u: (status, b"")})).image(u))
        with mock.patch.object(wrapped_art, "MAX_BYTES", 10):
            self.assertIsNone(c(Fake({u: (200, png())})).image(u))
        seq = iter([(429, b""), (503, b""), (200, png())])
        self.assertIsNotNone(c(Fake({u: lambda url: next(seq)})).image(u))  # retried through 429 and 503
        def boom(url):
            raise OSError("down")
        with self.assertRaises(wrapped_art.ApiError):
            c(Fake({u: boom})).image(u)

    def test_image_transport_stops_at_redirects_and_caps_the_body(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/r":
                    self.send_response(302); self.send_header("Location", "/big"); self.end_headers()
                else:
                    self.send_response(200); self.end_headers(); self.wfile.write(b"x" * 5000)
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"
        self.assertEqual(wrapped_art.image_transport(base + "/r", {}, 5), (302, b"/big"))
        with mock.patch.object(wrapped_art, "MAX_BYTES", 100):
            status, body = wrapped_art.image_transport(base + "/big", {}, 5)
        self.assertEqual((status, len(body)), (200, 101))  # stopped reading just past the cap

    def test_albums_without_a_source_are_not_cached_as_misses(self):
        with self.conn:
            self.conn.execute("DELETE FROM album_info")
        t = Fake({})
        client_run(self.conn, self.data, self.out, t)
        self.assertEqual(t.urls, [])
        self.assertEqual(self.manifest()["albums"], {})  # after `enrich` a rerun may find covers

    def test_corrupt_manifest_is_ignored_and_writes_are_atomic(self):
        (self.out / "art").mkdir(parents=True)
        (self.out / "art" / "manifest.json").write_text('{"albums": {"1"')  # cut off mid-write
        t = Fake({"https://coverartarchive.org/release-group/rg-alpha/front-500": (200, png())})
        client_run(self.conn, self.data, self.out, t)
        self.assertTrue(self.manifest()["albums"][str(self.ids["Alpha LP"])])
        self.assertFalse((self.out / "art" / "manifest.tmp").exists())
        self.assertEqual(wrapped_render._art(self.out / "nowhere"), {"albums": {}, "artists": {}, "names": {}})

    def test_cards_need_chrome_and_check_what_it_wrote(self):
        with mock.patch.object(wrapped_render, "chrome", return_value=None):
            with self.assertRaises(RuntimeError):
                wrapped_render.cards(self.data, self.out)
        def fake_run(cmd, **kw):  # exits 0 but writes nothing
            return None
        self.out.mkdir(exist_ok=True)
        stale = self.out / "cards" / "99-old-story.png"
        stale.parent.mkdir(exist_ok=True)
        stale.write_bytes(b"old")
        with mock.patch.object(wrapped_render, "chrome", return_value="chrome"), \
                mock.patch.object(wrapped_render.subprocess, "run", fake_run):
            with self.assertRaises(RuntimeError):
                wrapped_render.cards(self.data, self.out)
        self.assertFalse(stale.exists())  # cards of an earlier run are cleared

    def test_page_embeds_covers_and_escapes_script_text(self):
        t = Fake({"https://coverartarchive.org/release-group/rg-alpha/front-500": (200, png())})
        client_run(self.conn, self.data, self.out, t)
        with self.conn:
            self.conn.execute("UPDATE artists SET name = '</script><b>x' WHERE name = 'Beta'")
        data = wrapped.build(self.conn, 2024)
        html = wrapped_render.page(data, self.out).read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", html)
        self.assertEqual(html.count("</script>"), 3)  # data, art and code: the name could not close one
        self.assertIn("<\\/script>", html)

    def test_png_size_reads_the_header(self):
        p = Path(self.tmp.name) / "s.png"
        p.write_bytes(png(24))
        self.assertEqual(wrapped_render.png_size(p), (24, 24))

    def test_card_ids_skip_sections_without_data(self):
        ids = wrapped_render.card_ids(self.data)
        self.assertEqual(ids[0], "intro")
        self.assertEqual(ids[-1], "outro")
        self.assertNotIn("ages", ids)  # no release dates in this library
        self.assertNotIn("genres", ids)  # and no tags
        self.assertNotIn("loved", ids)


if __name__ == "__main__":
    unittest.main()
