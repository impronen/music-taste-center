# Taste Center

**Your last.fm listening history, on your own computer.** Browse it, search it, and see how your taste connects and changes over time. No ads, no account to create, nothing uploaded: everything lives in one file on your machine.

![The Overview page: a month of listening at a glance](docs/images/overview.jpg)

<sub>All screenshots show a fictional listening history.</sub>

## What you can do with it

- **See your listening at a glance.** Any period (last week, a year, a date range of your choice), with totals, your top artists, tracks and albums, new discoveries and a listening clock.
- **Follow how your taste connects.** Artists you play in the same sessions are linked, and the app finds your taste clusters on its own.
- **Spot your rhythms.** Which genres belong to which season, time of day or day of the week, and which artists come back every year.
- **Look at the decades.** Which release decades you listen to, how old the music is when you play it, how long you took to find albums after they came out, and which older records you dug up when.
- **Rediscover things.** Forgotten favourites, artists on the rise, obsessions, and recommendations from your own past.
- **Keep your library tidy.** Merge artists that last.fm spells in more than one way.

| | |
|---|---|
| ![Connections: taste clusters](docs/images/connections.jpg) | ![Rhythms: genres through the year](docs/images/rhythms.jpg) |
| *Connections*: your top artists, linked by listening together | *Rhythms*: what you play in which season |
| ![Decades](docs/images/decades.jpg) | |
| *Decades*: when the music you play was released | |

## What you need

- A computer with **Python 3.13 or newer**. Check with `python3 --version`; if it's older or missing, install it from [python.org](https://www.python.org/downloads/).
- A free **last.fm account** with some scrobbles, and (to download your history and to fetch genres and release dates) a free **last.fm API key**. You can get one in a minute at <https://www.last.fm/api/account/create>. Without an account you can still [try the app with fictional data](#try-it-with-fictional-data).
- A web browser. The app runs on your own computer and opens in your browser; it isn't a website and isn't reachable from other computers.

## Install and start

1. **Download the code.** On this page, click the green **Code** button and choose **Download ZIP**, then unzip it. (Or use `git clone` if you know git.)
2. **Start it.**
   - **On a Mac:** double-click **`Taste Center.command`**. The first time it sets everything up (this needs an internet connection and takes a minute), starts the app and opens your browser. If macOS refuses to open it because it was downloaded, right-click the file and choose **Open**. Close the Terminal window to stop the app. You can drag the file to the Dock for one-click access.
   - **On Linux, or any Mac if you prefer the Terminal:** open a terminal in the unzipped folder and run

     ```sh
     python3 -m venv .venv
     .venv/bin/pip install -r requirements.txt
     .venv/bin/python app.py
     ```

     Then open <http://127.0.0.1:8765> in your browser. Press Ctrl+C to stop the app.
   - **On Windows:** the same steps should work with `py -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt` and `.venv\Scripts\python app.py`. This hasn't been tested on Windows.

The app starts empty. Next, bring in your history.

## Get your listening history in

Pick whichever suits you. You can also use both: the app skips scrobbles it already has.

### Option A: connect your last.fm account (recommended)

1. Open the **Import** page in the app.
2. In the **last.fm account** card, save your last.fm **username** and your **API key**.
3. **Restart the app** (close it and start it again). It now downloads your whole scrobble history in the background, while you browse. A big library can take a few minutes.

From then on, every time you start the app it fetches the scrobbles you've added since. It does this at most three times a day, so restarting it a few times in a row is harmless.

### Option B: import a CSV file

If you already have your scrobbles in a CSV file, for example from one of the many free "export last.fm scrobbles to CSV" tools, drag it onto the **Import** page. Re-importing a newer, bigger export later is safe, because scrobbles you already have are skipped.

The file needs the artist, track and date of each play, and optionally the album. Either of these layouts works:

- no header row, with the columns in the order **artist, album, track, date**; or
- a header row naming the columns (`artist`, `album`, `track`, `date`, and a few common alternatives).

Dates can be in last.fm's text format (`04 Oct 2026, 16:42`), as unix time, or as ISO 8601. The text encoding (UTF-8, Windows-1252, Latin-1) is detected automatically. From a terminal you can also run `.venv/bin/python -m mtc import yourfile.csv`.

## Add genres, covers and release dates

Your scrobbles only say *what* you played and *when*. To unlock **genres**, **Rhythms**, **Decades** and album covers, the app needs to look your artists and albums up:

1. Save your API key on the **Import** page (if you haven't yet).
2. In the **Tags, covers & release dates** card, press **Fetch tags & covers**.

It works through your most-played artists and albums first, in the background, with progress on the page. You can browse while it runs, press **Stop** at any time, or close the app: everything fetched so far is kept, and the next fetch carries on where it stopped. A large library takes a while because the app is polite to last.fm and MusicBrainz (the free music database that supplies release dates) and sends them only a couple of requests per second.

## Try it with fictional data

Want to look around before using your own history? Create a demo library of invented artists. Run these in the app's folder (use `.venv\Scripts\python` on Windows):

```sh
.venv/bin/python -m tests.synthetic --demo-db demo.db
MTC_DB=demo.db .venv/bin/python app.py serve --no-update
```

(On Windows PowerShell, set the variable first with `$env:MTC_DB = "demo.db"`.) The demo has several years of listening, genres and release dates, so every page has something to show. `--no-update` keeps the app from fetching your real scrobbles into the demo file. Your real data is never touched: it lives in `data/`, and the demo is a separate file.

## The pages

| Page | What it shows |
|---|---|
| **Overview** | Totals and the change from the previous period, scrobbles per day or month, top artists, tracks and albums, new discoveries, and a listening clock |
| **Library** | Every artist, sortable and filterable, plus top lists and genres for any period |
| **Artist** | Plays per month, top tracks and albums, how you discovered the artist and where it led you, who you play it alongside, and time of day |
| **Connections** | Your top artists as a graph, linked by listening together, with taste clusters |
| **Eras** | Each year in review: top artists, the artist that defined it, and the biggest discovery |
| **Rhythms** | Genres (or places) through the year, the week and the day, seasonal artists, and how varied your taste is |
| **Decades** | Release decades, the age of the music you play, how long you took to find albums, and older records |
| **Insights** | Rediscover, on the rise, forgotten favourites, obsessions, staying power, gateways, binges and more |
| **Cleanup** | Merge duplicate spellings of an artist; the fix sticks for future imports |
| **Import** | Add scrobbles, connect your account, and fetch genres, covers and release dates |

Press **/** anywhere to search. The ◐ button switches between light and dark. For what each page measures and how, see [How it works](docs/how-it-works.md).

## Your data and privacy

- **Everything stays on your computer.** Your history lives in `data/mtc.db`, and your username and API key in `data/settings.json`, both inside the app's folder and both kept out of git. The app listens only on your own machine.
- **What is sent out.** Only when you fetch or update: your last.fm username and API key to last.fm; artist and album names to last.fm and MusicBrainz; and your browser loads album covers from last.fm's servers. The fonts are bundled, so nothing else is loaded from the internet.
- **Back up** by copying the `data` folder while the app is stopped. **Start over** by deleting `data/mtc.db`.
- **Update** by downloading the new version and copying your old `data` folder into it. The app upgrades the database on its own.

Taste Center is an independent hobby project. It isn't made by or affiliated with Last.fm or MusicBrainz; it uses their public APIs.

## Troubleshooting

- **"No scrobbles yet" after connecting your account.** The history download starts when the app starts. Make sure the username and API key are saved on the Import page, then restart the app. Checking the Terminal window for error messages helps.
- **Times of day are off by some hours.** The app works out days and hours in a time zone, and it assumes Finnish time (`Europe/Helsinki`) unless you say otherwise. Start it with your own zone, for example `MTC_TZ=America/New_York .venv/bin/python app.py` (on a Mac you can run `MTC_TZ=America/New_York "./Taste Center.command"` in Terminal), and then recalculate once with `MTC_TZ=America/New_York .venv/bin/python -m mtc rebuild`. Zone names look like `Europe/London` or `Asia/Tokyo`.
- **The page says it can't connect.** The app isn't running (start it again), or another program uses port 8765. Start it on another port with `.venv/bin/python app.py serve --port 8800`, or for the Mac launcher `MTC_PORT=8800 "./Taste Center.command"`.
- **Decades or Rhythms look empty.** They need genres and release dates: run **Fetch tags & covers** on the Import page and let it work through your library. Each page says what share of your plays it is based on.
- **Some artists appear twice.** Use the **Cleanup** page to merge them.

## For developers

How the code is organised, how to run the tests, and the house rules are in the [developer guide](docs/developer-guide.md). The measures behind each page are in [How it works](docs/how-it-works.md).
