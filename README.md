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
  insights.py    all read queries
  api.py         FastAPI JSON endpoints (/api/docs) + static UI
  migrations/    numbered SQL migrations (PRAGMA user_version)
static/          vanilla JS UI (app.js router/views, charts.js SVG charts, graph.js force graph)
tests/           unittest suite + synthetic history generator (fictional names only)
```

## Adding a live updater (last.fm API)

`ingest.ingest_records(conn, records, source="lastfm-api", label=user)` accepts any iterable of `ingest.Scrobble(artist, track, ts, album, artist_mbid, track_mbid, album_mbid)`. An updater only needs to:

1. read the newest timestamp: `SELECT MAX(ts) FROM scrobbles`
2. page `user.getrecenttracks` with `from=<that ts>` (skip the `nowplaying` track, which has no date)
3. pass the results to `ingest_records`, then call `derive.rebuild(conn)`

Duplicates are ignored, so overlapping windows are harmless. The MBIDs from the API are stored, which will help with release tracking later.

## Configuration

| Env var | Default | |
|---|---|---|
| `MTC_DB` | `data/mtc.db` | database path |
| `MTC_TZ` | `Europe/Helsinki` | zone for local hours and days. After changing it, run `python -m mtc rebuild` |

## Tests

```sh
TZ=Europe/Helsinki .venv/bin/python -m unittest discover -s tests -t .
```

## Colours

The UI uses the [Friends onThe Web](https://www.colourlovers.com/palette/1499441/Friends-onThe-Web-w) palette. Teal (#149095) carries data, orange (#E04807) is the brand highlight, and lime → green → deep green forms the heat ramp. Cluster colours extend the palette with a few extra hues. Their order is checked for colour-blind separation between neighbouring colours in both themes. Only the first three cluster colours stay distinct when every pair is compared, so the graph also lets you click a cluster to highlight it.
