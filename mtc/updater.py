"""Pull new scrobbles from last.fm (user.getRecentTracks) when the server starts.

The gate, counted from attempt timestamps kept in the `meta` table so restarting the server (or the
CLI) can't hammer the API: at most `MAX_RUNS_PER_DAY` attempts per local calendar day (`config.TZ`),
and at least `COOLDOWN_S` between two attempts, also across midnight.

What to fetch: everything after the newest stored scrobble, minus a day of overlap (offline
scrobbles can arrive late). Overlap is harmless: ingest ignores duplicates. last.fm pages come
newest first, so a run reads all its pages *before* ingesting anything; a failure half way
changes nothing and the next run starts from the same point.
"""
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from . import config, db, derive, ingest, settings
from .webapi import ApiError

MAX_RUNS_PER_DAY = 3
COOLDOWN_S = 4 * 3600
KEEP_S = 2 * 24 * 3600  # attempts older than this can't affect the gate
OVERLAP_S = 24 * 3600
PAGE_SIZE = 200  # getRecentTracks maximum
MAX_PAGES = 2000  # 400 000 scrobbles; a guard against a paging loop, not a real limit

ATTEMPTS_KEY = "updater_attempts"  # JSON list of unix seconds
LAST_KEY = "updater_last"  # JSON: result of the latest attempt


class Stopped(Exception):
    pass


def _attempts(conn, now: float) -> list[int]:
    try:
        values = json.loads(db.get_meta(conn, ATTEMPTS_KEY) or "[]")
    except json.JSONDecodeError:
        values = []
    return sorted(t for t in values if isinstance(t, int) and now - KEEP_S < t <= now)


def _day_bounds(now: float) -> tuple[int, int]:
    """Unix seconds of the start of the local calendar day containing `now`, and of the next one."""
    day = datetime.fromtimestamp(now, config.TZ).date()
    start = datetime.combine(day, dtime.min, config.TZ)
    return int(start.timestamp()), int(datetime.combine(day + timedelta(days=1), dtime.min, config.TZ).timestamp())


def status(conn, now: float | None = None) -> dict:
    """Whether a run is allowed now, when the next one is (and what holds it: "limit" = the day's
    runs are used up, "cooldown" = too soon after the last), and how the last one went."""
    now = time.time() if now is None else now
    recent = _attempts(conn, now)
    day_start, day_end = _day_bounds(now)
    today = [t for t in recent if t >= day_start]
    cooldown_end = recent[-1] + COOLDOWN_S if recent else 0
    capped = len(today) >= MAX_RUNS_PER_DAY
    next_at = max(cooldown_end, day_end if capped else 0)
    due = next_at <= now
    try:
        last = json.loads(db.get_meta(conn, LAST_KEY) or "null")
    except json.JSONDecodeError:
        last = None
    return {"due": due, "runs_today": len(today), "max_runs": MAX_RUNS_PER_DAY, "cooldown_s": COOLDOWN_S,
            "next_at": None if due else next_at, "reason": None if due else ("limit" if capped and day_end >= cooldown_end else "cooldown"),
            "last": last}


def fetch_new(client, user: str, since: int | None, stop: threading.Event | None = None) -> list[ingest.Scrobble]:
    """All scrobbles after `since` (unix seconds; None = the whole history), newest page first."""
    out, page = [], 1
    while True:
        if stop is not None and stop.is_set():
            raise Stopped()
        records, total_pages = client.recent_tracks_page(user, since, page, PAGE_SIZE)
        out.extend(records)
        if page >= total_pages:
            return out
        page += 1
        if page > MAX_PAGES:
            raise ApiError(f"more than {MAX_PAGES} pages; stopping")


def _claim(conn, now: int, force: bool) -> dict | None:
    """Check the gate and record this attempt in one write transaction, so the CLI and the server
    can't both slip under the limit. Returns the gate when the run isn't allowed, else None."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        gate = status(conn, now)
        if not gate["due"] and not force:
            conn.rollback()
            return gate
        db.set_meta(conn, ATTEMPTS_KEY, json.dumps([*_attempts(conn, now), now]))
        conn.commit()
        return None
    except BaseException:
        conn.rollback()
        raise


def _refund(conn, now: int) -> None:
    """A run that was stopped before it fetched anything shouldn't use up one of the day's runs."""
    with conn:
        db.set_meta(conn, ATTEMPTS_KEY, json.dumps([t for t in _attempts(conn, now) if t != now]))


def run_once(conn, client, user: str, *, now: float | None = None, force: bool = False,
             stop: threading.Event | None = None) -> dict:
    """One gated update. Returns {"state": "skipped" | "done" | "failed" | "stopped", ...}."""
    now = int(time.time() if now is None else now)
    blocked = _claim(conn, now, force)  # counted before the first request, so a crash mid-run still counts
    if blocked:
        return {"state": "skipped", "reason": blocked["reason"], "next_at": blocked["next_at"]}
    newest = conn.execute("SELECT MAX(ts) FROM scrobbles").fetchone()[0]
    since = max(0, min(newest, now) - OVERLAP_S) if newest else None  # a bogus future scrobble must not stall updates
    result: dict = {"at": now, "since": since}
    try:
        records = fetch_new(client, user, since, stop)
        added = ingest.ingest_records(conn, records, source="lastfm-api", label=user)["rows_added"] if records else 0
        # Also repairs a rebuild that an earlier run died in, after its scrobbles were already stored.
        if added or conn.execute("SELECT 1 FROM scrobbles WHERE session_id IS NULL LIMIT 1").fetchone():
            derive.rebuild(conn)
        result.update(state="done", read=len(records), added=added)
    except Stopped:
        result.update(state="stopped")
        _refund(conn, now)
    except ApiError as exc:  # includes Fatal: a bad or revoked key, which retrying can't fix
        result.update(state="failed", error=str(exc))
    except Exception as exc:  # leave a trace in the status instead of dying silently in a thread
        result.update(state="failed", error=f"{type(exc).__name__}: {exc}")
    with conn:
        db.set_meta(conn, LAST_KEY, json.dumps(result))
    return result


class AutoUpdater:
    """Runs `run_once` in a background thread, at most once at a time. `start_if_due` is called
    when the server starts; nothing else triggers it."""

    def __init__(self, db_path: str | Path, *, lastfm_factory: Callable | None = None):
        self.db_path = db_path
        self._lastfm_factory = lastfm_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.skipped: str | None = None  # why the last start_if_due didn't run

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_if_due(self) -> bool:
        user = settings.lastfm_username()
        if not user:
            self.skipped = "no last.fm username (python -m mtc set-user NAME)"
        elif not (self._lastfm_factory or settings.lastfm_api_key()):
            self.skipped = "no last.fm API key (python -m mtc set-key KEY)"
        elif self.running:
            self.skipped = "already running"
        else:
            conn = db.connect(self.db_path)
            try:
                gate = status(conn)
            finally:
                conn.close()
            if not gate["due"]:
                when = datetime.fromtimestamp(gate["next_at"], config.TZ).strftime("%H:%M")
                self.skipped = (f"{gate['runs_today']} runs today; next possible {when} tomorrow" if gate["reason"] == "limit"
                                else f"cooldown after the last run; next possible {when}")
            else:
                self.skipped = None
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, args=(user,), name="mtc-updater", daemon=True)
                self._thread.start()
                return True
        return False

    def _run(self, user: str) -> None:
        conn = db.connect(self.db_path)
        try:
            if self._lastfm_factory:
                client = self._lastfm_factory()
            else:
                from .lastfm import LastFm
                client = LastFm(settings.lastfm_api_key(), min_interval=config.LASTFM_MIN_INTERVAL_S)
            run_once(conn, client, user, stop=self._stop)
        except Exception as exc:  # e.g. building the client; run_once handles its own errors
            with conn:
                db.set_meta(conn, LAST_KEY, json.dumps({"at": int(time.time()), "state": "failed",
                                                        "error": f"{type(exc).__name__}: {exc}"}))
        finally:
            conn.close()

    def wait(self, timeout: float | None = None) -> None:
        """Block until the current run (if any) ends, without asking it to stop."""
        if self._thread is not None:
            self._thread.join(timeout)

    def shutdown(self, timeout: float = 5) -> None:
        self._stop.set()
        self.wait(timeout)
