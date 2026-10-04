"""Artist velocity on hand-made histories whose pace is known: a steady listener, a late bloomer who
plays an artist heavily for a month, and an artist that was already in rotation when tracking began."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, ingest, velocity
from mtc.api import create_app
from tests import synthetic

DAY0 = date(2020, 1, 1)


def ts(day: date, k: int = 0) -> int:
    """Noon UTC plus k * 5 minutes: the same local day in every time zone for k < 24."""
    return int(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp()) + k * 300


def history() -> list[tuple]:
    rows = []
    for d in range(300):                                   # Steady: one play a day, 300 days
        rows.append(("Steady", "S", f"s{d}", ts(DAY0 + timedelta(days=d))))
    for d in range(30):                                    # Late Bloomer: 14 plays a day for 30 days from day 200
        for k in range(14):
            rows.append(("Late Bloomer", "L", f"l{d}-{k}", ts(DAY0 + timedelta(days=200 + d), k)))
    for d in range(4):                                     # Early: played in the first days (already in rotation)
        for k in range(3):
            rows.append(("Early", "E", f"e{d}-{k}", ts(DAY0 + timedelta(days=d), k)))
    return rows


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "v.db"
        cls.conn = db.connect(cls.path)
        ingest.import_csv_text(cls.conn, synthetic.to_csv(history(), now_playing=False), label="t", encoding="utf-8")
        cls.id = {n: i for i, n in cls.conn.execute("SELECT id, name FROM artists")}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def get(self, *names):
        return velocity.velocity(self.conn, [self.id[n] for n in names])


class VelocityTests(Base):
    def test_the_curve_is_cumulative_weekly_and_ends_at_the_play_count(self):
        v = self.get("Steady")
        s = v["series"][0]
        self.assertEqual(date.fromisoformat(v["start"]).weekday(), 0)          # weeks start on Monday
        self.assertEqual(len(s["cum"]), v["weeks"])
        self.assertEqual((s["plays"], s["cum"][-1]), (300, 300))
        self.assertEqual(s["cum"], sorted(s["cum"]))                           # never decreases
        self.assertEqual(s["cum"][s["first_week"] - 1] if s["first_week"] else 0, 0)

    def test_the_series_share_one_time_axis_and_each_knows_its_first_week(self):
        v = self.get("Steady", "Late Bloomer")
        steady, late = v["series"]
        self.assertEqual(len(steady["cum"]), len(late["cum"]))
        self.assertEqual(steady["cum"][0] > 0, True)                           # Steady starts in week 0
        self.assertAlmostEqual(late["first_week"], 200 / 7, delta=1)
        self.assertEqual(late["cum"][late["first_week"] - 1], 0)               # nothing before it began
        self.assertEqual(late["cum"][-1], 420)                                 # 30 days x 14 plays

    def test_days_to_the_nth_play(self):
        steady = self.get("Steady")["series"][0]["facts"]["days_to"]
        self.assertEqual(steady, {"100": 99, "500": None, "1000": None})        # one a day: the 100th on day 99
        late = self.get("Late Bloomer")["series"][0]["facts"]["days_to"]
        self.assertEqual(late["100"], 7)                                        # 14 a day: 98 after day 6, 112 after day 7
        self.assertEqual(late["500"], None)

    def test_the_fastest_thirty_days(self):
        late = self.get("Late Bloomer")["series"][0]["facts"]["fastest"]
        self.assertEqual((late["plays"], late["days"]), (420, 30))
        self.assertEqual(late["from"], self.get("Late Bloomer")["series"][0]["first_day"])  # the burst starts with the artist
        steady = self.get("Steady")["series"][0]["facts"]["fastest"]
        self.assertEqual(steady["plays"], 30)

    def test_pace_now_against_lifetime(self):
        late = self.get("Late Bloomer")["series"][0]["facts"]
        self.assertIsNone(late["recent_per_month"])                             # younger than a year: a burst, not a pace
        self.assertGreater(late["lifetime_per_month"], 0)
        steady = self.get("Steady")["series"][0]["facts"]
        self.assertAlmostEqual(steady["lifetime_per_month"], 300 / (300 / velocity.DAYS_PER_MONTH), delta=0.2)

    def test_an_artist_already_in_rotation_when_tracking_began_is_marked(self):
        early, late = self.get("Early")["series"][0], self.get("Late Bloomer")["series"][0]
        self.assertTrue(early["known_before"])
        self.assertEqual(date.fromisoformat(early["known_until"]) - date.fromisoformat(early["first_day"]),
                         timedelta(days=30))
        self.assertFalse(late["known_before"])
        self.assertIsNone(late["known_until"])

    def test_unknown_ids_duplicates_and_the_limit(self):
        steady = self.id["Steady"]
        v = velocity.velocity(self.conn, [steady, steady, 99999, self.id["Early"]])
        self.assertEqual([s["name"] for s in v["series"]], ["Steady", "Early"])
        many = velocity.velocity(self.conn, [steady] + list(range(1000, 1010)))
        self.assertEqual(len(many["series"]), 1)
        self.assertEqual(velocity.velocity(self.conn, [])["series"], [])

    def test_an_empty_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "e.db")
            try:
                self.assertEqual(velocity.velocity(conn, [1]), {"start": None, "end": None, "weeks": 0, "series": []})
            finally:
                conn.close()


class CacheAndApiTests(unittest.TestCase):
    def test_new_scrobbles_refresh_the_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "c.db")
            try:
                ingest.import_csv_text(conn, synthetic.to_csv(history(), now_playing=False), label="a", encoding="utf-8")
                artist = conn.execute("SELECT id FROM artists WHERE name = 'Steady'").fetchone()[0]
                self.assertEqual(velocity.velocity(conn, [artist])["series"][0]["plays"], 300)
                more = [("Steady", "S", f"extra{i}", ts(DAY0 + timedelta(days=100), 20 + i)) for i in range(5)]
                ingest.import_csv_text(conn, synthetic.to_csv(more, now_playing=False), label="b", encoding="utf-8")
                self.assertEqual(velocity.velocity(conn, [artist])["series"][0]["plays"], 305)
            finally:
                conn.close()

    def test_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.db"
            conn = db.connect(path)
            ingest.import_csv_text(conn, synthetic.to_csv(history(), now_playing=False), label="t", encoding="utf-8")
            ids = [r[0] for r in conn.execute("SELECT id FROM artists ORDER BY id")]
            conn.close()
            with TestClient(create_app(path)) as c:
                ok = c.get("/api/velocity", params={"ids": ",".join(map(str, ids))})
                self.assertEqual(ok.status_code, 200)
                self.assertEqual({s["name"] for s in ok.json()["series"]}, {"Steady", "Late Bloomer", "Early"})
                self.assertEqual(c.get("/api/velocity", params={"ids": "999999"}).json()["series"], [])
                for bad in ("", "abc", "1,", "1,,2", "-1", "1;2", ",".join(["1"] * 7)):
                    self.assertEqual(c.get("/api/velocity", params={"ids": bad}).status_code, 422, bad)
                self.assertEqual(c.get("/api/velocity").status_code, 422)


if __name__ == "__main__":
    unittest.main()
