"""CLI: python -m mtc [serve|import|rebuild|enrich|update|set-key|set-user|set-birth-year|merge-artist|duplicates|stats]."""
import argparse
import json
import os
import sys

from . import config, db, derive, enrich, ingest, insights, maintenance, settings, updater


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mtc", description="Music Taste Center")
    p.add_argument("--db", default=None, help=f"database path (default {config.DB_PATH}, env MTC_DB)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the web UI (default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-update", action="store_true", help="don't pull new scrobbles from last.fm at startup")

    i = sub.add_parser("import", help="import one or more lastfm-to-csv exports")
    i.add_argument("files", nargs="+")

    sub.add_parser("rebuild", help="recompute local times and derived tables (after changing MTC_TZ)")

    e = sub.add_parser("enrich", help="fetch tags (last.fm) and release dates (MusicBrainz), most-played first")
    e.add_argument("--artists", type=int, default=None, metavar="N", help="max artists (default all pending, 0 = skip)")
    e.add_argument("--albums", type=int, default=None, metavar="N", help="max albums (default all pending, 0 = skip)")
    e.add_argument("--releases", type=int, default=None, metavar="N", help="max release-date lookups (0 = skip)")
    e.add_argument("--refresh-days", type=float, default=config.METADATA_TTL_DAYS,
                   help=f"refetch items older than this (default {config.METADATA_TTL_DAYS})")
    e.add_argument("--status", action="store_true", help="only print coverage")

    up = sub.add_parser("update", help="pull new scrobbles from last.fm (at most 3 runs per day, 4 hours apart)")
    up.add_argument("--force", action="store_true", help="ignore the daily limit")
    up.add_argument("--status", action="store_true", help="only print when it last ran and may run next")

    k = sub.add_parser("set-key", help="store your last.fm API key in data/settings.json")
    k.add_argument("api_key")
    u = sub.add_parser("set-user", help="store your last.fm username in data/settings.json")
    u.add_argument("username")
    b = sub.add_parser("set-birth-year", help="store your birth year (optional; Decades page: records from before you were born)")
    b.add_argument("year", nargs="?", type=int)
    b.add_argument("--clear", action="store_true", help="remove it")
    m = sub.add_parser("merge-artist", help="merge SOURCE into TARGET and keep a name rule for future imports")
    m.add_argument("source")
    m.add_argument("target")
    sub.add_parser("duplicates", help="list artists that look like spelling variants of each other")
    sub.add_parser("stats", help="print a short overview")

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "serve":
        import uvicorn

        from .api import create_app

        auto = not getattr(args, "no_update", False) and os.environ.get("MTC_AUTO_UPDATE", "1") != "0"
        uvicorn.run(create_app(args.db, auto_update=auto), host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8765))
        return 0

    conn = db.connect(args.db)
    if cmd == "import":
        for f in args.files:
            r = ingest.import_csv(conn, f)
            print(f"{r['label']}: read {r['rows_read']}, added {r['rows_added']}, duplicates {r['duplicates']},"
                  f" skipped {r['rows_skipped']} ({r['encoding']})")
    elif cmd == "rebuild":
        ingest.recompute_local_time(conn)
        print(json.dumps(derive.rebuild(conn)))
    elif cmd == "enrich":
        if not args.status:
            from .webapi import Fatal

            try:
                summary = enrich.run(conn, artists=args.artists, albums=args.albums, releases=args.releases,
                                     refresh_days=args.refresh_days)
                print(json.dumps(summary))
            except KeyboardInterrupt:
                print("\nStopped. Everything fetched so far is saved; run again to continue.")
            except Fatal as exc:
                print(f"Stopped: {exc}")
                return 1
        print(json.dumps(enrich.status(conn), indent=2))
    elif cmd == "update":
        if not args.status:
            user = settings.lastfm_username()
            if not user or not settings.lastfm_api_key():
                print("Needs both: python -m mtc set-user NAME and python -m mtc set-key KEY")
                return 1
            from .lastfm import LastFm

            r = updater.run_once(conn, LastFm(settings.lastfm_api_key(), min_interval=config.LASTFM_MIN_INTERVAL_S),
                                 user, force=args.force)
            print(json.dumps(r))
            if r["state"] == "failed":
                return 1
        print(json.dumps(updater.status(conn), indent=2))
    elif cmd == "set-key":
        settings.update(lastfm_api_key=args.api_key.strip())
        print(f"Saved to {config.SETTINGS_PATH}")
    elif cmd == "set-user":
        try:
            print(f"Saved {settings.set_lastfm_username(args.username)} to {config.SETTINGS_PATH}")
        except ValueError as exc:
            print(exc)
            return 1
    elif cmd == "set-birth-year":
        if (args.year is None) == (not args.clear):
            print("give a year, or --clear (not both)")
            return 1
        try:
            year = settings.set_birth_year(None if args.clear else args.year)
        except ValueError as exc:
            print(exc)
            return 1
        print(f"{'Cleared' if year is None else f'Saved {year}'} in {config.SETTINGS_PATH}")
    elif cmd == "merge-artist":
        ids = []
        for name in (args.source, args.target):
            row = conn.execute("SELECT id FROM artists WHERE name_key = ?", (ingest.key(name),)).fetchone()
            if row is None:
                print(f"No artist named {name!r}")
                return 1
            ids.append(row[0])
        try:
            r = maintenance.merge_artists(conn, *ids)
        except ValueError as exc:
            print(exc)
            return 1
        print(f"Merged {r['source']} into {r['target']}: {r['scrobbles_moved']} scrobbles moved,"
              f" {r['duplicates_dropped']} duplicates dropped. Future imports of {r['source']!r} go to {r['target']!r}.")
    elif cmd == "duplicates":
        for g in maintenance.duplicate_candidates(conn):
            print("  ".join(f"{a['name']} ({a['plays']})" + (" *" if a["id"] == g["target_id"] else "") for a in g["artists"]))
    elif cmd == "stats":
        o = insights.overview(conn)
        print(json.dumps({k: v for k, v in o.items() if k != "recent_top"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
