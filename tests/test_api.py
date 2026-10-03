import tempfile
import unittest
from datetime import date, timedelta
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

    def test_period_summary_and_activity(self):
        c = self.client
        everything = c.get("/api/summary").json()
        self.assertEqual(everything["plays"], c.get("/api/overview").json()["plays"])
        self.assertIsNone(everything["previous"])
        end = everything["end"]
        start = (date.fromisoformat(end) - timedelta(days=29)).isoformat()
        month = c.get("/api/summary", params={"start": start, "end": end}).json()
        top = c.get("/api/top/artist", params={"start": start, "end": end, "limit": 500}).json()
        self.assertEqual(month["plays"], sum(a["plays"] for a in top))
        self.assertEqual((month["calendar_days"], month["artists"]), (30, len(top)))
        self.assertLessEqual(month["longest_streak"], month["listening_days"])
        prev = month["previous"]
        self.assertEqual((prev["start"], prev["end"]),
                         ((date.fromisoformat(start) - timedelta(days=30)).isoformat(),
                          (date.fromisoformat(start) - timedelta(days=1)).isoformat()))
        self.assertEqual(prev["plays"], c.get("/api/summary", params={"start": prev["start"], "end": prev["end"]}).json()["plays"])
        # whole months compare with the same calendar months before them
        from mtc.insights import _previous
        self.assertEqual(_previous("2024-01-01", "2024-12-31"), ("2023-01-01", "2023-12-31"))
        self.assertEqual(_previous("2024-03-01", "2024-03-31"), ("2024-02-01", "2024-02-29"))
        self.assertEqual(_previous("2024-01-01", "2024-02-29"), ("2023-11-01", "2023-12-31"))
        self.assertEqual(_previous("2024-03-10", "2024-03-16"), ("2024-03-03", "2024-03-09"))
        # discoveries: first heard in the range, ranked by plays in it
        first = everything["start"]
        early = c.get("/api/summary", params={"start": first, "end": (date.fromisoformat(first) + timedelta(days=89)).isoformat()}).json()
        self.assertTrue(all(d["plays"] > 0 for d in early["discoveries"]))
        # activity: zero-filled days up to 120 days, months beyond
        act = c.get("/api/activity", params={"start": start, "end": end}).json()
        self.assertEqual((act["unit"], len(act["items"])), ("day", 30))
        self.assertEqual(sum(d["plays"] for d in act["items"]), month["plays"])
        self.assertEqual(c.get("/api/activity").json()["unit"], "month")
        # ranges outside the data are empty, not errors
        future = c.get("/api/summary", params={"start": "2099-01-01", "end": "2099-01-31"}).json()
        self.assertEqual(future["plays"], 0)
        self.assertEqual(c.get("/api/activity", params={"start": "2099-01-01"}).json()["items"], [])
        self.assertEqual(c.get("/api/summary", params={"start": "2024-1-1"}).status_code, 422)

    def test_library_table(self):
        c = self.client
        everything = c.get("/api/library").json()
        self.assertEqual(everything["total"], c.get("/api/artists", params={"limit": 1}).json()["total"])
        self.assertEqual(everything["items"][0]["plays"], max(a["plays"] for a in everything["items"]))
        end = c.get("/api/summary").json()["end"]
        start = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
        for kind in ("artist", "track", "album"):
            r = c.get("/api/library", params={"kind": kind, "start": start, "end": end, "limit": 500}).json()
            top = c.get(f"/api/top/{kind}", params={"start": start, "end": end, "limit": 500}).json()
            self.assertEqual((r["total"], sum(x["plays"] for x in r["items"])), (len(top), sum(x["plays"] for x in top)), kind)
        # filtering keeps working for any period; tracks also match their artist's name
        q = everything["items"][0]["name"][:4]
        filtered = c.get("/api/library", params={"kind": "track", "start": start, "end": end, "q": q}).json()
        self.assertTrue(filtered["total"] and all(q.casefold() in (t["name"] + t["artist"]).casefold() for t in filtered["items"]))
        names = [a["name"] for a in c.get("/api/library", params={"sort": "name", "limit": 500}).json()["items"]]
        self.assertEqual(names, sorted(names, key=str.casefold))
        page2 = c.get("/api/library", params={"limit": 5, "offset": 5}).json()["items"]
        self.assertEqual([a["id"] for a in page2], [a["id"] for a in everything["items"][5:10]])
        # all time is served from artist_stats; it must agree with aggregating the scrobbles
        lo = c.get("/api/summary").json()["start"]
        fast = c.get("/api/library", params={"limit": 500}).json()["items"]
        slow = c.get("/api/library", params={"start": lo, "end": end, "limit": 500}).json()["items"]
        self.assertEqual({a["id"]: (a["plays"], a["tracks"]) for a in fast}, {a["id"]: (a["plays"], a["tracks"]) for a in slow})
        # one artist exactly (not a name match), and the artist's name comes back for the chip
        a = everything["items"][0]
        one = c.get("/api/library", params={"kind": "track", "artist": a["id"], "limit": 500}).json()
        self.assertEqual(one["artist"]["name"], a["name"])
        self.assertTrue(one["total"] and all(t["artist_id"] == a["id"] for t in one["items"]))
        self.assertEqual(sum(t["plays"] for t in one["items"]), a["plays"])
        past = c.get("/api/library", params={"offset": 10_000}).json()
        self.assertEqual((past["items"], past["total"]), ([], everything["total"]))
        self.assertEqual(c.get("/api/library", params={"kind": "genre"}).json()["total"], 0)  # no tags here
        self.assertEqual(c.get("/api/library", params={"kind": "user"}).status_code, 422)
        self.assertEqual(c.get("/api/library", params={"sort": "plays; DROP"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
