"""Matching an album to a MusicBrainz release group: edition markers in the title must not hide it,
and cleaning the title must never pick another release of the same artist."""
import json
import re
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from mtc import db, enrich, ingest
from mtc.db import migrate
from mtc.musicbrainz import MusicBrainz, NotFound, clean_title, edition_free_title
from tests import synthetic


def group(title, artist="Fleetwood Mac", date="1977-02-04", primary="Album", secondary=(), score=100, credit=None):
    return {"id": f"rg-{title}", "score": score, "title": title, "first-release-date": date, "primary-type": primary,
            "secondary-types": list(secondary), "artist-credit": credit or [{"name": artist}]}


def mb_with(catalog=None, calls=None, raw=None):
    """A MusicBrainz client over a fake transport. `catalog` maps a MusicBrainz title to (artist credit,
    first release date) and answers only a search for exactly that title; `raw` is a fixed list of
    release groups returned for every search. Every searched title is appended to `calls`."""
    calls = calls if calls is not None else []

    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        unescape = lambda f: re.sub(r"\\(.)", r"\1", re.search(f + r':"((?:\\.|[^"\\])*)"', q["query"]).group(1))
        title = unescape("releasegroup")
        calls.append(title)
        if raw is not None:
            groups = raw
        else:
            found = (catalog or {}).get(title)
            groups = [group(title, found[0], found[1])] if found else []
        return 200, json.dumps({"release-groups": groups}).encode()
    return MusicBrainz(transport, min_interval=0, sleep=lambda s: None)


class EditionTitleTests(unittest.TestCase):
    def test_markers_are_removed(self):
        for title, expected in [
            ("Rumours (Deluxe Edition)", "Rumours"),
            ("Abbey Road (Remastered)", "Abbey Road"),
            ("Nevermind (Remastered) [Deluxe Edition]", "Nevermind"),
            ("Hotel California (40th Anniversary Expanded Edition)", "Hotel California"),
            ("Kind of Blue (Legacy Edition)", "Kind of Blue"),
            ("Album (Bonus Track Version)", "Album"),
            ("Album (Bonus Tracks)", "Album"),
            ("Album (2011 Remaster)", "Album"),
            ("A Night at the Opera [Mono]", "A Night at the Opera"),
            ("Thriller - 2003 Remaster", "Thriller"),
            ("A - B - Remastered 2011", "A - B"),
            ("Some Album - EP", "Some Album"),
            ("Some Album - Single", "Some Album"),
            ("Punk Rock - EP (Remastered)", "Punk Rock"),
        ]:
            self.assertEqual(edition_free_title(title), expected, title)

    def test_fullwidth_brackets_and_en_or_em_dashes_count_too(self):
        for title, expected in [("Album 【Remaster】", "Album"), ("Album – Remastered", "Album"), ("Album — Deluxe Edition", "Album"),
                                ("Album （Deluxe Edition）", "Album"), ("Album 〔Remastered〕", "Album")]:
            self.assertEqual(edition_free_title(title), expected, title)

    def test_real_subtitles_and_titles_are_left_alone(self):
        for title in ["Album (Special Guest)", "Album (Special Delivery)", "Album (Version 2)", "Album (Bonus)",
                      "Album (Disc 2: Bonus)", "Album - Special", "Mix (Version)", "The Wall (Live)", "Version 2.0",
                      "Special Beat Service", "Mono", "Special K", "Anniversary Waltz", "(What's the Story) Morning Glory?",
                      "Blue Album (Weezer)", "Title (feat. Someone)", "Unplugged (Acoustic)", "Deluxe", "Special",
                      "Plain Title", "Mix - Part 2", "", "   "]:
            self.assertEqual(edition_free_title(title), title, repr(title))

    def test_a_title_that_is_only_markers_is_kept(self):
        for title in ("(Deluxe Edition)", "（Deluxe）", "(Remastered)"):
            self.assertEqual(edition_free_title(title), title)

    def test_ep_and_single_suffixes_are_reported_as_the_type_to_look_for(self):
        self.assertEqual(clean_title("Closer - EP"), ("Closer", "EP"))
        self.assertEqual(clean_title("Closer - Single"), ("Closer", "Single"))
        self.assertEqual(clean_title("Closer - EP (Remastered)"), ("Closer", "EP"))
        self.assertEqual(clean_title("Closer (Deluxe Edition)"), ("Closer", None))
        self.assertEqual(clean_title("Closer"), ("Closer", None))

    def test_curly_apostrophes_and_dangling_dashes_are_handled(self):
        for title, expected in [("Thriller (Collector’s Edition)", "Thriller"), ("Foo - (Deluxe)", "Foo"), ("(Deluxe) - Foo", "Foo"),
                                ("Foo - [Remastered]", "Foo"), ("Foo (Live (Deluxe))", "Foo (Live)"), ("Foo ((Remastered))", "Foo")]:
            self.assertEqual(edition_free_title(title), expected, title)

    def test_cleaning_is_idempotent_and_terminates(self):
        pieces = ["(", ")", "[", "]", " - ", "Deluxe", "Edition", "EP", "Remastered", "x", " ", "–", "Single", "2011"]
        import random
        rng = random.Random(1)
        for _ in range(3000):
            title = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 9)))
            once = edition_free_title(title)
            self.assertEqual(edition_free_title(once), once, repr(title))


class SearchTests(unittest.TestCase):
    def test_a_title_with_markers_is_found_by_the_cleaned_title(self):
        calls = []
        mb = mb_with({"Rumours": ("Fleetwood Mac", "1977-02-04")}, calls)
        g = mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")
        self.assertEqual((g["release_date"], g["title"]), ("1977-02-04", "Rumours"))
        self.assertEqual(calls, ["Rumours (Deluxe Edition)", "Rumours"])  # exact first, then cleaned

    def test_the_exact_title_still_wins_and_costs_one_request(self):
        calls = []
        mb = mb_with({"Rumours (Deluxe Edition)": ("Fleetwood Mac", "2013-01-01"), "Rumours": ("Fleetwood Mac", "1977-02-04")}, calls)
        self.assertEqual(mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")["release_date"], "2013-01-01")
        self.assertEqual(len(calls), 1)

    def test_a_title_without_markers_costs_no_extra_request(self):
        calls = []
        mb = mb_with({}, calls)
        with self.assertRaises(NotFound):
            mb.search_release_group("Nobody", "Plain Title")
        self.assertEqual(calls, ["Plain Title"])

    def test_the_artist_must_still_match_after_cleaning(self):
        mb = mb_with({"Rumours": ("Some Tribute Band", "2001-01-01")})
        with self.assertRaises(NotFound):
            mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")

    def test_a_different_title_in_the_results_is_not_accepted(self):
        calls = []
        mb = mb_with(raw=[group("Rumours Live"), group("Tusk")], calls=calls)  # the fake returns these whatever is asked
        with self.assertRaises(NotFound):
            mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")
        self.assertEqual(len(calls), 2)

    def test_the_cleaned_search_may_return_the_group_with_the_original_title(self):
        mb = mb_with(raw=[group("Rumours (Deluxe Edition)", date="2013-01-01")])
        self.assertEqual(mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")["release_date"], "2013-01-01")

    def test_the_best_candidate_wins_when_the_first_is_wrong_or_too_weak(self):
        mb = mb_with(raw=[group("Other"), group("Rumours", score=80, date="1999-01-01"), group("Rumours", date="1977-02-04")])
        self.assertEqual(mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")["release_date"], "1977-02-04")

    def test_a_joined_artist_credit_counts(self):
        credit = [{"name": "Simon", "joinphrase": " & "}, {"name": "Garfunkel"}]
        mb = mb_with(raw=[group("Bridge", credit=credit, date="1970-01-26")])
        self.assertEqual(mb.search_release_group("Garfunkel", "Bridge [Remastered]")["release_date"], "1970-01-26")

    def test_special_characters_in_the_cleaned_title_are_escaped(self):
        calls = []
        mb = mb_with(raw=[group("AC/DC: Live? [1+1]", artist="AC/DC", date="1992-01-01")], calls=calls)
        g = mb.search_release_group("AC/DC", "AC/DC: Live? [1+1] (Deluxe Edition)")
        self.assertEqual((g["release_date"], calls[-1]), ("1992-01-01", "AC/DC: Live? [1+1]"))

    def test_an_ep_title_never_takes_the_date_of_the_album_with_the_same_name(self):
        mb = mb_with(raw=[group("Closer", artist="Band", primary="Album", date="1995-05-01"),
                          group("Closer", artist="Band", primary="Album", secondary=["Live"], date="2009-01-01")])
        for title in ("Closer - EP", "Closer - Single"):
            with self.assertRaises(NotFound, msg=title):
                mb.search_release_group("Band", title)

    def test_an_ep_title_takes_an_ep_group(self):
        mb = mb_with(raw=[group("Closer", artist="Band", primary="Album", date="1995-05-01"),
                          group("Closer", artist="Band", primary="EP", date="1994-03-01")])
        g = mb.search_release_group("Band", "Closer - EP")
        self.assertEqual((g["release_date"], g["release_type"]), ("1994-03-01", "EP"))

    def test_a_deluxe_title_does_not_take_a_live_or_compilation_group_of_the_same_name(self):
        mb = mb_with(raw=[group("Closer", artist="Band", secondary=["Live"], date="2009-01-01"),
                          group("Closer", artist="Band", secondary=["Compilation"], date="2012-01-01")])
        with self.assertRaises(NotFound):
            mb.search_release_group("Band", "Closer (Deluxe Edition)")

    def test_a_single_named_like_the_album_is_not_the_album(self):
        mb = mb_with(raw=[group("Foo", artist="Band", primary="Single", date="1990-01-01"),
                          group("Foo", artist="Band", primary="Album", date="1992-06-01")])
        self.assertEqual(mb.search_release_group("Band", "Foo (Deluxe Edition)")["release_date"], "1992-06-01")
        only_single = mb_with(raw=[group("Foo", artist="Band", primary="Single", date="1990-01-01")])
        with self.assertRaises(NotFound):
            only_single.search_release_group("Band", "Foo (Deluxe Edition)")

    def test_the_album_beats_an_ep_of_the_same_name_but_an_ep_alone_is_accepted(self):
        both = mb_with(raw=[group("Foo", artist="Band", primary="EP", date="1989-01-01"),
                            group("Foo", artist="Band", primary="Album", date="1992-06-01")])
        self.assertEqual(both.search_release_group("Band", "Foo (Remastered)")["release_date"], "1992-06-01")
        ep_only = mb_with(raw=[group("Foo", artist="Band", primary="EP", date="1989-01-01")])
        self.assertEqual(ep_only.search_release_group("Band", "Foo (Remastered)")["release_date"], "1989-01-01")

    def test_a_secondary_type_must_be_a_whole_word_of_the_title(self):
        for title, kind in [("Alive (Deluxe Edition)", "Live"), ("Olive (Remastered)", "Live"), ("Demolition (Remastered)", "Demo"),
                            ("Remixed (Deluxe Edition)", "Remix"), ("Deliverance (Deluxe Edition)", "Live")]:
            mb = mb_with(raw=[group(edition_free_title(title), artist="Band", secondary=[kind])])
            with self.assertRaises(NotFound, msg=title):
                mb.search_release_group("Band", title)

    def test_a_best_of_deluxe_edition_may_be_a_compilation_but_other_titles_may_not(self):
        comp = lambda title: mb_with(raw=[group(title, artist="Band", secondary=["Compilation"], date="2005-01-01")])
        for title in ("Greatest Hits (Deluxe Edition)", "The Best Of (Remastered)", "Very Best of [Expanded Edition]"):
            self.assertEqual(comp(edition_free_title(title)).search_release_group("Band", title)["release_date"], "2005-01-01", title)
        with self.assertRaises(NotFound):
            comp("Closer").search_release_group("Band", "Closer (Deluxe Edition)")

    def test_a_secondary_type_named_in_the_title_is_fine(self):
        mb = mb_with(raw=[group("Closer (Live)", artist="Band", secondary=["Live"], date="2009-01-01")])
        self.assertEqual(mb.search_release_group("Band", "Closer (Live) (Deluxe Edition)")["release_date"], "2009-01-01")

    def test_the_exact_title_search_is_not_subject_to_the_guards(self):
        mb = mb_with(raw=[group("Closer", artist="Band", secondary=["Live"], date="2009-01-01")])
        self.assertEqual(mb.search_release_group("Band", "Closer")["release_date"], "2009-01-01")  # unchanged behaviour


class RetryMigrationTests(unittest.TestCase):
    def test_unmatched_albums_with_a_marker_shaped_title_are_looked_up_again_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "m.db")
            try:
                with conn:
                    conn.execute("INSERT INTO artists(id, name, name_key) VALUES (1, 'A', 'a')")
                    titles = {1: "Plain", 2: "Rumours (Deluxe Edition)", 3: "Blue [Remastered]", 4: "Single - EP",
                              5: "Matched (Deluxe)", 6: "Errored (Deluxe)", 7: "Dated (Deluxe)"}
                    for i, t in titles.items():
                        conn.execute("INSERT INTO albums(id, artist_id, title, title_key) VALUES (?, 1, ?, ?)", (i, t, t.lower()))
                    state = {1: ("not_found", None, None), 2: ("not_found", None, None), 3: ("not_found", None, None),
                             4: ("not_found", None, None), 5: ("not_found", "rg-5", None), 6: ("error", None, None),
                             7: ("ok", "rg-7", "2001")}
                    for i, (st, rg, date) in state.items():
                        conn.execute("INSERT INTO album_info(album_id, status, fetched_at, mb_status, mb_fetched_at,"
                                     " release_group_mbid, release_date) VALUES (?, 'ok', 1000, ?, 1000, ?, ?)", (i, st, rg, date))
                    conn.execute("PRAGMA user_version = 3")
                migrate(conn)
                again = {r[0] for r in conn.execute("SELECT album_id FROM album_info WHERE mb_fetched_at IS NULL")}
                self.assertEqual(again, {2, 3, 4})  # plain titles, matched-but-undated, errors and dated ones are left
                self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(conn.execute("SELECT release_date FROM album_info WHERE album_id = 7").fetchone()[0], "2001")
            finally:
                conn.close()

    def test_a_tag_dated_album_keeps_its_year_until_a_match_replaces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "m.db")
            try:
                ingest.import_csv_text(conn, synthetic.to_csv(
                    [("Fleetwood Mac", "Rumours (Deluxe Edition)", f"t{i}", 1_600_000_000 + i * 600) for i in range(3)]
                    + [("Fleetwood Mac", "Nothing Found [Remastered]", f"u{i}", 1_600_100_000 + i * 600) for i in range(3)],
                    now_playing=False), label="t", encoding="utf-8")
                before = int(time.time()) - 3600  # a recent lookup, so the 120-day refresh doesn't pick the albums up by itself
                with conn:
                    for album_id, in conn.execute("SELECT id FROM albums").fetchall():
                        conn.execute("INSERT INTO album_info(album_id, status, fetched_at, release_date, release_date_source,"
                                     " mb_status, mb_fetched_at) VALUES (?, 'ok', ?, '1999', 'tag', 'not_found', ?)", (album_id, before, before))
                    conn.execute("PRAGMA user_version = 3")
                self.assertEqual(len(enrich.pending_releases(conn)), 0)
                migrate(conn)
                self.assertEqual(len(enrich.pending_releases(conn)), 2)
                calls = []
                mb = mb_with({"Rumours": ("Fleetwood Mac", "1977-02-04")}, calls)
                enrich.run(conn, musicbrainz=mb, artists=0, albums=0, log=lambda *a: None)
                rows = {t: r for t, *r in conn.execute(
                    "SELECT al.title, ai.release_date, ai.release_date_source, ai.mb_status, ai.mb_fetched_at > ?"
                    " FROM album_info ai JOIN albums al ON al.id = ai.album_id", (before,))}
                self.assertEqual(rows["Rumours (Deluxe Edition)"], ["1977-02-04", "musicbrainz", "ok", 1])
                self.assertEqual(rows["Nothing Found [Remastered]"], ["1999", "tag", "not_found", 1])  # the tag year survives
                self.assertEqual(sorted(calls), sorted(["Rumours (Deluxe Edition)", "Rumours", "Nothing Found [Remastered]", "Nothing Found"]))
                calls.clear()
                enrich.run(conn, musicbrainz=mb, artists=0, albums=0, log=lambda *a: None)
                self.assertEqual(calls, [])  # retried exactly once
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
