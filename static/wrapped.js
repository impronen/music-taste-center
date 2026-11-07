/* The year in review. Reads the embedded dataset (#wrapped-data) and cover images (#wrapped-art, data URIs so a
   canvas may read their pixels) and draws one card per id. Picture treatments are canvas drawings of a cover:
   ASCII, braille, halftone, Bayer dither, glitch and LED pixels. Everything from the data goes through
   textContent. `#card=ID&size=story|square` shows a single card (the PNG renderer uses it). */
(function () {
  "use strict";

  const D = JSON.parse(document.getElementById("wrapped-data").textContent);
  const ART = JSON.parse(document.getElementById("wrapped-art").textContent);
  const NB = " ";
  const fi = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 0 });
  const int = (n) => fi.format(n ?? 0).replace(/\s/g, NB);
  const pct = (x) => `${Math.round((x ?? 0) * 100)}${NB}%`;
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (iso) => { const d = new Date(iso + "T12:00:00"); return `${d.getDate()} ${MON[d.getMonth()]}`; };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, cls, text, parent) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function hash(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

  // ---------- covers ----------
  const images = new Map();
  function loadImage(albumId) {
    if (!images.has(albumId)) {
      images.set(albumId, new Promise((resolve) => {
        const uri = ART.albums[albumId];
        if (!uri) return resolve(null);
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = uri;
      }));
    }
    return images.get(albumId);
  }
  const albumOfArtist = (artistId) => ART.artists[artistId];

  // A cover with no file still gets a picture: seeded blobs, so the treatments have something to chew on.
  function blobs(seed, size) {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const g = c.getContext("2d"), r = rng(hash(seed));
    g.fillStyle = "#777"; g.fillRect(0, 0, size, size);
    for (let i = 0; i < 14; i++) {
      const gr = g.createRadialGradient(r() * size, r() * size, 0, r() * size, r() * size, size * (.2 + r() * .5));
      const v = Math.floor(r() * 255);
      gr.addColorStop(0, `rgba(${v},${(v * 2) % 255},${(v * 3) % 255},.9)`); gr.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = gr; g.fillRect(0, 0, size, size);
    }
    return c;
  }
  function sample(source, n) { // square cover-crop to n x n pixels
    const c = document.createElement("canvas");
    c.width = c.height = n;
    const g = c.getContext("2d", { willReadFrequently: true });
    const w = source.naturalWidth || source.width, h = source.naturalHeight || source.height, s = Math.min(w, h);
    g.drawImage(source, (w - s) / 2, (h - s) / 2, s, s, 0, 0, n, n);
    return { g, data: g.getImageData(0, 0, n, n).data, n };
  }
  const lum = (d, i) => (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) / 255;
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];

  const STYLES = {
    ascii(g, S, px, fg, bg, seed) {
      const ramp = " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$";
      const cols = 64, cell = px / cols, { data, n } = sample(S, cols);
      g.fillStyle = bg; g.fillRect(0, 0, px, px);
      g.fillStyle = fg; g.font = `700 ${cell * 1.5}px ui-monospace, Menlo, monospace`; g.textBaseline = "top";
      for (let y = 0; y < cols; y++) for (let x = 0; x < cols; x++) {
        const l = lum(data, (y * n + x) * 4);
        g.fillText(ramp[Math.floor(l * (ramp.length - 1))], x * cell, y * cell - cell * .15);
      }
    },
    braille(g, S, px, fg, bg) {
      const cols = 56, rows = 56, { data, n } = sample(S, cols * 2); // 2x4 dots per glyph, drawn as dots
      const w = px / n, h = px / n;
      g.fillStyle = bg; g.fillRect(0, 0, px, px);
      const err = new Float32Array(n * n);
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) err[y * n + x] = lum(data, (y * n + x) * 4);
      g.fillStyle = fg;
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
        const old = err[y * n + x], v = old < .5 ? 0 : 1, e = old - v;
        if (x + 1 < n) err[y * n + x + 1] += e * 7 / 16;
        if (y + 1 < n) { if (x > 0) err[(y + 1) * n + x - 1] += e * 3 / 16; err[(y + 1) * n + x] += e * 5 / 16; if (x + 1 < n) err[(y + 1) * n + x + 1] += e / 16; }
        if (!v) { g.beginPath(); g.arc(x * w + w / 2, y * h + h / 2, Math.min(w, h) * .38, 0, 6.3); g.fill(); }
      }
    },
    halftone(g, S, px, fg, bg) {
      const cols = 44, cell = px / cols, { data, n } = sample(S, cols);
      g.fillStyle = bg; g.fillRect(0, 0, px, px); g.fillStyle = fg;
      for (let y = 0; y < cols; y++) for (let x = 0; x < cols; x++) {
        const dark = 1 - lum(data, (y * n + x) * 4);
        g.beginPath(); g.arc(x * cell + cell / 2, y * cell + cell / 2, cell * .72 * Math.sqrt(dark), 0, 6.3); g.fill();
      }
    },
    bayer(g, S, px, fg, bg) {
      const n = 160, { data } = sample(S, n), cell = px / n;
      g.fillStyle = bg; g.fillRect(0, 0, px, px); g.fillStyle = fg;
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
        const t = (BAYER[(y % 4) * 4 + (x % 4)] + .5) / 16;
        if (lum(data, (y * n + x) * 4) < t) g.fillRect(x * cell, y * cell, Math.ceil(cell), Math.ceil(cell));
      }
    },
    glitch(g, S, px, fg, bg, seed) {
      const r = rng(hash(seed)), { data, n } = sample(S, 120), cell = px / n;
      g.fillStyle = bg; g.fillRect(0, 0, px, px);
      const shift = Math.round(n * .04), bands = [];
      for (let y = 0; y < n; ) { const h = 1 + Math.floor(r() * 9); bands.push([y, h, r() < .3 ? Math.round((r() - .5) * n * .3) : 0]); y += h; }
      for (const [y0, h, off] of bands) for (let y = y0; y < Math.min(n, y0 + h); y++) for (let x = 0; x < n; x++) {
        const sx = (x - off + n) % n, i = (y * n + sx) * 4, ir = (y * n + Math.min(n - 1, sx + shift)) * 4, ib = (y * n + Math.max(0, sx - shift)) * 4;
        g.fillStyle = `rgb(${data[ir]},${data[i + 1]},${data[ib + 2]})`;
        g.fillRect(x * cell, y * cell, Math.ceil(cell), Math.ceil(cell));
      }
    },
    pixels(g, S, px, fg, bg) {
      const cols = 28, cell = px / cols, { data, n } = sample(S, cols);
      g.fillStyle = bg; g.fillRect(0, 0, px, px);
      for (let y = 0; y < cols; y++) for (let x = 0; x < cols; x++) {
        const i = (y * n + x) * 4;
        g.fillStyle = `rgb(${data[i]},${data[i + 1]},${data[i + 2]})`;
        g.beginPath(); g.arc(x * cell + cell / 2, y * cell + cell / 2, cell * .46, 0, 6.3); g.fill();
      }
    },
  };
  const STYLE_NAMES = Object.keys(STYLES);

  function cover(parent, albumId, size, style, card) {
    const canvas = el("canvas", "cover", null, parent);
    canvas.width = canvas.height = size;
    canvas.style.width = "100%";
    const css = getComputedStyle(card);
    const fg = css.getPropertyValue("--fg").trim(), bg = css.getPropertyValue("--bg").trim();
    const name = style || STYLE_NAMES[hash(`${D.year}:${albumId}`) % STYLE_NAMES.length];
    canvas.dataset.style = name;
    const ready = loadImage(albumId).then((img) => {
      STYLES[name](canvas.getContext("2d"), img || blobs(albumId, 256), size, fg, bg, `${D.year}:${albumId}`);
    });
    pending.push(ready);
    return canvas;
  }
  const pending = [];

  // ---------- cards ----------
  const kicker = (card, label, n) => { const k = el("div", "kicker", null, card); el("b", null, `${D.year}`, k); el("span", null, `${label}`, k); el("span", null, n, k); };
  const top = (list, i) => list[i];
  const L = D.top_artists || [];

  const CARDS = {
    intro(card) {
      kicker(card, "your year in plays", "01");
      el("div", "mono", "you pressed play", card);
      el("div", "hero glitch", String(D.year), card);
      el("div", "num", int(D.totals.plays), card).style.fontSize = "var(--fs-hero)";
      el("p", "lead", `times. on ${int(D.totals.active_days)} days. across ${int(D.totals.artists)} artists and ${int(D.totals.tracks)} songs.`, card);
      const v = D.versus_last_year.plays;
      if (v.ratio != null) {
        const s = el("div", "stamp tilt-l", v.change >= 0 ? `+${int(v.change)}` : `−${int(-v.change)}`, card);
        el("small", null, `plays against ${D.year - 1} (${int(v.before)})`, s);
      }
      el("div", "spacer", null, card);
      const t = el("div", "ticker", null, card);
      el("span", null, (D.top_tracks || []).map((x) => `${x.name} — ${x.artist}`).join("   ///   "), t);
    },
    top_artist(card) {
      kicker(card, "the one", "02");
      const a = L[0], album = albumOfArtist(a.id);
      el("div", "mono", "most played artist", card);
      el("h2", "big glitch", a.name, card);
      const row = el("div", "row", null, card);
      el("div", "num", int(a.plays), row);
      el("div", "lead", `plays on ${int(a.days)} days`, row);
      const frame = el("div", "hard", null, card);
      frame.style.width = card.classList.contains("square") ? "360px" : "760px";
      cover(frame, album ?? `artist-${a.id}`, 640, "ascii", card);
      el("div", "spacer", null, card);
      const rest = el("div", "log more", null, card);
      L.slice(1, 5).forEach((x, i) => { const p = el("div", null, null, rest); el("span", "m", String(i + 2).padStart(2, "0"), p); p.append(`${x.name} `); el("span", "n", int(x.plays), p); });
    },
    tracks(card) {
      kicker(card, "on repeat", "03");
      el("h2", "big tilt-l", "your receipt", card);
      const r = el("div", "receipt", null, card);
      el("h3", null, `TOP ${Math.min(10, D.top_tracks.length)} — ${D.year}`, r);
      D.top_tracks.slice(0, 10).forEach((t, i) => {
        const line = el("div", "line" + (i >= 5 ? " more" : ""), null, r);
        el("span", null, String(i + 1).padStart(2, "0"), line);
        const t1 = el("span", "t", t.name, line); el("small", null, t.artist, t1);
        el("span", "lead-dots", null, line); el("span", null, int(t.plays), line);
      });
      el("div", "foot", `TOTAL PLAYS: ${int(D.top_tracks.reduce((s, t) => s + t.plays, 0))}`, r);
      if (D.repeat_day) {
        const s = el("div", "stamp tilt-r", `${int(D.repeat_day.plays)}× in one day`, card);
        el("small", null, `${D.repeat_day.name} on ${day(D.repeat_day.day)}`, s);
      }
    },
    albums(card) {
      kicker(card, "records", "04");
      el("h2", "big", "albums on the wall", card);
      const grid = el("div", "grid3", null, card);
      D.top_albums.slice(0, 9).forEach((a, i) => {
        const t = el("div", "tile", null, grid);
        cover(t, a.id, 360, null, card);
        el("i", null, String(i + 1), t);
      });
      const a = D.top_albums[0];
      el("div", "lead", `#1: ${a.name} — ${a.artist}`, card);
      el("div", "mono", `${int(a.plays)} plays`, card);
    },
    clock(card) {
      kicker(card, "when", "05");
      el("h2", "big", "the clock", card);
      const max = Math.max(...D.clock.flat(), 1), ramp = " .:-=+*#%@";
      let peak = [0, 0];
      D.clock.forEach((row, d) => row.forEach((n, h) => { if (n > D.clock[peak[0]][peak[1]]) peak = [d, h]; }));
      const grid = el("div", "clockgrid", null, card);
      el("span", null, "", grid);
      for (let h = 0; h < 24; h++) el("span", "hh", h % 3 === 0 ? String(h).padStart(2, "0") : "", grid);
      D.clock.forEach((row, d) => {
        el("span", "wd", WD[d], grid);
        row.forEach((n, h) => {
          const c = el("span", "cell" + (d === peak[0] && h === peak[1] ? " peak" : ""), ramp[Math.round((n / max) * (ramp.length - 1))].trim() || "·", grid);
          c.style.setProperty("--k", (0.08 + 0.92 * Math.sqrt(n / max)).toFixed(2));
        });
      });
      const night = D.clock.reduce((s, r) => s + r.slice(0, 6).reduce((a, b) => a + b, 0), 0);
      el("p", "lead", `peak: ${WD[peak[0]]} ${String(peak[1]).padStart(2, "0")}:00`, card);
      el("div", "mono", `${pct(night / D.totals.plays)} of your plays were between midnight and 6.`, card);
    },
    calendar(card) {
      kicker(card, "every day", "06");
      el("h2", "big", `${int(D.totals.active_days)} days`, card);
      const c = el("canvas", "cover", null, card); // a row per month, a square per day
      const lab = 96, cw = 28, gap = 3, ch = card.classList.contains("square") ? 30 : 68, cal = D.calendar, max = Math.max(...Object.values(cal), 1);
      c.width = lab + 31 * (cw + gap); c.height = 12 * (ch + gap); c.style.width = "100%"; c.style.animation = "none";
      const g = c.getContext("2d"), css = getComputedStyle(card), fg = css.getPropertyValue("--fg").trim(), acc = css.getPropertyValue("--acc").trim();
      g.font = "700 26px ui-monospace, Menlo, monospace"; g.textBaseline = "middle";
      for (let m = 0; m < 12; m++) {
        g.globalAlpha = 1; g.fillStyle = fg; g.fillText(MON[m].toUpperCase(), 0, m * (ch + gap) + ch / 2);
        for (let d = 1; d <= new Date(D.year, m + 1, 0).getDate(); d++) {
          const iso = `${D.year}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`, n = cal[iso] || 0;
          g.globalAlpha = n ? .25 + .75 * Math.sqrt(n / max) : .12;
          g.fillStyle = iso === (D.busiest_day || {}).day ? acc : fg;
          g.fillRect(lab + (d - 1) * (cw + gap), m * (ch + gap), cw, ch);
        }
      }
      if (D.streak.days) {
        const s = el("div", "stamp tilt-l", `${int(D.streak.days)}-day streak`, card);
        el("small", null, `${day(D.streak.start)} → ${day(D.streak.end)}`, s);
      }
      el("div", "mono", `busiest day: ${day(D.busiest_day.day)}, ${int(D.busiest_day.plays)} plays`, card);
      (D.sessions || []).slice(0, 1).forEach((s) => el("div", "mono", `longest sitting: ${int(s.plays)} plays, ${int(s.minutes)} min, ${day(s.day)}`, card));
    },
    genres(card) {
      kicker(card, "sound", "07");
      el("h2", "big", "the sound", card);
      const g = D.top_genres, items = g.items.slice(0, 8), max = items[0].share;
      const bars = el("div", "bars", null, card);
      items.forEach((it, i) => {
        const b = el("div", "bar" + (i >= 5 ? " more" : ""), null, bars), head = el("div", "bh", null, b);
        el("span", null, it.name, head); el("em", null, pct(it.share), head);
        el("i", null, null, el("div", "track", null, b)).style.width = `${Math.max(4, (it.share / max) * 100)}%`;
      });
      el("div", "mono", `based on ${pct(g.coverage)} of your plays that have genre tags.`, card);
    },
    months(card) {
      kicker(card, "month by month", "08");
      el("h2", "big", "who owned each month", card);
      const log = el("div", "log", null, card);
      D.artist_of_month.forEach((m, i) => {
        const p = el("div", null, null, log);
        el("span", "m", MON[i].toUpperCase(), p);
        p.append(m ? `${m.name} ` : "— ");
        if (m) el("span", "n", int(m.plays), p);
      });
      el("span", "cursor", null, el("div", "log", null, card));
    },
    discovery(card) {
      kicker(card, "taste", "09");
      el("h2", "big", "new to you", card);
      el("div", "num", pct(D.totals.new_artist_share), card);
      el("p", "lead", `of plays went to the ${int(D.totals.new_artists)} artists you found this year.`, card);
      const v = D.versus_last_year.new_artists;
      el("div", "mono", `${D.year - 1}: ${int(v.before)} new artists`, card);
      const waffle = el("canvas", "cover", null, card); // 100 squares, one per percent of plays
      waffle.width = waffle.height = 10 * 76; waffle.style.width = card.classList.contains("square") ? "300px" : "780px"; waffle.style.animation = "none";
      const wg = waffle.getContext("2d"), wcss = getComputedStyle(card), filled = Math.round((D.totals.new_artist_share || 0) * 100);
      for (let i = 0; i < 100; i++) {
        wg.fillStyle = i < filled ? wcss.getPropertyValue("--acc").trim() : wcss.getPropertyValue("--fg").trim();
        wg.globalAlpha = i < filled ? 1 : .18;
        wg.fillRect((i % 10) * 76, Math.floor(i / 10) * 76, 70, 70);
      }
      el("div", "spacer", null, card);
      (D.archetypes || []).forEach((a, i) => {
        const s = el("div", "stamp " + (i % 2 ? "tilt-r" : "tilt-l"), a.label, card);
        el("small", null, a.fact, s);
      });
    },
    moments(card) {
      kicker(card, "oddities", "10");
      el("h2", "big", "things we noticed", card);
      const h = D.highlights || {}, log = el("div", "log", null, card);
      const add = (m, t) => { const p = el("div", null, null, log); el("span", "m", m, p); p.append(t); };
      if (h.comeback) add("BACK", `${h.comeback.name} returned after ${int(Math.round(h.comeback.gap_days / 30))} months.`);
      if (h.one_song) add("ONCE", `${int(h.one_song.artists)} of ${int(h.one_song.of)} artists got a single song.`);
      if (h.double_life) add("2×", `${WD[h.double_life.days[0].weekday]}: ${h.double_life.days[0].name}. ${WD[h.double_life.days[1].weekday]}: ${h.double_life.days[1].name}.`);
      if (h.song_age) add("AGE", `the music you played was ${int(h.song_age.years)} years old on average.`);
      if (h.obscure) add("RARE", `${h.obscure.name}: about ${int(h.obscure.listeners)} listeners worldwide.`);
      if (D.sessions && D.sessions[0]) add("LONG", `${int(D.sessions[0].plays)} plays in one sitting on ${day(D.sessions[0].day)}.`);
      el("div", "log", null, card).append(el("span", "cursor"));
    },
    ages(card) {
      kicker(card, "age", "11");
      const r = D.release_years;
      el("h2", "big", `median year: ${r.median}`, card);
      const years = Object.keys(r.histogram).map(Number), lo = Math.min(...years), hi = Math.max(...years), max = Math.max(...Object.values(r.histogram));
      const h = el("div", "hist", null, card);
      for (let y = lo; y <= hi; y++) {
        const b = el("i", y === D.year ? "mark" : "", null, h);
        b.style.height = `${((r.histogram[y] || 0) / max) * 100}%`;
      }
      const ax = el("div", "axis", null, card); el("span", null, String(lo), ax); el("span", null, String(hi), ax);
      if (r.this_year_share != null) el("p", "lead", `${pct(r.this_year_share)} was released in ${D.year}.`, card);
      el("div", "mono", `based on ${pct(r.dated_plays / r.total_plays)} of plays with a known release date.`, card);
    },
    loved(card) {
      kicker(card, "kept", "12");
      el("div", "num", int(D.loved.count), card);
      el("p", "lead", "tracks you loved this year.", card);
      const log = el("div", "log", null, card);
      D.loved.recent.slice(0, 8).forEach((t, i) => { const p = el("div", i >= 4 ? "more" : "", null, log); el("span", "m", "♥", p); p.append(`${t.artist} — ${t.title}`); });
    },
    outro(card) {
      kicker(card, "end of file", "13");
      el("h2", "hero glitch", `see you ${D.year + 1}`, card).style.fontSize = "var(--fs-xl)";
      const mosaic = el("div", "mosaic", null, card);
      const ids = Object.keys(ART.albums).filter((k) => ART.albums[k]).slice(0, 20);
      (ids.length ? ids : D.top_albums.map((a) => String(a.id))).slice(0, 20).forEach((id) => cover(mosaic, id, 180, null, card));
      el("div", "spacer", null, card);
      el("div", "mono", `${int(D.totals.plays)} plays · ${int(D.totals.artists)} artists · ${int(D.totals.tracks)} songs`, card);
    },
  };

  function build(id, size, index) {
    const card = el("article", `card ${size}`);
    card.dataset.pal = String(index % 8);
    card.dataset.card = id;
    document.body.appendChild(card); // computed style needs it attached
    CARDS[id](card);
    const last = card.lastElementChild;
    if (last && !last.classList.contains("spacer") && !last.classList.contains("ticker")) last.style.marginTop = "auto";
    if (!card.querySelector(".ticker")) {
      const t = el("div", "ticker", null, card);
      el("span", null, (D.top_artists || []).map((a) => a.name).join("   ///   ") + "   ///   " + D.year, t);
    }
    return card;
  }

  async function main() {
    const ids = D.cards, params = new URLSearchParams(location.hash.slice(1));
    if (params.get("card")) { // single card for the PNG renderer
      document.body.classList.add("shot");
      const size = params.get("size") === "square" ? "square" : "story";
      build(params.get("card"), size, ids.indexOf(params.get("card")));
    } else {
      const root = el("main", "report"), head = el("header", null, null, root);
      el("span", null, `${D.year} · taste center`, head);
      el("span", null, `${int(D.totals.plays)} plays`, head);
      document.body.appendChild(root);
      const fit = [];
      ids.forEach((id, i) => {
        const frame = el("div", "frame", null, root), card = build(id, "story", i);
        frame.appendChild(card);
        fit.push([frame, card]);
        if (D.pngs) { const dl = el("div", "dl", null, root), a = el("a", null, "story png", dl); a.href = `cards/${String(i + 1).padStart(2, "0")}-${id}-story.png`; dl.append(" · "); const b = el("a", null, "square png", dl); b.href = `cards/${String(i + 1).padStart(2, "0")}-${id}-square.png`; }
      });
      const resize = () => { const k = Math.min(1, (Math.min(innerWidth, 1100) - 32) / 1080); fit.forEach(([f, c]) => { f.style.width = `${1080 * k}px`; f.style.height = `${1920 * k}px`; c.style.transform = `scale(${k})`; }); };
      addEventListener("resize", resize); resize();
    }
    await Promise.all([document.fonts.ready, ...pending]);
    document.title = `${D.year} wrapped`;
    document.documentElement.dataset.ready = "1";
  }
  main();
})();
