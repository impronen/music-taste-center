# Taste Center

Local FastAPI + SQLite app (Python 3.14, vanilla JS in `static/`). See README for features and architecture.

## Commands (always the project `.venv`)
- Run: `.venv/bin/python app.py` (http://127.0.0.1:8765). The server does not auto-reload; restart after Python changes.
- Import: `.venv/bin/python -m mtc import file.csv`. Rebuild derived tables or local times: `python -m mtc rebuild`
- Account: `python -m mtc set-user NAME` (last.fm username, for the live updater; `settings.lastfm_username()`)
- Metadata: `python -m mtc set-key KEY`, then `python -m mtc enrich` (last.fm tags + MusicBrainz dates; resumable)
  - The UI's Import page does the same via `/api/metadata/key` and `/api/metadata/job` (`mtc/jobs.py`, a background thread)
- Tests: `TZ=Europe/Helsinki .venv/bin/python -m unittest discover -s tests -t .`
- JS syntax: `for f in static/*.js; do node --check "$f"; done` (`node --check a.js b.js` only checks the first file)

## Rules
- No personal data in the repo: `data/`, `*.db` and `*.csv` are gitignored. Tests and demos use `tests/synthetic.py` (fictional names). Never read or print the real `data/mtc.db` when debugging; use a copy or the synthetic DB via `MTC_DB`.
- Schema changes only through new numbered files in `mtc/migrations/`. Derived tables (`artist_stats`, `artist_links`, `scrobbles.session_id`) are rebuilt by `derive.rebuild` and never edited by hand.
- Every source (CSV, the last.fm updater in `mtc/updater.py`: 3 runs per calendar day with a 4 h cooldown, started by `create_app(auto_update=True)` only) goes through `ingest.ingest_records`. It reads all records before taking the write lock, so page the API first; run long work (imports, the updater) in a thread, never on the event loop. It applies the name rules in `artist_aliases`; merges go through `maintenance.merge_artists` only.
- External APIs go through `webapi.JsonApi` subclasses (throttle, User-Agent, retries). Tests use fake transports only, never the network. Don't lower `LASTFM_MIN_INTERVAL_S` (0.5) or `MUSICBRAINZ_MIN_INTERVAL_S` (1.1).
- `data/settings.json` holds the API key, username and optional birth year: never print the key or commit the file.
- Non-GET `/api` requests with a foreign `Origin` are refused (the server is reachable from any website via localhost); keep it that way.
- Writes that change what views show must bump `db.bump(conn, "scrobbles_version" | "tags_version")` inside their transaction; server caches (`rhythms._cached`) and the UI cache (`X-Data-Version`, checked via `/api/version` on every page change) depend on it.
- New tables with a foreign key to artists/tracks/albums must be handled by `maintenance.merge_artists` and listed in `maintenance.MERGE_HANDLES` (a test enforces this).
- SQL is always parameterized. In the UI, data goes through the `html` tagged template (escapes by default) or `textContent`.
- Numbers use Finnish formatting (`Fmt.int` gives `12 345`). No locale compact notation.
- Sizes are tokens too (`--fs-*` type scale with a 13px minimum, `--gap`, `--card-pad`, `--radius-*`); don't hard-code font sizes or radii. Side-by-side cards go in `.grid.cols-2/3/4/7-5`, which stretches a row to one height. Cards of very uneven length go in `.columns-3`.
- Lift heatmaps use the validated diverging tokens `--div-neg-*`, `--div-mid` and `--div-pos-*` through `Charts.matrix`. Rhythm statistics live in `mtc/rhythms.py`; keep the per-year expectation (it detrends) and the shrinkage when changing them.
- Colours are CSS tokens in `static/style.css` (Organic design system), defined once for both themes with `light-dark()`. Never add a second dark-theme block. Re-run the dataviz palette validator if you change `--series-*`, `--div-*` or `--seq-*`.
- Layout patterns: page head (kicker, Caprasimo h1, `.lead` sentence), cards without borders tinted by role (`.canvas` for big charts, `.accent-soft`/`.sage-soft` for story and time content), pill controls (`.seg`, `.btn`, `.chip`), `rankList()` rows that are whole links. Interactive elements must work from the keyboard (charts are one tab stop with arrow keys).
- UI changes: verify in the browser (Chrome MCP, or `javascript_tool` DOM checks) before calling them done.
