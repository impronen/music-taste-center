"""Cover images for the year in review, fetched once into data/wrapped/YEAR/art/.

Python only downloads and caches the files; the picture treatments (ASCII, halftone, dither...) are
drawn in the browser from these files, so no image library is needed. There are no artist photos
to fetch (last.fm stopped serving them), so an artist is shown with the cover of the album you
played most by them that year.

Sources, in order: Cover Art Archive by release group, by release, then the last.fm image (unless
it is last.fm's grey placeholder). A miss is remembered in the manifest, so a rerun resumes and
does not ask again.
"""
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .webapi import ApiError, JsonApi, Transient, Transport

MAX_BYTES = 5_000_000
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"  # last.fm's star placeholder image
ALLOWED_HOSTS = ("coverartarchive.org", "archive.org", "lastfm.freetls.fastly.net", "last.fm")  # the archive redirects to archive.org
REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 4
COVER_ART_MIN_INTERVAL_S = 1.0  # one request a second: the archive is a free service


def image_kind(body: bytes) -> str | None:
    if body[:3] == b"\xff\xd8\xff":
        return "jpg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # surface the 3xx so each hop is checked against the allow-list


def image_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """Like webapi.urllib_transport, but never follows redirects (a redirect answers (status, Location))
    and reads at most MAX_BYTES + 1 bytes of the body."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            if exc.code in REDIRECTS:
                return exc.code, (exc.headers.get("Location") or "").encode()
            return exc.code, exc.read(MAX_BYTES + 1)


def allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    return parts.scheme == "https" and bool(host) and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


class CoverArt(JsonApi):
    """Binary downloads through JsonApi's throttle and retries (the body is an image, not JSON)."""
    base_url = "https://coverartarchive.org/"
    min_interval = COVER_ART_MIN_INTERVAL_S

    def _get(self, url: str) -> tuple[int, bytes] | None:
        """One answer, following redirects by hand and checking the host on every hop. None: refused."""
        for _ in range(MAX_HOPS + 1):
            if not allowed(url):
                return None
            self._throttle()
            self.requests += 1
            status, body = self.transport(url, {"User-Agent": config.USER_AGENT, "Accept": "image/*"}, self.timeout)
            if status not in REDIRECTS:
                return status, body
            url = urllib.parse.urljoin(url, body.decode("utf-8", "replace").strip())
        return None

    def image(self, url: str) -> tuple[bytes, str] | None:
        """(bytes, 'jpg'|'png') or None when the service has no usable image there."""
        if not allowed(url):
            return None
        last_error = "unknown"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(min(60.0, 2.0 * 2 ** (attempt - 1)))
            try:
                got = self._get(url)
            except (OSError, TimeoutError) as exc:
                last_error = f"network: {exc}"
                continue
            if got is None:
                return None
            status, body = got
            if status in (400, 403, 404):
                return None
            if status in (429, 503) or status >= 500:
                last_error = f"HTTP {status}"
                continue
            if status != 200 or len(body) > MAX_BYTES:
                return None
            kind = image_kind(body)
            return (body, kind) if kind else None
        raise ApiError(f"gave up after {self.retries + 1} attempts: {last_error}")

    def _interpret(self, status: int, data: dict | None) -> dict:  # JSON is never requested here
        raise Transient("unused")


def targets(conn: sqlite3.Connection, data: dict) -> tuple[list[int], dict[int, int]]:
    """Album ids to fetch, and for each wanted artist the album that stands for it."""
    s, e = data["range"]
    wanted = [a["id"] for a in data.get("top_artists", [])]
    wanted += [m["id"] for m in data.get("artist_of_month", []) if m]
    stands_for: dict[int, int] = {}
    for artist in dict.fromkeys(wanted):
        row = conn.execute(
            "SELECT album_id FROM scrobbles WHERE artist_id = ? AND album_id IS NOT NULL AND lday BETWEEN ? AND ?"
            " GROUP BY album_id ORDER BY COUNT(*) DESC, album_id LIMIT 1", (artist, s, e)).fetchone()
        if row:
            stands_for[artist] = row[0]
    albums = list(dict.fromkeys([*(a["id"] for a in data.get("top_albums", [])), *stands_for.values()]))
    return albums, stands_for


def _load(path: Path) -> dict:
    """The saved manifest, or an empty one when it is missing or unreadable (a run cut off mid-write)."""
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(m, dict) and isinstance(m.get("albums"), dict):
            return {"albums": m["albums"], "artists": m.get("artists") or {}, "names": m.get("names") or {}}
    except (OSError, ValueError):
        pass
    return {"albums": {}, "artists": {}, "names": {}}


def _save(path: Path, manifest: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # a Ctrl-C leaves the old file whole


def _name(conn, album_id: int) -> dict | None:
    row = conn.execute("SELECT al.title, ar.name FROM albums al JOIN artists ar ON ar.id = al.artist_id WHERE al.id = ?",
                       (album_id,)).fetchone()
    return {"title": row[0], "artist": row[1]} if row else None


def _urls(conn, album_id: int) -> list[str]:
    row = conn.execute("SELECT release_group_mbid, mbid, image_url FROM album_info WHERE album_id = ?",
                       (album_id,)).fetchone()
    if row is None:
        return []
    group, release, image = row
    urls = []
    if group:
        urls.append(f"https://coverartarchive.org/release-group/{urllib.parse.quote(group)}/front-500")
    if release:
        urls.append(f"https://coverartarchive.org/release/{urllib.parse.quote(release)}/front-500")
    if image and PLACEHOLDER not in image:
        urls.append(image)
    return urls


def fetch(conn: sqlite3.Connection, data: dict, out_dir: Path, transport: Transport = image_transport,
          progress=None, sleep=None) -> dict:
    """Download what the year's cards need into out_dir/art/ and write art/manifest.json:
    {"albums": {id: "album-<id>.jpg" | null}, "artists": {artist id: album id}, "names": {id: {title, artist}}}."""
    art = out_dir / "art"
    art.mkdir(parents=True, exist_ok=True)
    manifest_path = art / "manifest.json"
    manifest = _load(manifest_path)
    client = CoverArt(transport, **({"sleep": sleep} if sleep else {}))
    albums, stands_for = targets(conn, data)
    manifest["artists"] = {str(a): b for a, b in stands_for.items()}
    manifest["names"] = {str(a): n for a in albums if (n := _name(conn, a))}  # cards print the album title
    done = 0
    for album in albums:
        key = str(album)
        have = manifest["albums"].get(key)
        if key in manifest["albums"] and (have is None or (art / have).exists()):
            continue  # already fetched, or already known to have no picture
        urls = _urls(conn, album)
        if not urls:
            continue  # nothing to ask yet (not enriched): not a miss, so a later run tries again
        found, gave_up = None, False
        for url in urls:
            try:
                found = client.image(url)
            except ApiError:
                gave_up = True  # a bad moment is not "no cover"
                continue
            if found:
                break
        if gave_up and not found:
            continue  # leave it out of the manifest so the next run tries again
        if found:
            name = f"album-{album}.{found[1]}"
            (art / name).write_bytes(found[0])
            manifest["albums"][key] = name
        else:
            manifest["albums"][key] = None
        _save(manifest_path, manifest)
        done += 1
        if progress:
            progress(done, len(albums))
    _save(manifest_path, manifest)
    return {"albums": len(albums), "with_cover": sum(1 for v in manifest["albums"].values() if v),
            "requests": client.requests}
