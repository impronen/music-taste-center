"""The startup scrobble updater: what it asks last.fm for, the 3-a-day gate, and failing safely.
All HTTP goes through a fake user.getRecentTracks; nothing touches the network."""
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from mtc import db, derive, ingest, settings, updater
from mtc.__main__ import main
from mtc.api import create_app
from mtc.ingest import Scrobble
from mtc.lastfm import LastFm

T0 = 1_700_000_000 - 1_700_000_000 % 60
DAY = 86400


def track(ts, artist, title, album="", mbid=""):
    return {"name": title, "mbid": mbid, "artist": {"#text": artist, "mbid": ""}, "album": {"#text": album, "mbid": ""},
            "date": {"uts": str(ts), "#text": "x"}}


def fake_recent(scrobbles, queries=None, fail_page=None, now_playing=True, bare=False):
    """scrobbles: [(ts, artist, title)]. Answers like user.getRecentTracks: newest first, paged,
    `from` honoured, a dateless "now playing" entry first on page 1."""
    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert q["method"] == "user.getRecentTracks" and headers["User-Agent"].startswith("TasteCenter/")
        if queries is not None:
            queries.append(q)
        page, limit = int(q["page"]), int(q["limit"])
        if bare:
            return 200, b"{}"
        if fail_page == page:
            return 500, b""
        rows = sorted((s for s in scrobbles if s[0] > int(q.get("from", 0))), reverse=True)
        pages = -(-len(rows) // limit)
        items = [track(ts, a, t) for ts, a, t in rows[(page - 1) * limit: page * limit]]
        if now_playing and page == 1:
            items.insert(0, {"name": "Playing now", "artist": {"#text": "Live", "mbid": ""}, "@attr": {"nowplaying": "true"}})
        body = {"recenttracks": {"track": items[0] if len(items) == 1 else items,
                                 "@attr": {"page": str(page), "totalPages": str(pages), "total": str(len(rows))}}}
        return 200, json.dumps(body).encode()
    return transport


def client(scrobbles, **kw):
    return LastFm("k", fake_recent(scrobbles, **kw), min_interval=0, sleep=lambda s: None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "u.db"
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def seed(self, n=3):
        ingest.ingest_records(self.conn, [Scrobble("Old", f"o{i}", T0 + i * 3600) for i in range(n)], source="csv")
        derive.rebuild(self.conn)


class FetchTests(Base):
    def test_page_parsing_skips_now_playing_and_handles_a_single_track(self):
        lf = client([(T0, "Cher", "Believe")])
        recs, pages = lf.recent_tracks_page("me", None, 1)  # one track comes back as a bare object
        self.assertEqual((recs, pages), ([Scrobble("Cher", "Believe", T0)], 1))
        self.assertEqual(client([]).recent_tracks_page("me", None, 1)[1], 0)

    def test_asks_for_everything_after_the_newest_scrobble_minus_a_day_and_adds_only_new_ones(self):
        self.seed(3)  # newest stored: T0 + 2h
        newest = T0 + 2 * 3600
        queries = []
        upstream = [(T0 + i * 3600, "Old", f"o{i}") for i in range(3)]  # already stored (a day of overlap)
        upstream += [(newest + 60 * i, "New", f"n{i}") for i in range(1, 451)]  # 450 new: three pages of 200
        r = updater.run_once(self.conn, client(upstream, queries=queries), "me", now=T0 + 3 * DAY)
        self.assertEqual(r["state"], "done")
        self.assertEqual({q["from"] for q in queries}, {str(newest - updater.OVERLAP_S)})
        self.assertEqual([q["page"] for q in queries], ["1", "2", "3"])
        self.assertEqual((r["read"], r["added"]), (453, 450))  # the overlap is read again and ignored
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0], 453)
        self.assertEqual(self.conn.execute("SELECT source FROM imports ORDER BY id DESC LIMIT 1").fetchone()[0], "lastfm-api")
        self.assertEqual(self.conn.execute("SELECT plays FROM artist_stats st JOIN artists a ON a.id = st.artist_id"
                                           " WHERE a.name = 'New'").fetchone()[0], 450)  # derived tables rebuilt

    def test_an_empty_library_fetches_the_whole_history(self):
        queries = []
        r = updater.run_once(self.conn, client([(T0 + i * 60, "A", f"t{i}") for i in range(5)], queries=queries), "me", now=T0)
        self.assertNotIn("from", queries[0])
        self.assertEqual((r["state"], r["added"]), ("done", 5))

    def test_a_scrobble_dated_in_the_future_does_not_move_the_starting_point_past_now(self):
        ingest.ingest_records(self.conn, [Scrobble("Odd", "future", T0 + 400 * DAY)], source="csv")
        queries = []
        updater.run_once(self.conn, client([(T0, "A", "t")], queries=queries), "me", now=T0 + DAY)
        self.assertEqual(queries[0]["from"], str(T0))

    def test_a_failure_half_way_ingests_nothing_and_the_next_run_starts_from_the_same_point(self):
        self.seed(1)
        upstream = [(T0 + 3600 + i * 60, "New", f"n{i}") for i in range(300)]  # two pages
        r = updater.run_once(self.conn, client(upstream, fail_page=2), "me", now=T0 + DAY)
        self.assertEqual(r["state"], "failed")
        self.assertIn("gave up", r["error"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0], 1)  # not the newest 200 alone
        again = updater.run_once(self.conn, client(upstream), "me", now=T0 + DAY + 60)
        self.assertEqual((again["state"], again["added"]), ("done", 300))
        self.assertEqual(again["since"], r["since"])


class RobustnessTests(Base):
    def test_an_answer_without_a_recenttracks_block_is_a_failure_not_nothing_new(self):
        self.seed(1)
        r = updater.run_once(self.conn, client([], bare=True), "me", now=T0 + DAY)
        self.assertEqual(r["state"], "failed")
        self.assertIn("recenttracks", r["error"])

    def test_nothing_new_writes_no_import_row(self):
        self.seed(1)
        before = self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        r = updater.run_once(self.conn, client([]), "me", now=T0 + DAY)
        self.assertEqual((r["state"], r["read"], r["added"]), ("done", 0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0], before)

    def test_a_run_repairs_derived_tables_left_stale_by_an_earlier_run_that_died_in_rebuild(self):
        self.seed(2)
        ingest.ingest_records(self.conn, [Scrobble("Late", "x", T0 + 9 * 3600)], source="lastfm-api")  # stored, never rebuilt
        self.assertIsNone(self.conn.execute("SELECT session_id FROM scrobbles ORDER BY ts DESC").fetchone()[0])
        r = updater.run_once(self.conn, client([]), "me", now=T0 + DAY)  # nothing new to add
        self.assertEqual(r["added"], 0)
        self.assertIsNotNone(self.conn.execute("SELECT session_id FROM scrobbles ORDER BY ts DESC").fetchone()[0])

    def test_a_stopped_run_does_not_use_up_one_of_the_days_runs(self):
        self.seed(1)
        stop = threading.Event()
        stop.set()
        self.assertEqual(updater.run_once(self.conn, client([]), "me", now=T0 + DAY, stop=stop)["state"], "stopped")
        self.assertEqual(updater.status(self.conn, T0 + DAY)["runs_in_window"], 0)

    def test_the_cli_and_the_server_cannot_both_slip_under_the_limit(self):
        self.seed(1)
        states = []

        def attempt():
            conn = db.connect(self.path)
            try:
                states.append(updater.run_once(conn, client([]), "me", now=T0 + DAY)["state"])
            finally:
                conn.close()
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual((states.count("done"), states.count("skipped")), (3, 5))
        self.assertEqual(updater.status(self.conn, T0 + DAY)["runs_in_window"], 3)

    def test_serve_without_a_subcommand_starts(self):
        with mock.patch("uvicorn.run") as run:
            self.assertEqual(main(["--db", str(self.path)]), 0)
        self.assertEqual(run.call_args.kwargs["port"], 8765)


class GateTests(Base):
    def test_three_runs_in_24_hours_then_wait_for_the_oldest_to_age_out(self):
        self.seed(1)
        lf = client([])
        for i in range(3):
            self.assertEqual(updater.run_once(self.conn, lf, "me", now=T0 + i * 3600)["state"], "done")
        skipped = updater.run_once(self.conn, lf, "me", now=T0 + 3 * 3600)
        self.assertEqual(skipped, {"state": "skipped", "reason": "limit", "next_at": T0 + DAY})
        self.assertFalse(updater.status(self.conn, T0 + DAY - 1)["due"])
        self.assertTrue(updater.status(self.conn, T0 + DAY + 1)["due"])  # the first run left the window
        self.assertEqual(updater.run_once(self.conn, lf, "me", now=T0 + DAY + 1)["state"], "done")
        # --force ignores the limit
        self.assertEqual(updater.run_once(self.conn, lf, "me", now=T0 + DAY + 2, force=True)["state"], "done")

    def test_failed_runs_count_too(self):
        self.seed(1)
        for i in range(3):
            updater.run_once(self.conn, client([(T0 + 9999, "X", "y")], fail_page=1), "me", now=T0 + i)
        self.assertEqual(updater.run_once(self.conn, client([]), "me", now=T0 + 10)["state"], "skipped")
        self.assertEqual(updater.status(self.conn, T0 + 10)["last"]["state"], "failed")


class StartupTests(Base):
    def setUp(self):
        super().setUp()
        self.seed(1)
        self.user = mock.patch.object(settings, "lastfm_username", return_value="me")
        self.user.start()
        self.addCleanup(self.user.stop)
        self.calls = []
        self.upstream = [(T0 + 7200 + i * 60, "New", f"n{i}") for i in range(4)]

    def factory(self):
        return lambda: client(self.upstream, queries=self.calls)

    def test_runs_once_at_startup_then_the_gate_holds_for_a_restart_spree(self):
        with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
            c.app.state.updater.wait(10)
            s = c.get("/api/updater").json()
        self.assertEqual((s["last"]["state"], s["last"]["added"], s["runs_in_window"]), ("done", 4, 1))
        for _ in range(2):
            with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
                c.app.state.updater.wait(10)
        with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
            c.app.state.updater.wait(10)
            s = c.get("/api/updater").json()
        self.assertEqual((s["due"], s["runs_in_window"], s["skipped"]), (False, 3, "3 runs in the last 24 hours"))
        self.assertEqual(len(self.calls), 3)  # one page per run, and no fourth request

    def test_off_by_default_so_tests_and_tools_never_reach_the_network(self):
        with TestClient(create_app(self.path, lastfm_factory=self.factory())) as c:
            self.assertEqual(c.get("/api/updater").json()["runs_in_window"], 0)
        self.assertEqual(self.calls, [])

    def test_a_broken_settings_file_does_not_stop_the_server_starting(self):
        with mock.patch.object(settings, "lastfm_username", side_effect=OSError("unreadable")):
            with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
                self.assertEqual(c.get("/api/updater").status_code, 200)

    def test_without_a_username_it_says_why_and_does_not_count_a_run(self):
        with mock.patch.object(settings, "lastfm_username", return_value=None):
            with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
                s = c.get("/api/updater").json()
        self.assertIn("username", s["skipped"])
        self.assertEqual((s["runs_in_window"], self.calls), (0, []))


if __name__ == "__main__":
    unittest.main()
