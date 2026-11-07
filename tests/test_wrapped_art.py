"""Cover downloads for the year in review: sources and their order, caching, misses, retries, safety."""
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

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
