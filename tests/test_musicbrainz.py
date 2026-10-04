"""Matching an album to a MusicBrainz release group: edition markers in the title must not hide it."""
import json
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from mtc import db
from mtc.db import migrate
from mtc.musicbrainz import MusicBrainz, NotFound, edition_free_title


def mb_with(catalog: dict[str, tuple[str, str]], calls: list):
    """catalog: MusicBrainz title -> (artist credit, first release date). Answers a release-group
    search with the entry whose title was searched for; records each searched title."""
    def transport(url, headers, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        unescape = lambda f: re.sub(r"\\(.)", r"\1", re.search(f + r':"((?:\\.|[^"\\])*)"', q["query"]).group(1))
        title = unescape("releasegroup")
        calls.append(title)
        found = catalog.get(title)
        groups = [{"id": "rg-1", "score": 100, "title": title, "first-release-date": found[1], "primary-type": "Album",
                   "artist-credit": [{"name": found[0]}]}] if found else []
        return 200, json.dumps({"release-groups": groups}).encode()
    return MusicBrainz(transport, min_interval=0, sleep=lambda s: None)


class EditionTitleTests(unittest.TestCase):
    def test_markers_are_removed(self):
        for title, expected in [
            ("Rumours (Deluxe Edition)", "Rumours"),
            ("Abbey Road (Remastered)", "Abbey Road"),
            ("Nevermind (Remastered) [Deluxe Edition]", "Nevermind"),
            ("Hotel California (40th Anniversary Expanded Edition)", "Hotel California"),
            ("Thriller - 2003 Remaster", "Thriller"),
            ("Some Album - EP", "Some Album"),
            ("Some Album - Single", "Some Album"),
            ("A - B - Remastered 2011", "A - B"),
            ("Punk Rock - EP (Remastered)", "Punk Rock"),
            ("Kind of Blue (Legacy Edition)", "Kind of Blue"),
        ]:
            self.assertEqual(edition_free_title(title), expected, title)

    def test_real_titles_are_left_alone(self):
        for title in ["(What's the Story) Morning Glory?", "Blue Album (Weezer)", "Live at the Fillmore (Live)",
                      "Unplugged (Acoustic)", "Deluxe", "Special", "Plain Title", "Mix - Part 2", "", "   "]:
            self.assertEqual(edition_free_title(title), title, repr(title))

    def test_a_title_that_is_only_markers_is_kept(self):
        self.assertEqual(edition_free_title("(Deluxe Edition)"), "(Deluxe Edition)")


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
        calls = []
        mb = mb_with({"Rumours": ("Some Tribute Band", "2001-01-01")}, calls)
        with self.assertRaises(NotFound):
            mb.search_release_group("Fleetwood Mac", "Rumours (Deluxe Edition)")
        self.assertEqual(len(calls), 2)

    def test_a_different_album_with_a_similar_title_is_not_accepted(self):
        calls = []
        mb = mb_with({"Rumours": ("Fleetwood Mac", "1977-02-04")}, calls)
        with self.assertRaises(NotFound):
            mb.search_release_group("Fleetwood Mac", "Rumours Live (Deluxe Edition)")


class RetryMigrationTests(unittest.TestCase):
    def test_unmatched_albums_with_markers_are_looked_up_again_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "m.db")
            try:
                with conn:
                    conn.execute("INSERT INTO artists(id, name, name_key) VALUES (1, 'A', 'a')")
                    titles = {1: "Plain", 2: "Rumours (Deluxe Edition)", 3: "Blue [Remastered]", 4: "Single - EP",
                              5: "Matched (Deluxe)", 6: "Errored (Deluxe)", 7: "Dated (Deluxe)"}
                    for i, t in titles.items():
                        conn.execute("INSERT INTO albums(id, artist_id, title, title_key) VALUES (?, 1, ?, ?)", (i, t, t.lower()))
                    # (mb_status, release_group_mbid, release_date) per album, all fetched on day 1000
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
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(conn.execute("SELECT release_date FROM album_info WHERE album_id = 7").fetchone()[0], "2001")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
