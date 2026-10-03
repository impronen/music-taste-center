# Taste Center

Local FastAPI + SQLite app (Python 3.14, vanilla JS in `static/`). See README for features and architecture.

## Commands (always the project `.venv`)
- Run: `.venv/bin/python app.py` (http://127.0.0.1:8765). The server does not auto-reload; restart after Python changes.
- Import: `.venv/bin/python -m mtc import file.csv`. Rebuild derived tables or local times: `python -m mtc rebuild`
- Account: `python -m mtc set-user NAME` (last.fm username, for the live updater; `settings.lastfm_username()`)
- Metadata: `python -m mtc set-key KEY`, then `python -m mtc enrich` (last.fm tags + MusicBrainz dates; resumable)
  - The UI's Import page does the same via `/api/metadata/key` and `/api/metadata/job` (`mtc/jobs.py`, a background thread)
- Tests: `TZ=Europe/Helsinki .venv/bin/python -m unittest discover -s tests -t .`
- JS syntax: `node --check static/*.js`

## Rules
- No personal data in the repo: `data/`, `*.db` and `*.csv` are gitignored. Tests and demos use `tests/synthetic.py` (fictional names). Never read or print the real `data/mtc.db` when debugging; use a copy or the synthetic DB via `MTC_DB`.
- Schema changes only through new numbered files in `mtc/migrations/`. Derived tables (`artist_stats`, `artist_links`, `scrobbles.session_id`) are rebuilt by `derive.rebuild` and never edited by hand.
- Every source (CSV, a future last.fm API updater) goes through `ingest.ingest_records`. It applies the name rules in `artist_aliases`; merges go through `maintenance.merge_artists` only.
- External APIs go through `webapi.JsonApi` subclasses (throttle, User-Agent, retries). Tests use fake transports only, never the network. Don't lower `LASTFM_MIN_INTERVAL_S` (0.5) or `MUSICBRAINZ_MIN_INTERVAL_S` (1.1).
- `data/settings.json` holds the API key and username: never print the key or commit the file.
- Non-GET `/api` requests with a foreign `Origin` are refused (the server is reachable from any website via localhost); keep it that way.
- SQL is always parameterized. In the UI, data goes through the `html` tagged template (escapes by default) or `textContent`.
- Numbers use Finnish formatting (`Fmt.int` gives `12 345`). No locale compact notation.
- Colours are CSS tokens in `static/style.css`, from the Friends onThe Web palette. Re-run the dataviz palette validator if you change the categorical slots.
- UI changes: verify in the browser (Chrome MCP, or `javascript_tool` DOM checks) before calling them done.
