/* The year in review. Reads the embedded dataset (#wrapped-data) and cover images (#wrapped-art, data URIs so a
   canvas may read their pixels) and draws one card per id. Picture treatments are canvas drawings of a cover:
   mixed coloured glyphs as the main look, with glitch, CRT and torn variants. Everything from the data goes through
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

  // "Mixed glyphs" (ascii-magic.com/styles/mixed): each cell takes the photo's colour and one of five glyph
  // families, picked by a hash of its position, so the texture varies across the frame. Light to dense.
  const FAMILIES = [" .·:+╪╬", " ',-=≡▒", " ^~xX×╳", " ;!|┃║╫", " ·o0O@#"];
  const DARK = 0.1;
  function mixed(g, S, px, seed) {
    const cell = Math.max(5, Math.round(px / 80)), cols = Math.floor(px / cell), { data, n } = sample(S, cols);
    g.fillStyle = "#050505"; g.fillRect(0, 0, px, px);
    g.font = `700 ${cell * 1.3}px ui-monospace, Menlo, monospace`; g.textAlign = "center"; g.textBaseline = "middle";
    const salt = hash(seed);
    for (let y = 0; y < cols; y++) for (let x = 0; x < cols; x++) {
      const i = (y * n + x) * 4;
      const k = 1.6, rgb = [0, 1, 2].map((c) => Math.max(0, Math.min(255, (data[i + c] - 128) * k + 128 + 20)));
      const l = lum(data, i);
      const lc = Math.max(0, Math.min(1, (l - .5) * k + .5 + .08));
      if (lc < DARK) continue;
      const fam = FAMILIES[hash(`${salt}:${x}:${y}`) % FAMILIES.length];
      const ch = fam[Math.max(1, Math.round(lc * (fam.length - 1)))];
      g.fillStyle = `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`;
      g.fillText(ch, x * cell + cell / 2, y * cell + cell / 2);
    }
  }

  // ---- damage: block displacement, channel offset, scan tearing, CRT bulge and phosphor ----
  function photo(g, S, px) { // the picture itself, square-cropped
    const w = S.naturalWidth || S.width, h = S.naturalHeight || S.height, s = Math.min(w, h);
    g.drawImage(S, (w - s) / 2, (h - s) / 2, s, s, 0, 0, px, px);
  }
  function displace(g, px, r, blocks) {
    for (let b = 0; b < blocks; b++) {
      const y = Math.floor(r() * px), h = Math.max(2, Math.floor(px * (.008 + .045 * r()))), dx = Math.round((r() - .5) * px * .16);
      g.drawImage(g.canvas, 0, y, px, h, dx, y, px, h);
    }
  }
  function rgbSplit(g, px, k) {
    const src = g.getImageData(0, 0, px, px), out = g.createImageData(px, px), a = src.data, o = out.data;
    for (let y = 0; y < px; y++) for (let x = 0; x < px; x++) {
      const i = (y * px + x) * 4, ir = (y * px + Math.min(px - 1, x + k)) * 4, ib = (y * px + Math.max(0, x - k)) * 4;
      o[i] = a[ir]; o[i + 1] = a[i + 1]; o[i + 2] = a[ib + 2]; o[i + 3] = 255;
    }
    g.putImageData(out, 0, 0);
  }
  function scanlines(g, px, step, alpha) {
    g.fillStyle = `rgba(0,0,0,${alpha})`;
    for (let y = 0; y < px; y += step) g.fillRect(0, y, px, Math.max(1, step / 3));
  }
  function barrel(g, px) { // squeeze rows towards top and bottom, like a curved tube
    const tmp = document.createElement("canvas"); tmp.width = tmp.height = px;
    tmp.getContext("2d").drawImage(g.canvas, 0, 0);
    g.fillStyle = "#000"; g.fillRect(0, 0, px, px);
    for (let y = 0; y < px; y += 2) {
      const ny = (y / px) * 2 - 1, w = px * (1 - .05 * ny * ny), x = (px - w) / 2;
      g.drawImage(tmp, 0, y, px, 2, x, y, w, 2);
    }
    const v = g.createRadialGradient(px / 2, px / 2, px * .35, px / 2, px / 2, px * .75);
    v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.4)");
    g.fillStyle = v; g.fillRect(0, 0, px, px);
  }

  // ascii-magic "glitch": the photo underneath, sparse glowing glyphs over it, then torn and channel-shifted
  function glitchy(g, S, px, seed) {
    const r = rng(hash(seed + ":g"));
    g.filter = "saturate(1.5) contrast(1.15) brightness(.85)"; photo(g, S, px); g.filter = "none";
    const cell = Math.max(7, Math.round(px / 56)), cols = Math.floor(px / cell), { data, n } = sample(S, cols), set = "X0$S8#:+";
    g.font = `700 ${cell * 1.2}px ui-monospace, Menlo, monospace`; g.textAlign = "center"; g.textBaseline = "middle";
    g.shadowColor = "rgba(255,238,200,.95)"; g.shadowBlur = cell;
    g.fillStyle = "rgba(255,244,214,.95)";
    for (let y = 0; y < cols; y++) for (let x = 0; x < cols; x++) {
      if (lum(data, (y * n + x) * 4) > .55 && r() < .4) g.fillText(set[Math.floor(r() * set.length)], x * cell + cell / 2, y * cell + cell / 2);
    }
    g.shadowBlur = 0;
    displace(g, px, r, 4); rgbSplit(g, px, Math.max(2, Math.round(px * .008))); displace(g, px, r, 1); scanlines(g, px, 4, .14);
  }
  function crt(g, S, px, seed) {
    const small = document.createElement("canvas"), k = 72; small.width = small.height = k;
    const sg = small.getContext("2d"); sg.filter = "saturate(1.4) contrast(1.2)"; photo(sg, S, k);
    g.imageSmoothingEnabled = false; g.drawImage(small, 0, 0, px, px); g.imageSmoothingEnabled = true;
    for (let x = 0; x < px; x += 3) { // phosphor stripes
      g.fillStyle = "rgba(255,0,0,.08)"; g.fillRect(x, 0, 1, px);
      g.fillStyle = "rgba(0,255,0,.08)"; g.fillRect(x + 1, 0, 1, px);
      g.fillStyle = "rgba(0,80,255,.09)"; g.fillRect(x + 2, 0, 1, px);
    }
    scanlines(g, px, 3, .26);
    g.globalCompositeOperation = "lighter"; g.globalAlpha = .22; g.filter = `blur(${Math.max(2, px / 90)}px)`; g.drawImage(g.canvas, 0, 0);
    g.globalCompositeOperation = "source-over"; g.globalAlpha = 1; g.filter = "none";
    const r = rng(hash(seed + ":c")); displace(g, px, r, 1); rgbSplit(g, px, Math.max(1, Math.round(px * .004)));
    barrel(g, px);
  }
  function torn(g, S, px, seed) { // the main look, broken
    const r = rng(hash(seed + ":t"));
    mixed(g, S, px, seed); displace(g, px, r, 5); rgbSplit(g, px, Math.max(2, Math.round(px * .007))); scanlines(g, px, 4, .12);
  }
  const LOOKS = [[mixed, .5], [glitchy, .22], [crt, .16], [torn, .12]];
  function pickLook(seed) {
    let v = (hash(`look:${seed}`) % 1000) / 1000;
    for (const [fn, w] of LOOKS) { if (v < w) return fn; v -= w; }
    return mixed;
  }

  function cover(parent, albumId, size) {
    const canvas = el("canvas", "cover", null, parent), seed = `${D.year}:${albumId}`, look = pickLook(seed);
    canvas.width = canvas.height = size;
    canvas.style.width = "100%";
    canvas.dataset.style = look.name;
    pending.push(loadImage(albumId).then((img) => look(canvas.getContext("2d", { willReadFrequently: true }), img || blobs(albumId, 256), size, seed)));
    return canvas;
  }
  const pending = [];

  // The title plate printed under a cover: name from the manifest, or from the top albums.
  function caption(parent, albumId, rank) {
    const n = (ART.names || {})[albumId] || (D.top_albums || []).filter((a) => String(a.id) === String(albumId)).map((a) => ({ title: a.name, artist: a.artist }))[0];
    if (!n) return null;
    const c = el("div", "cap", null, parent);
    el("b", null, (rank ? `${rank}  ` : "") + n.title, c);
    el("span", null, n.artist, c);
    return c;
  }

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
      const lead = (D.top_albums || [])[0];
      if (lead) {
        const wrap = el("div", "plate more", null, card), win = el("div", "arch", null, wrap);
        wrap.style.width = "520px"; wrap.style.marginLeft = "auto";
        cover(win, lead.id, 520);
        caption(wrap, lead.id);
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
      const wrap = el("div", "plate", null, card), frame = el("div", "arch tall", null, wrap);
      wrap.style.width = card.classList.contains("square") ? "360px" : "620px";
      cover(frame, album ?? `artist-${a.id}`, 640);
      if (album != null) caption(wrap, album);
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
        const t = el("div", "tile" + (i >= 8 ? " more" : ""), null, grid), w = el("div", "arch", null, t);
        cover(w, a.id, 360);
        el("i", null, String(i + 1), w);
        const c = el("div", "cap", null, t);
        el("b", null, a.name, c); el("span", null, a.artist, c);
      });
      const a = D.top_albums[0];
      el("div", "mono", `#1 ${a.name} — ${int(a.plays)} plays`, card);
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
      waffle.width = waffle.height = 10 * 76; waffle.style.width = card.classList.contains("square") ? "300px" : "100%"; waffle.style.animation = "none";
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
      const h = D.highlights || {}, log = el("div", "log roomy", null, card);
      let shown = 0;
      const add = (m, t) => { const p = el("div", shown++ >= 3 ? "more" : "", null, log); el("span", "m", m, p); p.append(t); };
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
      (ids.length ? ids : D.top_albums.map((a) => String(a.id))).slice(0, 20).forEach((id) => cover(mosaic, id, 180));
      el("div", "spacer", null, card);
      el("div", "mono", `${int(D.totals.plays)} plays · ${int(D.totals.artists)} artists · ${int(D.totals.tracks)} songs`, card);
    },
  };

  // Cards after the first four are laid out like a torn collage: blocks pushed to different sides, tilted,
  // overlapping, with a huge ghost number and stray debris text behind them. Seeded, so reruns match.
  const SCATTER = new Set(["clock", "calendar", "genres", "months", "discovery", "moments", "ages", "loved", "outro"]);
  const WIDE = ".clockgrid, canvas, .bars, .hist, .mosaic, .log, .axis, .receipt";
  function scatter(card, body, id, index) {
    const r = rng(hash(`${D.year}:${id}:layout`)), sq = card.classList.contains("square");
    body.classList.add("scatter");
    [...body.children].forEach((n, i) => {
      if (n.classList.contains("spacer")) return;
      if (n.matches(WIDE)) {
        n.style.transform = `rotate(${((r() - .5) * 2.2).toFixed(2)}deg)`;
        if (n.matches(".log")) { if (r() < .5) n.style.marginLeft = "auto"; }
      } else {
        const k = r(), side = k < .38 ? "flex-start" : k < .8 ? "flex-end" : "center";
        n.style.alignSelf = side;
        if (side === "flex-end") n.style.textAlign = "right";
        n.style.maxWidth = `${72 + Math.floor(r() * 24)}%`;
        n.style.transform = `rotate(${((r() - .5) * (n.matches(".stamp") ? 3 : 7)).toFixed(1)}deg)`;
      }
      n.style.marginTop = i && r() < .3 ? `-${18 + Math.floor(r() * 36)}px` : `${Math.floor(r() * (sq ? 16 : 56))}px`;
    });
    distract(card, id, r);
  }

  // Distractions: a different handful per card, picked by the card's seed, so no two cards carry the same noise.
  const SETS = ["▓▒░█▌▐", "╬╪┼╫╳╋", "x+*·:;", "01", "░▒▓▒░", "/\\|-_", "▀▄▌▐■□", "<>{}[]()"];
  const ARTS = ["  *\n ***\n*****\n ***\n  *", "┌──┐\n│><│\n└┬┬┘", "( o_o )\n /|_|\\", "▁▂▃▅▇▅▃▂▁", "$ play --loop\n> _"];
  const KINDS = {
    ghost(card, r, idx) {
      const g = el("div", "ghost", String(idx).padStart(2, "0"), card);
      g.style.top = `${-10 + Math.floor(r() * 50)}%`;
      g.style[r() < .5 ? "left" : "right"] = `${-12 - Math.floor(r() * 12)}%`;
      g.style.transform = `rotate(${Math.round((r() - .5) * 24)}deg)`;
    },
    hex(card, r) {
      const bits = [`ERR_${Math.floor(r() * 900 + 100)}`, `0x${Math.floor(r() * 65535).toString(16).toUpperCase().padStart(4, "0")}`, "[ SIGNAL LOST ]", "///", `${D.year}.${String(1 + Math.floor(r() * 12)).padStart(2, "0")}`];
      for (let i = 0; i < 3; i++) put(card, "debris", bits[Math.floor(r() * bits.length)], r);
    },
    glyphs(card, r) { // a random patch of one glyph family
      const set = [...SETS[Math.floor(r() * SETS.length)]], rows = 3 + Math.floor(r() * 5), cols = 6 + Math.floor(r() * 12);
      put(card, "debris patch", Array.from({ length: rows }, () => Array.from({ length: cols }, () => set[Math.floor(r() * set.length)]).join("")).join("\n"), r);
    },
    art(card, r) { put(card, "debris patch", ARTS[Math.floor(r() * ARTS.length)], r); },
    rec(card, r) { const p = put(card, "debris rec", `● REC 00:${String(Math.floor(r() * 59)).padStart(2, "0")}:${String(Math.floor(r() * 59)).padStart(2, "0")}`, r); p.style.transform = "none"; },
    meter(card, r) { const n = 3 + Math.floor(r() * 8); put(card, "debris", `[${"█".repeat(n)}${"░".repeat(12 - n)}] ${Math.round((n / 12) * 100)}%`, r); },
    crosses(card, r) { for (let i = 0; i < 7; i++) put(card, "debris cross", "+", r); },
    barcode(card, r) {
      const bars = []; let x = 0;
      while (x < 100) { const w = 1 + r() * 4, gap = 1 + r() * 4; bars.push(`var(--fg) ${x}% ${x + w}%`, `transparent ${x + w}% ${x + w + gap}%`); x += w + gap; }
      const b = put(card, "debris barcode", "", r); b.style.background = `linear-gradient(90deg, ${bars.join(",")})`;
    },
  };
  function put(card, cls, text, r) {
    const d = el("div", cls, text, card);
    d.style.top = `${9 + Math.floor(r() * 82)}%`; d.style.left = `${5 + Math.floor(r() * 72)}%`;
    d.style.transform = `rotate(${Math.round((r() - .5) * 20)}deg)`;
    return d;
  }
  function distract(card, id, r) {
    const names = Object.keys(KINDS), n = 2 + (r() < .4 ? 1 : 0), pick = [];
    while (pick.length < n) { const k = names[Math.floor(r() * names.length)]; if (!pick.includes(k)) pick.push(k); }
    const index = D.cards.indexOf(id) + 1;
    pick.forEach((k) => KINDS[k](card, r, index));
  }

  // The first four cards keep their layout but get a lighter version: a few blocks nudged and tilted.
  function nudge(card, body, id) {
    const r = rng(hash(`${D.year}:${id}:nudge`));
    [...body.children].forEach((n) => {
      if (n.classList.contains("spacer") || n.matches(".receipt, .stamp, .plate, .grid3")) {
        if (n.matches(".grid3")) n.style.transform = `rotate(${((r() - .5) * 1.4).toFixed(2)}deg)`;
        return;
      }
      if (n.matches(".big, .hero, .num, .lead, .mono, .row, .log")) {
        n.style.transform = `translateX(${Math.round((r() - .5) * 36)}px) rotate(${((r() - .5) * 3.4).toFixed(1)}deg)`;
      }
    });
    distract(card, id, r);
  }

  // Zalgo: stacked combining marks above, through and below a letter (glyphy.io/font-generator/zalgo-text).
  const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
  const Z_UP = [...range(0x300, 0x314), ...range(0x33d, 0x344), 0x346, ...range(0x34a, 0x34c), ...range(0x350, 0x352), 0x357, 0x35b];
  const Z_MID = [0x315, 0x31b, 0x321, 0x322, 0x327, 0x328, ...range(0x334, 0x338), 0x358];
  const Z_DOWN = [...range(0x316, 0x319), ...range(0x31c, 0x320), ...range(0x323, 0x326), ...range(0x329, 0x333), ...range(0x339, 0x33c), 0x345, ...range(0x347, 0x349), 0x34d, 0x34e, ...range(0x353, 0x356), 0x359, 0x35a, ...range(0x35c, 0x362)];
  function zalgo(text, level, r) {
    const marks = (set, n) => Array.from({ length: n }, () => String.fromCharCode(set[Math.floor(r() * set.length)])).join("");
    return [...text].map((c) => {
      if (!/[\p{L}\p{N}]/u.test(c) || r() > .4) return c;
      return c + marks(Z_UP, Math.ceil(r() * 3 * level)) + (r() < .3 * level ? marks(Z_MID, 1) : "") + marks(Z_DOWN, Math.ceil(r() * 2 * level));
    }).join("");
  }

  function build(id, size, index) {
    const card = el("article", `card ${size}`);
    card.dataset.pal = String(index % 8);
    card.dataset.card = id;
    document.body.appendChild(card); // computed style needs it attached
    CARDS[id](card);
    const body = el("div", "body"); // the kicker stays on top, the rest is centred in what is left
    [...card.children].slice(1).filter((n) => !n.classList.contains("ticker")).forEach((n) => body.appendChild(n));
    card.appendChild(body);
    if (SCATTER.has(id)) scatter(card, body, id, index); else nudge(card, body, id);
    el("div", "roll", null, card).style.top = `${20 + (hash(`${D.year}:${id}:roll`) % 55)}%`; // a CRT refresh bar
    const zr = rng(hash(`${D.year}:${id}:zalgo`)), level = index < 4 ? .7 : zr() < .35 ? .45 : 0; // the tidy cards get it most
    if (level) card.querySelectorAll(".big, .hero").forEach((n) => { n.textContent = zalgo(n.textContent, n.matches(".hero") ? level * .6 : level, zr); n.classList.add("zal"); });
    card.querySelectorAll(".glitch").forEach((n) => { n.dataset.text = n.textContent; });
    el("div", "side l", `taste center ✦ ${D.year}`, card);
    el("div", "side r", `wrapped ✦ ${String(index + 1).padStart(2, "0")}/${String(D.cards.length).padStart(2, "0")}`, card);
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
