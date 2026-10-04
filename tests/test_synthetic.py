"""The fictional demo library (`python -m tests.synthetic --demo-db PATH`) that the README sends newcomers to."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, decades, rhythms
from mtc.api import create_app
from tests import synthetic


class DemoDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "demo.db"
        synthetic.build_demo_db(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_page_has_something_to_show(self):
        with TestClient(create_app(self.path)) as c:
            overview = c.get("/api/overview").json()
            dec, age, lag, rh = (c.get(f"/api/decades{p}").json() for p in ("", "/age", "/lag", "/rhythms"))
            genres = c.get("/api/rhythms").json()
        self.assertFalse(overview["empty"])
        self.assertGreater(overview["plays"], 10_000)
        self.assertGreater(dec["coverage"], 0.8)
        self.assertGreater(len(dec["decades"]), 3)
        self.assertTrue(age["covered"] and lag["covered"] and lag["eras"])
        self.assertIsNotNone(rh["genres"])  # genre tags are there
        self.assertGreater(genres["coverage"], 0.8)
        self.assertTrue(any(s["genres"] for s in genres["seasons"]))  # the seasonal habits show up

    def test_it_looks_like_a_fetch_left_it(self):
        conn = db.connect(self.path)
        try:
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM tags GROUP BY kind"))
            sources = {r[0] for r in conn.execute("SELECT DISTINCT release_date_source FROM album_info WHERE release_date IS NOT NULL")}
            undated = conn.execute("SELECT COUNT(*) FROM album_info WHERE release_date IS NULL").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(set(kinds), {"genre", "place"})
        self.assertEqual(sources, {"musicbrainz", "tag"})
        self.assertGreater(undated, 0)  # some albums MusicBrainz doesn't know

    def test_it_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "again.db"
            synthetic.build_demo_db(other)
            a, b = db.connect(self.path), db.connect(other)
            try:
                q = "SELECT release_date FROM album_info ORDER BY album_id"
                self.assertEqual(a.execute(q).fetchall(), b.execute(q).fetchall())
            finally:
                a.close()
                b.close()

    def test_it_never_touches_an_existing_file(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            synthetic.build_demo_db(self.path)
        result = subprocess.run([sys.executable, "-m", "tests.synthetic", "--demo-db", str(self.path)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_csv_output_is_unchanged_without_the_flag(self):
        result = subprocess.run([sys.executable, "-m", "tests.synthetic"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("﻿"))


if __name__ == "__main__":
    unittest.main()
