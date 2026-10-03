"""HTTP layer: JSON endpoints under /api and the single-page UI from static/."""
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, derive, enrich, fsutil, ingest, insights, jobs, settings
from .webapi import Fatal

MAX_UPLOAD_BYTES = 300 * 1024 * 1024
DATE = r"^\d{4}-\d{2}-\d{2}$"


class FetchRequest(BaseModel):
    """Limits per phase: omitted = everything pending, 0 = skip."""
    artists: int | None = Field(None, ge=0)
    albums: int | None = Field(None, ge=0)
    releases: int | None = Field(None, ge=0)


class KeyRequest(BaseModel):
    key: str = Field(..., pattern=r"^\s*[A-Za-z0-9]{16,64}\s*$")


def create_app(db_path: str | Path | None = None, *, lastfm_factory: Callable | None = None,
               musicbrainz_factory: Callable | None = None) -> FastAPI:
    """The factories replace the real last.fm / MusicBrainz clients (tests pass fakes)."""
    path = Path(db_path or config.DB_PATH)
    db.connect(path).close()  # apply migrations at startup
    job = jobs.EnrichJob(path, lastfm_factory=lastfm_factory, musicbrainz_factory=musicbrainz_factory)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        job.shutdown()  # finish the current item, then stop

    app = FastAPI(title="Music Taste Center", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.enrich_job = job

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

    @app.get("/api/genres")
    def genre_profile(start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE),
                      limit: int = Query(40, ge=1, le=200), c=Conn):
        return insights.genres(c, start, end, limit)

    @app.get("/api/tags/{tag_id}")
    def tag(tag_id: int, c=Conn):
        return found(insights.tag(c, tag_id))

    @app.get("/api/metadata/status")
    def metadata_status(c=Conn):
        return {**enrich.status(c), "has_key": bool(settings.lastfm_api_key()),
                "pending_artists": len(enrich.pending_artists(c)), "pending_albums": len(enrich.pending_albums(c)),
                "pending_releases": len(enrich.pending_releases(c))}

    @app.get("/api/metadata/job")
    def fetch_job():
        return job.snapshot()

    @app.post("/api/metadata/job", status_code=202)
    def start_fetch(req: FetchRequest | None = None):
        req = req or FetchRequest()
        try:
            return job.start(artists=req.artists, albums=req.albums, releases=req.releases)
        except jobs.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        except Fatal as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/metadata/job/stop")
    def stop_fetch():
        return job.stop()

    @app.put("/api/metadata/key")
    def save_key(req: KeyRequest):
        """Stores the last.fm API key in data/settings.json. The key is never sent back."""
        settings.save({**settings.load(), "lastfm_api_key": req.key.strip()})
        return {"has_key": True}

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
    async def same_origin_writes(request: Request, call_next):
        # Any website could POST to localhost; browsers always send Origin on those requests.
        origin = request.headers.get("origin")
        if (request.method not in ("GET", "HEAD", "OPTIONS") and origin
                and urlsplit(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

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
