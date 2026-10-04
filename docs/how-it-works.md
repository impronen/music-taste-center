# How Taste Center works

The details behind the pages: what is measured, how, and what is sent to the internet. For installing and using the app, see the [README](../README.md).

- [Connections](#connections)
- [Tags, covers, genres and release dates](#tags-covers-genres-and-release-dates)
- [Rhythms](#rhythms)
- [Decades](#decades)
- [Cleaning up duplicate artists](#cleaning-up-duplicate-artists)
- [The live updater](#the-live-updater)

## Connections

- **Sessions.** A gap of more than 30 minutes between scrobbles starts a new session.
- **Links.** Two artists are linked when they appear in the same sessions. The score is the Ochiai coefficient `shared / sqrt(sessions_a × sessions_b)`, so a huge artist doesn't link to everything. Very long shuffle sessions only count their 30 most-played artists, and a link needs at least 3 shared sessions.
- **Clusters.** Weighted label propagation over each artist's strongest links.
- **Gateways.** An artist's gateway is whatever you played immediately before your first listen in the same session. Artists first heard in the first 30 days of your history are treated as already known, not discovered.
- **Comparisons.** A period is compared with the one just before it: the same calendar months for years and months (2024 vs 2023), otherwise the same number of days.
- **"Now"** means your newest scrobble, not today. An old export still gives sensible "recent" and "forgotten" results.

All tunables live in `mtc/config.py`.

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

## Rhythms

Rhythms need genre tags (Import → Fetch tags & covers). Every view shows what share of your plays it is based on.

- **Genre-weighted plays.** As in the genre profile, an artist's plays are split across its top 5 genre tags. For places ("finnish"), artists that were looked up but have no place tag count as "elsewhere".
- **Lift.** A bucket (a month, a season, a part of the day) is compared with *that same year's* mix: lift = plays / expected plays. 1,5× means half as much again as usual. A genre that simply grew over the years therefore doesn't look seasonal, and partial first and last years don't skew anything. Small buckets are pulled towards 1 (20 pseudo-plays), and cells with fewer than 30 expected plays are left blank.
- **Recurrence.** "In 5 of 6 winters" counts the years in which the bucket was above that year's own expectation. A season lists a genre only if it is at least 1,2× and held in at least half of those years. Winter is December–February, and December counts towards the next year's winter.
- **Seasonal artists.** Each play becomes a point on a yearly circle (day of the year), with every year weighted equally. An artist is seasonal when the plays cluster tightly enough (mean resultant length ≥ 0,5), in at least 3 years, with each year's peak within 30 days of the overall peak in at least 60 % of the years. *Coming up* means the season starts within 4 weeks of your newest scrobble.
- **Diversity** is the "effective number of genres", exp(Shannon entropy) of each month's genre mix: 4,0 is as varied as four equally played genres.
- **Speed.** One grouped pass over the scrobbles feeds every view. Results are cached until the next import, rebuild, merge or tag fetch: about 0,3 s at 260k scrobbles, then instant.

## Decades

The Decades page looks at *when the music you play was released*. It needs release dates (see above), so every section says what share of your plays it is based on: scrobbles without an album, and albums with fewer than three plays, can't be dated.

- **Release dates.** MusicBrainz gives the original release, not a reissue, as `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. When MusicBrainz has nothing, a year tag from last.fm can supply the year. For sums that need a day, a year-only date counts as 1 July and a year-month as the 15th. Years outside 1900–2100 are ignored as data errors.
- **Decades in vogue.** Plays by release year and decade, each listening year's decade mix, and a decade × listening-year heatmap. The heatmap is a *lift*, as in Rhythms: a decade's share in a year compared with its share of all your plays, with small cells pulled towards "as usual". "In vogue lately" compares the last 12 months with everything before them in the same way.
- **How old the music is.** The album's age on the day you played it, in whole days. Typical age is the median; the age mix per year uses four buckets (under 1 year, 1–5, 5–20, 20+). The bucket limits are the fewest days an exact anniversary can span, so a play on the anniversary falls in the older bucket. A play dated before its release date counts as age 0.
- **How long you took to find it.** The wait from an album's release to the first day you played it, **only for albums released since your history begins** (the day of your first scrobble). A record from before that could not have been found on release, so "found 47 years late" would say nothing. Wait buckets: under 1 year, 1–3, 3–10, 10+. *There on release* lists albums first played within 30 days of a **full** release date (a bare year is too vague to call a week). Release dates that straddle the start of your history ("2020" when it began in January 2020) can't be placed and are left out, as are dates more than a year after your newest scrobble.
- **Older records.** Albums released before your history began are counted by share of plays instead: *released while tracking*, *your years, before tracking*, and *before you were born* (the last two need the optional birth year). A chart shows the year you first played each older album. Artists already in your rotation in the first 30 days of the history are left out of that chart, because their first scrobble is no discovery.
- **Birth year.** Optional. Set it on the Decades page (Older records card) or with `python -m mtc set-birth-year 1990` (`--clear` removes it). It is stored in `data/settings.json` on your machine and used for nothing else.
- **Decades through the year and day.** The Rhythms method with decades as the rows: decade × season, time of day and weekday/weekend heatmaps, each compared with that same year's decade mix. A heatmap where nothing differs from usual says so instead of drawing a blank grid.
- **What each decade sounds like.** A decade × genre map comparing each decade's genre mix with your overall mix, plus the signature genres of each decade. Needs genre tags.

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

## The live updater

When the server starts it pulls your new scrobbles from last.fm in a background thread, so the UI opens right away. It needs your username and API key (**Import → last.fm account**, or `python -m mtc set-user NAME` and `set-key KEY`; the env vars `LASTFM_USER` and `LASTFM_API_KEY` override the file). On an empty library it fetches your whole history.

- **At most 3 runs per calendar day, 4 hours apart.** The day is the local one (`MTC_TZ`) and resets at midnight; the 4-hour cooldown also holds across midnight. Each attempt, successful or not, is timestamped in the database (`meta`, key `updater_attempts`), so restarting the server a few times in a row doesn't hammer the API. A start with no username or key doesn't count.
- **What it fetches:** everything after your newest stored scrobble, minus a day of overlap for late offline scrobbles (`user.getRecentTracks`, 200 per page, the "now playing" track skipped). All pages are read before anything is stored, so a failure half way changes nothing and the next run starts from the same point. Duplicates are ignored, so the overlap is harmless.
- **See it:** `GET /api/updater` (last result, runs today, next allowed time) or `python -m mtc update --status`.
- **Run it by hand:** `python -m mtc update` (same limit; `--force` ignores it).
- **Turn it off:** `python -m mtc serve --no-update`, or `MTC_AUTO_UPDATE=0`. `create_app` leaves it off unless `auto_update=True` is passed, so tests never reach the network.

The code is `mtc/updater.py` and `LastFm.recent_tracks_page`; everything goes through `ingest.ingest_records` with `source="lastfm-api"`. Open pages notice the new scrobbles on the next page change (the `X-Data-Version` header).
