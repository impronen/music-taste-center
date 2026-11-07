"""The Wrapped tab's API: which years exist, running the pipeline in a job, serving what it made safely."""
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import wrapped_render
from mtc.api import create_app
from tests import synthetic
from tests.test_wrapped_art import png


def wait(client, limit=60):
    end = time.time() + limit
    while time.time() < end:
        job = client.get("/api/wrapped/job").json()
        if job["state"] not in ("running", "stopping"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class WrappedWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.requested = []

        def transport(url, headers, timeout):
            cls.requested.append(url)
            return 200, png()
        cls.client = TestClient(create_app(Path(cls.tmp.name) / "w.db", cover_transport=transport))
        body = synthetic.to_csv(synthetic.generate(days=800)).encode("cp1252", errors="replace")
        cls.client.post("/api/import", content=body, headers={"x-filename": "export.csv"})
        cls.years = cls.client.get("/api/wrapped").json()["years"]

    @classmethod
    def tearDownClass(cls):
        cls.client.app.state.wrapped_job.shutdown()
        cls.tmp.cleanup()

    def test_lists_every_year_with_plays_newest_first(self):
        years = [y["year"] for y in self.years]
        self.assertEqual(years, sorted(years, reverse=True))
        self.assertGreaterEqual(len(years), 2)
        self.assertTrue(all(y["plays"] > 0 for y in self.years))

    def test_not_made_yet_is_a_404_and_bad_input_is_refused(self):
        c = self.client
        self.assertEqual(c.get("/api/wrapped/1999").status_code, 404)
        self.assertEqual(c.get("/api/wrapped/abc").status_code, 422)
        self.assertEqual(c.post("/api/wrapped/job", json={"year": 12}).status_code, 422)
        self.assertEqual(c.get("/api/wrapped/1999/file/index.html").status_code, 404)

    def test_job_makes_a_year_and_serves_it(self):
        c = self.client
        year = self.years[0]["year"]
        r = c.post("/api/wrapped/job", json={"year": year, "art": True, "png": False})
        self.assertEqual(r.status_code, 202)
        job = wait(c)
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["summary"]["png"], False)
        self.assertTrue(job["summary"]["covers"] is None or job["summary"]["covers"]["albums"] >= 0)
        made = {y["year"]: y for y in c.get("/api/wrapped").json()["years"]}
        self.assertTrue(made[year]["made"])
        detail = c.get(f"/api/wrapped/{year}").json()
        self.assertEqual(detail["totals"]["plays"], made[year]["plays"])
        self.assertTrue(detail["report"])
        page = c.get(detail["report"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("sandbox", page.headers["content-security-policy"])  # the page cannot reach the app's origin
        self.assertEqual(c.get(detail["json"]).status_code, 200)
        for csv in detail["csv"]:
            self.assertEqual(c.get(csv["url"]).status_code, 200)

    def test_only_known_files_are_served(self):
        c = self.client
        year = self.years[0]["year"]
        for bad in ("../../w.db", "..%2f..%2fw.db", "art/manifest.json", "cards/x.png", "x.py", "%2e%2e/w.db"):
            self.assertIn(c.get(f"/api/wrapped/{year}/file/{bad}").status_code, (404, 422), bad)  # never the file

    def test_pngs_fall_back_to_the_page_when_chrome_is_missing(self):
        c = self.client
        year = self.years[-1]["year"]
        from unittest import mock
        with mock.patch.object(wrapped_render, "chrome", return_value=None):
            c.post("/api/wrapped/job", json={"year": year, "art": False, "png": True})
            job = wait(c)
        self.assertEqual(job["state"], "done")
        self.assertIn("Chrome", job["summary"]["png_error"])
        self.assertEqual(c.get(c.get(f"/api/wrapped/{year}").json()["report"]).status_code, 200)

    def test_one_job_at_a_time_and_stop(self):
        c = self.client
        year = self.years[0]["year"]
        from mtc import wrapped_run
        from unittest import mock
        gate = __import__("threading").Event()
        real = wrapped_run.generate

        def slow(*a, **kw):
            gate.wait(5)
            return real(*a, **kw)
        with mock.patch.object(wrapped_run, "generate", slow):
            self.assertEqual(c.post("/api/wrapped/job", json={"year": year, "art": False, "png": False}).status_code, 202)
            self.assertEqual(c.post("/api/wrapped/job", json={"year": year}).status_code, 409)
            self.assertEqual(c.post("/api/wrapped/job/stop").json()["state"], "stopping")
            gate.set()
            wait(c)

    def test_year_without_plays_fails_in_the_job_not_the_server(self):
        c = self.client
        self.assertEqual(c.post("/api/wrapped/job", json={"year": 1950, "art": False, "png": False}).status_code, 202)
        job = wait(c)
        self.assertEqual(job["state"], "failed")
        self.assertIn("1950", job["error"])


if __name__ == "__main__":
    unittest.main()
