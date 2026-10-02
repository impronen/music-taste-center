"""CLI: python -m mtc [serve|import|rebuild|stats]."""
import argparse
import json
import sys

from . import config, db, derive, ingest, insights


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
    elif cmd == "stats":
        o = insights.overview(conn)
        print(json.dumps({k: v for k, v in o.items() if k != "recent_top"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
