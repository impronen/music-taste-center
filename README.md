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
.venv/bin/python -m tests.synthetic --rhythms > /tmp/demo.csv   # --rhythms adds seasonal habits
MTC_DB=/tmp/demo.db .venv/bin/python -m mtc import /tmp/demo.csv
MTC_DB=/tmp/demo.db .venv/bin/python app.py
```

## What's in it

| View | What it shows |
|---|---|
| **Overview** | Any period: last 7/30/90 days, last year, all time, a calendar year or month, or a custom date range. Shows totals with the change vs the previous period, scrobbles per day (up to 120 days) or per month, top artists, tracks and albums, new discoveries, and a listening clock. Also all-time *novelty* (share of plays going to artists discovered in the previous 12 months) and recent plays |
| **Library** | Every artist, sortable and filterable, plus top artists, tracks, albums or genres for any period (the same period bar, or a day or month clicked on a chart) |
| **Artist** | Plays per month, top tracks and albums, *how you discovered them* (the artist you were playing right before), who they *led you to*, artists *listened alongside*, and time of day |
| **Connections** | Force graph of your top artists, linked by co-listening, with taste clusters found automatically |
| **Eras** | Per year: top artists, the *signature* artist (most over-represented compared with all time), and the biggest new discovery |
| **Cleanup** | Merge artists that are spelled in more than one way, with suggested duplicates and name rules that fix future imports |
| **Rhythms** | How genres (or places) move through the year, the week and the day: a genre × month heatmap, what stands out each season, time of day, weekdays vs weekends, seasonal artists (with "coming up"), genre drift per year, and how varied your mix is |
| **Insights** | Rediscover (recommendations from your own past), on the rise, forgotten favourites, obsessions, staying power, gateways, binges, one-track artists, deep dives |

### How the connections work

- **Sessions.** A gap of more than 30 minutes between scrobbles starts a new session.
- **Links.** Two artists are linked when they appear in the same sessions. The score is the Ochiai coefficient `shared / sqrt(sessions_a × sessions_b)`, so a huge artist doesn't link to everything. Very long shuffle sessions only count their 30 most-played artists, and a link needs at least 3 shared sessions.
- **Clusters.** Weighted label propagation over each artist's strongest links.
- **Gateways.** An artist's gateway is whatever you played immediately before your first listen in the same session. Artists first heard in the first 30 days of your history are treated as already known, not discovered.
- **Comparisons.** A period is compared with the one just before it: the same calendar months for years and months (2024 vs 2023), otherwise the same number of days.
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
  maintenance.py artist merges, name rules (artist_aliases) and duplicate suggestions
  rhythms.py     cyclical patterns: seasons, time of day/week, seasonal artists, drift, diversity
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

## How the rhythms work

Rhythms need genre tags (Import → Fetch tags & covers). Every view shows what share of your plays it is based on.

- **Genre-weighted plays.** As in the genre profile, an artist's plays are split across its top 5 genre tags. For places ("finnish"), artists that were looked up but have no place tag count as "elsewhere".
- **Lift.** A bucket (a month, a season, a part of the day) is compared with *that same year's* mix: lift = plays / expected plays. 1,5× means half as much again as usual. A genre that simply grew over the years therefore doesn't look seasonal, and partial first and last years don't skew anything. Small buckets are pulled towards 1 (20 pseudo-plays), and cells with fewer than 30 expected plays are left blank.
- **Recurrence.** "In 5 of 6 winters" counts the years in which the bucket was above that year's own expectation. A season lists a genre only if it is at least 1,2× and held in at least half of those years. Winter is December–February, and December counts towards the next year's winter.
- **Seasonal artists.** Each play becomes a point on a yearly circle (day of the year), with every year weighted equally. An artist is seasonal when the plays cluster tightly enough (mean resultant length ≥ 0,5), in at least 3 years, with each year's peak within 30 days of the overall peak in at least 60 % of the years. *Coming up* means the season starts within 4 weeks of your newest scrobble.
- **Diversity** is the "effective number of genres", exp(Shannon entropy) of each month's genre mix: 4,0 is as varied as four equally played genres.
- **Speed.** One grouped pass over the scrobbles feeds every view. Results are cached until the next import, rebuild, merge or tag fetch: about 0,3 s at 260k scrobbles, then instant.

## Cleaning up duplicate artists

last.fm data has spelling variants of the same artist ("Sunn 0)))" with a zero vs "Sunn O)))"). On the **Cleanup** page, or via **Merge…** on an artist page, merge the wrong spelling into the right one:

- **Everything moves.** All scrobbles go to the artist you keep. Tracks and albums with the same (case-insensitive) title are combined, and a play scrobbled under both spellings at the same minute is kept once. Derived tables are rebuilt.
- **A name rule stays.** Future imports (CSV or API) of the merged spelling go straight to the kept artist, and re-importing an old export doesn't bring the duplicate back. Rules follow chained merges. You can also add a rule for a spelling you haven't imported yet. Removing a rule doesn't split artists that were already merged.
- **Suggestions.** Artists whose names match when accents, punctuation, 0/o, `&`/"and" and a leading "the" are ignored, or that last.fm autocorrects to the same name (after a tag fetch). It suggests keeping last.fm's spelling, otherwise the most played one. **Not the same** hides a group.
- **Merges can't be undone**, so the button asks for a second click.

```sh
.venv/bin/python -m mtc duplicates                         # list suggestions (* = suggested keeper)
.venv/bin/python -m mtc merge-artist "Sunn 0)))" "Sunn O)))"   # merge SOURCE into TARGET
```

## Live updater (last.fm API)

When the server starts it pulls your new scrobbles from last.fm in a background thread, so the UI opens right away. It needs your username and API key (**Import → last.fm account**, or `python -m mtc set-user NAME` and `set-key KEY`; the env vars `LASTFM_USER` and `LASTFM_API_KEY` override the file).

- **At most 3 runs in any 24 hours.** Each attempt, successful or not, is timestamped in the database (`meta`, key `updater_attempts`), so restarting the server a few times in a row doesn't hammer the API. A start with no username or key doesn't count.
- **What it fetches:** everything after your newest stored scrobble, minus a day of overlap for late offline scrobbles (`user.getRecentTracks`, 200 per page, the "now playing" track skipped). An empty library fetches the whole history. All pages are read before anything is stored, so a failure half way changes nothing and the next run starts from the same point. Duplicates are ignored, so the overlap is harmless.
- **See it:** `GET /api/updater` (last result, runs in the window, next allowed time) or `python -m mtc update --status`.
- **Run it by hand:** `python -m mtc update` (same limit; `--force` ignores it).
- **Turn it off:** `python -m mtc serve --no-update`, or `MTC_AUTO_UPDATE=0`. `create_app` leaves it off unless `auto_update=True` is passed, so tests never reach the network.

The code is `mtc/updater.py` and `LastFm.recent_tracks_page`; everything goes through `ingest.ingest_records` with `source="lastfm-api"`. Open pages notice the new scrobbles on the next page change (the `X-Data-Version` header).

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
