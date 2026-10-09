"""The startup scrobble updater: what it asks last.fm for, the daily cap and cooldown gate, and failing safely,
plus the loved tracks it refreshes on each run.
All HTTP goes through a fake user.getRecentTracks / user.getLovedTracks; nothing touches the network."""
import json
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from mtc import config, db, derive, ingest, insights, maintenance, settings, updater
from mtc.__main__ import main
from mtc.api import create_app
from mtc.ingest import Scrobble
from mtc.lastfm import LastFm
from mtc.webapi import ApiError

T0 = 1_700_000_000 - 1_700_000_000 % 60
DAY = 86400
HOUR = 3600
MIDNIGHT = int(datetime(2026, 3, 10, tzinfo=config.TZ).timestamp())  # a local midnight, away from DST changes


def track(ts, artist, title, album="", mbid=""):
    return {"name": title, "mbid": mbid, "artist": {"#text": artist, "mbid": ""}, "album": {"#text": album, "mbid": ""},
            "date": {"uts": str(ts), "#text": "x"}}


def loved_answer(q, loved, loved_queries, loved_fail, bare=False):
    """user.getLovedTracks: newest first, paged; loved is [(ts, artist, title)]."""
    if loved_queries is not None:
        loved_queries.append(q)
    if loved_fail:
        return 500, b""
    if bare:
        return 200, b"{}"
    page, limit = int(q["page"]), int(q["limit"])
    rows = sorted(loved, reverse=True)
    items = [{"name": t, "mbid": "", "date": {"uts": str(ts), "#text": "x"}, "artist": {"name": a, "mbid": ""}}
             for ts, a, t in rows[(page - 1) * limit: page * limit]]
    body = {"lovedtracks": {"track": items[0] if len(items) == 1 else items,
                            "@attr": {"page": str(page), "totalPages": str(-(-len(rows) // limit)), "total": str(len(rows))}}}
    return 200, json.dumps(body).encode()


def fake_recent(scrobbles, queries=None, fail_page=None, now_playing=True, bare=False,
                loved=(), loved_queries=None, loved_fail=False):
    """scrobbles: [(ts, artist, title)]. Answers like user.getRecentTracks: newest first, paged,
    `from` honoured, a dateless "now playing" entry first on page 1. `queries` records only those
    calls; user.getLovedTracks calls go to `loved_queries`."""
    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert headers["User-Agent"].startswith("TasteCenter/")
        if q["method"] == "user.getLovedTracks":
            return loved_answer(q, loved, loved_queries, loved_fail, bare)
        assert q["method"] == "user.getRecentTracks"
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
        again = updater.run_once(self.conn, client(upstream), "me", now=T0 + DAY + updater.COOLDOWN_S)
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
        self.assertEqual(updater.status(self.conn, T0 + DAY)["runs_today"], 0)

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
        self.assertEqual((states.count("done"), states.count("skipped")), (1, 7))  # the cooldown lets one through
        self.assertEqual(updater.status(self.conn, T0 + DAY)["runs_today"], 1)

    def test_serve_without_a_subcommand_starts(self):
        with mock.patch("uvicorn.run") as run:
            self.assertEqual(main(["--db", str(self.path)]), 0)
        self.assertEqual(run.call_args.kwargs["port"], 8765)


class GateTests(Base):
    def run_at(self, now, **kw):
        return updater.run_once(self.conn, client([]), "me", now=now, **kw)

    def test_a_run_too_soon_after_the_last_waits_for_the_cooldown(self):
        self.seed(1)
        self.assertEqual(self.run_at(MIDNIGHT + 9 * HOUR)["state"], "done")
        soon = self.run_at(MIDNIGHT + 9 * HOUR + 60)
        self.assertEqual(soon, {"state": "skipped", "reason": "cooldown", "next_at": MIDNIGHT + 9 * HOUR + updater.COOLDOWN_S})
        self.assertFalse(updater.status(self.conn, MIDNIGHT + 13 * HOUR - 1)["due"])
        self.assertEqual(self.run_at(MIDNIGHT + 13 * HOUR)["state"], "done")

    def test_three_runs_a_calendar_day_then_the_next_day_starts_fresh(self):
        self.seed(1)
        for h in (8, 12, 16):
            self.assertEqual(self.run_at(MIDNIGHT + h * HOUR)["state"], "done")
        skipped = self.run_at(MIDNIGHT + 20 * HOUR)  # cooldown is over, the day's runs are not
        self.assertEqual(skipped, {"state": "skipped", "reason": "limit", "next_at": MIDNIGHT + DAY})
        self.assertEqual(updater.status(self.conn, MIDNIGHT + 20 * HOUR)["runs_today"], 3)
        self.assertEqual(self.run_at(MIDNIGHT + DAY + 60)["state"], "done")  # 16:00 was 8 h before, so no cooldown either

    def test_the_cooldown_holds_across_midnight(self):
        self.seed(1)
        self.assertEqual(self.run_at(MIDNIGHT + 23 * HOUR)["state"], "done")
        s = updater.status(self.conn, MIDNIGHT + DAY + 60)
        self.assertEqual((s["due"], s["runs_today"], s["reason"], s["next_at"]), (False, 0, "cooldown", MIDNIGHT + 27 * HOUR))
        self.assertEqual(self.run_at(MIDNIGHT + 27 * HOUR)["state"], "done")

    def test_yesterdays_runs_do_not_count_today_however_close(self):
        self.seed(1)
        for h in (14, 18, 22):
            self.assertEqual(self.run_at(MIDNIGHT + h * HOUR)["state"], "done")
        # the day's cap is spent, but at 02:00 (4 h after the last run) a new day has begun
        self.assertTrue(updater.status(self.conn, MIDNIGHT + 26 * HOUR)["due"])
        self.assertEqual(updater.status(self.conn, MIDNIGHT + 26 * HOUR)["runs_today"], 0)

    def test_force_ignores_the_cooldown_and_the_limit(self):
        self.seed(1)
        for h in (8, 12, 16):
            self.run_at(MIDNIGHT + h * HOUR)
        self.assertEqual(self.run_at(MIDNIGHT + 16 * HOUR + 1, force=True)["state"], "done")

    def test_failed_runs_count_too(self):
        self.seed(1)
        updater.run_once(self.conn, client([(T0 + 9999, "X", "y")], fail_page=1), "me", now=MIDNIGHT + HOUR)
        self.assertEqual(self.run_at(MIDNIGHT + 2 * HOUR)["state"], "skipped")
        self.assertEqual(updater.status(self.conn, MIDNIGHT + 2 * HOUR)["last"]["state"], "failed")


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

    def test_runs_once_at_startup_then_a_restart_spree_is_held_by_the_cooldown(self):
        with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
            c.app.state.updater.wait(10)
            s = c.get("/api/updater").json()
        self.assertEqual((s["last"]["state"], s["last"]["added"], s["runs_today"]), ("done", 4, 1))
        for _ in range(3):
            with TestClient(create_app(self.path, lastfm_factory=self.factory(), auto_update=True)) as c:
                c.app.state.updater.wait(10)
                s = c.get("/api/updater").json()
        self.assertEqual((s["due"], s["reason"], s["runs_today"]), (False, "cooldown", 1))
        self.assertIn("cooldown", s["skipped"])
        self.assertEqual(len(self.calls), 1)  # one page for the one run, and no further request

    def test_off_by_default_so_tests_and_tools_never_reach_the_network(self):
        with TestClient(create_app(self.path, lastfm_factory=self.factory())) as c:
            self.assertEqual(c.get("/api/updater").json()["runs_today"], 0)
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
        self.assertEqual((s["runs_today"], self.calls), (0, []))


class LovedTests(Base):
    def loved(self):
        return {tuple(r) for r in self.conn.execute("SELECT artist, title, loved_at FROM loved_tracks")}

    def test_page_parsing_handles_a_single_track_and_a_missing_block(self):
        lf = client([], loved=[(T0, "A", "One")])
        self.assertEqual(lf.loved_tracks_page("me", 1), ([("A", "One", T0)], 1, 1, 1))
        with self.assertRaises(ApiError):
            client([], bare=True).loved_tracks_page("me", 1)

    def test_each_run_replaces_the_loved_tracks_and_bumps_the_version_only_on_change(self):
        self.seed(1)
        first = [(T0, "Old", "o0"), (T0 + 60, "Elsewhere", "Never scrobbled")]
        r = updater.run_once(self.conn, client([], loved=first), "me", now=T0 + DAY)
        self.assertEqual((r["state"], r["loved"], r["changed"]), ("done", 2, True))
        self.assertEqual(self.loved(), {("Old", "o0", T0), ("Elsewhere", "Never scrobbled", T0 + 60)})
        version = db.versions(self.conn)
        r = updater.run_once(self.conn, client([], loved=first), "me", now=T0 + DAY, force=True)
        self.assertEqual((r["loved"], r["changed"]), (2, False))
        self.assertEqual(db.versions(self.conn), version)  # nothing changed, caches stay warm
        r = updater.run_once(self.conn, client([], loved=first[:1]), "me", now=T0 + DAY, force=True)  # one unloved
        self.assertEqual((r["loved"], r["changed"]), (1, True))
        self.assertEqual(self.loved(), {("Old", "o0", T0)})
        self.assertGreater(db.versions(self.conn), version)

    def test_pages_through_all_loved_tracks(self):
        loved = [(T0 + i, "A", f"t{i}") for i in range(5)]
        queries = []
        with mock.patch.object(updater, "LOVED_PAGE_SIZE", 2):
            r = updater.run_once(self.conn, client([], loved=loved, loved_queries=queries), "me", now=T0)
        self.assertEqual([q["page"] for q in queries], ["1", "2", "3"])
        self.assertEqual(r["loved"], 5)

    def test_an_answer_with_loved_tracks_missing_keeps_the_old_list(self):
        loved = [(T0 + i, "A", f"t{i}") for i in range(30)]
        updater.run_once(self.conn, client([], loved=loved), "me", now=T0)

        def short(user, page, limit):  # the right totals, but only one entry on the page
            return [("A", "t0", T0)], 1, 1, 30
        lf = client([])
        with mock.patch.object(lf, "loved_tracks_page", short):
            r = updater.run_once(self.conn, lf, "me", now=T0 + DAY)
        self.assertEqual(r["state"], "done")
        self.assertIn("1 of 30", r["loved_error"])
        self.assertEqual(len(self.loved()), 30)

    def test_a_loved_tracks_failure_keeps_the_scrobbles_and_the_old_list(self):
        updater.run_once(self.conn, client([], loved=[(T0, "A", "t")]), "me", now=T0)
        upstream = [(T0 + 60, "New", "n")]
        r = updater.run_once(self.conn, client(upstream, loved_fail=True), "me", now=T0 + DAY)
        self.assertEqual((r["state"], r["added"]), ("done", 1))
        self.assertIn("gave up", r["loved_error"])
        self.assertEqual(self.loved(), {("A", "t", T0)})

    def test_loved_tracks_resolve_by_name_through_spellings_merges_and_later_imports(self):
        ingest.ingest_records(self.conn, [Scrobble("Band", "Song", T0), Scrobble("Band", "Song", T0 + 60),
                                          Scrobble("Bnad", "Other", T0 + 120), Scrobble("Band", "Plain", T0 + 180)],
                              source="csv")
        derive.rebuild(self.conn)
        r = updater.store_loved(self.conn, [("Band", "Song", T0 + 500), ("BAND", "song", T0 + 400),  # one track, twice
                                            ("Band", "Other", T0 + 600), ("Band", "Later", T0 + 700)])
        self.assertEqual(r["loved"], 3)  # spellings that normalize alike count once, earliest date
        band = self.conn.execute("SELECT id FROM artists WHERE name = 'Band'").fetchone()[0]
        song = self.conn.execute("SELECT id FROM tracks WHERE title = 'Song'").fetchone()[0]
        self.assertEqual(insights.track(self.conn, song)["loved_at"], T0 + 400)
        self.assertEqual(insights.artist(self.conn, band)["n_loved"], 1)  # "Other" is still under the misspelling
        maintenance.merge_artists(self.conn, self.conn.execute("SELECT id FROM artists WHERE name = 'Bnad'").fetchone()[0], band)
        self.assertEqual(insights.artist(self.conn, band)["n_loved"], 2)
        ingest.ingest_records(self.conn, [Scrobble("Band", "Later", T0 + 800)], source="csv")  # scrobbled after loving
        derive.rebuild(self.conn)
        detail = insights.artist(self.conn, band)
        self.assertEqual(detail["n_loved"], 3)
        self.assertEqual({t["name"]: t["loved_at"] for t in detail["tracks"]},
                         {"Song": T0 + 400, "Other": T0 + 600, "Later": T0 + 700, "Plain": None})
        unloved = insights.insight_list(self.conn, "unloved")
        self.assertEqual(([t["name"] for t in unloved["items"]], unloved["loved_count"]), (["Plain"], 3))

    def test_loved_then_left_and_unloved_insights(self):
        old, new = T0, T0 + 400 * DAY
        ingest.ingest_records(self.conn, [Scrobble("A", "Gone", old + i * 60) for i in range(3)]
                              + [Scrobble("A", "Still", new + i * 60) for i in range(2)]
                              + [Scrobble("A", "Plain", new + 600)], source="csv")
        derive.rebuild(self.conn)
        self.assertEqual(insights.insights(self.conn)["unloved"], [])  # nothing loved: no card to fill
        updater.store_loved(self.conn, [("A", "Gone", old), ("A", "Still", old)])
        cards = insights.insights(self.conn)
        self.assertEqual([(t["name"], t["plays"]) for t in cards["loved_left"]], [("Gone", 3)])
        self.assertEqual([t["name"] for t in cards["unloved"]], ["Plain"])
        self.assertEqual(cards["loved_count"], 2)

    def test_library_love_rate_per_artist(self):
        old, new = T0, T0 + 400 * DAY
        ingest.ingest_records(self.conn, [Scrobble("A", "Gone", old), Scrobble("A", "Kept", old + 60),
                                          Scrobble("A", "Kept", new), Scrobble("A", "Plain", new + 60),
                                          Scrobble("A", "Other", new + 120)]
                              + [Scrobble("Many", f"m{i}", new + 600 + i * 60) for i in range(6)]  # most plays, no loves
                              + [Scrobble("Few", "f", new + 1200), Scrobble("Few", "g", old + 120)], source="csv")
        derive.rebuild(self.conn)
        updater.store_loved(self.conn, [("A", "Gone", old), ("a", "kept", old), ("A", "Kept", old + 1),  # one track, twice
                                        ("Few", "f", old), ("Nobody", "Never played", old)])

        def rows(**kw):
            r = insights.library(self.conn, "artist", **kw)
            return r["total"], [(x["name"], x["loved"], x["loved_rate"]) for x in r["items"]]
        self.assertEqual(rows(), (3, [("Many", 0, None), ("A", 2, 0.5), ("Few", 1, 0.5)]))
        self.assertEqual(rows(sort="loved"), (3, [("A", 2, 0.5), ("Few", 1, 0.5), ("Many", 0, None)]))
        self.assertEqual(rows(sort="loved", limit=1, offset=1), (3, [("Few", 1, 0.5)]))
        self.assertEqual(rows(sort="loved", q="a"), (2, [("A", 2, 0.5), ("Many", 0, None)]))
        # a period: count and rate cover only the tracks played in it ("Gone" isn't, so A has one)
        day = datetime.fromtimestamp(new, config.TZ).date().isoformat()
        self.assertEqual(rows(start=day, end=day, sort="loved", limit=2),
                         (3, [("A", 1, 1 / 3), ("Few", 1, 1.0)]))
        self.assertEqual(rows(start=day, end=day, sort="loved", offset=2), (3, [("Many", 0, None)]))
        before = datetime.fromtimestamp(old, config.TZ).date().isoformat()  # Few's loved track wasn't played then
        self.assertEqual(rows(start=before, end=before, sort="loved"), (2, [("A", 2, 1.0), ("Few", 0, None)]))
        self.assertEqual(rows(start=before, end=day, sort="loved"), rows(sort="loved"))  # the whole span is all time
        self.assertEqual(rows(sort="tracks")[1][0][0], "Many")  # the other keys still work
        a = self.conn.execute("SELECT id FROM artists WHERE name = 'A'").fetchone()[0]
        detail = insights.artist(self.conn, a)
        self.assertEqual((detail["n_loved"], detail["n_tracks"]), (2, 4))  # what the top-tracks subtitle divides


class LoveTimingTests(Base):
    def test_love_timing_on_the_track_page_and_the_two_cards(self):
        d0 = T0 + 60 * DAY
        recs = [Scrobble("A", "Early", T0)]  # the history starts here
        recs += [Scrobble("A", "Quick", d0 + i * DAY) for i in range(6)]
        recs += [Scrobble("A", "Slow", T0 + 40 * DAY + i * DAY) for i in range(15)]
        recs += [Scrobble("A", "Pre", d0 + 10 * DAY), Scrobble("A", "Just", d0 + 20 * DAY)]
        ingest.ingest_records(self.conn, recs, source="csv")
        derive.rebuild(self.conn)
        updater.store_loved(self.conn, [("A", "Quick", d0 + 120), ("A", "Slow", T0 + 51 * DAY + 60),
                                        ("A", "Early", T0 + 120), ("A", "Pre", d0), ("A", "Just", d0 + 20 * DAY - 3600)])
        ids = dict(self.conn.execute("SELECT title, id FROM tracks"))
        love = {name: insights.track(self.conn, ids[name])["love"] for name in ("Quick", "Slow", "Early", "Pre", "Just")}
        # loved two minutes into the first play: that play was still going, so none were over
        self.assertEqual(love["Quick"], {"plays_before": 0, "after_s": 120, "before_first": False,
                                         "just_before": False, "history_start": False})
        self.assertEqual((love["Slow"]["plays_before"], love["Slow"]["after_s"]), (11, 11 * DAY + 60))
        self.assertTrue(love["Early"]["history_start"])  # its first plays may predate the history
        self.assertTrue(love["Pre"]["before_first"])
        self.assertEqual((love["Just"]["just_before"], love["Just"]["before_first"], love["Just"]["plays_before"]), (True, False, 0))
        cards = insights.insights(self.conn)
        self.assertEqual(sorted(t["name"] for t in cards["instant_love"]), ["Just", "Quick"])  # not Early (history start) nor Pre
        self.assertEqual([(t["name"], t["plays_before"]) for t in cards["slow_burners"]], [("Slow", 11)])
        self.assertEqual({t["plays_before"] for t in cards["instant_love"]}, {0})  # the card's SQL agrees with track()

    def test_a_love_from_before_the_history_has_no_timing(self):
        ingest.ingest_records(self.conn, [Scrobble("A", "Song", T0)], source="csv")
        derive.rebuild(self.conn)
        updater.store_loved(self.conn, [("A", "Song", T0 - DAY)])
        song = self.conn.execute("SELECT id FROM tracks").fetchone()[0]
        detail = insights.track(self.conn, song)
        self.assertEqual((detail["loved_at"], detail["love"]), (T0 - DAY, None))


if __name__ == "__main__":
    unittest.main()
