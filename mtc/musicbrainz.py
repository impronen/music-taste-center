"""MusicBrainz client for release dates (https://musicbrainz.org/doc/MusicBrainz_API).

Rate limit: 1 request/second with a meaningful User-Agent. Dates come from the release
*group* (first-release-date), i.e. when the album first came out, not a later reissue.
"""
import re
import unicodedata

from .ingest import key
from .webapi import ApiError, JsonApi, NotFound, Transient

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')

# Edition markers that streaming services and last.fm append to an album title but MusicBrainz
# keeps out of it ("Rumours (Deluxe Edition)", "Thriller - 2003 Remaster"). A bracketed or " - "
# part is only removed when EVERY word in it belongs to this vocabulary and at least one is a core
# word, so "(Special Guest)", "(Version 2)", "(Bonus)", "(Live)" and "(Disc 2)" are real titles.
_CORE = {"deluxe", "remaster", "remastered", "edition", "expanded", "anniversary", "reissue", "re-issue", "mono", "stereo"}
_FILLER = {"version", "bonus", "track", "tracks", "special", "collector's", "collectors", "limited", "standard", "super",
           "legacy", "platinum", "tour", "mix", "digital", "explicit", "clean", "digipak", "international", "and", "the", "&"}
_YEAR_OR_ORDINAL = re.compile(r"^(?:\d{4}|\d+(?:st|nd|rd|th))$")
_BRACKET_GROUP = re.compile(r"[(\[]([^()\[\]]*)[)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+([^-]*?)\s*$")
# Dashes and brackets that streaming services also use (NFKC already turns fullwidth brackets into ASCII).
_PUNCTUATION = str.maketrans({"–": "-", "—": "-", "‒": "-", "―": "-", "【": "[", "】": "]", "〔": "(", "〕": ")"})


def _is_marker(text: str) -> bool:
    words = [w for w in (w.strip(".,:;") for w in text.lower().split()) if w]
    if not words or not all(w in _CORE or w in _FILLER or _YEAR_OR_ORDINAL.match(w) for w in words):
        return False
    return any(w in _CORE for w in words) or ("bonus" in words and ("track" in words or "tracks" in words))


def _prepared(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).translate(_PUNCTUATION).split())


def clean_title(title: str) -> tuple[str, str | None]:
    """The title without edition markers, plus "EP" or "Single" when a " - EP" / " - Single" suffix
    was removed (those are separate release groups from the album of the same name)."""
    cleaned, release_type = _prepared(title), None
    while True:
        step = " ".join(_BRACKET_GROUP.sub(lambda m: "" if _is_marker(m.group(1)) else m.group(0), cleaned).split())
        suffix = _DASH_SUFFIX.search(step)
        if suffix and suffix.group(1).lower() in ("ep", "single"):
            release_type, step = suffix.group(1).upper() if suffix.group(1).lower() == "ep" else "Single", step[:suffix.start()]
        elif suffix and _is_marker(suffix.group(1)):
            step = step[:suffix.start()]
        step = step.strip()
        if step == cleaned or not step:
            return cleaned, release_type if cleaned != _prepared(title) else None
        cleaned = step


def edition_free_title(title: str) -> str:
    """`title` without edition markers, or `title` itself when there was nothing to remove."""
    cleaned, _ = clean_title(title)
    return title if cleaned == _prepared(title) else cleaned


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
        artist matches, among results scoring at least min_score. When the title carries edition
        markers and the exact title finds nothing, search once more without them, with two guards
        against picking another release of the same artist: a " - EP" / " - Single" title needs a
        group of that type, and a group with secondary types (Live, Compilation, Remix...) is only
        accepted when the original title says so. The artist must match either way."""
        try:
            return self._search(artist, title, {key(title)}, min_score)
        except NotFound:
            cleaned = edition_free_title(title)
            if cleaned == title:
                raise
        return self._search(artist, cleaned, {key(cleaned), key(title)}, min_score, retry_of=title)

    def _search(self, artist: str, title: str, accepted_titles: set[str], min_score: int,
                retry_of: str | None = None) -> dict:
        data = self.get("release-group/", {
            "query": f"releasegroup:{lucene_quote(title)} AND artist:{lucene_quote(artist)}",
            "fmt": "json", "limit": "5",
        })
        want_artist = key(artist)
        wanted_type = clean_title(retry_of)[1] if retry_of else None
        for rg in data.get("release-groups") or []:
            credit = " ".join(
                (c.get("name") or "") + (c.get("joinphrase") or "") for c in rg.get("artist-credit") or []
            )
            if (rg.get("score") or 0) < min_score or key(rg.get("title") or "") not in accepted_titles \
                    or want_artist not in key(credit):
                continue
            if retry_of is not None:
                if wanted_type and rg.get("primary-type") != wanted_type:
                    continue
                if any(t.lower() not in retry_of.lower() for t in rg.get("secondary-types") or []):
                    continue
            return _group(rg)
        raise NotFound("no confident match")
