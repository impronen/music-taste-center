# Taste Center

A local, ad-free last.fm with data tools. Import your scrobble history, browse it, and see how your taste connects and moves over time. Everything stays on your machine in one SQLite file (`data/mtc.db`).

## Quick start

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 1. Export your history with https://github.com/impronen/lastfm-to-csv
# 2. Import it (or drag the file onto the Import page)
.venv/bin/python -m mtc import ~/Downloads/yourname.csv

# 3. Browse
.venv/bin/python app.py          # http://127.0.0.1:8765
```

Or double-click **`Taste Center.command`** in Finder. It sets up `.venv` on first run, starts the server and opens the browser. If the server is already running, it just opens the browser. Close the Terminal window to stop it. You can drag the file to the Dock for one-click access.

Re-importing a newer full export is safe. A scrobble is identified by (time, artist, track), so rows already in the database are skipped.

Try it without your own data:

```sh
.venv/bin/python -m tests.synthetic > /tmp/demo.csv
MTC_DB=/tmp/demo.db .venv/bin/python -m mtc import /tmp/demo.csv
MTC_DB=/tmp/demo.db .venv/bin/python app.py
```

## What's in it

| View | What it shows |
|---|---|
| **Overview** | Totals, streaks, scrobbles per month, *novelty* (share of plays going to artists discovered in the previous 12 months), a listening clock (weekday × hour), and the top artists, tracks and albums of the last 30 days |
| **Library** | Every artist, sortable and filterable, plus top artists, tracks or albums for any period (last 30/90/365 days, a year, or a month clicked on a chart) |
| **Artist** | Plays per month, top tracks and albums, *how you discovered them* (the artist you were playing right before), who they *led you to*, artists *listened alongside*, and time of day |
| **Connections** | Force graph of your top artists, linked by co-listening, with taste clusters found automatically |
| **Eras** | Per year: top artists, the *signature* artist (most over-represented compared with all time), and the biggest new discovery |
| **Insights** | Rediscover (recommendations from your own past), on the rise, forgotten favourites, obsessions, staying power, gateways, binges, one-track artists, deep dives |

### How the connections work

- **Sessions.** A gap of more than 30 minutes between scrobbles starts a new session.
- **Links.** Two artists are linked when they appear in the same sessions. The score is the Ochiai coefficient `shared / sqrt(sessions_a × sessions_b)`, so a huge artist doesn't link to everything. Very long shuffle sessions only count their 30 most-played artists, and a link needs at least 3 shared sessions.
- **Clusters.** Weighted label propagation over each artist's strongest links.
- **Gateways.** An artist's gateway is whatever you played immediately before your first listen in the same session. Artists first heard in the first 30 days of your history are treated as already known, not discovered.
- **"Now"** means your newest scrobble, not today. An old export still gives sensible "recent" and "forgotten" results.

All tunables live in `mtc/config.py`.

## Layout

```
mtc/
  ingest.py      CSV parsing + ingest_records(): the single entry point for any source
  derive.py      sessions, artist stats, gateways, co-listening links (rebuilt after each import)
  enrich.py      resumable metadata fetch (artists, albums, release dates)
  jobs.py        background fetch for the UI's button (one at a time, progress, stop)
  lastfm.py      last.fm client (read-only methods, JSON quirks)
  musicbrainz.py MusicBrainz client (release-group dates)
  webapi.py      shared throttled HTTP client with retries; transport injectable for tests
  tags.py        tag normalization and classification
  insights.py    all read queries
  api.py         FastAPI JSON endpoints (/api/docs) + static UI
  migrations/    numbered SQL migrations (PRAGMA user_version)
static/          vanilla JS UI (app.js router/views, charts.js SVG charts, graph.js force graph)
tests/           unittest suite + synthetic history generator (fictional names only)
```

## Tags, covers, genres and release dates

In the app: **Import → Tags, covers & release dates**. Paste your last.fm API key once (get one at https://www.last.fm/api/account/create), then press **Fetch tags & covers**. The fetch runs in the server in the background, with progress shown on the page and as a badge on the Import tab. You can browse while it runs. **Stop**, or closing the app, keeps everything fetched so far, and the next fetch continues from there.

The same from a terminal:

```sh
# once: get a key at https://www.last.fm/api/account/create
.venv/bin/python -m mtc set-key YOUR_KEY        # stored in data/settings.json (gitignored), or set LASTFM_API_KEY
.venv/bin/python -m mtc enrich                  # everything pending, most-played first
.venv/bin/python -m mtc enrich --artists 200 --albums 0 --releases 0   # a smaller batch
.venv/bin/python -m mtc enrich --status         # coverage only
```

| Phase | Source | Calls | What it stores |
|---|---|---|---|
| artists | last.fm `artist.getInfo` + `artist.getTopTags` | 2 per artist | tags with weights (0–100), corrected name, MBID, global listeners and plays, bio summary |
| albums | last.fm `album.getInfo` + `album.getTopTags` | 2 per album with 3+ plays | tags, MBID, cover art URL, track count, plus a provisional release year from year tags |
| releases | MusicBrainz release lookup by MBID, or a search by name | 1–2 per album | original release date (release-group `first-release-date`) and type (Album/EP/Single) |

- **Why two sources.** last.fm's `album.getInfo` no longer returns a release date, even though its docs still show one. MusicBrainz is the source of truth for dates. A name search only counts as a match if the title matches exactly and the artist matches too.
- **Pacing.** last.fm runs at 2 requests/s, because it warns against "several calls per second". MusicBrainz runs at 1 request/s, its published limit. Both send an identifying User-Agent and retry with backoff on rate-limit or 5xx errors. A bad API key stops the run.
- **Resumable.** Every item is saved as soon as it's fetched, so Ctrl+C loses nothing. Re-running fetches only what's missing, what failed, or what is older than 120 days (`METADATA_TTL_DAYS`). A failed refresh never overwrites good data.
- **Tag hygiene.** Tags are classified as `genre`, `place` ("finnish"), `year`, `decade` or `other` ("seen live", "favorites"); see `mtc/tags.py`. A tag equal to the artist's own name is dropped, and spelling variants ("post rock" / "post-rock") merge.
- **Genre profile.** Each artist's plays are split across its top 5 genre tags in proportion to their weights. The result appears under Library → Genres, on per-genre pages, and in Eras.
- **Images.** Album covers come from last.fm and are loaded from its CDN. last.fm no longer serves artist photos (only a placeholder), so an artist page shows the cover of your most-played album.
- **What leaves your machine.** Only artist and album names, sent to last.fm and MusicBrainz, plus the cover image requests. last.fm's terms allow non-commercial use and at most 100 MB of cached data; this stores a few kB per artist.

## Adding a live updater (last.fm API)

Your username and API key are saved under **Import → last.fm account**, or with `python -m mtc set-user NAME` and `set-key KEY`. Read them with `settings.lastfm_username()` and `settings.lastfm_api_key()`. Both return `None` when unset, and the env vars `LASTFM_USER` and `LASTFM_API_KEY` override the file. To run on every app start, hook into the `lifespan` in `mtc/api.py` (or the launcher), preferably in a background thread like `mtc/jobs.py` so the UI opens right away.

`ingest.ingest_records(conn, records, source="lastfm-api", label=user)` accepts any iterable of `ingest.Scrobble(artist, track, ts, album, artist_mbid, track_mbid, album_mbid)`. An updater only needs to:

1. read the newest timestamp: `SELECT MAX(ts) FROM scrobbles`
2. page `user.getrecenttracks` with `from=<that ts>` (skip the `nowplaying` track, which has no date)
3. pass the results to `ingest_records`, then call `derive.rebuild(conn)`

Duplicates are ignored, so overlapping windows are harmless. The MBIDs from the API are stored, which will help with release tracking later.

## Configuration

| Env var | Default | |
|---|---|---|
| `MTC_DB` | `data/mtc.db` | database path |
| `LASTFM_USER` / `LASTFM_API_KEY` | from `data/settings.json` | override the saved username / API key |
| `MTC_SETTINGS` | `data/settings.json` | settings file path |
| `MTC_TZ` | `Europe/Helsinki` | zone for local hours and days. After changing it, run `python -m mtc rebuild` |

## Tests

```sh
TZ=Europe/Helsinki .venv/bin/python -m unittest discover -s tests -t .
```

## Colours

The UI uses the [Friends onThe Web](https://www.colourlovers.com/palette/1499441/Friends-onThe-Web-w) palette. Teal (#149095) carries data, orange (#E04807) is the brand highlight, and lime → green → deep green forms the heat ramp. Cluster colours extend the palette with a few extra hues. Their order is checked for colour-blind separation between neighbouring colours in both themes. Only the first three cluster colours stay distinct when every pair is compared, so the graph also lets you click a cluster to highlight it.
