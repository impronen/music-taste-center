"""Background run of the year in review for the web UI: at most one at a time, in a thread
(same shape as jobs.EnrichJob). The CLI (`python -m mtc wrapped YEAR`) uses the same pipeline."""
import threading
import time
from pathlib import Path

from . import db, settings, wrapped_run
from .jobs import Busy
from .webapi import Transport


class WrappedJob:
    def __init__(self, db_path: str | Path, out_root: Path, *, transport: Transport | None = None):
        self.db_path = db_path
        self.out_root = Path(out_root)
        self._transport = transport
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = {"state": "idle"}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def out_dir(self, year: int) -> Path:
        return self.out_root / str(year)

    def start(self, year: int, *, art: bool = True, png: bool = True) -> dict:
        with self._lock:
            if self.running:
                raise Busy("a wrapped is already being made")
            self._stop.clear()
            self._state = {"state": "running", "year": year, "phase": "data", "done": 0, "total": 1, "error": None,
                           "started_at": int(time.time()), "finished_at": None, "summary": None}
            self._thread = threading.Thread(target=self._run, args=(year, art, png, settings.birth_year()),
                                            name="mtc-wrapped", daemon=True)
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
            return dict(self._state)

    def _run(self, year: int, art: bool, png: bool, birth_year: int | None) -> None:
        final, error, summary = "done", None, None
        conn = None
        try:
            conn = db.connect(self.db_path)
            summary = wrapped_run.generate(conn, year, self.out_dir(year), art=art, png=png, birth_year=birth_year,
                                           transport=self._transport, progress=self._progress, stop=self._stop.is_set)
        except wrapped_run.Stopped:
            final = "stopped"
        except Exception as exc:  # tell the UI instead of dying silently
            final, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                conn.close()
        with self._lock:
            self._state.update(state=final, error=error, summary=summary, finished_at=int(time.time()), phase=None)

    def _progress(self, phase: str, done: int, total: int) -> None:
        with self._lock:
            self._state.update(phase=phase, done=done, total=total)
