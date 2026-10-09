# How Taste Center works

The details behind the pages: what is measured, how, and what is sent to the internet. For installing and using the app, see the [README](../README.md).

- [The pages in detail](#the-pages-in-detail)
- [Artist velocity](#artist-velocity)
- [Connections](#connections)
- [Tags, covers, genres and release dates](#tags-covers-genres-and-release-dates)
- [Rhythms](#rhythms)
- [Decades](#decades)
- [Cleaning up duplicate artists](#cleaning-up-duplicate-artists)
- [The live updater](#the-live-updater)

## The pages in detail

| Page | What it shows |
|---|---|
| **Overview** | Any period: last 7/30/90 days, last year, all time, a calendar year or month, or a custom date range. Totals with the change vs the previous period, scrobbles per day (up to 120 days) or per month, top artists, tracks and albums, new discoveries, and a listening clock. Also all-time *novelty* (share of plays going to artists discovered in the previous 12 months) and recent plays |
| **Library** | Every artist, sortable and filterable, plus top artists, tracks, albums or genres for any period (the same period bar, or a day or month clicked on a chart). The artists' *Loved* column counts their tracks loved on last.fm and the love rate: the share of the artist's tracks you've played that are loved. In a period both cover only the tracks played in that period |
| **Artist** | Plays per month, a *velocity* chart (cumulative plays, comparable across up to six artists), top tracks (with how many are loved on last.fm, and what share of the tracks you've played that is) and albums, *how you discovered them* (the artist you were playing right before), who they *led you to*, artists *listened alongside*, and time of day |
| **Connections** | Force graph of your top artists, linked by co-listening, with taste clusters found automatically |
| **Eras** | Per year: top artists, the *signature* artist (most over-represented compared with all time), and the biggest new discovery |
| **Cleanup** | Merge artists that are spelled in more than one way, with suggested duplicates and name rules that fix future imports; link loved tracks whose title doesn't match |
| **Rhythms** | How genres (or places) move through the year, the week and the day: a genre × month heatmap, what stands out each season, time of day, weekdays vs weekends, seasonal artists (with "coming up"), genre drift per year, and how varied your mix is |
| **Decades** | Release decades, the age of the music you play, how long you took to find albums, and older records (see [Decades](#decades)) |
| **Insights** | Rediscover (recommendations from your own past), on the rise, forgotten favourites, obsessions, staying power, gateways, binges, one-track artists, loved then left, not loved (yet), love at first listen, slow burners, deep dives; "See all" on a card opens the full list (`#/insights/binges` etc.), 50 more at a time up to 500 |
| **Import** | Add a CSV (a scrobble is identified by time, artist and track, so rows already stored are skipped), connect your last.fm account, and fetch tags, covers and release dates |

## Artist velocity

The **Velocity** card on every artist page shows the artist's *cumulative* plays over time: a steeper line is a faster pace. You can add up to five more artists (your top artists, the ones you play alongside it, or anyone found by search) to see how fast you got through each of them. The selection and the view live in the page address (`#/artist/12?vs=5,9&view=relative`), so a comparison can be bookmarked.

- **Two views.** *Calendar* puts every curve on the real date axis. *Since first play* starts each curve at zero on that artist's first play, so artists you found years apart become comparable.
- **Weekly curve.** The curve is the cumulative play count at the end of each week (Monday to Sunday, in your time zone), the same weeks for every artist.
- **Already in rotation.** An artist first played in the first 30 days of your history was probably known before tracking began, and plays from before then aren't counted. The first 30 days of your history are drawn dashed for such an artist, and it is marked "known before", because its pace in that stretch isn't a discovery pace.
- **Pace facts** under the chart: the time from the first play to the 100th, 500th and 1 000th play (a dash when it hasn't got there), the **fastest 30 days** (the most plays in any 30 consecutive days, and when that stretch began), and plays per month over the **last year** against **overall** (since the first play). "Now" means your newest scrobble. The last-year figure needs an artist older than a year, and the overall one at least 30 days, so a short burst isn't mistaken for a pace.
- **Data.** `GET /api/velocity?ids=1,2,3` (up to six ids). Unknown ids are ignored. The per-artist day counts are cached until the next import or merge.

## Connections

- **Sessions.** A gap of more than 30 minutes between scrobbles starts a new session.
- **Links.** Two artists are linked when they appear in the same sessions. The score is the Ochiai coefficient `shared / sqrt(sessions_a × sessions_b)`, so a huge artist doesn't link to everything. Very long shuffle sessions only count their 30 most-played artists, and a link needs at least 3 shared sessions.
- **Clusters.** Weighted label propagation over each artist's strongest links.
- **Rediscover** (Insights) recommends artists you haven't played for a year that are linked to your 20 most played artists of the last 90 days. A candidate needs at least 5 plays, or one loved track on last.fm (then any number of plays will do, but it still needs those links). The rank is the sum of its link scores × log(1 + plays) × a loved boost of 1 + 0,5 per loved track, counting at most four (so 1,5× for one, 3× at most): an artist whose tracks you loved, then stopped playing, comes first among equals, but loves can't bury a much better co-listening match. An artist let in on a love with fewer than 5 plays counts one love at most (1,5×), because link scores already run high for artists with few sessions. With nothing loved the order is the plain link × plays one. Ties go to the lowest artist id. Tiles and the full list show "♥ 3 loved" for such artists.
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

- **Why two sources.** last.fm's `album.getInfo` no longer returns a release date, even though its docs still show one. MusicBrainz is the source of truth for dates. A name search only counts as a match if the title matches exactly and the artist matches too. If the exact title finds nothing and it carries an edition marker ("(Deluxe Edition)", "[Remastered]", "- 2011 Remaster", "- EP"), the search runs once more without the marker, because last.fm and streaming services add those but MusicBrainz keeps them out of the title. The artist must match either way. Compilations are credited to "Various Artists" on MusicBrainz, so an album of yours credited to one performer doesn't match them; that is deliberate, since a generic title like "Greatest Hits" would otherwise pick up the wrong album's date.
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
- **Seasonal artists.** Each play becomes a point on a yearly circle (day of the year), with every year weighted equally. An artist needs at least 30 plays, and a year only counts with at least 3 of its plays in it. It is seasonal when the plays cluster tightly enough (mean resultant length ≥ 0,5), in at least 3 years, with each year's peak within 30 days of the overall peak in at least 60 % of the years. *Coming up* means the season starts within 4 weeks of your newest scrobble.
- **Diversity** is the "effective number of genres", exp(Shannon entropy) of each month's genre mix: 4,0 is as varied as four equally played genres.
- **Speed.** One grouped pass over the scrobbles feeds every view. Results are cached until the next import, rebuild, merge or tag fetch: about 0,3 s at 260k scrobbles, then instant.

## Decades

The Decades page looks at *when the music you play was released*. It needs release dates (see above), so every section says what share of your plays it is based on: scrobbles without an album, and albums with fewer than three plays, can't be dated.

- **Release dates.** MusicBrainz gives the original release, not a reissue, as `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. When MusicBrainz has nothing, a year tag from last.fm can supply the year. For sums that need a day, a year-only date counts as 1 July and a year-month as the 15th. Years outside 1900–2100 are ignored as data errors.
- **Decades in vogue.** Plays by release year and decade, each listening year's decade mix, and a decade × listening-year heatmap. The heatmap is a *lift*, as in Rhythms: a decade's share in a year compared with its share of all your plays, with small cells pulled towards "as usual". "In vogue lately" compares the last 12 months with everything before them in the same way.
- **How old the music is.** The album's age on the day you played it, in whole days. Typical age is the median; the age mix per year uses four buckets (under 1 year, 1–5, 5–20, 20+). The bucket limits are the fewest days an exact anniversary can span, so a play on the anniversary falls in the older bucket. A play dated before its release date counts as age 0.
- **How long you took to find it.** The wait from an album's release to the first day you played it, **only for albums released since your history begins** (the day of your first scrobble). A record from before that could not have been found on release, so "found 47 years late" would say nothing. Wait buckets: under 1 year, 1–3, 3–10, 10+. *There on release* lists albums first played within 30 days of a **full** release date (a bare year is too vague to call a week); like the longest-waits list, it only names albums with at least 5 plays. Release dates that straddle the start of your history ("2020" when it began in January 2020) can't be placed and are left out, as are release years later than the year after your newest scrobble.
- **Older records.** Albums released before your history began are counted by share of plays instead: *released while tracking*, *your years, before tracking*, and *before you were born* (the last two need the optional birth year). A chart shows the year you first played each older album. Artists already in your rotation in the first 30 days of the history are left out of that chart, because their first scrobble is no discovery.
- **Birth year.** Optional. Set it on the Decades page (Older records card) or with `python -m mtc set-birth-year 1990` (`--clear` removes it). It is stored in `data/settings.json` on your machine and used for nothing else.
- **Decades through the year and day.** The Rhythms method with decades as the rows: decade × season, time of day and weekday/weekend heatmaps, each compared with that same year's decade mix. A heatmap where nothing differs from usual says so instead of drawing a blank grid.
- **What each decade sounds like.** A decade × genre map comparing each decade's genre mix with your overall mix, plus the signature genres of each decade. Needs genre tags.

## Cleaning up duplicate artists

last.fm data has spelling variants of the same artist ("Sunn 0)))" with a zero vs "Sunn O)))"). On the **Cleanup** page, or via **More → Merge a duplicate spelling…** on an artist page, merge the wrong spelling into the right one:

- **Everything moves.** All scrobbles go to the artist you keep. Tracks and albums with the same (case-insensitive) title are combined, and a play scrobbled under both spellings at the same minute is kept once. Derived tables are rebuilt.
- **A name rule stays.** Future imports (CSV or API) of the merged spelling go straight to the kept artist, and re-importing an old export doesn't bring the duplicate back. Rules follow chained merges. You can also add a rule for a spelling you haven't imported yet. Removing a rule doesn't split artists that were already merged.
- **Suggestions.** Artists whose names match when accents, punctuation, 0/o, `&`/"and" and a leading "the" are ignored, or that last.fm autocorrects to the same name (after a tag fetch). It suggests keeping last.fm's spelling, otherwise the most played one. **Not the same** hides a group.
- **Merges can't be undone**, so a suggested merge asks for a second click and a manual one asks for confirmation in a dialog.
- **Loved tracks that don't match.** A loved track whose title is spelled differently from your scrobbles ("Song (Remastered 2011)", "feat." parts, a different apostrophe) gets no heart. The Cleanup page lists them with why: the artist isn't in your library (merge it or add a name rule), or no track has that title. In the second case it suggests up to 3 of that artist's tracks, matched with accents, punctuation and edition suffixes (remaster, live, version, feat., …) ignored, or by close spelling. **Link to …** saves a loved-title rule (`loved_title_rules`, `GET /api/loved/unmatched`, `POST /api/loved/rules`) that the `loved` view uses first. It's keyed by the loved entry's names, so it survives each update replacing the loved list and follows artist merges; **Undo** removes it.

```sh
.venv/bin/python -m mtc duplicates                         # list suggestions (* = suggested keeper)
.venv/bin/python -m mtc merge-artist "Sunn 0)))" "Sunn O)))"   # merge SOURCE into TARGET
```

## The live updater

When the server starts it pulls your new scrobbles from last.fm in a background thread, so the UI opens right away. It needs your username and API key (**Import → last.fm account**, or `python -m mtc set-user NAME` and `set-key KEY`; the env vars `LASTFM_USER` and `LASTFM_API_KEY` override the file). On an empty library it fetches your whole history, up to 2000 pages of 200 scrobbles (400 000); a bigger library should be imported from a CSV file instead.

- **At most 3 runs per calendar day, 4 hours apart.** The day is the local one (`MTC_TZ`) and resets at midnight; the 4-hour cooldown also holds across midnight. Each attempt, successful or not, is timestamped in the database (`meta`, key `updater_attempts`), so restarting the server a few times in a row doesn't hammer the API. A start with no username or key doesn't count.
- **What it fetches:** everything after your newest stored scrobble, minus a day of overlap for late offline scrobbles (`user.getRecentTracks`, 200 per page, the "now playing" track skipped). All pages are read before anything is stored, so a failure half way changes nothing and the next run starts from the same point. Duplicates are ignored, so the overlap is harmless.
- **Loved tracks:** each run then replaces the list of tracks you've loved on last.fm (`user.getLovedTracks`, newest first, 1 000 per page) in `loved_tracks`. They're kept by artist and title, not by track id, and matched to your library when read (the `loved` view, which follows merge name rules), so a track you loved before scrobbling it, or an artist you merge later, still lines up. If this step fails, the run still counts as done with a `loved_error`, and the old list stays. Loved tracks show as a heart on track, album and artist pages and feed four Insights cards and lift loved artists in Rediscover. Love timing compares the love date with the track's scrobbles: plays over before the love (a scrobble's time is when the play started, so a play that started under 10 minutes before the love counts as the one being listened to) and the time since the first play. last.fm keeps only the latest love date, so a track unloved and loved again counts from the second time. A track first played in the history's first 30 days only gets "at least" figures and never counts as a first-listen love, since its listening may predate the history.
- **See it:** `GET /api/updater` (last result, runs today, next allowed time) or `python -m mtc update --status`.
- **Run it by hand:** `python -m mtc update` (same limit; `--force` ignores it).
- **Turn it off:** `python -m mtc serve --no-update`, or `MTC_AUTO_UPDATE=0`. `create_app` leaves it off unless `auto_update=True` is passed, so tests never reach the network.

The code is `mtc/updater.py` and `LastFm.recent_tracks_page`; everything goes through `ingest.ingest_records` with `source="lastfm-api"`. Open pages notice the new scrobbles on the next page change (the `X-Data-Version` header).
