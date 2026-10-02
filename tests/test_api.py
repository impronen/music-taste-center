import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc.api import create_app
from tests import synthetic


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.client = TestClient(create_app(Path(cls.tmp.name) / "api.db"))
        body = synthetic.to_csv(synthetic.generate(days=300)).encode("cp1252", errors="replace")
        cls.upload = cls.client.post("/api/import", content=body, headers={"x-filename": "../x/export.csv"})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_upload_decodes_cp1252(self):
        r = self.upload.json()
        self.assertEqual(r["encoding"], "cp1252")
        self.assertEqual(r["label"], "export.csv")  # path components stripped
        self.assertGreater(r["rows_added"], 1000)
        names = [a["name"] for a in self.client.get("/api/artists", params={"limit": 500}).json()["items"]]
        self.assertIn("Kärpäset", names)

    def test_endpoints(self):
        for url in ["/api/overview", "/api/timeline", "/api/top/artist", "/api/top/track?start=2019-03-01&end=2019-06-30",
                    "/api/top/album", "/api/clock", "/api/eras", "/api/insights", "/api/graph?n=20", "/api/imports",
                    "/api/recent", "/api/search?q=k%C3%A4r", "/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        a = self.client.get("/api/top/artist?limit=1").json()[0]
        self.assertEqual(self.client.get(f"/api/artists/{a['id']}").json()["name"], a["name"])

    def test_validation(self):
        self.assertEqual(self.client.get("/api/top/genre").status_code, 404)
        self.assertEqual(self.client.get("/api/top/artist?start=2019-1-1").status_code, 422)
        self.assertEqual(self.client.get("/api/artists/999999").status_code, 404)
        self.assertEqual(self.client.post("/api/import", content=b"").status_code, 400)

    def test_like_wildcards_are_literal(self):
        self.assertEqual(self.client.get("/api/artists", params={"q": "%"}).json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
