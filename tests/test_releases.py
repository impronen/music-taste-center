"""Upcoming releases: parsing Wikipedia's album table, the refresh over fake ListenBrainz and
Wikipedia transports, and matching releases to library artists (fictional names throughout)."""
import json
import time
import tempfile
import unittest
import urllib.parse
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from mtc import db, derive, ingest, maintenance, releases, updater
from mtc.webapi import Fatal, NotFound
from mtc.api import create_app
from mtc.ingest import Scrobble

TODAY = date(2026, 10, 7)  # a Wednesday: this week's Friday is 9 Oct, the window 3 Oct to 20 Nov
LATEST = 1_790_000_000      # the newest scrobble (early October 2026)
YEAR = 365 * 86400

OCTOBER = """=== October ===
<!--Unsourced additions will be removed-->
{| class="wikitable plainrowheaders"
|+ List of albums to be released in October 2026
| colspan="6" style="text-align:center;"|{{Monthbar|Oct=no|TBA=y}}
|-
! scope="col"| Release date
! scope="col"| Artist
! scope="col"| Album
! scope="col"| Genre
! scope="col"| Label
! scope="col"| {{abbr|Ref.|Reference}}
|-
! scope="row" rowspan="3" style="text-align: center;" | October<br>9
| [[Velvet Orchard (band)|Velvet Orchard]]
| ''[[Night Garden (album)|Night Garden (Deluxe Edition)]]''
| [[Dream pop]]
| [[Fictional Records|Fictional]]
| <ref>{{#invoke:cite | web |url=https://example.com/vo-night-garden |title=Velvet Orchard ''Night Garden'' |work=Example}}</ref>
|-
| [[Harbor Lights]] and [[Moss Choir]]
| ''Tidewater''
|
|
| <ref name="tide" />
|-
| Copper Lantern Band
| ''Embers''
|
| Self-released
|
|-
| Sundial
| ''Shadows''
|
|
|
|-
| [[Sebastian Hill]] and [[Copper Lantern]]
| ''Duets''
|
|
|
|-
| Velvet Orchard
| ''Bloom''
|
|
| <ref>{{cite web |url=https://example.com/bloom |title=t}}</ref>
|-
! scope="row" rowspan="1" style="text-align: center;" | October<br>16
| Nobody Known
| ''First Light''<!-- a comment -->
| [[Folk music|Folk]]
| Self-released
| <ref>{{cite web |url=https://example.com/first-light |title=t}}</ref>
|}
"""


def lb_release(artist, title, day, kind="Album", mbids=(), rg=None, caa=None):
    return {"artist_credit_name": artist, "release_name": title, "release_date": day, "release_group_primary_type": kind,
            "artist_mbids": list(mbids), "release_group_mbid": rg, "release_mbid": None,
            **({"caa_id": 42, "caa_release_mbid": caa} if caa else {})}


LB_RELEASES = [
    lb_release("Velvet Orchard", "Bloom", "2026-10-09", kind="Single", mbids=["mb-vo"]),  # the title single...
    lb_release("Velvet Orchard", "Bloom", "2026-10-30", mbids=["mb-vo"]),                 # ...of a later album
    lb_release("Copper Lantern", "Halfway", "2026-11"),                                   # a partial date
    lb_release("The Velvet Orchard", "Night Garden", "2026-10-09", mbids=["mb-vo"], rg="rg-ng", caa="rel-ng"),
    lb_release("Copper Lantern", "Spark", "2026-10-14", kind="Single"),
    lb_release("Sundial", "Shadows", "2026-10-16", mbids=["mb-sundial-other"]),  # a namesake, not ours
    lb_release("Rare Bird", "Feathers", "2026-10-23"),                           # too few plays to count
    lb_release("Stranger", "Unknown", "2026-10-23", kind="EP"),
    lb_release("Stranger", "Talk", "2026-10-23", kind="Broadcast"),              # not an album, EP or single
    lb_release("Velvet Orchard", "Far Away", "2026-12-30", mbids=["mb-vo"]),     # outside the window
]


def lb_client(releases_=LB_RELEASES, calls=None, status=200):
    def transport(url, headers, timeout):
        if calls is not None:
            calls.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)))
        return status, json.dumps({"payload": {"releases": releases_, "total_count": len(releases_)}}).encode()
    return releases.ListenBrainz(transport, min_interval=0, sleep=lambda s: None)


def wiki_client(months=None, missing=False):
    months = {"October": OCTOBER, "November": "=== November ===\n{|\n|}"} if months is None else months
    names = list(months)

    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if missing or q["page"] != "List of 2026 albums":
            return 200, json.dumps({"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}).encode()
        if q["prop"] == "sections":
            sections = [{"index": "1", "line": "Fourth quarter"}] + [{"index": str(i + 2), "line": n} for i, n in enumerate(names)]
            return 200, json.dumps({"parse": {"sections": sections}}).encode()
        return 200, json.dumps({"parse": {"wikitext": months[names[int(q["section"]) - 2]]}}).encode()
    return releases.Wikipedia(transport, min_interval=0, sleep=lambda s: None)


def library_db(path):
    """Velvet Orchard: 50 plays this year (MusicBrainz id mb-vo) and 2 loved; Copper Lantern: 30
    plays two years ago (alias "Copper Lantern Band"); Sundial: 20 plays (id mb-sundial-ours);
    Harbor Lights: 5 plays; Rare Bird: 2 plays."""
    out = []
    for artist, n, start in (("Velvet Orchard", 50, LATEST - 100 * 86400), ("Copper Lantern", 30, LATEST - 2 * YEAR),
                             ("Sundial", 20, LATEST - 3 * YEAR), ("Harbor Lights", 5, LATEST - 3 * YEAR), ("Rare Bird", 2, LATEST - 3 * YEAR)):
        out += [Scrobble(artist, f"{artist} {i % 5}", start + i * 3600) for i in range(n)]
    out.append(Scrobble("Velvet Orchard", "Velvet Orchard 0", LATEST))
    conn = db.connect(path)
    ingest.ingest_records(conn, out, source="csv")
    derive.rebuild(conn)
    ids = dict(conn.execute("SELECT name, id FROM artists"))
    with conn:
        for name, mbid in (("Velvet Orchard", "mb-vo"), ("Sundial", "mb-sundial-ours")):
            conn.execute("INSERT INTO artist_info(artist_id, status, mbid, fetched_at) VALUES (?, 'ok', ?, 0)", (ids[name], mbid))
    maintenance.add_alias(conn, "Copper Lantern Band", ids["Copper Lantern"])
    updater.store_loved(conn, [("Velvet Orchard", "Velvet Orchard 1", LATEST), ("Velvet Orchard", "Velvet Orchard 2", LATEST)])
    return conn, ids


class ParseTest(unittest.TestCase):
    def test_rows_dates_and_cells(self):
        rows = releases.parse_month(OCTOBER, 2026, 10)
        self.assertEqual([(r["release_date"], r["artist"], r["title"]) for r in rows], [
            ("2026-10-09", "Velvet Orchard", "Night Garden (Deluxe Edition)"),
            ("2026-10-09", "Harbor Lights and Moss Choir", "Tidewater"),
            ("2026-10-09", "Copper Lantern Band", "Embers"),
            ("2026-10-09", "Sundial", "Shadows"),
            ("2026-10-09", "Sebastian Hill and Copper Lantern", "Duets"),
            ("2026-10-09", "Velvet Orchard", "Bloom"),
            ("2026-10-16", "Nobody Known", "First Light"),
        ])
        self.assertEqual((rows[0]["genre"], rows[0]["label"], rows[0]["source_url"]),
                         ("Dream pop", "Fictional", "https://example.com/vo-night-garden"))
        self.assertEqual((rows[1]["genre"], rows[1]["label"], rows[1]["source_url"]), (None, None, None))
        self.assertEqual(rows[1]["artist_parts"], "Harbor Lights\nMoss Choir")
        self.assertEqual((rows[-1]["genre"], rows[-1]["source_url"]), ("Folk", "https://example.com/first-light"))

    def test_impossible_date_and_empty_table(self):
        text = '|-\n! scope="row" | February<br>30\n| Someone\n| \'\'Something\'\'\n|}'
        self.assertEqual(releases.parse_month(text, 2026, 2), [])
        self.assertEqual(releases.parse_month("=== November ===\n{|\n|}", 2026, 11), [])

    def test_harder_table_forms(self):
        text = "\n".join([
            '|-', '! scope="row" rowspan="4" | October&nbsp;23',
            '| rowspan="2" style="x" | [[Alpha Band]]', "| ''First''", '| Pop', '| Indie',
            '| <ref>{{cite web', ' |url=https://example.com/alpha', ' |title=t}}</ref>',
            '|-', "| ''Second''", '| Pop', '| Indie', '|',
            '|-', "| {{sortname|Theta|Band}} || ''Third<ref name=\"a/b\">some text</ref>'' || || Label",
            '|-', "| Belle and Sebastian || ''Fourth''",
            '|-', "| Kappa", "| ''Fifth''<ref>{{cite web", "|url=https://example.com/fifth", "|title=t}}</ref>", "| Rock", "| Label",
            '|-', "| E=MC2 || ''Sixth<ref>{{citation|url=http://x}}</ref>''",
            '|}'])
        rows = releases.parse_month(text, 2026, 10)
        self.assertEqual([(r["release_date"], r["artist"], r["title"]) for r in rows], [
            ("2026-10-23", "Alpha Band", "First"), ("2026-10-23", "Alpha Band", "Second"),
            ("2026-10-23", "Theta Band", "Third"), ("2026-10-23", "Belle and Sebastian", "Fourth"),
            ("2026-10-23", "Kappa", "Fifth"), ("2026-10-23", "E=MC2", "Sixth")])
        self.assertEqual((rows[4]["genre"], rows[4]["label"]), ("Rock", "Label"))
        self.assertEqual(rows[0]["source_url"], "https://example.com/alpha")
        self.assertEqual((rows[2]["genre"], rows[2]["label"]), (None, "Label"))
        self.assertIsNone(rows[3]["artist_parts"])  # no links: never split into "Belle" and "Sebastian"

    def test_weeks(self):
        self.assertEqual(releases.release_friday(date(2026, 10, 9)), date(2026, 10, 9))   # Friday
        self.assertEqual(releases.release_friday(date(2026, 10, 10)), date(2026, 10, 16))  # Saturday starts the next week
        self.assertEqual(releases.window(TODAY), (date(2026, 10, 3), date(2026, 11, 20)))
        self.assertEqual(releases.window(date(2026, 10, 9)), (date(2026, 10, 3), date(2026, 11, 20)))


class UpcomingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.ids = library_db(Path(self.tmp.name) / "r.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def refresh(self, lb=None, wiki=None):
        return releases.refresh(self.conn, lb or lb_client(), wiki or wiki_client(), today=TODAY)

    def test_refresh_stores_the_window(self):
        calls = []
        result = self.refresh(lb_client(calls=calls))
        self.assertEqual(calls, [{"release_date": "2026-10-03", "days": "49", "past": "false", "future": "true"}])
        self.assertEqual(result, {"listenbrainz": {"ok": True, "releases": 7}, "wikipedia": {"ok": True, "releases": 7}})
        stored = {r[0] for r in self.conn.execute("SELECT title FROM upcoming_releases")}
        self.assertNotIn("Far Away", stored)
        self.assertNotIn("Talk", stored)
        self.assertNotIn("Halfway", stored)  # no full date

    def test_matching_ranking_and_weeks(self):
        self.refresh()
        u = releases.upcoming(self.conn, today=TODAY)
        self.assertEqual([w["friday"] for w in u["weeks"]][:3], ["2026-10-09", "2026-10-16", "2026-10-23"])
        self.assertEqual(len(u["weeks"]), releases.WEEKS_AHEAD + 1)
        wk = {w["friday"]: w for w in u["weeks"]}
        first = wk["2026-10-09"]["mine"]
        # by MusicBrainz id (the credit is spelled differently), merged with Wikipedia's row of the same album
        self.assertEqual(first[0]["title"], "Night Garden")
        self.assertEqual(first[0]["sources"], ["listenbrainz", "wikipedia"])
        self.assertEqual((first[0]["source_url"], first[0]["genre"], first[0]["cover_url"]),
                         ("https://example.com/vo-night-garden", "Dream pop", "https://coverartarchive.org/release/rel-ng/42-250.jpg"))
        vo = first[0]["artists"][0]
        self.assertEqual((vo["name"], vo["plays"], vo["recent_plays"], vo["loved"]), ("Velvet Orchard", 51, 51, 2))
        # by an alias rule, and by one name of an "A and B" credit; ranked by fit
        self.assertEqual([r["title"] for r in first], ["Night Garden", "Bloom", "Embers", "Duets", "Tidewater"])
        self.assertEqual(first[4]["artists"][0]["name"], "Harbor Lights")
        self.assertEqual(first[3]["artists"][0]["name"], "Copper Lantern")  # one linked name of the credit
        # the title single and the album stay apart; Wikipedia's row joins the album
        self.assertEqual((first[1]["release_type"], first[1]["sources"]), ("Single", ["listenbrainz"]))
        album = [r for r in wk["2026-10-30"]["mine"] if r["title"] == "Bloom"][0]
        self.assertEqual((album["release_type"], album["sources"], album["source_url"]),
                         ("Album", ["listenbrainz", "wikipedia"], "https://example.com/bloom"))
        self.assertGreater(first[2]["fit"], first[4]["fit"])
        # a single dated Wednesday 14 Oct belongs to the week ending Friday 16 Oct
        self.assertEqual([r["title"] for r in wk["2026-10-16"]["mine"]], ["Spark"])
        # Sundial's MusicBrainz id says it's another Sundial; Rare Bird has too few plays
        self.assertEqual(wk["2026-10-23"]["mine"], [])
        # the other Sundial's album: Wikipedia's row (no ids) joins ListenBrainz's, so it isn't yours either
        self.assertEqual(wk["2026-10-16"]["more"], 0)
        self.assertEqual([r["title"] for r in wk["2026-10-16"]["also"]], ["First Light", "Shadows"])
        self.assertEqual(wk["2026-10-23"]["more"], 2)  # Rare Bird's album and the stranger's EP

    def test_wikipedia_album_is_not_folded_into_its_title_single(self):
        lb = lb_client([lb_release("Velvet Orchard", "Bloom", "2026-10-09", kind="Single", mbids=["mb-vo"])])
        october = "|-\n! scope=\"row\" | October<br>30\n| Velvet Orchard\n| ''Bloom''\n|}"
        releases.refresh(self.conn, lb, wiki_client({"October": october, "November": ""}), today=TODAY)
        wk = {w["friday"]: w for w in releases.upcoming(self.conn, today=TODAY)["weeks"]}
        self.assertEqual([(r["release_type"], r["sources"]) for r in wk["2026-10-09"]["mine"]], [("Single", ["listenbrainz"])])
        self.assertEqual([(r["release_type"], r["sources"]) for r in wk["2026-10-30"]["mine"]], [(None, ["wikipedia"])])

    def test_failing_source_keeps_its_rows(self):
        self.refresh()
        result = self.refresh(lb_client(status=403))
        self.assertFalse(result["listenbrainz"]["ok"])
        u = releases.upcoming(self.conn, today=TODAY)
        self.assertIn("403", u["sources"]["listenbrainz"]["error"])
        self.assertIsNone(u["sources"]["wikipedia"]["error"])
        self.assertEqual(u["weeks"][0]["mine"][0]["title"], "Night Garden")  # still there
        self.refresh()
        self.assertIsNone(releases.upcoming(self.conn, today=TODAY)["sources"]["listenbrainz"]["error"])

    def test_a_source_that_never_worked(self):
        broken = releases.Wikipedia(lambda url, headers, timeout: (200, b'{"parse": {}}'), min_interval=0, sleep=lambda s: None)
        self.refresh(wiki=broken)  # an answer without the expected fields
        u = releases.upcoming(self.conn, today=TODAY)
        self.assertIsNone(u["sources"]["wikipedia"]["at"])
        self.assertTrue(u["sources"]["wikipedia"]["error"])
        self.assertTrue(u["stale"])
        self.assertEqual(u["weeks"][0]["mine"][0]["title"], "Night Garden")  # ListenBrainz's list still shows

    def test_missing_wikipedia_page(self):
        result = self.refresh(wiki=wiki_client(missing=True))
        self.assertEqual(result["wikipedia"], {"ok": True, "releases": 0})

    def test_stale_until_both_sources_fetched(self):
        self.assertTrue(releases.upcoming(self.conn, today=TODAY)["stale"])
        self.refresh()
        self.assertFalse(releases.upcoming(self.conn, today=TODAY)["stale"])

    def test_stale_when_a_new_week_comes_into_view(self):
        self.refresh()
        self.assertFalse(releases.upcoming(self.conn, today=TODAY)["stale"])
        self.assertTrue(releases.upcoming(self.conn, today=date(2026, 10, 10))["stale"])  # Saturday: one more week

    def test_one_refresh_at_a_time(self):
        with releases._refreshing:
            with self.assertRaises(releases.Busy):
                self.refresh()


class FakeLastFm:
    """last.fm's similar artists and tags, counting calls."""
    SIMILAR = {"Velvet Orchard": [("Stranger", None, 0.9), ("Moss Choir", None, 0.5), ("Velvet Orchard", None, 1.0)],
               "Copper Lantern": [("Stranger", None, 0.4)]}
    TAGS = {"Stranger": [("Dream Pop", 100), ("seen live", 80)], "Nobody Known": [("dream pop", 100), ("folk", 60)]}

    def __init__(self, fatal=False):
        self.calls, self.fatal = [], fatal

    def similar_artists(self, artist, limit=250):
        self.calls.append(("similar", artist))
        if self.fatal:
            raise Fatal("last.fm error 10: Invalid API key")
        if artist == "Sundial":
            raise NotFound("no such artist")
        return self.SIMILAR.get(artist, [])

    def artist_tags(self, artist):
        self.calls.append(("tags", artist))
        return self.TAGS.get(artist, [])


class NewToYouTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn, self.ids = library_db(Path(self.tmp.name) / "n.db")
        with self.conn:  # genre tags (library_db already made artist_info rows, so not add_tags)
            for name, genre in (("Velvet Orchard", "dream pop"), ("Copper Lantern", "folk"), ("Sundial", "rock")):
                tag_id = self.conn.execute("INSERT INTO tags(name, kind) VALUES (?, 'genre')", (genre,)).lastrowid
                self.conn.execute("INSERT INTO artist_tags VALUES (?, ?, 100)", (self.ids[name], tag_id))
            db.bump(self.conn, "tags_version")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def refresh(self, lastfm):
        return releases.refresh(self.conn, lb_client(), wiki_client(), today=TODAY, lastfm=lastfm)

    def test_similar_and_genre_fit(self):
        fm = FakeLastFm()
        result = self.refresh(fm)
        self.assertEqual(result["lastfm"]["ok"], True)
        u = releases.upcoming(self.conn, today=TODAY, has_lastfm=True)
        wk = {w["friday"]: w for w in u["weeks"]}
        stranger = wk["2026-10-23"]["new"]
        self.assertEqual([(n["artist"], n["title"]) for n in stranger], [("Stranger", "Unknown")])
        self.assertEqual(stranger[0]["like"], ["Velvet Orchard", "Copper Lantern"])  # the one you play more first
        self.assertEqual(stranger[0]["genres"], ["dream pop"])                      # "seen live" isn't a genre
        self.assertEqual(stranger[0]["similarity"], 1.0)
        self.assertNotIn("artist_mbids", stranger[0])
        self.assertEqual(wk["2026-10-23"]["more"], 1)  # Stranger's EP moved from the count to "new"
        # not similar to anyone, but dream pop fits what you play now: from Wikipedia's list into "new"
        # (folk, played two years ago, barely counts any more)
        first_light = [n for n in wk["2026-10-16"]["new"] if n["title"] == "First Light"]
        self.assertEqual((first_light[0]["like"], first_light[0]["genres"]), ([], ["dream pop", "folk"]))
        self.assertGreaterEqual(first_light[0]["genre_fit"], releases.MIN_GENRE_FIT)
        self.assertNotIn("First Light", [a["title"] for a in wk["2026-10-16"]["also"]])
        # an artist's own name in its similar list is dropped, and a library artist is never "new"
        self.assertFalse(any(n["artist"] == "Velvet Orchard" for w in u["weeks"] for n in w["new"]))
        self.assertEqual((u["lastfm"]["seeds_done"], u["lastfm"]["seeds"]), (5, 5))  # all five; "not found" counts as looked up
        self.assertFalse(u["stale"])

    def test_tag_spellings_match(self):
        with self.conn:  # the library spells it "dream-pop"; last.fm says "Dream Pop" and "dreampop"
            self.conn.execute("UPDATE tags SET name = 'dream-pop' WHERE name = 'dream pop'")
            db.bump(self.conn, "tags_version")
        fm = FakeLastFm()
        fm.TAGS = {**FakeLastFm.TAGS, "Stranger": [("Dream Pop", 100), ("dreampop", 40)]}
        self.refresh(fm)
        wk = {w["friday"]: w for w in releases.upcoming(self.conn, today=TODAY, has_lastfm=True)["weeks"]}
        stranger = wk["2026-10-23"]["new"][0]
        self.assertEqual((stranger["genres"], stranger["genre_fit"]), (["dream pop"], 1.0))  # stored normalized

    def test_failing_artist_is_retried_after_a_day(self):
        class Flaky(FakeLastFm):
            def similar_artists(self, artist, limit=250):
                if artist == "Harbor Lights":
                    raise releases.ApiError("gave up after 5 attempts: last.fm error 8")
                return super().similar_artists(artist, limit)
        self.refresh(Flaky())
        u = releases.upcoming(self.conn, today=TODAY, has_lastfm=True)
        self.assertEqual(u["lastfm"]["seeds_done"], u["lastfm"]["seeds"])  # counted for now: no refresh loop
        self.assertFalse(u["stale"])
        at = self.conn.execute("SELECT fetched_at FROM similar_seeds WHERE seed_key = 'harbor lights'").fetchone()[0]
        self.assertLess(at, time.time() - (releases.CACHE_DAYS - releases.RETRY_DAYS - 0.01) * 86400)

    def test_last_fm_down_stops_early(self):
        class Down(FakeLastFm):
            def similar_artists(self, artist, limit=250):
                self.calls.append(("similar", artist))
                raise releases.ApiError("network: timed out")
        fm = Down()
        result = self.refresh(fm)
        self.assertEqual(len(fm.calls), releases.MAX_FAILURES)
        self.assertFalse(result["lastfm"]["ok"])
        self.assertIn("isn't answering", releases.upcoming(self.conn, today=TODAY, has_lastfm=True)["lastfm"]["error"])

    def test_without_a_key(self):
        self.refresh(FakeLastFm())
        u = releases.upcoming(self.conn, today=TODAY)
        self.assertFalse(u["lastfm"]["has_key"])  # the page then hides "new to you" and asks for a key
        self.assertTrue(all(w["new"] == [] for w in releases.upcoming(self.conn, today=TODAY, with_new=False)["weeks"]))

    def test_start_releases_the_lock_when_a_client_fails(self):
        def broken():
            raise RuntimeError("no clients")
        with self.assertLogs("uvicorn.error", level="ERROR"):
            releases.start(Path(self.tmp.name) / "n.db", broken, lambda: None)
            releases.join(10)
        self.assertFalse(releases.refreshing())
        releases.start(Path(self.tmp.name) / "n.db", lambda: (lb_client(), wiki_client()), lambda: None)
        releases.join(10)
        self.assertFalse(releases.refreshing())

    def test_cached_for_a_month(self):
        self.refresh(FakeLastFm())
        again = FakeLastFm()
        self.refresh(again)
        self.assertEqual(again.calls, [])

    def test_bad_key_keeps_the_release_lists(self):
        result = self.refresh(FakeLastFm(fatal=True))
        self.assertFalse(result["lastfm"]["ok"])
        self.assertTrue(result["listenbrainz"]["ok"])
        u = releases.upcoming(self.conn, today=TODAY, has_lastfm=True)
        self.assertIn("Invalid API key", u["lastfm"]["error"])
        self.assertFalse(u["stale"])  # tried just now: no automatic retry until "Check again" or 12 hours
        self.assertEqual(sum(len(w["new"]) for w in u["weeks"]), 0)  # nothing looked up, nothing scored


class ApiTest(unittest.TestCase):
    def test_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(Path(tmp) / "a.db", releases_factory=lambda: (lb_client([]), wiki_client({})),
                                           lastfm_factory=FakeLastFm))
            u = client.get("/api/upcoming").json()
            self.assertEqual(len(u["weeks"]), releases.WEEKS_AHEAD + 1)
            self.assertTrue(u["stale"])
            self.assertEqual(client.post("/api/upcoming/refresh").status_code, 202)
            releases.join(10)
            r = client.get("/api/upcoming").json()
            self.assertFalse(r["stale"])
            self.assertFalse(r["refreshing"])
            self.assertIsNotNone(r["sources"]["listenbrainz"]["at"])
            with releases._refreshing:
                self.assertEqual(client.post("/api/upcoming/refresh").status_code, 409)
            self.assertEqual(client.post("/api/upcoming/refresh", headers={"origin": "https://evil.example"}).status_code, 403)
            routes = [r for r in client.app.routes if getattr(r, "path", "") == "/api/upcoming/refresh"]
            self.assertEqual(len(routes), 1)


if __name__ == "__main__":
    unittest.main()
