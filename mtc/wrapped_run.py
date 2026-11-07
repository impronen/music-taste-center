"""The whole year-in-review pipeline in one call, shared by the CLI and the web job:
dataset (JSON + CSV), covers, then the report page and PNG cards. Progress and stop are callbacks,
so a caller can show a bar and cancel between steps (what is already written stays valid)."""
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import wrapped, wrapped_art, wrapped_render
from .webapi import Transport


class Stopped(Exception):
    """The caller asked to stop; files written so far are kept and a rerun continues."""


def generate(conn: sqlite3.Connection, year: int, out_dir: Path, *, art: bool = True, png: bool = True,
             birth_year: int | None = None, transport: Transport | None = None,
             progress: Callable[[str, int, int], None] | None = None,
             stop: Callable[[], bool] | None = None) -> dict:
    """Build everything into out_dir. Raises ValueError when the year has no plays, Stopped on request.
    Returns {"totals", "files", "covers", "png", "png_error"}."""
    say = progress or (lambda *_: None)

    def check():
        if stop and stop():
            raise Stopped()

    data = wrapped.build(conn, year, birth_year)
    if data.get("empty"):
        raise ValueError(f"No scrobbles in {year}.")
    say("data", 0, 1)
    files = wrapped.write(data, out_dir)
    say("data", 1, 1)
    covers = None
    if art:
        def art_step(i, n):
            say("covers", i, n)
            check()
        check()
        covers = wrapped_art.fetch(conn, data, out_dir, **({"transport": transport} if transport else {}), progress=art_step)
    check()
    png_error = None
    made_png = False
    if png:
        def card_step(i, n):
            say("cards", i, n)
            check()
        try:
            wrapped_render.cards(data, out_dir, progress=card_step)
            made_png = True
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            png_error = str(exc)
    if not made_png:
        wrapped_render.page(data, out_dir)
    return {"totals": data["totals"], "files": [p.name for p in files], "covers": covers, "png": made_png,
            "png_error": png_error}
