"""Background metadata fetch for the web UI: at most one enrich.run at a time, in a thread.

The CLI (`python -m mtc enrich`) and this job share enrich.run, so both are resumable and
safe to mix; a CLI run alongside a UI run would only duplicate a few lookups.
"""
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from . import config, db, enrich, settings
from .webapi import Fatal

PHASES = ("artists", "albums", "releases")
# Nominal seconds per item until a phase has its own measured pace.
NOMINAL_S = {"artists": 2 * config.LASTFM_MIN_INTERVAL_S + 0.3, "albums": 2 * config.LASTFM_MIN_INTERVAL_S + 0.3,
             "releases": 1.5 * config.MUSICBRAINZ_MIN_INTERVAL_S}


class Busy(Exception):
    pass


class EnrichJob:
    def __init__(self, db_path: str | Path, *, lastfm_factory: Callable | None = None,
                 musicbrainz_factory: Callable | None = None):
        self.db_path = db_path
        self._lastfm_factory = lastfm_factory
        self._musicbrainz_factory = musicbrainz_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = {"state": "idle"}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, artists: int | None = None, albums: int | None = None, releases: int | None = None) -> dict:
        limits = {"artists": artists, "albums": albums, "releases": releases}
        with self._lock:
            if self.running:
                raise Busy("a fetch is already running")
            conn = db.connect(self.db_path)
            try:
                todo = {
                    "artists": len(enrich.pending_artists(conn, artists)) if artists != 0 else 0,
                    "albums": len(enrich.pending_albums(conn, albums)) if albums != 0 else 0,
                    "releases": len(enrich.pending_releases(conn, releases)) if releases != 0 else 0,
                }
            finally:
                conn.close()
            if (todo["artists"] or todo["albums"]) and not self._lastfm_factory and not settings.lastfm_api_key():
                raise Fatal("add your last.fm API key first")
            if releases != 0:
                # Albums found on last.fm in this run get a release-date lookup too.
                todo["releases"] += todo["albums"] if releases is None else 0
                if releases is not None:
                    todo["releases"] = min(todo["releases"], releases)
            self._stop.clear()
            self._state = {
                "state": "running", "started_at": int(time.time()), "finished_at": None, "error": None,
                "phase": None, "current": None, "log": deque(maxlen=8),
                "phases": {name: {"state": "skipped" if limits[name] == 0 else "queued", "done": 0,
                                  "total": todo[name], "counts": {}, "started": None} for name in PHASES},
            }
            self._thread = threading.Thread(target=self._run, args=(limits,), name="mtc-enrich", daemon=True)
            self._thread.start()
        return self.snapshot()

    def stop(self) -> dict:
        with self._lock:
            if self.running:
                self._stop.set()
                self._state["state"] = "stopping"
        return self.snapshot()

    def shutdown(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
            if "phases" not in s:
                return s
            s["eta_s"] = self._eta(s) if s["state"] in ("running", "stopping") else None
            s["phases"] = {k: {**{f: x for f, x in v.items() if f != "started"}, "counts": dict(v["counts"])}
                           for k, v in s["phases"].items()}
            s["log"] = list(s["log"])
        return s

    # ------------------------------------------------------------ worker side

    def _run(self, limits: dict) -> None:
        final, error = "done", None
        conn = db.connect(self.db_path)
        try:
            summary = enrich.run(
                conn,
                lastfm=self._lastfm_factory() if self._lastfm_factory else None,
                musicbrainz=self._musicbrainz_factory() if self._musicbrainz_factory else None,
                **limits, log=lambda _: None, progress=self._progress, stop=self._stop)
            final = "stopped" if summary.get("stopped") else "done"
        except Fatal as exc:
            final, error = "failed", str(exc)
        except Exception as exc:  # keep the UI informed instead of dying silently
            final, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            conn.close()
        with self._lock:
            st = self._state
            st.update(state=final, error=error, finished_at=int(time.time()), current=None)
            for ph in st["phases"].values():
                if ph["state"] == "running":
                    ph["state"] = "done" if final == "done" else final
            st["phase"] = None

    def _progress(self, phase: str, done: int, total: int, detail: str, status: str | None) -> None:
        with self._lock:
            st = self._state
            ph = st["phases"][phase]
            if done == 0:
                prev = st["phase"]
                if prev and st["phases"][prev]["state"] == "running":
                    st["phases"][prev]["state"] = "done"
                st["phase"] = phase
                ph.update(state="running", total=total, started=time.monotonic())
                return
            ph["done"] = done
            ph["counts"][status] = ph["counts"].get(status, 0) + 1
            st["current"] = detail
            st["log"].append({"phase": phase, "item": detail, "status": status})
            if done == total:
                ph["state"] = "done"

    @staticmethod
    def _eta(s: dict) -> int:
        seconds = 0.0
        for name, ph in s["phases"].items():
            if ph["state"] not in ("queued", "running"):
                continue
            pace = NOMINAL_S[name]
            if ph["state"] == "running" and ph["done"] >= 5 and ph["started"]:
                pace = (time.monotonic() - ph["started"]) / ph["done"]
            seconds += max(0, ph["total"] - ph["done"]) * pace
        return round(seconds)
