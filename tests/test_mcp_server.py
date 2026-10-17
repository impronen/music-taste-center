"""The MCP server: tools answer from a read-only connection and can't change the library
(fictional names throughout)."""
import asyncio
import json
import sqlite3
import tempfile
from contextlib import closing
import unittest
import unittest.mock
from pathlib import Path


from mcp.server.mcpserver.exceptions import ToolError

from mtc import db
from mtc.mcp_server import build_server, connect_readonly
from tests import synthetic


def call(server, tool, **args):
    async def go():
        result = await server.call_tool(tool, args)
        return result
    return asyncio.run(go())


class McpServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "demo.db"
        synthetic.build_demo_db(cls.path)
        cls.server = build_server(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def data(self, tool, **args):
        result = call(self.server, tool, **args)
        self.assertFalse(getattr(result, "is_error", False), result)
        if getattr(result, "structured_content", None) is not None:
            sc = result.structured_content
            return sc.get("result", sc) if isinstance(sc, dict) else sc
        return json.loads(result.content[0].text)

    def test_tools_are_listed_and_read_only(self):
        tools = asyncio.run(self.server.list_tools())
        names = {t.name for t in tools}
        self.assertTrue({"taste_profile", "check_library", "query_sql", "artist_details", "top"} <= names)
        self.assertTrue(all(t.annotations and t.annotations.read_only_hint for t in tools))

    def test_top_and_artist_details(self):
        top = self.data("top", kind="artist", limit=3)
        self.assertEqual(len(top), 3)
        info = self.data("artist_details", artist_id=top[0]["id"])
        self.assertEqual(info["name"], top[0]["name"])
        self.assertNotIn("monthly", info)

    def test_check_library_folds_case_and_finnish_letters(self):
        known = self.data("top", kind="artist", limit=1)[0]["name"]
        r = self.data("check_library", names=[known.upper(), "Nobody At All"])
        self.assertTrue(r[0]["known"])
        self.assertFalse(r[1]["known"])
        self.assertTrue(self.data("check_library", names=["ÅKERLUND TRIO"])[0]["known"])

    def test_taste_profile_has_text(self):
        self.assertIn("My music taste", self.data("taste_profile", task="discover")["text"])

    def test_taste_profile_drops_working_data(self):
        profile = self.data("taste_profile")["profile"]
        self.assertTrue({"genres", "artists", "loved"} <= set(profile))
        self.assertFalse({"genre_vector", "genre_idf", "seeds"} & set(profile))

    def test_upcoming_releases_limit_caps_each_list(self):
        # the demo DB has no cached releases, so feed the tool a week with more items than the limit
        item = lambda i: {"artist": f"Artist {i}", "title": f"Title {i}", "release_date": "2030-01-04", "label": "Fictional",
                          "cover_url": "https://example.test/c.jpg", "release_group_mbid": "x", "source": "wikipedia", "sources": ["wikipedia"]}
        week = {"friday": "2030-01-04", "more": 2, **{k: [item(i) for i in range(6)] for k in ("new", "mine", "also")}}
        with unittest.mock.patch("mtc.mcp_server.releases.upcoming", return_value={"first": "2030-01-01", "weeks": [week]}):
            for limit in (1, 4):
                out = self.data("upcoming_releases", limit=limit)["weeks"][0]
                self.assertEqual(out["more"], 2)
                for k in ("new", "mine", "also"):
                    self.assertEqual([r["title"] for r in out[k]], [f"Title {i}" for i in range(limit)])
                    self.assertFalse({"cover_url", "release_group_mbid", "source", "sources"} & set(out[k][0]))
                    self.assertEqual(out[k][0]["label"], "Fictional")
        with self.assertRaises(Exception):
            call(self.server, "upcoming_releases", limit=0)
        self.assertEqual(len(week["new"]), 6)  # the shared result is not modified

    def test_sql_is_read_only_and_capped(self):
        r = self.data("query_sql", sql="SELECT id FROM scrobbles")
        self.assertEqual(len(r["rows"]), 500)
        self.assertTrue(r["truncated"])
        before = sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
        for sql in ("DELETE FROM scrobbles", "DROP TABLE scrobbles", "PRAGMA query_only=OFF; DELETE FROM scrobbles"):
            with self.assertRaises(Exception):
                call(self.server, "query_sql", sql=sql)
        after = sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0]
        self.assertEqual(before, after)

    def test_errors_reach_the_model(self):
        # only ToolError messages are passed on; any other exception becomes "Error executing tool"
        for tool, args, text in (("query_sql", {"sql": "SELECT * FROM nope"}, "no such table"),
                                 ("query_sql", {"sql": "ATTACH 'x.db' AS x"}, "not authorized"),
                                 ("query_sql", {"sql": "PRAGMA query_only=OFF"}, "not authorized"),
                                 ("artist_details", {"artist_id": 99999}, "No artist")):
            with self.assertRaisesRegex(ToolError, text):
                call(self.server, tool, **args)

    def test_big_cells_are_cut(self):
        with self.assertRaisesRegex(ToolError, "too big"):
            call(self.server, "query_sql", sql="SELECT printf('%.*c', 500000000, 'x')")
        r = self.data("query_sql", sql="SELECT printf('%.*c', 900000, 'x'), zeroblob(5)")
        self.assertLess(len(r["rows"][0][0]), 2100)
        self.assertEqual(r["rows"][0][1], "<5 bytes>")

    def test_old_schema_is_refused(self):
        old = Path(self.tmp.name) / "old.db"
        with closing(sqlite3.connect(old)) as c:
            c.execute("CREATE TABLE t(x)")
        with self.assertRaisesRegex(ToolError, "schema version 0"):
            connect_readonly(old)

    def test_describe_schema(self):
        self.assertIn("ts INTEGER", self.data("describe_schema")["scrobbles"])

    def test_missing_library_says_so(self):
        with self.assertRaises(ToolError):
            connect_readonly(Path(self.tmp.name) / "nope.db")

    def test_connection_cannot_write(self):
        conn = connect_readonly(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO meta(key, value) VALUES ('x', '1')")
        db.versions(conn)  # reading still works


if __name__ == "__main__":
    unittest.main()
