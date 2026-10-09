"""A taste profile as a ready-made research prompt for any AI agent (copied from the Upcoming page).

The profile leans on recent listening and on genres more than on artist names:
- Every play counts with a weight that halves every HALF_LIFE_DAYS before the latest scrobble, so
  "now" means roughly the last few months without a hard cut-off.
- Genres: an artist's weighted plays are split over its top genre tags, as in the genre profile.
  Listed: the biggest now, the ones rising (share now against the all-time share, smoothed), the
  long-term core and the ones fading. Places (e.g. "finnish") the same way, when they matter.
- Artists come second and fewer: the most played now, recent discoveries, all-time staples and
  recently loved tracks. The prompt tells the agent to treat them as examples of a sound.
- Tasks: "releases" (new albums on the coming Fridays, with what the Upcoming page already found),
  "discover" (artists to explore) or "profile" (the profile alone).
"""
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import config, releases, taste_gap
from .insights import LOVED, _artist_genre_shares
from .rhythms import _cached

HALF_LIFE_DAYS = 90
SMOOTH = 0.005            # added to both shares in the rising/fading ratio, so tiny genres can't jump
RISING = 1.5              # "rising" needs the share now at least this many times the all-time share
FADING = 0.5              # "less lately" at most this
MIN_SHARE = 0.02          # a genre needs this share (now, or all time for fading) to be named
MIN_PLACE_SHARE = 0.05
MIN_TOP_SHARE = 0.005     # a genre listed under "now" rounds to at least 1 %
MIN_COVERAGE = 0.5        # rising and fading need genre tags on this share of recent listening
MIN_TREND_PLAYS = 10      # ...and this many weighted plays of the genre (now for rising, all time for fading)
DISCOVERY_DAYS = 180
TASKS = ("releases", "discover", "profile")
# place tags that name the same scene
PLACE_NAMES = {"usa": "american", "uk": "british", "finland": "finnish", "suomi": "finnish",
               "suomeksi": "finnish", "sweden": "swedish", "norway": "norwegian", "denmark": "danish", "iceland": "icelandic",
               "germany": "german", "france": "french", "japan": "japanese"}
WEEKS_IN_PROMPT = 2       # Fridays the release task asks about


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _ratio(x: float) -> str:
    return f"{x:.1f}×"  # a decimal point: the prompt is English, and "1,9" in a comma list reads as two numbers


def _shares(weights: Counter, shares: dict) -> tuple[Counter, float]:
    out, covered = Counter(), 0.0
    for artist_id, w in weights.items():
        if artist_id in shares:
            covered += w
            for tag_id, share in shares[artist_id]:
                out[tag_id] += w * share
    return Counter({t: v / covered for t, v in out.items()}) if covered else Counter(), covered


def profile(conn: sqlite3.Connection) -> dict | None:
    """The numbers behind the prompt (cached until scrobbles or tags change). None without scrobbles."""
    return _cached(conn, ("taste_prompt",), lambda: _profile(conn))


def _profile(conn) -> dict | None:
    ref, first, plays = conn.execute("SELECT MAX(ts), MIN(ts), COUNT(*) FROM scrobbles").fetchone()
    if not plays:
        return None
    now, ever = Counter(), Counter()
    for artist_id, age_days, n in conn.execute(
            "SELECT artist_id, (? - ts) / 86400, COUNT(*) FROM scrobbles GROUP BY 1, 2", (ref,)):
        now[artist_id] += n * 0.5 ** (age_days / HALF_LIFE_DAYS)
        ever[artist_id] += n
    names = dict(conn.execute("SELECT id, name FROM artists"))
    tag_names = dict(conn.execute("SELECT id, name FROM tags"))
    out = {"plays": plays, "first_ts": first, "last_ts": ref, "genres": {}, "places": []}

    total = sum(now.values())
    genre = _artist_genre_shares(conn, "genre")
    # shares among tagged artists (rising and fading compare these, like with like) ...
    g_now, covered = _shares(now, genre)
    g_ever, covered_ever = _shares(ever, genre)
    coverage = covered / total
    out["genre_coverage"] = coverage
    # ... and shown as shares of all recent listening, so they add up with the untagged rest
    shown = Counter({t: v * coverage for t, v in g_now.items()})
    ratio = {t: (g_now[t] + SMOOTH) / (g_ever[t] + SMOOTH) for t in set(g_now) | set(g_ever)}
    named = lambda ts: [{"name": tag_names[t], "now": shown[t], "ever": g_ever[t], "ratio": ratio[t]} for t in ts]
    trends = coverage >= MIN_COVERAGE
    out["genres"] = {
        "now": named([t for t, v in shown.most_common(15) if v >= MIN_TOP_SHARE]),
        "rising": named(sorted((t for t in g_now if trends and shown[t] >= MIN_SHARE and g_now[t] * covered >= MIN_TREND_PLAYS
                                and ratio[t] >= RISING), key=lambda t: -ratio[t])[:6]),
        "core": named([t for t, _ in g_ever.most_common(8)]),
        "fading": named(sorted((t for t in g_ever if trends and g_ever[t] >= MIN_SHARE and g_ever[t] * covered_ever >= MIN_TREND_PLAYS
                                and ratio[t] <= FADING), key=lambda t: ratio[t])[:5]),
    }
    # A scene counts an artist's whole recent listening when the artist has that place tag (an artist
    # tagged finnish, nordic and scandinavian is fully finnish), as a share of all recent listening.
    scenes_of: dict[int, set] = {}
    for artist_id, name in conn.execute("SELECT x.artist_id, t.name FROM artist_tags x JOIN tags t ON t.id = x.tag_id"
                                        " WHERE t.kind = 'place' AND x.weight > 0"):
        scenes_of.setdefault(artist_id, set()).add(PLACE_NAMES.get(name, name))
    places = Counter()
    for artist_id, names_ in scenes_of.items():
        for n in names_:
            places[n] += now.get(artist_id, 0) / total
    out["places"] = [{"name": n, "now": share} for n, share in places.most_common(4) if share >= MIN_PLACE_SHARE]

    gap = taste_gap.taste_gap(conn)["genres"]["over"]
    out["loved_genres"] = [{"name": g["name"], "ratio": g["ratio"]} for g in gap[:5]]
    recent_start = ref - DISCOVERY_DAYS * 86400
    out["artists"] = {
        "now": [names[a] for a, _ in now.most_common(12)],
        "discoveries": [r[0] for r in conn.execute(
            "SELECT a.name FROM artist_stats s JOIN artists a ON a.id = s.artist_id"
            " WHERE s.first_ts > ? AND s.prehistory = 0 AND s.plays >= 5 ORDER BY s.plays DESC LIMIT 8", (recent_start,))],
        "staples": [names[a] for a, _ in ever.most_common(8)],
    }
    out["loved"] = [f"{r[0]} – {r[1]}" for r in conn.execute(
        f"SELECT a.name, t.title FROM ({LOVED}) lv JOIN tracks t ON t.id = lv.track_id JOIN artists a ON a.id = t.artist_id"
        " ORDER BY lv.loved_at DESC LIMIT 8")]
    return out


def _end(text: str) -> str:
    """A sentence ending: no "Inc.." when the last name already ends in a full stop."""
    return text if text.endswith((".", "!", "?")) else text + "."


def _date(d: date) -> str:
    return f"{d.day} {d.strftime('%B')} {d.year}"


def build(conn: sqlite3.Connection, task: str = "releases", today: date | None = None) -> dict:
    """The prompt text for one task, with the profile it was built from."""
    p = profile(conn)
    if p is None:
        return {"task": task, "text": "", "profile": None}
    first = datetime.fromtimestamp(p["first_ts"], config.TZ).year
    last = datetime.fromtimestamp(p["last_ts"], config.TZ).date()
    g = p["genres"]
    lines = ["# My music taste",
             f"From my last.fm history: {p['plays']:,} plays from {first} to {_date(last)}.".replace(",", " ")
             + f" Recent listening counts more (a play's weight halves every {HALF_LIFE_DAYS // 30} months).", ""]
    if g["now"]:
        lines.append("Genres I'm into now (share of my recent listening): " + ", ".join(f"{x['name']} {_pct(x['now'])}" for x in g["now"]) + ".")
        if p["genre_coverage"] < 0.9:
            lines.append(f"(Only {_pct(p['genre_coverage'])} of my recent listening has genre tags, so these shares leave part of it out"
                         + ("." if p["genre_coverage"] >= MIN_COVERAGE else ", too much to tell what's rising or fading.") + ")")
        if g["rising"]:
            lines.append("Rising lately (compared with my long-term average): " + ", ".join(f"{x['name']} ({_ratio(x['ratio'])})" for x in g["rising"]) + ".")
        lines.append("Long-term core: " + ", ".join(x["name"] for x in g["core"]) + ".")
        if g["fading"]:
            lines.append("Less into lately: " + ", ".join(x["name"] for x in g["fading"]) + ".")
        if p["loved_genres"]:
            lines.append("Genres I love more often than I play them (loved tracks): " + ", ".join(x["name"] for x in p["loved_genres"]) + ".")
    else:
        lines.append("(No genre tags fetched yet, so the artists below are all there is.)")
    if p["places"]:
        lines.append("Scenes that matter: " + ", ".join(f"{x['name']} {_pct(x['now'])}" for x in p["places"]) + ".")
    a = p["artists"]
    lines += ["", _end("Artists I play most right now: " + "; ".join(a["now"]))]  # names can hold commas
    if a["discoveries"]:
        lines.append(_end(f"Recent discoveries (first played in the last {DISCOVERY_DAYS // 30} months): " + "; ".join(a["discoveries"])))
    lines.append(_end("All-time staples: " + "; ".join(a["staples"])))
    if p["loved"]:
        lines.append(_end("Tracks I loved most recently: " + "; ".join(p["loved"])))
    lines += ["", "How to read this: the genres matter more than the artist names, which are examples of the sound rather than "
              "a list to stay within. What I play now matters more than the long-term core."]

    finnish = any(x["name"] in ("finnish", "finland", "suomi") for x in p["places"])
    if task == "releases":
        today = today or releases.local_today()
        friday = releases.release_friday(today)
        fridays = [friday + timedelta(days=7 * w) for w in range(WEEKS_IN_PROMPT)]
        known = _known_releases(conn, today, fridays[-1])
        lines += ["", "# Task",
                  f"Find new albums and EPs coming out on Friday {_date(fridays[0])}"
                  + "".join(f" and Friday {_date(f)}" for f in fridays[1:]) + " that I'm likely to enjoy.",
                  "- Search the web: release calendars, label announcements and music press"
                  + (", including Finnish music media" if finnish else "") + ".",
                  "- Most of all, find artists I don't play yet whose sound fits the genres above; new releases by the artists above count too.",
                  "- For each pick: artist, title, release date, album or EP, one or two sentences on why it fits me "
                  "(name the genres or artists it relates to) and a source link. Check the date against a source and mark anything unconfirmed.",
                  "- Give 10 to 15 picks, best match first."]
        if known:
            lines.append(_end("- Already on my list, no need to find these (but say if one deserves special attention): " + "; ".join(known)))
    elif task == "discover":
        lines += ["", "# Task",
                  "Recommend 10 artists I probably don't know yet who fit the genres above, leaning towards what I'm into now and what's rising.",
                  "- Leave out the artists named above and the most obvious big names of these genres.",
                  "- For each: why it fits me (name the genres or artists it relates to), one album to start with, and a link.",
                  "- Search the web to check that each artist is real and active, and to find newer artists, not only established ones."]
    return {"task": task, "text": "\n".join(lines), "profile": p}


def _known_releases(conn, today: date, until: date) -> list[str]:
    weeks = releases.upcoming(conn, today)["weeks"]
    out = []
    for w in weeks:
        if w["friday"] > until.isoformat():
            break
        out += [f"{r['artists'][0]['name']} – {r['title']} ({r['release_date']})" for r in w["mine"] if r["release_type"] != "Single"][:10]
    return out[:15]
