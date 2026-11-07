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
import sqlite3
import urllib.parse
from pathlib import Path

from . import config
from .webapi import ApiError, JsonApi, Transient, Transport, urllib_transport

MAX_BYTES = 5_000_000
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"  # last.fm's star placeholder image
ALLOWED_HOSTS = ("coverartarchive.org", "lastfm.freetls.fastly.net", "last.fm")
COVER_ART_MIN_INTERVAL_S = 1.0  # one request a second: the archive is a free service


def image_kind(body: bytes) -> str | None:
    if body[:3] == b"\xff\xd8\xff":
        return "jpg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


class CoverArt(JsonApi):
    """Binary downloads through JsonApi's throttle and retries (the body is an image, not JSON)."""
    base_url = "https://coverartarchive.org/"
    min_interval = COVER_ART_MIN_INTERVAL_S

    def image(self, url: str) -> tuple[bytes, str] | None:
        """(bytes, 'jpg'|'png') or None when the service has no usable image there."""
        host = urllib.parse.urlsplit(url)
        if host.scheme != "https" or not any(host.hostname == h or host.hostname.endswith("." + h)
                                             for h in ALLOWED_HOSTS):
            return None
        last_error = "unknown"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(min(60.0, 2.0 * 2 ** (attempt - 1)))
            self._throttle()
            self.requests += 1
            try:
                status, body = self.transport(url, {"User-Agent": config.USER_AGENT, "Accept": "image/*"}, self.timeout)
            except (OSError, TimeoutError) as exc:
                last_error = f"network: {exc}"
                continue
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


def fetch(conn: sqlite3.Connection, data: dict, out_dir: Path, transport: Transport = urllib_transport,
          progress=None, sleep=None) -> dict:
    """Download what the year's cards need into out_dir/art/ and write art/manifest.json:
    {"albums": {id: "album-<id>.jpg" | null}, "artists": {artist id: album id}}."""
    art = out_dir / "art"
    art.mkdir(parents=True, exist_ok=True)
    manifest_path = art / "manifest.json"
    manifest = {"albums": {}, "artists": {}}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    client = CoverArt(transport, **({"sleep": sleep} if sleep else {}))
    albums, stands_for = targets(conn, data)
    manifest["artists"] = {str(a): b for a, b in stands_for.items()}
    done = 0
    for album in albums:
        key = str(album)
        have = manifest["albums"].get(key)
        if key in manifest["albums"] and (have is None or (art / have).exists()):
            continue  # already fetched, or already known to have no picture
        found, gave_up = None, False
        for url in _urls(conn, album):
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
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        done += 1
        if progress:
            progress(done, len(albums))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"albums": len(albums), "with_cover": sum(1 for v in manifest["albums"].values() if v),
            "requests": client.requests}
