"""Invariants that future features (a live last.fm updater, new-release following) rely on:
locks, races between fetch / merge / import, views between ingest and rebuild, cache versions,
local time, and merges covering every table that points at an artist, track or album."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from mtc import config, db, derive, enrich, ingest, insights, maintenance, rhythms
from mtc.api import create_app
from mtc.ingest import Scrobble
from mtc.lastfm import LastFm
from tests import synthetic
from tests.test_enrich import FakeClock, lastfm_transport

T0 = 1_700_000_000 - 1_700_000_000 % 60


def utc(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "r.db"
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def artist_id(self, name):
        return self.conn.execute("SELECT id FROM artists WHERE name = ?", (name,)).fetchone()[0]


class ConcurrencyTests(Base):
    def test_ingest_does_not_hold_the_lock_while_records_are_produced(self):
        other = sqlite3.connect(self.path, timeout=0.2)
        writes = []

        def paged():  # like an updater paging user.getRecentTracks
            for page in range(3):
                # another writer (enrich, a merge) must get through between pages
                with other:
                    other.execute("INSERT INTO meta(key, value) VALUES (?, 'x')", (f"page{page}",))
                writes.append(page)
                yield from (Scrobble("A", f"t{page}-{i}", T0 + page * 3600 + i * 60) for i in range(5))
        r = ingest.ingest_records(self.conn, paged(), source="lastfm-api")
        other.close()
        self.assertEqual((writes, r["rows_added"]), ([0, 1, 2], 15))

    def test_merge_during_a_fetch_request_is_reported_not_fatal(self):
        ingest.ingest_records(self.conn, [Scrobble("Kärpäset", "x", T0), Scrobble("Karpaset", "y", T0 + 60)], source="t")
        derive.rebuild(self.conn)
        typo, good = self.artist_id("Karpaset"), self.artist_id("Kärpäset")
        inner = lastfm_transport()

        def transport(url, headers, timeout):
            if "Karpaset" in url and self.conn.execute("SELECT 1 FROM artists WHERE id = ?", (typo,)).fetchone():
                maintenance.merge_artists(self.conn, typo, good)  # the user merges while the request is in flight
            return inner(url, headers, timeout)
        lf = LastFm("k", transport, min_interval=0, sleep=FakeClock().sleep)
        self.assertEqual(enrich.enrich_artist(self.conn, lf, typo, "Karpaset"), ("merged", 0))
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        # and a whole run carries on past it
        summary = enrich.run(self.conn, lastfm=LastFm("k", lastfm_transport(), min_interval=0), albums=0, releases=0,
                             log=lambda *_: None)
        self.assertNotIn("error", summary["artists"])

    def test_upload_runs_off_the_event_loop_and_bumps_the_data_version(self):
        with TestClient(create_app(self.path)) as client:
            v0 = client.get("/api/overview").headers["x-data-version"]
            body = synthetic.to_csv(synthetic.generate(days=60)).encode()
            r = client.post("/api/import", content=body, headers={"x-filename": "a.csv"})
            self.assertGreater(r.json()["rows_added"], 0)
            v1 = client.get("/api/overview").headers["x-data-version"]
            self.assertNotEqual(v0, v1)
            self.assertEqual(client.get("/api/version").json(), {"version": v1})  # the UI checks this per page
            client.post("/api/import", content=body, headers={"x-filename": "a.csv"})  # nothing new
            # a re-import still rebuilds nothing new, but the version only moves when data moved
            self.assertEqual(client.get("/api/overview").headers["x-data-version"], v1)
            # a failed merge neither rebuilds nor changes the version
            self.assertEqual(client.post("/api/maintenance/merge", json={"source_ids": [999], "target_id": 1}).status_code, 404)
            self.assertEqual(client.get("/api/overview").headers["x-data-version"], v1)

    def test_version_header_is_read_before_the_handler_so_a_racing_write_cannot_make_old_data_look_new(self):
        real = insights.overview

        def overview_then_write(c):
            out = real(c)
            with self.conn:  # a write commits after the body was computed, before the response leaves
                db.bump(self.conn, "scrobbles_version")
            return out
        with TestClient(create_app(self.path)) as client:
            v0 = client.get("/api/overview").headers["x-data-version"]
            with mock.patch.object(insights, "overview", overview_then_write):
                stale = client.get("/api/overview").headers["x-data-version"]
            self.assertEqual(stale, v0)  # the old version, so the UI refetches instead of caching old data as new
            self.assertNotEqual(client.get("/api/overview").headers["x-data-version"], v0)

    def test_merge_reports_its_own_error_when_the_rebuild_also_fails(self):
        ingest.ingest_records(self.conn, [Scrobble("A", "x", T0), Scrobble("B", "y", T0 + 60)], source="t")
        derive.rebuild(self.conn)
        a, b = self.artist_id("A"), self.artist_id("B")
        calls = []

        def merge(c, source, target, rebuild=True):
            calls.append(source)
            if len(calls) == 2:
                raise RuntimeError("merge broke")
            return {"source_id": source}
        with TestClient(create_app(self.path)) as client, \
                mock.patch.object(maintenance, "merge_artists", merge), \
                mock.patch.object(derive, "rebuild", side_effect=ValueError("rebuild broke")):
            with self.assertRaisesRegex(RuntimeError, "merge broke"):  # not the rebuild's ValueError
                client.post("/api/maintenance/merge", json={"source_ids": [a, b], "target_id": 999})
        self.assertEqual(calls, [a, b])


class ConsistencyTests(Base):
    def test_views_work_between_ingest_and_rebuild(self):
        ingest.ingest_records(self.conn, [Scrobble("A", "t", T0 + i * 3600) for i in range(30)], source="t")
        derive.rebuild(self.conn)
        # new artist ingested, derived tables not rebuilt yet (or the rebuild failed)
        ingest.ingest_records(self.conn, [Scrobble("New", "n", T0 + 200_000 + i * 600) for i in range(40)], source="t")
        eras = insights.eras(self.conn)
        self.assertEqual(sum(e["plays"] for e in eras), 70)
        timeline = insights.timeline(self.conn)
        self.assertEqual(sum(m["plays"] for m in timeline), 70)  # plays of artists without stats still count
        insights.overview(self.conn)
        insights.summary(self.conn)
        rhythms.seasonal_artists(self.conn)

    def test_merges_cover_every_table_that_references_artists_tracks_or_albums(self):
        # Adding a table with a foreign key to artists/tracks/albums (e.g. for release following)
        # must come with merge support: update maintenance.MERGE_HANDLES and merge_artists.
        refs = set()
        for (table,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for fk in self.conn.execute(f"PRAGMA foreign_key_list({table})"):
                if fk["table"] in ("artists", "tracks", "albums"):
                    refs.add((table, fk["from"], fk["table"]))
        self.assertEqual(refs, maintenance.MERGE_HANDLES)

    def test_local_days_across_dst_and_midnight(self):
        if config.TZ.key != "Europe/Helsinki":
            self.skipTest("expects MTC_TZ=Europe/Helsinki")
        # EET -> EEST at 01:00 UTC on 30 March 2025
        self.assertEqual(ingest.local_parts(utc(2025, 3, 30, 0, 30)), ("2025-03-30", 2, 6))
        self.assertEqual(ingest.local_parts(utc(2025, 3, 30, 1, 30)), ("2025-03-30", 4, 6))
        # 22:30 UTC on New Year's Eve is already next year locally
        self.assertEqual(ingest.local_parts(utc(2025, 12, 31, 22, 30)), ("2026-01-01", 0, 3))
        ingest.ingest_records(self.conn, [Scrobble("A", "t", utc(2025, 12, 31, 22, 30))], source="t")
        derive.rebuild(self.conn)
        ov = insights.overview(self.conn)
        self.assertEqual((ov["first_day"], ov["last_day"]), ("2026-01-01", "2026-01-01"))


class CacheVersionTests(Base):
    def test_rhythms_follow_lookups_and_survive_unrelated_tag_writes(self):
        ingest.import_csv_text(self.conn, synthetic.to_csv(synthetic.generate(days=800)), label="t", encoding="utf-8")
        place = rhythms.overview(self.conn, "place")
        self.assertEqual(place["covered"], 0)
        # last.fm lookups (found or not) change what Places compare against
        lf = LastFm("k", lastfm_transport(), min_interval=0)
        enrich.run(self.conn, lastfm=lf, albums=0, releases=0, log=lambda *_: None)
        self.assertGreater(rhythms.overview(self.conn, "place")["covered"], 0)
        # scrobble-only results are not recomputed by a tag write
        rhythms.seasonal_artists(self.conn)
        key = (self.conn.execute("PRAGMA database_list").fetchone()[2], "seasonal_artists")  # cache is per database
        before = rhythms._cache[key]
        aid = self.conn.execute("SELECT id FROM artists LIMIT 1").fetchone()[0]
        enrich.enrich_artist(self.conn, lf, aid, self.conn.execute("SELECT name FROM artists WHERE id = ?", (aid,)).fetchone()[0])
        rhythms.seasonal_artists(self.conn)
        self.assertIs(rhythms._cache[key], before)
        # a new import invalidates it
        ingest.import_csv_text(self.conn, synthetic.to_csv([("Late Band", "", "x", T0 + 10**8)]), label="u", encoding="utf-8")
        rhythms.seasonal_artists(self.conn)
        self.assertIsNot(rhythms._cache[key], before)


if __name__ == "__main__":
    unittest.main()
