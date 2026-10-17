"""MCP server: your listening data as read-only tools for Claude (`python -m mtc mcp`, stdio).

Every tool is a thin wrapper over the same functions the web UI uses (insights, taste_gap,
taste_prompt, releases), run on a read-only connection: nothing here can change the library.
The ready-made taste profile and `check_library` are the building blocks for finding new music:
Claude searches the web for candidates, then `check_library` drops the ones already in the library.
"""
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import config, db, insights, releases, settings, taste_gap, taste_prompt

DATE = r"^\d{4}-\d{2}-\d{2}$"
Day = Annotated[str | None, Field(pattern=DATE, description="local date YYYY-MM-DD; omit for no limit")]
SQL_MAX_ROWS = 500
SQL_MAX_SECONDS = 10
SQL_MAX_CELL = 2000        # characters kept per cell
SQL_MAX_LENGTH = 1_000_000  # SQLite refuses to build any string or blob longer than this
CHECK_MAX_NAMES = 100
_SQL_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
# Rows that are large in the UI (a year of monthly counts, 24 hourly bars) but noise to a model.
ARTIST_DROP = ("monthly", "hours", "image_url")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Tools over one person's last.fm listening history (scrobbles, tags, release dates, loved tracks). "
    "Dates are local dates (YYYY-MM-DD). Start with taste_profile to learn the taste, use search or "
    "top to find ids for artist_details, and use check_library before recommending anything: it says "
    "which suggested artists the listener already plays. query_sql runs read-only SELECTs for anything "
    "the other tools don't cover; `describe_schema` lists the tables."
)


def connect_readonly(path: str | Path | None = None) -> sqlite3.Connection:
    """A read-only connection. Unlike db.connect it never migrates: the app does that on startup."""
    path = Path(path or config.DB_PATH)
    if not path.exists():
        raise ToolError(f"No library at {path}. Import your scrobbles first (python -m mtc import FILE.csv).")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQL_MAX_LENGTH)
    expected = max(int(f.name.split("_", 1)[0]) for f in db.MIGRATIONS_DIR.glob("*.sql"))
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found != expected:
        conn.close()
        raise ToolError(f"The library is at schema version {found}, this server needs {expected}. Start the app once to migrate it.")
    conn.create_function("fold", 1, lambda s: s.casefold() if isinstance(s, str) else s, deterministic=True)  # lower() is ASCII-only: Ä/ä
    return conn


def _cell(value):
    if isinstance(value, bytes):
        value = f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > SQL_MAX_CELL:
        return value[:SQL_MAX_CELL] + f"… ({len(value)} characters)"
    return value


def build_server(db_path: str | Path | None = None) -> MCPServer:
    mcp = MCPServer("taste-center", instructions=INSTRUCTIONS)

    def run(fn, *args, **kwargs):
        # One short-lived connection per call: the app and the updater keep writing meanwhile.
        # ToolError is the only exception type whose message the model gets to see.
        try:
            with closing(connect_readonly(db_path)) as conn:
                return fn(conn, *args, **kwargs)
        except (sqlite3.Error, ValueError) as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations=READ_ONLY)
    def library_overview() -> dict:
        """Size and span of the library: plays, artists, albums, first and last play, streaks, metadata coverage."""
        return run(insights.overview)

    @mcp.tool(annotations=READ_ONLY)
    def listening_summary(start: Day = None, end: Day = None) -> dict:
        """Headline numbers for a date range (default all time), compared with the previous period, plus artists first heard in it."""
        return run(insights.summary, start, end)

    @mcp.tool(annotations=READ_ONLY)
    def top(kind: Literal["artist", "album", "track"] = "artist", start: Day = None, end: Day = None,
            limit: Annotated[int, Field(ge=1, le=200)] = 25) -> list[dict]:
        """Most played artists, albums or tracks in a date range (default all time)."""
        return run(insights.top, kind, start, end, limit)

    @mcp.tool(annotations=READ_ONLY)
    def genre_profile(start: Day = None, end: Day = None, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict:
        """Play-weighted genre shares (an artist's plays are split over its top tags) for a date range."""
        return run(insights.genres, start, end, limit)

    @mcp.tool(annotations=READ_ONLY)
    def recent_plays(limit: Annotated[int, Field(ge=1, le=200)] = 50) -> list[dict]:
        """The latest scrobbles, newest first."""
        return run(insights.recent, limit)

    @mcp.tool(annotations=READ_ONLY)
    def search(q: Annotated[str, Field(min_length=1, max_length=100)],
               limit: Annotated[int, Field(ge=1, le=25)] = 8) -> dict:
        """Find artists, albums and tracks by name; returns ids for artist_details."""
        return run(insights.search, q, limit)

    @mcp.tool(annotations=READ_ONLY)
    def artist_details(artist_id: Annotated[int, Field(ge=1)]) -> dict:
        """One artist: plays, rank, first/last play, tags, top tracks and albums, loved tracks, the artist that led you to them, and co-listened artists."""
        info = run(insights.artist, artist_id)
        if info is None:
            raise ToolError(f"No artist with id {artist_id}. Use search to find ids.")
        return {k: v for k, v in info.items() if k not in ARTIST_DROP}

    @mcp.tool(annotations=READ_ONLY)
    def similar_in_library(artist_id: Annotated[int, Field(ge=1)],
                           limit: Annotated[int, Field(ge=1, le=50)] = 15) -> list[dict]:
        """Artists you play in the same sessions as this one (co-listening), best first."""
        return run(insights.related, artist_id, limit)

    @mcp.tool(annotations=READ_ONLY)
    def insight_list(kind: Literal[tuple(insights.INSIGHT_KINDS)],  # type: ignore[valid-type]
                     limit: Annotated[int, Field(ge=1, le=100)] = 25) -> dict:
        """Curated lists: rediscover, rising, forgotten, obsessions, staying-power, gateways, binges, one-track, loved-left, instant-love, slow-burners, unloved, deep-dives."""
        return run(insights.insight_list, kind, limit, 0)

    @mcp.tool(annotations=READ_ONLY)
    def taste_profile(task: Literal["profile", "discover", "releases"] = "profile") -> dict:
        """The taste profile that the web app's AI prompt is built from: recent listening weighted over a ~90 day half-life, genres rising/core/fading, recent discoveries, staples, loved tracks. `text` is the ready prompt for the task."""
        return run(taste_prompt.build, task)

    @mcp.tool(annotations=READ_ONLY)
    def loved_taste_gap() -> dict:
        """Genres, decades and artists that are over- or under-represented among loved tracks compared with plays: what you love more than you play."""
        return run(taste_gap.taste_gap)

    @mcp.tool(annotations=READ_ONLY)
    def upcoming_releases() -> dict:
        """Cached releases of the coming Fridays: new to you (similar to your artists or fitting your genres), by artists in your library, and others. Refreshing is done in the web app."""
        return run(releases.upcoming, has_lastfm=bool(settings.lastfm_api_key()), with_new=True)

    @mcp.tool(annotations=READ_ONLY)
    def check_library(names: Annotated[list[str], Field(min_length=1, max_length=CHECK_MAX_NAMES)]) -> list[dict]:
        """For candidate artist names (e.g. recommendations found on the web): whether each is already in the library, with plays and first/last play. Use it to keep recommendations new."""
        def check(conn):
            out = []
            for name in names:
                row = conn.execute(
                    "SELECT a.id, a.name, st.plays, st.first_ts, st.last_ts FROM artists a"
                    " JOIN artist_stats st ON st.artist_id = a.id WHERE fold(a.name) = ? ORDER BY st.plays DESC LIMIT 1",
                    (name.strip().casefold(),)).fetchone()
                out.append({"name": name, "known": row is not None, **(dict(row) if row else {})})
            return out
        return run(check)

    @mcp.tool(annotations=READ_ONLY)
    def describe_schema() -> dict[str, list[str]]:
        """Tables and columns that query_sql can read."""
        def schema(conn):
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {t: [f"{c[0]} {c[1]}".strip() for c in conn.execute("SELECT name, type FROM pragma_table_info(?)", (t,))] for t in tables}
        return run(schema)

    @mcp.tool(annotations=READ_ONLY)
    def query_sql(sql: Annotated[str, Field(max_length=5000)]) -> dict:
        """Run one read-only SELECT (max 500 rows, 10 s). scrobbles.ts is a unix time in UTC, scrobbles.lday the local date. See describe_schema."""
        def query(conn):
            deadline = time.monotonic() + SQL_MAX_SECONDS
            conn.set_progress_handler(lambda: time.monotonic() > deadline, 100_000)
            # Reads only: no ATTACH (it can create or open other files), PRAGMA, writes or transactions.
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _SQL_ALLOWED else sqlite3.SQLITE_DENY)
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(SQL_MAX_ROWS + 1)
            except sqlite3.Error as exc:
                raise ToolError(f"SQL failed: {exc}") from None
            columns = [d[0] for d in cur.description or []]
            return {"columns": columns, "rows": [[_cell(v) for v in r] for r in rows[:SQL_MAX_ROWS]],
                    "truncated": len(rows) > SQL_MAX_ROWS}
        return run(query)

    return mcp


def serve(db_path: str | Path | None = None) -> None:
    build_server(db_path).run("stdio")
