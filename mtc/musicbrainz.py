"""MusicBrainz client for release dates (https://musicbrainz.org/doc/MusicBrainz_API).

Rate limit: 1 request/second with a meaningful User-Agent. Dates come from the release
*group* (first-release-date), i.e. when the album first came out, not a later reissue.
"""
import re

from .ingest import key
from .webapi import ApiError, JsonApi, NotFound, Transient

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def lucene_quote(text: str) -> str:
    return '"' + _LUCENE_SPECIAL.sub(r"\\\1", text) + '"'


def _group(rg: dict) -> dict:
    return {
        "release_group_mbid": rg.get("id"),
        "release_date": rg.get("first-release-date") or None,
        "release_type": rg.get("primary-type") or None,
        "title": rg.get("title"),
    }


class MusicBrainz(JsonApi):
    base_url = "https://musicbrainz.org/ws/2/"
    min_interval = 1.1

    def _interpret(self, status: int, data: dict | None) -> dict:
        if status in (400, 404):  # 400 = malformed MBID, 404 = unknown MBID
            raise NotFound(f"HTTP {status}")
        if status in (429, 503) or status >= 500 or data is None:
            raise Transient(f"HTTP {status}")
        if status != 200:
            raise ApiError(f"HTTP {status}")
        return data

    def release_group_of_release(self, release_mbid: str) -> dict:
        """Look up a release MBID (what last.fm returns for albums) and return its group."""
        data = self.get(f"release/{release_mbid}", {"inc": "release-groups", "fmt": "json"})
        rg = data.get("release-group")
        if not rg:
            raise NotFound("release has no release group")
        return _group(rg)

    def search_release_group(self, artist: str, title: str, min_score: int = 90) -> dict:
        """Best release group whose title matches exactly (normalized) and whose credited
        artist matches, among results scoring at least min_score."""
        data = self.get("release-group/", {
            "query": f"releasegroup:{lucene_quote(title)} AND artist:{lucene_quote(artist)}",
            "fmt": "json", "limit": "5",
        })
        want_title, want_artist = key(title), key(artist)
        for rg in data.get("release-groups") or []:
            credit = " ".join(
                (c.get("name") or "") + (c.get("joinphrase") or "") for c in rg.get("artist-credit") or []
            )
            if (rg.get("score") or 0) >= min_score and key(rg.get("title") or "") == want_title \
                    and want_artist in key(credit):
                return _group(rg)
        raise NotFound("no confident match")
