"""CLI: python -m mtc [serve|import|rebuild|enrich|set-key|set-user|stats]."""
import argparse
import json
import sys

from . import config, db, derive, enrich, ingest, insights, settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mtc", description="Music Taste Center")
    p.add_argument("--db", default=None, help=f"database path (default {config.DB_PATH}, env MTC_DB)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the web UI (default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)

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

    k = sub.add_parser("set-key", help="store your last.fm API key in data/settings.json")
    k.add_argument("api_key")
    u = sub.add_parser("set-user", help="store your last.fm username in data/settings.json")
    u.add_argument("username")
    sub.add_parser("stats", help="print a short overview")

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db), host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", 8765))
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
    elif cmd == "set-key":
        settings.save({**settings.load(), "lastfm_api_key": args.api_key.strip()})
        print(f"Saved to {config.SETTINGS_PATH}")
    elif cmd == "set-user":
        try:
            print(f"Saved {settings.set_lastfm_username(args.username)} to {config.SETTINGS_PATH}")
        except ValueError as exc:
            print(exc)
            return 1
    elif cmd == "stats":
        o = insights.overview(conn)
        print(json.dumps({k: v for k, v in o.items() if k != "recent_top"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
