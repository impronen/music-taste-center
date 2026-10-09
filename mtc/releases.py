"""Upcoming releases: what comes out on the next few Fridays, from artists in the library first.

Two free sources, cached in `upcoming_releases` and replaced per source by `refresh`:
- ListenBrainz fresh releases (https://listenbrainz.org/explore/fresh-releases/), MusicBrainz data:
  albums, EPs and singles with artist MBIDs, a few thousand over two months, thinning out after
  about six weeks.
- Wikipedia's "List of YYYY albums" (MediaWiki API, one section per month): fewer, bigger
  releases, a good part of which MusicBrainz doesn't have yet, with the news article it cites.

A release is "yours" when its artist is in the library with at least MIN_PLAYS plays, matched by
MusicBrainz id (artist_info.mbid, which the metadata fetch fills) or by name (with the alias rules,
and each linked name of a Wikipedia "[[A]] and [[B]]" credit). A Wikipedia row of a release that
ListenBrainz also lists is merged into it, and ListenBrainz's ids decide whose it is.
Yours are ranked by how much the artist is played: all time, in the 12 months up to the latest
scrobble, and loved tracks. Releases are grouped by release week, Saturday to Friday, labelled by
the Friday.
"""
import html
import math
import re
import threading
import time
from datetime import date, datetime, timedelta

from . import config, db
from .ingest import key
from .insights import LOVED_BY_ARTIST
from .musicbrainz import edition_free_title
from .webapi import ApiError, Fatal, JsonApi, NotFound, Transient

WEEKS_AHEAD = 6                 # Fridays after the current week's (MusicBrainz thins out after ~6)
STALE_AFTER_S = 12 * 3600       # the page refreshes the cache when it's older than this
TYPES = ("Album", "EP", "Single")
RECENT_DAYS = 365
MIN_PLAYS = 3                   # an artist played less isn't "yours" (and a name clash costs less)
SOURCES = ("listenbrainz", "wikipedia")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


class ListenBrainz(JsonApi):
    base_url = "https://api.listenbrainz.org/1/"
    min_interval = 1.0
    retries = 1     # the page waits for the refresh: fail soon and keep the last list
    timeout = 60.0  # two months of releases is a few megabytes

    def _interpret(self, status: int, data: dict | None) -> dict:
        if status == 429 or status >= 500 or data is None:
            raise Transient(f"HTTP {status}")
        if status != 200:
            raise Fatal(f"ListenBrainz answered HTTP {status}")
        return data

    def fresh_releases(self, start: date, days: int) -> list[dict]:
        """Releases dated from `start` through `days` days after it (the API allows at most 90)."""
        data = self.get("explore/fresh-releases/", {"release_date": start.isoformat(), "days": str(min(days, 90)),
                                                     "past": "false", "future": "true"})
        return (data.get("payload") or {}).get("releases") or []


class Wikipedia(JsonApi):
    base_url = "https://en.wikipedia.org/w/api.php"
    min_interval = 1.0
    retries = 1

    def _interpret(self, status: int, data: dict | None) -> dict:
        if status == 429 or status >= 500 or data is None:
            raise Transient(f"HTTP {status}")
        if status != 200:
            raise Fatal(f"Wikipedia answered HTTP {status}")
        if "error" in data:
            if data["error"].get("code") == "missingtitle":
                raise NotFound("no such page")
            raise Fatal(f"Wikipedia: {data['error'].get('info') or data['error'].get('code')}")
        return data

    def _parse(self, page: str, **params: str) -> dict:
        return self.get("", {"action": "parse", "page": page, "format": "json", "formatversion": "2",
                             "redirects": "1", **params})["parse"]

    def month_wikitext(self, year: int, month: int) -> str | None:
        """The table of albums released in one month, or None when there is no such section yet."""
        page = f"List of {year} albums"
        known = self.__dict__.setdefault("_sections", {})  # one sections request per page
        if page not in known:
            known[page] = self._parse(page, prop="sections")["sections"]
        sections = known[page]
        index = next((s["index"] for s in sections if s.get("line") == MONTHS[month - 1]), None)
        if index is None:
            return None
        return self._parse(page, prop="wikitext", section=str(index))["wikitext"]


# ---------------------------------------------------------------- wikitext

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_REF = re.compile(r"<ref(?:\s[^>]*?)?(?<!/)>.*?</ref\s*>|<ref[^>]*/>", re.S | re.I)
_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_EXT_LINK = re.compile(r"\[https?://\S+\s+([^\]]*)\]")
_TAG = re.compile(r"<[^>]+>")
# cell attributes before a lone pipe: `rowspan="2" style="…" | text`
_ATTRS = re.compile(r"""^\s*(?:[a-z-]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s|<\[{']+)\s*)+\|(?!\|)""", re.I)
_ROWSPAN = re.compile(r"""rowspan\s*=\s*["']?(\d+)""", re.I)
_DATE_CELL = re.compile(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                        r"\s*(?:<br\s*/?>|\s)\s*(\d{1,2})\b", re.I)
_CITE_URL = re.compile(r"\|\s*url\s*=\s*(https?://[^\s|}]+)")


def _template(m: re.Match) -> str:
    """The text a display template stands for ({{sortname|Theta|Band}} -> "Theta Band"); others go."""
    name, *args = m.group(1).split("|")
    args = [a.strip() for a in args if "=" not in a]
    name = name.strip().lower()
    if name == "sortname" and len(args) >= 2:
        return f"{args[0]} {args[1]}"
    if name in ("nowrap", "small", "nobold", "ill", "interlanguage link") and args:
        return args[0]
    if name == "lang" and args:
        return args[-1]
    return ""


def _plain(cell: str) -> str:
    """Wikitext cell -> plain text: no references, templates, links, markup or HTML."""
    text = _LINK.sub(r"\1", _REF.sub("", _COMMENT.sub("", cell)))
    for _ in range(3):  # nested templates, innermost first
        text = _TEMPLATE.sub(_template, text)
    text = _EXT_LINK.sub(r"\1", text)
    text = re.sub(r"<br\s*/?>", ", ", text, flags=re.I)
    text = html.unescape(_TAG.sub("", text).replace("'''", "").replace("''", ""))
    return " ".join(text.split()).strip(" ,")


def _cells(row: str) -> tuple[list[str], list[str]]:
    """A table row's header cells (`!`) and data cells (`|`, also `||` on one line). A line continues
    the cell before it when it starts with neither, or while that cell has an open reference or
    template (a citation spread over lines that start with `|url=…`)."""
    heads, data, last = [], [], None
    for line in row.split("\n"):
        open_ = last and (last[-1].count("{{") > last[-1].count("}}")
                          or len(re.findall(r"<ref(?:\s[^>]*?)?(?<!/)>", last[-1], re.I)) > last[-1].lower().count("</ref"))
        if open_:
            last[-1] += "\n" + line
        elif line.startswith(("|}", "|+", "|-", "{|")):
            last = None
        elif line.startswith("!"):
            heads += line[1:].split("!!")
            last = heads
        elif line.startswith("|"):
            data += line[1:].split("||")
            last = data
        elif last:
            last[-1] += "\n" + line
    return heads, data


def parse_month(wikitext: str, year: int, month: int) -> list[dict]:
    """Rows of a month's album table: the date cell (a row header, spanning that day's rows), then
    artist, album, genre, label and the reference cells. An artist cell spanning rows is carried
    into the rows below it."""
    out, day, carry, carry_left = [], None, None, 0
    for row in wikitext.split("\n|-"):
        heads, cells = _cells(row)
        for h in heads:
            if m := _DATE_CELL.search(h.replace("&nbsp;", " ")):
                day = int(m.group(1))
        if carry_left and cells:
            cells.insert(0, carry)
            carry_left -= 1
        elif cells and (span := _ROWSPAN.search(cells[0].split("|")[0])) and int(span.group(1)) > 1:
            carry, carry_left = cells[0], int(span.group(1)) - 1
        if day is None or len(cells) < 2:
            continue
        try:
            released = date(year, month, day)
        except ValueError:
            continue
        cells = [_ATTRS.sub("", c) for c in cells]
        artist, title = _plain(cells[0]), _plain(cells[1])
        if not artist or not title:
            continue
        links = [_plain(t) for t in _LINK.findall(_REF.sub("", _COMMENT.sub("", cells[0])))]
        rest = cells[2:]
        url = next((m.group(1) for c in rest if (m := _CITE_URL.search(c))), None)
        out.append({"release_date": released.isoformat(), "artist": artist, "title": title,
                    "artist_parts": "\n".join(links) if len(links) > 1 else None,
                    "genre": (_plain(rest[0]) or None) if rest else None,
                    "label": (_plain(rest[1]) or None) if len(rest) > 1 else None, "source_url": url})
    return out


# ---------------------------------------------------------------- weeks


def release_friday(d: date) -> date:
    """The Friday that ends the release week (Saturday to Friday) `d` falls in."""
    return d + timedelta(days=(4 - d.weekday()) % 7)


def local_today() -> date:
    return datetime.now(config.TZ).date()


def window(today: date) -> tuple[date, date]:
    """First and last day shown: the current release week (ending this Friday, or today when today
    is Friday) and WEEKS_AHEAD more."""
    friday = release_friday(today)
    return friday - timedelta(days=6), friday + timedelta(days=7 * WEEKS_AHEAD)


# ---------------------------------------------------------------- refresh

_refreshing = threading.Lock()


class Busy(Exception):
    """A refresh is already running."""


def _cover(r: dict) -> str | None:
    if r.get("caa_id") and r.get("caa_release_mbid"):
        return f"https://coverartarchive.org/release/{r['caa_release_mbid']}/{r['caa_id']}-250.jpg"
    return None


def _day(text) -> date | None:
    """A full YYYY-MM-DD date, or None."""
    try:
        return date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None


def _from_listenbrainz(lb: ListenBrainz, first: date, last: date) -> list[dict]:
    out = []
    for r in lb.fresh_releases(first, (last - first).days + 1):
        if r.get("release_group_primary_type") not in TYPES or not r.get("release_name") or not r.get("artist_credit_name"):
            continue
        day = _day(r.get("release_date"))
        if day is None or not first <= day <= last:  # MusicBrainz dates can be partial ("2026-11")
            continue
        out.append({"release_date": r["release_date"], "artist": r["artist_credit_name"], "title": r["release_name"],
                    "release_type": r["release_group_primary_type"],
                    "artist_mbids": " ".join(r.get("artist_mbids") or []) or None,
                    "release_group_mbid": r.get("release_group_mbid"), "cover_url": _cover(r)})
    return out


def _from_wikipedia(wiki: Wikipedia, first: date, last: date) -> list[dict]:
    out, months = [], sorted({(d.year, d.month) for d in (first + timedelta(days=i) for i in range((last - first).days + 1))})
    for year, month in months:
        try:
            text = wiki.month_wikitext(year, month)
        except NotFound:  # next year's page doesn't exist yet
            continue
        if text:
            out += [r for r in parse_month(text, year, month) if first.isoformat() <= r["release_date"] <= last.isoformat()]
    return out


COLUMNS = ("source", "release_date", "artist", "title", "release_type", "artist_mbids", "artist_parts", "release_group_mbid",
           "cover_url", "source_url", "genre", "label")


def refresh(conn, lb: ListenBrainz, wiki: Wikipedia, today: date | None = None) -> dict:
    """Fetch both sources (network first, then one short write per source). A source that fails
    keeps its previous rows and records the error. Raises Busy when a refresh is already running."""
    if not _refreshing.acquire(blocking=False):
        raise Busy("already checking for new releases")
    try:
        first, last = window(today or local_today())
        result = {}
        for source, fetch in (("listenbrainz", lambda: _from_listenbrainz(lb, first, last)),
                              ("wikipedia", lambda: _from_wikipedia(wiki, first, last))):
            try:
                rows = fetch()
            except (ApiError, KeyError, TypeError) as exc:  # KeyError/TypeError: an unexpected answer
                with conn:
                    db.set_meta(conn, f"upcoming_{source}_error", str(exc) or type(exc).__name__)
                result[source] = {"ok": False, "error": str(exc)}
                continue
            with conn:
                conn.execute("DELETE FROM upcoming_releases WHERE source = ?", (source,))
                conn.executemany(f"INSERT INTO upcoming_releases({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                                 [tuple({**r, "source": source}.get(c) for c in COLUMNS) for r in rows])
                db.set_meta(conn, f"upcoming_{source}_at", str(int(time.time())))
                db.set_meta(conn, f"upcoming_{source}_error", "")
            result[source] = {"ok": True, "releases": len(rows)}
        return result
    finally:
        _refreshing.release()


def refreshing() -> bool:
    return _refreshing.locked()


# ---------------------------------------------------------------- reading

def _artist_lookup(conn):
    by_name = dict(conn.execute("SELECT name_key, id FROM artists"))
    by_name.update(conn.execute("SELECT name_key, artist_id FROM artist_aliases"))  # a name rule wins
    by_mbid = dict(conn.execute("SELECT mbid, artist_id FROM artist_info WHERE mbid IS NOT NULL AND mbid != ''"))
    mbid_of = {i: m for m, i in by_mbid.items()}

    def match(name: str, mbids: str | None, parts: str | None) -> list[int]:
        """Library artists of a credit: by MusicBrainz id, else by the whole name, else by each
        linked name of a Wikipedia credit ("[[A]] and [[B]]"), but never a library artist whose
        known id says the credit is another artist of the same name."""
        given = (mbids or "").split()
        ids = [by_mbid[m] for m in given if m in by_mbid]
        if ids:
            return list(dict.fromkeys(ids))
        names = [key(name)] if key(name) in by_name else [key(p) for p in (parts or "").split("\n") if p]
        found = (by_name[k] for k in names if k in by_name)
        return list(dict.fromkeys(i for i in found if not (given and i in mbid_of)))
    return match


def _artist_facts(conn, ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    latest = conn.execute("SELECT MAX(ts) FROM scrobbles").fetchone()[0] or 0
    facts = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT a.id, a.name, s.plays, s.last_ts, i.image_url FROM artists a JOIN artist_stats s ON s.artist_id = a.id"
        f" LEFT JOIN artist_info i ON i.artist_id = a.id WHERE a.id IN ({marks}) AND s.plays >= ?", [*ids, MIN_PLAYS])}
    recent = dict(conn.execute(f"SELECT artist_id, COUNT(*) FROM scrobbles WHERE ts > ? AND artist_id IN ({marks})"
                               " GROUP BY artist_id", [latest - RECENT_DAYS * 86400, *ids]))
    loved = dict(conn.execute(f"SELECT artist_id, n FROM ({LOVED_BY_ARTIST}) WHERE artist_id IN ({marks})", list(ids)))
    for i, f in facts.items():
        f["recent_plays"], f["loved"] = recent.get(i, 0), loved.get(i, 0)
    return facts


def fit(f: dict) -> float:
    """How much an artist is in your listening: all-time plays, this year's plays weighted more
    (a current favourite beats an old one), and loved tracks, on log scales."""
    return math.log1p(f["plays"]) + 1.5 * math.log1p(f["recent_plays"]) + 0.5 * min(f["loved"], 6)


def _status(conn) -> dict:
    out = {}
    for s in SOURCES:
        at = db.get_meta(conn, f"upcoming_{s}_at")
        out[s] = {"at": int(at) if at else None, "error": db.get_meta(conn, f"upcoming_{s}_error") or None}
    return out


def upcoming(conn, today: date | None = None) -> dict:
    """The cached releases by release week: yours ranked by fit, the rest of Wikipedia's list, and
    how many more albums and EPs ListenBrainz has that week."""
    today = today or local_today()
    first, last = window(today)
    status = _status(conn)
    ats = [s["at"] for s in status.values()]
    stale = any(a is None or time.time() - a > STALE_AFTER_S for a in ats)
    match = _artist_lookup(conn)
    merged: list[dict] = []
    by_credit: dict[tuple, list[dict]] = {}  # ListenBrainz releases by (credit as written, title)
    by_artist: dict[tuple, list[dict]] = {}  # ...and by (library artists or credit, title)
    for r in conn.execute("SELECT * FROM upcoming_releases WHERE release_date BETWEEN ? AND ?"
                          " ORDER BY source, release_date", (first.isoformat(), last.isoformat())):
        r = dict(r)
        day = _day(r["release_date"])
        if day is None:
            continue
        title = key(edition_free_title(r["title"]))
        ids = match(r["artist"], r["artist_mbids"], r.pop("artist_parts"))
        who = (tuple(ids) or key(r["artist"]), title)
        if r["source"] == "wikipedia":
            # Wikipedia's row of a ListenBrainz release adds its news link, genre and label. The same
            # credit and title first, so ListenBrainz's ids decide the artist (a namesake stays one).
            # Wikipedia lists albums: never fold one into its title single.
            same = [m for m in by_credit.get((key(r["artist"]), title)) or by_artist.get(who) or [] if m["release_type"] != "Single"]
            if same:
                m = min(same, key=lambda m: TYPES.index(m["release_type"]))  # an album before an EP
                m["sources"].append("wikipedia")
                for c in ("source_url", "genre", "label"):
                    m[c] = m[c] or r[c]
                continue
        entry = {**r, "artist_ids": ids, "sources": [r["source"]], "friday": release_friday(day).isoformat()}
        merged.append(entry)
        if r["source"] == "listenbrainz":
            by_credit.setdefault((key(r["artist"]), title), []).append(entry)
            by_artist.setdefault(who, []).append(entry)
    facts = _artist_facts(conn, {i for m in merged for i in m["artist_ids"]})
    weeks = {(first + timedelta(days=6 + 7 * w)).isoformat(): {"mine": [], "also": [], "more": 0} for w in range(WEEKS_AHEAD + 1)}
    for m in merged:
        week = weeks.get(m["friday"])
        if week is None:
            continue
        m.pop("artist_mbids")
        artists = [facts[i] for i in m.pop("artist_ids") if i in facts]
        if artists:
            best = max(artists, key=fit)
            week["mine"].append({**m, "artists": [{k: a[k] for k in ("id", "name", "plays", "recent_plays", "loved", "image_url")}
                                                  for a in artists], "fit": round(fit(best), 3)})
        elif "wikipedia" in m["sources"]:
            week["also"].append(m)
        elif m["release_type"] in ("Album", "EP"):
            week["more"] += 1
    for w in weeks.values():
        w["mine"].sort(key=lambda m: (-m["fit"], m["release_date"], key(m["artist"])))
        w["also"].sort(key=lambda m: (m["release_date"], key(m["artist"])))
    return {"first": first.isoformat(), "last": last.isoformat(), "today": today.isoformat(),
            "sources": status, "stale": stale, "refreshing": refreshing(),
            "weeks": [{"friday": f, **w} for f, w in weeks.items()]}


def clients() -> tuple[ListenBrainz, Wikipedia]:
    return ListenBrainz(), Wikipedia()

