"""The year in review as a web page and as PNG cards.

`page` builds one self-contained index.html: the dataset, the cover images (as data URIs, so the
canvas may read their pixels), the fonts, wrapped.css and wrapped.js. `cards` screenshots each card
(story 1080x1920 and square 1080x1080) with headless Chrome through the page's `#card=ID` mode.
"""
import base64
import json
import shutil
import subprocess
from pathlib import Path

from . import config

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
)
SIZES = {"story": (1080, 1920), "square": (1080, 1080)}
FONTS = ("caprasimo-latin", "caprasimo-latin-ext", "figtree-latin", "figtree-latin-ext")
SHOT_TIMEOUT_S = 90


def card_ids(data: dict) -> list[str]:
    """The cards this year has something for, in order."""
    t = data["totals"]
    ids = ["intro"]
    if data.get("top_artists"):
        ids.append("top_artist")
    if data.get("top_tracks"):
        ids.append("tracks")
    if data.get("top_albums"):
        ids.append("albums")
    if t["plays"]:
        ids += ["clock", "calendar"]
    if data.get("top_genres", {}).get("items"):
        ids.append("genres")
    if any(data.get("artist_of_month", [])):
        ids.append("months")
    ids.append("discovery")
    h = data.get("highlights") or {}
    if any(h.values()) or data.get("sessions"):
        ids.append("moments")
    r = data.get("release_years", {})
    if r.get("dated_plays", 0) >= 20:
        ids.append("ages")
    if data.get("loved", {}).get("count"):
        ids.append("loved")
    ids.append("outro")
    return ids


def _font_css() -> str:
    parts = []
    ranges = {"latin": "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,U+2122,U+2212",
              "latin-ext": "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1E00-1EFF,U+2020,U+20A0-20AB"}
    for name in FONTS:
        family = "Caprasimo" if name.startswith("caprasimo") else "Figtree"
        weight = "400" if family == "Caprasimo" else "400 800"
        woff = (config.STATIC_DIR / "fonts" / f"{name}.woff2").read_bytes()
        subset = "latin-ext" if name.endswith("ext") else "latin"
        parts.append(f'@font-face{{font-family:"{family}";font-weight:{weight};'
                     f'src:url(data:font/woff2;base64,{base64.b64encode(woff).decode()}) format("woff2");'
                     f'unicode-range:{ranges[subset]}}}')
    return "\n".join(parts)


def _json(value) -> str:
    """JSON that is safe inside a <script> element."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


def _art(out_dir: Path) -> dict:
    manifest_path = out_dir / "art" / "manifest.json"
    art = {"albums": {}, "artists": {}, "names": {}}
    if not manifest_path.exists():
        return art
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for album, name in manifest.get("albums", {}).items():
        path = out_dir / "art" / name if name else None
        if path and path.is_file():
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            art["albums"][album] = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    art["names"] = manifest.get("names", {})
    art["artists"] = {a: b for a, b in manifest.get("artists", {}).items() if str(b) in art["albums"]}
    return art


def page(data: dict, out_dir: Path, pngs: bool = False) -> Path:
    """Write out_dir/index.html. Returns its path."""
    data = {**data, "cards": card_ids(data), "pngs": pngs}
    css = (config.STATIC_DIR / "wrapped.css").read_text(encoding="utf-8")
    js = (config.STATIC_DIR / "wrapped.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{int(data["year"])} wrapped</title><style>{_font_css()}\n{css}</style></head><body>'
            f'<script id="wrapped-data" type="application/json">{_json(data)}</script>'
            f'<script id="wrapped-art" type="application/json">{_json(_art(out_dir))}</script>'
            f'<script>{js}</script></body></html>')
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def chrome() -> str | None:
    return next((p for p in CHROME_PATHS if Path(p).exists()), None) or shutil.which("google-chrome") or shutil.which("chromium")


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def cards(data: dict, out_dir: Path, progress=None) -> list[Path]:
    """Screenshot every card at both sizes into out_dir/cards/NN-id-size.png. Needs Chrome."""
    binary = chrome()
    if binary is None:
        raise RuntimeError("Chrome was not found. Install Google Chrome, or run with --no-png.")
    index = page(data, out_dir, pngs=True)
    target = out_dir / "cards"
    target.mkdir(exist_ok=True)
    ids = card_ids(data)
    written = []
    for i, card in enumerate(ids, 1):
        for size, (w, h) in SIZES.items():
            path = target / f"{i:02d}-{card}-{size}.png"
            subprocess.run(
                [binary, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                 f"--window-size={w},{h}", "--virtual-time-budget=8000", f"--screenshot={path}",
                 f"{index.as_uri()}#card={card}&size={size}"],
                check=True, capture_output=True, timeout=SHOT_TIMEOUT_S)
            written.append(path)
        if progress:
            progress(i, len(ids))
    return written
