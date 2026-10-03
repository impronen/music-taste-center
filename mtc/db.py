"""SQLite connection and numbered migrations (mtc/migrations/NNN_*.sql, applied in order)."""
import sqlite3
from pathlib import Path

from . import config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A generous busy timeout: the background metadata fetch writes while imports and reads run.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        number = int(file.name.split("_", 1)[0])
        if number <= version:
            continue
        sql = file.read_text(encoding="utf-8")
        # One transaction per migration, so a failing script leaves user_version untouched.
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number:d};\nCOMMIT;")


# Version counters for caches: "scrobbles" changes with imports, rebuilds and merges; "tags"
# with every metadata write (tag fetch, not-found lookups, merges). Bump inside the writing
# transaction so readers never see new data with an old version.
VERSION_KEYS = ("scrobbles_version", "tags_version")


def bump(conn: sqlite3.Connection, *keys: str) -> None:
    for key in keys:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, '1')"
                     " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (key,))


def versions(conn: sqlite3.Connection) -> tuple[int, int]:
    v = dict(conn.execute("SELECT key, value FROM meta WHERE key IN (?, ?)", VERSION_KEYS))
    return tuple(int(v.get(k) or 0) for k in VERSION_KEYS)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
