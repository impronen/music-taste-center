"""HTTP layer: JSON endpoints under /api and the single-page UI from static/."""
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import config, db, decades, velocity, derive, enrich, fsutil, ingest, insights, jobs, maintenance, rhythms, settings, taste_gap, updater
from .webapi import Fatal, NotFound

MAX_UPLOAD_BYTES = 300 * 1024 * 1024
DATE = r"^\d{4}-\d{2}-\d{2}$"
SQLITE_INT_MAX = 2**63 - 1  # a bigger id overflows SQLite (500) instead of not matching


class FetchRequest(BaseModel):
    """Limits per phase: omitted = everything pending, 0 = skip."""
    artists: int | None = Field(None, ge=0)
    albums: int | None = Field(None, ge=0)
    releases: int | None = Field(None, ge=0)


class KeyRequest(BaseModel):
    key: str = Field(..., pattern=r"^\s*[A-Za-z0-9]{16,64}\s*$")


class MergeRequest(BaseModel):
    source_ids: list[int] = Field(..., min_length=1, max_length=50)
    target_id: int


class AliasRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    target_id: int


class LovedRuleRequest(BaseModel):
    artist: str = Field(..., min_length=1, max_length=1000)
    title: str = Field(..., min_length=1, max_length=1000)
    track_id: int = Field(..., ge=1, le=SQLITE_INT_MAX)


class DismissRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=20000)


class BirthYearRequest(BaseModel):
    year: int | None = Field(..., ge=1900, le=2100)  # required, so only an explicit null clears it


class UsernameRequest(BaseModel):
    username: str = Field(..., pattern=r"^\s*[A-Za-z][A-Za-z0-9_-]{1,14}\s*$")


def create_app(db_path: str | Path | None = None, *, lastfm_factory: Callable | None = None,
               musicbrainz_factory: Callable | None = None, auto_update: bool = False) -> FastAPI:
    """The factories replace the real last.fm / MusicBrainz clients (tests pass fakes).
    `auto_update` pulls new scrobbles from last.fm at startup, gated to 3 runs a day (updater.py);
    it is off by default so tests and tools never reach the network, and `serve` turns it on."""
    path = Path(db_path or config.DB_PATH)
    db.connect(path).close()  # apply migrations at startup
    job = jobs.EnrichJob(path, lastfm_factory=lastfm_factory, musicbrainz_factory=musicbrainz_factory)
    auto = updater.AutoUpdater(path, lastfm_factory=lastfm_factory)

    @asynccontextmanager
    async def lifespan(_app):
        if auto_update:
            try:  # the updater is optional: a bad settings file must not stop the server starting
                await run_in_threadpool(auto.start_if_due)
            except Exception:
                logging.getLogger("uvicorn.error").exception("couldn't start the last.fm updater")
        yield
        await run_in_threadpool(auto.shutdown)
        job.shutdown()  # finish the current item, then stop

    app = FastAPI(title="Music Taste Center", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.enrich_job = job
    app.state.updater = auto

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

    db_uri = path.resolve().as_uri() + "?mode=ro"

    def data_version() -> str | None:
        """Changes whenever scrobbles or metadata change (any process: UI, CLI, updater)."""
        try:
            with closing(sqlite3.connect(db_uri, uri=True, timeout=1)) as ro:
                return ".".join(map(str, db.versions(ro)))
        except sqlite3.Error:
            return None

    @app.get("/api/version")
    def version():
        """Cheap check the UI makes on every page change; the X-Data-Version header carries it."""
        return {"version": data_version()}

    @app.get("/api/overview")
    def overview(c=Conn):
        return insights.overview(c)

    @app.get("/api/summary")
    def period_summary(start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE), c=Conn):
        return insights.summary(c, start, end)

    @app.get("/api/activity")
    def period_activity(start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE), c=Conn):
        return insights.activity(c, start, end)

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

    @app.get("/api/library")
    def library(kind: str = Query("artist", pattern="^(artist|track|album|genre)$"),
                start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE),
                q: str = Query("", max_length=200), sort: str = Query("plays", pattern="^[a-z]{1,20}$"),
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), artist: int | None = Query(None, ge=1), c=Conn):
        return insights.library(c, kind, start, end, q.strip(), sort, limit, offset, artist_id=artist)

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

    @app.get("/api/insights/{kind}")
    def insight_list(kind: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), c=Conn):
        if kind not in insights.INSIGHT_KINDS:
            raise HTTPException(404, "unknown insight")
        return insights.insight_list(c, kind, limit, offset)

    @app.get("/api/loved/gap")
    def loved_gap(c=Conn):
        """Genres, decades and artists over- and under-represented among loved tracks vs plays."""
        return taste_gap.taste_gap(c)

    @app.get("/api/graph")
    def graph(n: int = Query(120, ge=10, le=400), per_node: int = Query(6, ge=1, le=20), c=Conn):
        return insights.graph(c, n, per_node)

    @app.get("/api/genres")
    def genre_profile(start: str | None = Query(None, pattern=DATE), end: str | None = Query(None, pattern=DATE),
                      limit: int = Query(40, ge=1, le=200), c=Conn):
        return insights.genres(c, start, end, limit)

    @app.get("/api/tags/{tag_id}")
    def tag(tag_id: int, c=Conn):
        return {**found(insights.tag(c, tag_id)), "months": rhythms.tag_months(c, tag_id)}

    @app.get("/api/rhythms")
    def rhythm_overview(kind: str = Query("genre", pattern="^(genre|place)$"), c=Conn):
        return rhythms.overview(c, kind)

    @app.get("/api/decades")
    def decade_overview(c=Conn):
        return decades.overview(c)

    @app.get("/api/decades/age")
    def album_age(c=Conn):
        return decades.album_age(c)

    @app.get("/api/decades/lag")
    def discovery_lag(c=Conn):
        return decades.discovery_lag(c, settings.birth_year())

    @app.get("/api/velocity")
    def artist_velocity(ids: str = Query(..., pattern=r"^\d{1,18}(,\d{1,18}){0,5}$"), c=Conn):
        """Weekly cumulative plays for up to six artists (`ids=1,2,3`), with their pace facts."""
        return velocity.velocity(c, [int(i) for i in ids.split(",")])

    @app.get("/api/decades/rhythms")
    def decade_rhythms(c=Conn):
        return decades.rhythm_overview(c)

    @app.get("/api/rhythms/artists")
    def seasonal_artists(c=Conn):
        return rhythms.seasonal_artists(c)

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
        settings.update(lastfm_api_key=req.key.strip())
        return {"has_key": True}

    @app.post("/api/metadata/key/verify")
    def verify_key():
        """One cheap last.fm call with the saved key; remembers success for the "works" chip."""
        key = settings.lastfm_api_key()
        if not key and not lastfm_factory:
            raise HTTPException(400, "no API key saved")
        from .lastfm import LastFm
        client = lastfm_factory() if lastfm_factory else LastFm(key, min_interval=config.LASTFM_MIN_INTERVAL_S)
        try:
            client.artist_info("Cher")
        except NotFound:
            pass  # last.fm answered, so the key is fine
        except Fatal as exc:
            settings.update(lastfm_key_ok=None, lastfm_key_ok_at=None)
            return {"works": False, "error": str(exc)}
        except Exception as exc:  # network trouble is not the key's fault
            return {"works": None, "error": f"couldn't reach last.fm: {exc}"}
        settings.mark_key_works(key)
        return {"works": True}

    @app.get("/api/updater")
    def updater_status(c=Conn):
        """When the startup scrobble update last ran, how it went, and when it may run next."""
        return {**updater.status(c), "running": auto.running, "skipped": auto.skipped}

    @app.get("/api/settings")
    def get_settings():
        """Non-secret settings for the UI. The API key itself is never sent back."""
        return {"lastfm_username": settings.lastfm_username(), "has_key": bool(settings.lastfm_api_key()),
                "key_works": settings.key_works(), "birth_year": settings.birth_year()}

    @app.put("/api/settings/username")
    def save_username(req: UsernameRequest):
        return {"lastfm_username": settings.set_lastfm_username(req.username)}

    @app.put("/api/settings/birth-year")
    def save_birth_year(req: BirthYearRequest):
        try:
            return {"birth_year": settings.set_birth_year(req.year)}
        except ValueError as exc:  # a year in the future
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/maintenance/duplicates")
    def duplicates(c=Conn):
        return maintenance.duplicate_candidates(c)

    @app.post("/api/maintenance/merge")
    def merge(req: MergeRequest, c=Conn):
        """Merge each source artist into the target, then rebuild derived tables once."""
        if req.target_id in req.source_ids:
            raise HTTPException(400, "the target can't also be a source")
        results = []
        failure: Exception | None = None
        try:
            for s in dict.fromkeys(req.source_ids):
                results.append(maintenance.merge_artists(c, s, req.target_id, rebuild=False))
        except Exception as exc:
            failure = exc
        if results:  # rebuild only when something was merged
            try:
                derive.rebuild(c)
            except Exception:
                if failure is None:  # otherwise the merge's own error is the one worth reporting
                    raise
        if isinstance(failure, LookupError):
            raise HTTPException(404, str(failure)) from None
        if failure:
            raise failure
        return {"target_id": req.target_id, "merged": results}

    @app.post("/api/maintenance/merge/preview")
    def merge_preview(req: MergeRequest, c=Conn):
        try:
            return maintenance.merge_preview(c, req.source_ids, req.target_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/api/maintenance/dismiss")
    def dismiss(req: DismissRequest, c=Conn):
        maintenance.dismiss(c, req.key)
        return {"ok": True}

    @app.get("/api/maintenance/aliases")
    def alias_list(c=Conn):
        return maintenance.aliases(c)

    @app.post("/api/maintenance/aliases")
    def alias_add(req: AliasRequest, c=Conn):
        try:
            return maintenance.add_alias(c, req.name, req.target_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.delete("/api/maintenance/aliases/{alias_id}")
    def alias_remove(alias_id: int, c=Conn):
        return {"removed": found(maintenance.remove_alias(c, alias_id) or None)}

    @app.get("/api/loved/unmatched")
    def loved_unmatched(c=Conn):
        """Loved tracks that don't match the library (with why and likely matches), the loved-title
        rules that link others, and how many tracks are loved in all."""
        return {"total": c.execute("SELECT COUNT(*) FROM loved_tracks").fetchone()[0],
                "items": maintenance.unmatched_loved(c), "rules": maintenance.loved_rules(c)}

    @app.post("/api/loved/rules")
    def loved_rule_add(req: LovedRuleRequest, c=Conn):
        try:
            return maintenance.add_loved_rule(c, req.artist, req.title, req.track_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.delete("/api/loved/rules/{rule_id}")
    def loved_rule_remove(rule_id: int = PathParam(..., ge=1, le=SQLITE_INT_MAX), c=Conn):
        return {"removed": found(maintenance.remove_loved_rule(c, rule_id) or None)}

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
        label = Path(unquote(request.headers.get("x-filename", "upload.csv"))).name[:200]

        def work():  # parse + ingest + rebuild take seconds: keep them off the event loop
            text, encoding = fsutil.decode(body)
            return ingest.import_csv_text(c, text, label=label, encoding=encoding)
        return await run_in_threadpool(work)

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
        is_api = request.url.path.startswith("/api/")
        # Read the version before the handler runs: a write that commits meanwhile then leaves the
        # header older than the body, so the UI refetches, rather than caching old data as new.
        v = await run_in_threadpool(data_version) if is_api else None
        response = await call_next(request)
        if not is_api:
            response.headers["Cache-Control"] = "no-cache"
        elif v is not None:
            response.headers["X-Data-Version"] = v  # the UI drops its cache when this changes
        return response

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(config.STATIC_DIR / "index.html")

    return app
