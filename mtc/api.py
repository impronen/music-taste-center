"""HTTP layer: JSON endpoints under /api and the single-page UI from static/."""
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, derive, fsutil, ingest, insights

MAX_UPLOAD_BYTES = 300 * 1024 * 1024
DATE = r"^\d{4}-\d{2}-\d{2}$"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Music Taste Center", docs_url="/api/docs", openapi_url="/api/openapi.json")
    path = Path(db_path or config.DB_PATH)
    db.connect(path).close()  # apply migrations at startup

    def conn() -> Iterator[sqlite3.Connection]:
        c = db.connect(path)
        try:
            yield c
        finally:
            c.close()

    Conn = Depends(conn)

    def found(value):
        if value is None:
            raise HTTPException(404, "not found")
        return value

    @app.get("/api/overview")
    def overview(c=Conn):
        return insights.overview(c)

    @app.get("/api/timeline")
    def timeline(c=Conn):
        return insights.timeline(c)

    @app.get("/api/top/{kind}")
    def top(kind: str, start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE),
            limit: int = Query(25, ge=1, le=500), c=Conn):
        if kind not in ("artist", "track", "album"):
            raise HTTPException(404, "kind must be artist, track or album")
        return insights.top(c, kind, start, end, limit)

    @app.get("/api/clock")
    def clock(start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE), c=Conn):
        return insights.clock(c, start, end)

    @app.get("/api/artists")
    def artists(q: str = "", sort: str = "plays", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), c=Conn):
        return insights.artists(c, q.strip(), sort, limit, offset)

    @app.get("/api/artists/{artist_id}")
    def artist(artist_id: int, c=Conn):
        return found(insights.artist(c, artist_id))

    @app.get("/api/albums/{album_id}")
    def album(album_id: int, c=Conn):
        return found(insights.album(c, album_id))

    @app.get("/api/tracks/{track_id}")
    def track(track_id: int, c=Conn):
        return found(insights.track(c, track_id))

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1, max_length=200), c=Conn):
        return insights.search(c, q.strip())

    @app.get("/api/recent")
    def recent(limit: int = Query(50, ge=1, le=500), c=Conn):
        return insights.recent(c, limit)

    @app.get("/api/eras")
    def eras(c=Conn):
        return insights.eras(c)

    @app.get("/api/insights")
    def insight_cards(c=Conn):
        return insights.insights(c)

    @app.get("/api/graph")
    def graph(n: int = Query(120, ge=10, le=400), per_node: int = Query(6, ge=1, le=20), c=Conn):
        return insights.graph(c, n, per_node)

    @app.get("/api/imports")
    def import_log(c=Conn):
        return insights.imports(c)

    @app.post("/api/import")
    async def import_upload(request: Request, c=Conn):
        """Upload a CSV as the raw request body (the UI sends the file directly)."""
        body = await request.body()
        if not body:
            raise HTTPException(400, "empty upload")
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file too large")
        text, encoding = fsutil.decode(body)
        label = Path(unquote(request.headers.get("x-filename", "upload.csv"))).name[:200]
        return ingest.import_csv_text(c, text, label=label, encoding=encoding)

    @app.post("/api/rebuild")
    def rebuild(c=Conn):
        return derive.rebuild(c)

    @app.middleware("http")
    async def revalidate(request: Request, call_next):
        # Local app: always revalidate UI files (ETag) so code updates show up without a hard reload.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(config.STATIC_DIR / "index.html")

    return app
