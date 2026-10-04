# Developer guide

Taste Center is a local FastAPI + SQLite app with a vanilla-JS UI (no build step). This page is for people changing the code; for installing and using the app, see the [README](../README.md), and for what the pages measure, [How it works](how-it-works.md).

## Layout

```
mtc/
  ingest.py      CSV parsing + ingest_records(): the single entry point for any source
  derive.py      sessions, artist stats, gateways, co-listening links (rebuilt after each import)
  enrich.py      resumable metadata fetch (artists, albums, release dates)
  jobs.py        background fetch for the UI's button (one at a time, progress, stop)
  updater.py     the startup scrobble updater (daily limit and cooldown)
  lastfm.py      last.fm client (read-only methods, JSON quirks)
  musicbrainz.py MusicBrainz client (release-group dates)
  webapi.py      shared throttled HTTP client with retries; transport injectable for tests
  tags.py        tag normalization and classification
  maintenance.py artist merges, name rules (artist_aliases) and duplicate suggestions
  rhythms.py     cyclical patterns: seasons, time of day/week, seasonal artists, drift, diversity
  decades.py     release decades, album age, discovery lag, decade rhythms and genres
  insights.py    all read queries
  settings.py    the gitignored data/settings.json (API key, username, optional birth year)
  api.py         FastAPI JSON endpoints (/api/docs) + static UI
  migrations/    numbered SQL migrations (PRAGMA user_version)
static/          vanilla JS UI (app.js router/views, charts.js SVG charts, graph.js force graph)
tests/           unittest suite + synthetic history generator (fictional names only)
docs/            this guide, how-it-works.md and the README screenshots
```

## Running and testing

Always use the project's `.venv`:

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python app.py                                                  # http://127.0.0.1:8765, no auto-reload: restart after Python changes
TZ=Europe/Helsinki .venv/bin/python -m unittest discover -s tests -t .   # the whole suite (the fixtures assume this zone)
for f in static/*.js; do node --check "$f"; done                         # JS syntax, one file at a time
```

The suite runs on Python 3.13 and 3.14. It never touches the network: the last.fm and MusicBrainz clients take injectable transports, and tests use fake ones.

## A fictional demo library

`tests/synthetic.py` generates an invented listening history (fictional names only) with taste clusters, discoveries, obsessions and, with `--rhythms`, seasonal habits.

```sh
.venv/bin/python -m tests.synthetic --rhythms > demo.csv        # a CSV in the lastfm export format
.venv/bin/python -m tests.synthetic --demo-db demo.db           # a complete library: scrobbles + genre tags + release dates
MTC_DB=demo.db .venv/bin/python app.py serve --no-update
```

`--demo-db` refuses to write over an existing file. Use `--no-update` with a demo database so the live updater doesn't pull your real scrobbles into it. The README screenshots were taken from such a demo library.

## Configuration

| Env var | Default | |
|---|---|---|
| `MTC_DB` | `data/mtc.db` | database path |
| `MTC_SETTINGS` | `data/settings.json` | settings file path |
| `MTC_TZ` | `Europe/Helsinki` | zone for local hours and days. After changing it, run `python -m mtc rebuild` |
| `MTC_PORT` | `8765` | port used by `Taste Center.command` (or `serve --port`) |
| `MTC_AUTO_UPDATE` | on | `0` turns off the startup scrobble update |
| `LASTFM_USER` / `LASTFM_API_KEY` | from `data/settings.json` | override the saved username / API key |

## Rules of the road

The full list is in `CLAUDE.md`; the ones that matter most:

- **No personal data in the repo.** `data/`, `*.db` and `*.csv` are gitignored; tests and demos use the synthetic generator. Never commit `data/settings.json`.
- **Schema changes** only through new numbered files in `mtc/migrations/`. Derived tables (`artist_stats`, `artist_links`, `scrobbles.session_id`) are rebuilt by `derive.rebuild` and never edited by hand.
- **Every source goes through `ingest.ingest_records`**, and long work runs in a thread, never on the event loop.
- **External APIs** go through `webapi.JsonApi` subclasses (throttle, User-Agent, retries). Don't lower the minimum intervals in `mtc/config.py`.
- **Writes that change what views show** must bump `db.bump(conn, "scrobbles_version" | "tags_version")`; the server caches and the UI cache depend on it.
- SQL is always parameterized. In the UI, data goes through the `html` tagged template or `textContent`.
- Numbers use Finnish formatting in the UI (`Fmt.int` gives `12 345`); no locale compact notation.

## Design

The UI uses the "Organic" design system: a cream background, terracotta (#C67139) for data and primary actions, sage (#7A8A5E) for discovery, time and "more than usual", with Caprasimo headings over Figtree text. Both fonts are self-hosted in `static/fonts/` (SIL Open Font License, see the `OFL-*.txt` files there), so the app loads nothing from the internet at runtime.

- **One theme definition.** Every colour is a token on `:root`, defined once for light and dark with `light-dark()`. The ◐ button overrides the system setting.
- **Data colours are validated.** The cluster colours (terracotta, blue, sage, plum, ochre) and the lift scale (terracotta = less, sand = as usual, sage = more) keep the design's hues. Their lightness and chroma were adjusted until they passed the dataviz palette validator in both themes: colour-blind separation between neighbouring clusters, and visible steps that clear the background. The graph also dims every cluster except the selected one, so it doesn't rely on colour alone.
- **Accessible by default.**
  - Charts are focusable: arrow keys read values, and Enter opens the day or month.
  - Sortable columns are buttons with `aria-sort`.
  - Toggles expose `aria-pressed`.
  - Focus moves to the page heading after navigation.
  - Muted text keeps at least 4.5:1 contrast.
