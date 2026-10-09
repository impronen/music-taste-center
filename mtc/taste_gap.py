"""Taste gap: what you love (last.fm loved tracks) compared with what you play.

Three comparisons, each "share among loved tracks" against "share of plays":
- genres: a loved track counts as its artist's genre shares (insights._artist_genre_shares, summing
  to 1), and plays are split the same way, so both sides use one weighting. Only loved tracks and
  plays of artists with genre tags count.
- decades: a loved track is dated by the dated album it was played from most (ties: the earliest
  release), plays by their own album, as on the Decades page (album_info.release_date). Only dated
  loved tracks and dated plays count.
- artists: loved tracks per artist against plays per artist, over all matched loved tracks and plays.

Small counts, two guards:
- Smoothed ratio. With L loved tracks on a side and a play share p, the expected loved count is
  E = L × p and the ratio is (loved + K) / (E + K): K pseudo-loves pull every ratio towards 1, as
  the lift in rhythms.py does with plays. K is 5 for genres and decades, 2 for artists.
- Minimum support. A genre is only compared when it has at least 1 % of loves or of plays and at
  least 10 loved tracks' worth either loved or expected (so "played a lot, never loved" and a
  niche genre loved far beyond its plays both qualify).
  An artist is "loved more" only with at least 3 loved tracks, and "played more" only when at
  least 3 loves would be expected from its plays. Lists keep ratios of at least 1.25× (or at most
  1 / 1.25). Decades need 10 dated loved tracks before they say anything.
- `enough` per list says whether the support can be reached at all with this many loved tracks,
  so an empty list can be told apart from "nothing stands out".
"""
import sqlite3
from collections import Counter, defaultdict

from . import decades
from .insights import LOVED, _artist_genre_shares
from .rhythms import _cached

GENRE_PRIOR = 5             # pseudo-loves for genre and decade ratios
ARTIST_PRIOR = 2            # pseudo-loves for artist ratios
MIN_GENRE_LOVED = 10        # loved tracks' worth (loved or expected) for a genre to be compared
MIN_GENRE_SHARE = 0.01      # ...and this share of loves or of plays
MIN_ARTIST_LOVED = 3        # loved tracks for "loved more than played"
MIN_ARTIST_EXPECTED = 3     # expected loves for "played more than loved"
MIN_DATED_LOVED = 10        # dated loved tracks before the decade comparison says anything
MIN_DECADE_SHARE = 0.02     # a decade gets a row with this share of loves or of plays
GAP = 1.25                  # a list keeps ratios at least this far from 1 (either way)
LIMIT = 8


def taste_gap(conn: sqlite3.Connection) -> dict:
    """The loved-vs-played comparison; cached until scrobbles, loved tracks or tags change."""
    return _cached(conn, ("taste_gap",), lambda: _taste_gap(conn), tags=True)


def _ratio(loved: float, expected: float, prior: float) -> float:
    return (loved + prior) / (expected + prior)


def _split(items: list[dict], min_ratio: float = GAP) -> tuple[list[dict], list[dict]]:
    over = sorted((x for x in items if x["ratio"] >= min_ratio), key=lambda x: (-x["ratio"], x["id"]))
    under = sorted((x for x in items if x["ratio"] <= 1 / min_ratio), key=lambda x: (x["ratio"], x["id"]))
    return over[:LIMIT], under[:LIMIT]


def _taste_gap(conn) -> dict:
    total_loved = conn.execute("SELECT COUNT(*) FROM loved_tracks").fetchone()[0]
    loved = dict(conn.execute(  # track_id -> artist_id, one row per matched track
        f"SELECT lv.track_id, t.artist_id FROM ({LOVED}) lv JOIN tracks t ON t.id = lv.track_id"))
    plays = dict(conn.execute("SELECT artist_id, COUNT(*) FROM scrobbles GROUP BY artist_id"))
    total_plays = sum(plays.values())
    out = {"loved": {"total": total_loved, "matched": len(loved), "with_genre": 0, "with_year": 0},
           "plays": {"total": total_plays, "with_genre": 0, "with_year": 0},
           "genres": {"over": [], "under": [], "loved": 0, "enough": {"over": False, "under": False}},
           "decades": {"enough": False, "items": []},
           "artists": {"over": [], "under": [], "loved": len(loved), "enough": {"over": False, "under": False}},
           "rules": {"min_genre_loved": MIN_GENRE_LOVED, "min_genre_share": MIN_GENRE_SHARE,
                     "min_artist_loved": MIN_ARTIST_LOVED, "min_artist_expected": MIN_ARTIST_EXPECTED,
                     "min_dated_loved": MIN_DATED_LOVED, "min_decade_share": MIN_DECADE_SHARE, "gap": GAP}}
    if not loved or not total_plays:
        return out
    _genres(conn, loved, plays, out)
    _decades(conn, loved, total_plays, out)
    _artists(conn, loved, plays, total_plays, out)
    return out


def _genres(conn, loved: dict, plays: dict, out: dict) -> None:
    shares = _artist_genre_shares(conn)
    by_loved: Counter = Counter()
    by_plays: Counter = Counter()
    n_loved = 0
    for artist_id in loved.values():
        if artist_id in shares:
            n_loved += 1
            for tag_id, share in shares[artist_id]:
                by_loved[tag_id] += share
    covered = 0
    for artist_id, n in plays.items():
        if artist_id in shares:
            covered += n
            for tag_id, share in shares[artist_id]:
                by_plays[tag_id] += n * share
    out["loved"]["with_genre"] = n_loved
    out["plays"]["with_genre"] = covered
    out["genres"]["loved"] = n_loved
    if not n_loved or not covered:
        return
    # Whether any genre could reach the support at all: "loved more" needs MIN_GENRE_LOVED loves in
    # one genre, "played more" that many expected from the biggest genre's play share.
    out["genres"]["enough"] = {"over": n_loved >= MIN_GENRE_LOVED,
                               "under": n_loved * max(by_plays.values(), default=0) / covered >= MIN_GENRE_LOVED}
    names = dict(conn.execute("SELECT id, name FROM tags WHERE kind = 'genre'"))
    items = []
    for tag_id in set(by_loved) | set(by_plays):
        play_share = by_plays[tag_id] / covered
        loved_share = by_loved[tag_id] / n_loved
        expected = n_loved * play_share
        if max(loved_share, play_share) < MIN_GENRE_SHARE or max(by_loved[tag_id], expected) < MIN_GENRE_LOVED:
            continue
        items.append({"id": tag_id, "name": names[tag_id], "loved": round(by_loved[tag_id], 1),
                      "expected": round(expected, 1), "loved_share": round(loved_share, 4),
                      "play_share": round(play_share, 4),
                      "ratio": round(_ratio(by_loved[tag_id], expected, GENRE_PRIOR), 4)})
    out["genres"]["over"], out["genres"]["under"] = _split(items)


def _decades(conn, loved: dict, total_plays: int, out: dict) -> None:
    # Each loved track's most played dated album (ties: earliest release, then lowest album id).
    best: dict[int, tuple] = {}
    for track_id, album_id, release, n in conn.execute(
            "SELECT s.track_id, s.album_id, ai.release_date, COUNT(*) FROM scrobbles s"
            " JOIN album_info ai ON ai.album_id = s.album_id"
            f" WHERE ai.release_date IS NOT NULL AND s.track_id IN (SELECT track_id FROM ({LOVED})) GROUP BY 1, 2"):
        year = int(release[:4])
        if not decades.MIN_YEAR <= year <= decades.MAX_YEAR:
            continue
        rank = (-n, year, album_id)
        if track_id not in best or rank < best[track_id][0]:
            best[track_id] = (rank, year)
    by_loved = Counter(year // 10 * 10 for _, year in best.values())
    n_dated = sum(by_loved.values())
    # Plays by the release decade of their own album: the same dating as decades._query, counted per
    # album first (a covering index scan) rather than per scrobble.
    by_plays: Counter = Counter()
    for release, n in conn.execute(
            "SELECT ai.release_date, x.n FROM (SELECT album_id, COUNT(*) AS n FROM scrobbles"
            " WHERE album_id IS NOT NULL GROUP BY album_id) x"
            " JOIN album_info ai ON ai.album_id = x.album_id WHERE ai.release_date IS NOT NULL"):
        year = int(release[:4])
        if decades.MIN_YEAR <= year <= decades.MAX_YEAR:
            by_plays[year // 10 * 10] += n
    dated_plays = sum(by_plays.values())
    out["loved"]["with_year"] = n_dated
    out["plays"]["with_year"] = dated_plays
    d = out["decades"]
    d.update({"loved": n_dated, "coverage": round(n_dated / len(loved), 4),
              "play_coverage": round(dated_plays / total_plays, 4)})
    if n_dated < MIN_DATED_LOVED or not dated_plays:
        return
    play_share = {decade: n / dated_plays for decade, n in by_plays.items()}
    items = []
    for decade in sorted(set(by_loved) | set(play_share)):
        loved_share = by_loved[decade] / n_dated
        p = play_share.get(decade, 0.0)
        if max(loved_share, p) < MIN_DECADE_SHARE:
            continue
        expected = n_dated * p
        items.append({"decade": decade, "label": f"{decade}s", "loved": by_loved[decade],
                      "expected": round(expected, 1), "loved_share": round(loved_share, 4), "play_share": round(p, 4),
                      "ratio": round(_ratio(by_loved[decade], expected, GENRE_PRIOR), 4)})
    d.update({"enough": True, "items": items})


def _artists(conn, loved: dict, plays: dict, total_plays: int, out: dict) -> None:
    n_loved = len(loved)
    per_artist = Counter(loved.values())
    # "played more" can only list someone when the biggest artist's plays would predict enough loves
    out["artists"]["enough"] = {"over": n_loved >= MIN_ARTIST_LOVED,
                                "under": n_loved * max(plays.values()) / total_plays >= MIN_ARTIST_EXPECTED}
    candidates = []
    for artist_id in set(per_artist) | set(plays):
        k, p = per_artist[artist_id], plays.get(artist_id, 0)
        expected = n_loved * p / total_plays
        if k >= MIN_ARTIST_LOVED or expected >= MIN_ARTIST_EXPECTED:
            candidates.append((artist_id, k, p, expected))
    items = defaultdict(list)
    for artist_id, k, p, expected in candidates:
        ratio = _ratio(k, expected, ARTIST_PRIOR)
        row = {"id": artist_id, "loved": k, "plays": p, "expected": round(expected, 1),
               "loved_share": round(k / n_loved, 4), "play_share": round(p / total_plays, 4), "ratio": round(ratio, 4)}
        if k >= MIN_ARTIST_LOVED and ratio >= GAP:
            items["over"].append(row)
        if expected >= MIN_ARTIST_EXPECTED and ratio <= 1 / GAP:
            items["under"].append(row)
    over = sorted(items["over"], key=lambda x: (-x["ratio"], -x["loved"], x["id"]))[:LIMIT]
    under = sorted(items["under"], key=lambda x: (x["ratio"], -x["plays"], x["id"]))[:LIMIT]
    names = _names(conn, [x["id"] for x in over + under])
    for x in over + under:
        x["name"] = names[x["id"]]
    out["artists"]["over"], out["artists"]["under"] = over, under


def _names(conn, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return dict(conn.execute(f"SELECT id, name FROM artists WHERE id IN ({marks})", ids))
