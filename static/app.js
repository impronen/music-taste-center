/* Taste Center UI: hash router + views (Organic design). Data-derived strings are escaped by the
   html`` tag; charts put labels in via textContent. */
(function () {
  "use strict";
  const { Fmt } = window.Charts;
  const view = document.getElementById("view");

  // ---------- templating ----------
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
  const raw = (s) => new Raw(s);
  const part = (v) => (v instanceof Raw ? v.s : Array.isArray(v) ? v.map(part).join("") : v == null || v === false ? "" : esc(v));
  const html = (strings, ...vals) => raw(strings.reduce((acc, s, i) => acc + s + (i < vals.length ? part(vals[i]) : ""), ""));
  const mount = (el, tpl) => { el.innerHTML = tpl.s; };
  // Each view reads `rendering` synchronously when it starts and mounts through paint(seq, …),
  // which refuses (and stops the view) once a newer route has started.
  class Stale extends Error {}
  let routeSeq = 0, rendering = 0;
  const paint = (seq, tpl) => {
    if (seq !== routeSeq) throw new Stale();
    mount(view, tpl);
  };

  // ---------- data ----------
  // GETs are cached for a few minutes; imports, merges and finished tag fetches clear the cache.
  const TTL_MS = 5 * 60 * 1000;
  const cache = new Map();
  let dataVersion = null;
  function noteVersion(res) {
    const v = res.headers.get("x-data-version");
    if (!v) return;
    if (dataVersion && v !== dataVersion) cache.clear(); // scrobbles or tags changed somewhere
    dataVersion = v;
  }
  async function api(path, { fresh = false } = {}) {
    const hit = cache.get(path);
    if (!fresh && hit && Date.now() - hit.at < TTL_MS) return hit.data;
    const res = await fetch(path);
    noteVersion(res);
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const data = await res.json();
    cache.set(path, { at: Date.now(), data });
    return data;
  }
  async function postJson(path, body, method = "POST", invalid = "Invalid input") {
    const res = await fetch(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
    noteVersion(res);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.status === 422 ? invalid : `HTTP ${res.status}`);
    return data;
  }
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== "")).toString();

  // ---------- icons (Lucide, stroke 2.75) ----------
  const ICONS = {
    calendar: '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
    filter: '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    upload: '<path d="M12 3v12"/><path d="m17 8-5-5-5 5"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>',
    arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    external: '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
  };
  const icon = (name, size = 18) => raw(`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" style="width:${size}px;height:${size}px">${ICONS[name]}</svg>`);

  // ---------- shared bits ----------
  const DOCS = "https://github.com/impronen/music-taste-center/blob/main/docs/how-it-works.md";
  const link = {
    artist: (id, name) => html`<a href="#/artist/${id}">${name}</a>`,
    track: (id, name) => html`<a href="#/track/${id}">${name}</a>`,
    album: (id, name) => html`<a href="#/album/${id}">${name}</a>`,
  };
  const clusterColor = (i) => (i < 5 ? `var(--series-${i + 1})` : "var(--series-other)");
  const year = (d) => (d ? String(d).slice(0, 4) : "");
  const hashOf = (s) => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  // A tinted initial (circle for artists, rounded square for albums), or the cover once fetched.
  const initial = (name, url, square = false) => html`<span class="initial ${square ? "square" : ""} i-${hashOf(name) % 6}">${url
    ? html`<img src="${url}" alt="" loading="lazy" referrerpolicy="no-referrer">` : (String(name).trim()[0] ?? "?").toUpperCase()}</span>`;
  const tagChips = (tags) => (tags?.length ? html`<div class="chips tags">${tags.map((t) =>
    t.kind === "year" || t.kind === "decade"
      ? html`<span class="chip plain">${t.name}</span>`
      : html`<a class="chip ${t.kind === "place" ? "place" : ""}" href="#/tag/${t.id}">${t.name}</a>`)}</div>` : "");
  const DAYPART = (h) => (h < 6 ? "at night" : h < 12 ? "in the morning" : h < 18 ? "in the afternoon" : "after 18:00");
  const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  /* Ranked list: every row is one link (numbered circle, name, value, pill bar).
     opts: { href(it), name(it), sub(it), value(it), cover(it) -> {name,url}, max, sage, compact } */
  function rankList(items, opts) {
    if (!items.length) return html`<p class="empty">Nothing here yet.</p>`;
    const val = opts.value ?? ((it) => it.plays);
    const top = opts.max ?? Math.max(...items.map(val), 1);
    const cls = ["rank", opts.sage ? "sage" : "", opts.compact ? "compact" : "", opts.cover ? "covers" : "", opts.nobar ? "nobar" : ""].join(" ");
    return html`<ol class="${cls}">${items.map((it, i) => html`<li><a href="${opts.href(it)}">
        ${opts.cover ? html`<span class="cov">${initial(opts.cover(it).name, opts.cover(it).url, true)}</span>` : html`<span class="pos">${i + 1}</span>`}
        <span class="name">${opts.name ? opts.name(it) : it.name}${opts.sub ? html`<small>${opts.sub(it)}</small>` : ""}</span>
        <span class="num">${opts.valueText ? opts.valueText(it) : Fmt.int(val(it))}</span>
        <span class="bar"><i style="width:${((val(it) / top) * 100).toFixed(1)}%"></i></span></a></li>`)}</ol>`;
  }
  const artistHref = (it) => `#/artist/${it.id}`;
  const trackHref = (it) => `#/track/${it.id}`;
  const albumHref = (it) => `#/album/${it.id}`;
  // a length of time: "the same day", "1 day", "12 days", "5 months", "2,5 years"
  const span = (seconds) => {
    const days = Math.round(seconds / 86400);
    return days < 1 ? "the same day" : days === 1 ? "1 day" : Fmt.ago(0, seconds).replace(" ago", "");
  };
  const nPlays = (n) => `${Fmt.int(n)} ${n === 1 ? "play" : "plays"}`;
  // how a loved track was loved (track page). A track first played in the history's first month
  // may have been played before it, so it gets "at least" and no claims about the first listen.
  function loveTiming(lv) {
    const n = lv.plays_before;
    if (lv.history_start) {
      if (lv.before_first || lv.just_before) return "Loved before its first scrobble here.";
      return n === 0 ? "Loved during its first scrobble here."
        : `Loved after at least ${nPlays(n)}${lv.after_s >= 86400 ? `, at least ${span(lv.after_s)} after its first scrobble here` : ""}.`;
    }
    if (lv.before_first) return "Loved before you first scrobbled it.";
    if (lv.just_before) return "Loved just before your first scrobble of it.";
    if (n === 0) return "Loved during your first listen.";
    return lv.after_s < 86400 ? `Loved after ${nPlays(n)}, the same day you first played it.`
      : `Loved after ${nPlays(n)}, ${span(lv.after_s)} after your first play.`;
  }
  // a track name with a heart when it's loved on last.fm (rankList's name option)
  const lovedName = (t) => html`${t.name}${t.loved_at ? html` <span class="loved" title="Loved on ${Fmt.date(t.loved_at)}">${icon("heart", 14)}<span class="sr-only">(loved on ${Fmt.date(t.loved_at)})</span></span>` : ""}`;

  /* card(title, body, { sub, cls, aside, foot, id, h3 }); h3 for cards under a section's own h2 */
  function card(title, body, o = {}) {
    if (typeof o === "string" || o instanceof Raw) o = { sub: o };
    return html`<section class="card ${o.cls ?? ""}" ${o.id ? raw(`id="${esc(o.id)}"`) : ""}>
      ${title ? html`<div class="card-head"><div>${o.h3 ? html`<h3>${title}</h3>` : html`<h2>${title}</h2>`}${o.sub ? html`<p>${o.sub}</p>` : ""}</div>${o.aside ? html`<div class="aside">${o.aside}</div>` : ""}</div>` : ""}
      ${body}${o.foot ? html`<div class="card-foot">${o.foot}</div>` : ""}</section>`;
  }
  const tile = (label, value, sub = "", cls = "", small = false) => html`<div class="tile ${cls}"><div class="label">${label}</div>
    <div class="value ${small ? "sm" : ""}">${value}</div>${sub ? html`<div class="sub">${sub}</div>` : ""}</div>`;
  function delta(cur, prev, ref) {
    if (prev == null || !prev) return "";
    const d = cur / prev - 1;
    if (Math.abs(d) < 0.005) return html`<span class="delta flat">± 0 % ${ref}</span>`;
    return html`<span class="delta ${d > 0 ? "up" : "down"}">${d > 0 ? "↑" : "↓"} ${Fmt.pct(Math.abs(d))}${ref ? ` ${ref}` : ""}</span>`;
  }
  const storage = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };
  function scrollToId(id) {
    const el = document.getElementById(id);
    if (el) { el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); el.focus?.({ preventScroll: true }); }
  }

  // ---------- periods ----------
  // Presets count back from the newest scrobble, so an old export still makes sense.
  const QUICK = [["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["365d", "Year"], ["all", "All time"]];
  const isoDay = (ts) => new Date(ts * 1000).toISOString().slice(0, 10);
  // first/last local day of the history (the server's time zone), as YYYY-MM-DD
  const firstDay = (ov) => ov.first_day ?? isoDay(ov.first_ts);
  const lastDay = (ov) => ov.last_day ?? isoDay(ov.last_ts);
  function periods(ov) {
    if (!ov || ov.empty) return [{ key: "all", label: "All time" }];
    const end = new Date(lastDay(ov) + "T00:00:00Z"); // pure date arithmetic in UTC
    const iso = (d) => d.toISOString().slice(0, 10);
    const back = (days) => iso(new Date(end - days * 864e5));
    const out = [
      { key: "7d", label: "Last 7 days", days: 7, start: back(6), end: iso(end) },
      { key: "30d", label: "Last 30 days", days: 30, start: back(29), end: iso(end) },
      { key: "90d", label: "Last 90 days", days: 90, start: back(89), end: iso(end) },
      { key: "365d", label: "Last 12 months", days: 365, start: back(364), end: iso(end) },
    ];
    const y0 = +firstDay(ov).slice(0, 4);
    for (let y = end.getUTCFullYear(); y >= y0; y--) out.push({ key: String(y), label: String(y), start: `${y}-01-01`, end: `${y}-12-31` });
    out.push({ key: "all", label: "All time" });
    return out;
  }
  function parseRange(key, ov) {
    const c = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(key || "");
    if (c) {
      const [start, end] = c[1] <= c[2] ? [c[1], c[2]] : [c[2], c[1]];
      return { key: `${start}..${end}`, label: start === end ? Fmt.day(start) : `${Fmt.day(start)} – ${Fmt.day(end)}`, start, end, custom: true };
    }
    const m = /^(\d{4}-\d{2})$/.exec(key || "");
    if (m) {
      const [y, mo] = m[1].split("-").map(Number);
      const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
      return { key, label: Fmt.month(m[1]), start: `${m[1]}-01`, end: `${m[1]}-${last}`, month: true };
    }
    return periods(ov).find((p) => p.key === key) ?? periods(ov).at(-1);
  }
  const rangeText = (a, b) => (a === b ? Fmt.day(a) : `${Fmt.day(a)} – ${Fmt.day(b)}`);
  function prevRef(period, prev) {
    if (!prev) return "";
    if (/^\d{4}$/.test(period.key)) return `vs ${prev.start.slice(0, 4)}`;
    if (period.month) return `vs ${Fmt.month(prev.start.slice(0, 7)).split(" ")[0]}`;
    if (period.days) return `vs previous ${period.days} days`;
    return "vs the period before";
  }
  // Presets as a pill segment, plus one button that opens a popover with year, month and dates.
  function periodControls(period, ov, { compact = false } = {}) {
    const years = periods(ov).filter((p) => /^\d{4}$/.test(p.key));
    const [lo, hi] = [firstDay(ov), lastDay(ov)];
    const isQuick = QUICK.some(([k]) => k === period.key);
    return html`<div class="period-row">
      ${compact ? "" : html`<div class="seg" role="group" aria-label="Period">${QUICK.map(([k, l]) =>
        html`<button type="button" data-period="${k}" aria-pressed="${String(k === period.key)}">${l}</button>`)}</div>`}
      <div class="popover-wrap">
        <button type="button" class="btn secondary" id="period-more" aria-expanded="false" aria-controls="period-pop">
          ${icon("calendar")} ${compact ? period.label : isQuick ? "A year, month or dates…" : period.label}</button>
        <div class="popover" id="period-pop" role="dialog" aria-label="Choose a period" hidden>
          ${compact ? html`<div class="seg" role="group" aria-label="Presets">${QUICK.map(([k, l]) =>
            html`<button type="button" data-period="${k}" aria-pressed="${String(k === period.key)}">${l}</button>`)}</div>` : ""}
          <label class="field">Year<select id="period-year"><option value="">Choose a year…</option>
            ${years.map((y) => html`<option value="${y.key}" ${y.key === period.key ? raw("selected") : ""}>${y.label}</option>`)}</select></label>
          <label class="field">Month<input type="month" id="period-month" min="${lo.slice(0, 7)}" max="${hi.slice(0, 7)}" value="${period.month ? period.key : ""}"></label>
          <div class="field" style="display:grid;gap:6px;font-weight:600;font-size:var(--fs-sm)">From – to
            <div class="row"><input type="date" id="period-from" min="${lo}" max="${hi}" value="${period.start ?? lo}" aria-label="From">
              <input type="date" id="period-to" min="${lo}" max="${hi}" value="${period.end ?? hi}" aria-label="To"></div></div>
          <div class="actions"><button type="button" class="btn secondary small" id="period-close">Cancel</button>
            <button type="button" class="btn primary small" id="period-apply">Show these dates</button></div>
        </div></div></div>`;
  }
  function bindPeriodControls(onChange) {
    view.querySelectorAll("[data-period]").forEach((b) => b.addEventListener("click", () => onChange(b.dataset.period)));
    const btn = view.querySelector("#period-more"), pop = view.querySelector("#period-pop");
    const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", () => {
      pop.hidden = !pop.hidden;
      btn.setAttribute("aria-expanded", String(!pop.hidden));
      if (!pop.hidden) pop.querySelector("select, input, button")?.focus();
    });
    pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); btn.focus(); } });
    document.addEventListener("click", function outside(e) {
      if (!document.body.contains(pop)) return document.removeEventListener("click", outside);
      if (!e.target.closest(".popover-wrap")) close();
    });
    view.querySelector("#period-close").addEventListener("click", () => { close(); btn.focus(); });
    view.querySelector("#period-year").addEventListener("change", (e) => e.target.value && onChange(e.target.value));
    view.querySelector("#period-month").addEventListener("change", (e) => e.target.value && onChange(e.target.value));
    const from = view.querySelector("#period-from"), to = view.querySelector("#period-to");
    const apply = () => from.value && to.value && onChange(`${from.value}..${to.value}`);
    view.querySelector("#period-apply").addEventListener("click", apply);
    [from, to].forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); }));
  }

  // ---------- views ----------
  function emptyState(seq) {
    paint(seq, html`<div class="page-head"><div><h1>No scrobbles yet</h1>
      <p class="lead">Connect your last.fm account, or import a file of your scrobbles (your listening history), to get started.</p>
      <p style="margin-top:20px"><a class="btn primary" href="#/import">${icon("upload")} Go to Import</a></p></div></div>`);
  }

  async function overviewView(params) {
    const seq = rendering;
    const ov = await api("/api/overview");
    if (ov.empty) return emptyState(seq);
    const period = parseRange(params.get("period") || storage.get("mtc-period") || "30d", ov);
    const range = { start: period.start, end: period.end };
    const [sum, act, timeline, clock, ta, tt, tal, recent, seasonal] = await Promise.all([
      api("/api/summary?" + qs(range)), api("/api/activity?" + qs(range)), api("/api/timeline"), api("/api/clock?" + qs(range)),
      api("/api/top/artist?" + qs({ ...range, limit: 6 })), api("/api/top/track?" + qs({ ...range, limit: 6 })),
      api("/api/top/album?" + qs({ ...range, limit: 6 })), api("/api/recent?limit=8"),
      api("/api/rhythms/artists").catch(() => null),
    ]);
    const prev = sum.previous;
    const ref = prevRef(period, prev);
    const unitDay = act.unit === "day";
    const newWhen = period.key === "all" ? "in the past year" : "this period";  // All time shows only the last 12 months of discoveries
    // headline: "Your last 30 days of listening", "Your 2024 in listening", "All your listening"
    const title = period.key === "all" ? "All your listening" : period.days ? `Your last ${period.days === 365 ? "12 months" : `${period.days} days`} of listening`
      : /^\d{4}$/.test(period.key) ? `Your ${period.key} in listening` : period.month ? `Your ${period.label} in listening` : "Your listening, these dates";
    // when do you listen: busiest part of the day, and the single busiest weekday/hour
    const hours = Array.from({ length: 24 }, (_, h) => clock.reduce((s, row) => s + row[h], 0));
    const band = [0, 6, 12, 18].map((b) => [b, hours.slice(b, b + 6).reduce((a, x) => a + x, 0)]).sort((a, b) => b[1] - a[1])[0][0];
    let peak = [0, 0, -1];
    clock.forEach((row, wd) => row.forEach((v, h) => { if (v > peak[2]) peak = [wd, h, v]; }));
    const peakDay = act.items.reduce((best, d) => (d.plays > (best?.plays ?? -1) ? d : best), null);
    const novNow = timeline.length ? timeline[timeline.length - 1].novelty : null;
    const since = (() => {
      if (novNow == null) return "";
      const earlier = timeline.slice(0, -1).filter((m) => m.novelty != null);
      const higher = [...earlier].reverse().find((m) => m.novelty >= novNow);
      return higher ? `your highest since ${higher.month.slice(0, 4)}` : "your highest yet";
    })();
    const recentRows = [];
    let lastDay = null;
    for (const r of recent) {
      const day = new Date(r.ts * 1000).toDateString();
      if (lastDay && day !== lastDay) recentRows.push(html`<li class="day-sep">${Fmt.date(r.ts)}</li>`);
      lastDay = day;
      recentRows.push(html`<li class="link-row"><a href="#/track/${r.track_id}"><span class="time">${Fmt.time(r.ts)}</span>
        <span class="grow"><b>${r.track}</b> <span class="meta">· ${r.artist}</span></span></a></li>`);
    }
    paint(seq, html`
      <section class="hero-ov">
        <div>
          <span class="kicker">${rangeText(sum.start, sum.end)}</span>
          <h1 style="margin-top:10px">${title}</h1>
          <p class="lead">You played <strong>${Fmt.int(sum.plays)} scrobbles</strong> from ${Fmt.int(sum.artists)} artists${sum.new_artists ? `, ${Fmt.int(sum.new_artists)} of them brand new` : ""}.
            ${ta[0] ? html`Mostly <strong>${ta[0].name}</strong>, mostly ${DAYPART(band)}.` : ""}</p>
          ${periodControls(period, ov)}
        </div>
        <div class="big-circle" aria-hidden="false"><div>
          <span class="k">Scrobbles</span><span class="v">${Fmt.int(sum.plays)}</span>
          <span class="s">${Fmt.dec(sum.per_day)} a day</span>${delta(sum.plays, prev?.plays, ref)}</div></div>
      </section>
      <div class="tiles">
        ${tile("Artists", Fmt.int(sum.artists), html`${delta(sum.artists, prev?.artists, "")} top 10 are ${Fmt.pct(sum.top10_share)} of plays`)}
        ${tile("New artists", Fmt.int(sum.new_artists), html`${delta(sum.new_artists, prev?.new_artists, "")} first heard this period`, "sage")}
        ${tile("Tracks", Fmt.int(sum.tracks), html`${delta(sum.tracks, prev?.tracks, "")} across ${Fmt.int(sum.albums)} albums`)}
        ${tile("Listening days", html`${Fmt.int(sum.listening_days)} of ${Fmt.int(sum.calendar_days)}`, html`longest streak ${Fmt.int(sum.longest_streak)} days`, "accent")}
      </div>
      ${card(unitDay ? "Day by day" : "Month by month", html`<div class="chart" id="c-timeline"></div>`, {
        cls: "canvas", sub: `${unitDay ? "Click a day" : "Click a month"} to open it in the Library.`,
        aside: peakDay?.plays ? html`<span class="callout"><b>${unitDay ? Fmt.dayShort(peakDay.key) : Fmt.month(peakDay.key)}</b>&ensp;${Fmt.int(peakDay.plays)} scrobbles${peakDay.top ? ` · top: ${peakDay.top.name}` : ""}</span>` : "" })}
      ${seasonal?.coming_up.length ? card("", html`<div class="card-head" style="margin-bottom:12px"><div><h2>Your season</h2>
          <p>Artists you return to around this time every year · <a class="text-link" href="#/rhythms">Rhythms</a></p></div></div>
        <div class="chips">${seasonal.coming_up.map((a) => html`<a class="chip" href="#/artist/${a.id}">${a.name}
          <span class="pill ${a.status === "now" ? "sage" : "accent"}">${a.status === "now" ? "In season" : `From ~${Fmt.dayShort(`2001-${a.start}`)}`}</span></a>`)}</div>`, { cls: "accent-soft" }) : ""}
      <div class="grid cols-3">
        ${card("Top artists", rankList(ta, { href: artistHref }), { cls: "fill", foot: html`<a href="#/library?${qs({ kind: "artist", period: period.key })}">All ${Fmt.int(sum.artists)} artists →</a>` })}
        ${card("Top tracks", rankList(tt, { href: trackHref, sub: (t) => t.artist, nobar: true, valueText: (t) => `${Fmt.int(t.plays)}×` }),
          { cls: "fill", foot: html`<a href="#/library?${qs({ kind: "track", period: period.key })}">All tracks →</a>` })}
        ${card("Top albums", rankList(tal, { href: albumHref, sub: (t) => t.artist, nobar: true, cover: (t) => ({ name: t.name, url: t.image_url }) }),
          { cls: "fill", foot: html`<a href="#/library?${qs({ kind: "album", period: period.key })}">All albums →</a>` })}
      </div>
      <div class="grid cols-7-5">
        ${card("When you listen", html`<div class="chart" id="c-clock"></div>`, { cls: "sage-soft",
          sub: html`Bigger dot, more plays.${peak[2] > 0 ? html` Your peak is <strong>${DAYS[peak[0]]} around ${String(peak[1]).padStart(2, "0")}:00</strong>.` : ""}` })}
        ${card("Fresh ears", html`<div class="chart" id="c-novelty"></div>
          ${novNow != null ? html`<p style="margin-top:12px">Now <strong>${Fmt.pct(novNow)}</strong>, ${since}.</p>` : ""}`,
          { aside: html`<span class="pill">All time</span>`, sub: "Share of plays going to artists found in the past 12 months." })}
      </div>
      <div class="grid cols-2">
        ${card("New to you", sum.discoveries.length ? html`<ul class="rows">${sum.discoveries.slice(0, 6).map((r) => html`<li class="link-row"><a href="#/artist/${r.id}">
            <span class="grow"><b>${r.name}</b> ${r.gateway_name ? html`<span class="chip" style="padding:0;background:none"><span class="via">via ${r.gateway_name}</span></span>` : ""}</span>
            <span class="key">${Fmt.int(r.plays)} plays</span></a></li>`)}</ul>`
          : html`<p class="empty box">No new artists ${newWhen}.</p>`,
          { cls: "accent-soft", sub: sum.discoveries_new ? `${Fmt.int(sum.discoveries_new)} artists first heard ${newWhen}, and what led you there.` : "" })}
        ${card("Just played", html`<ul class="rows">${recentRows}</ul>`)}
      </div>`);
    bindPeriodControls((key) => {
      storage.set("mtc-period", key);
      location.hash = "#/?" + qs({ period: key });
    });
    const items = act.items;
    Charts.columns(document.getElementById("c-timeline"), items, {
      value: (d) => d.plays, height: 220, label: unitDay ? "Scrobbles per day" : "Scrobbles per month",
      xLabel: (d, i) => (unitDay ? (items.length <= 14 || i % 7 === 0 ? Fmt.dayShort(d.key) : null) : d.key.endsWith("-01") ? d.key.slice(0, 4) : items.length <= 24 ? Fmt.month(d.key).split(" ")[0] : null),
      tip: (d) => ({
        title: unitDay ? Fmt.day(d.key) : Fmt.month(d.key),
        rows: [{ value: Fmt.int(d.plays), label: "scrobbles" }, ...(d.top ? [{ value: d.top.name, label: `top artist · ${Fmt.int(d.top.plays)}` }] : []),
          { value: Fmt.int(d.new_artists), label: "new artists" }],
      }),
      onClick: (d) => (location.hash = `#/library?${qs({ kind: "artist", period: unitDay ? `${d.key}..${d.key}` : d.key })}`),
    });
    Charts.clock(document.getElementById("c-clock"), clock, { label: "Plays by weekday and hour" });
    Charts.line(document.getElementById("c-novelty"), timeline, {
      value: (d) => d.novelty, height: 170, label: "Share of plays from recent discoveries",
      xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null),
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: d.novelty == null ? "–" : Fmt.pct(d.novelty), label: "from recent discoveries" }] }),
    });
  }

  // ---------- library: one table for every kind and period ----------
  const KINDS = [["artist", "Artists"], ["track", "Tracks"], ["album", "Albums"], ["genre", "Genres"]];
  const LIB_COLS = {
    artist: [["name", "Artist"], ["plays", "Plays", "num"], ["tracks", "Tracks", "num"], ["years", "Years", "num"], ["loved", "Loved", "num"], ["first", "First heard"], ["last", "Last played"]],
    track: [["name", "Track"], ["artist", "Artist"], ["plays", "Plays", "num"], ["first", "First played"], ["last", "Last played"]],
    album: [["name", "Album"], ["artist", "Artist"], ["plays", "Plays", "num"], ["tracks", "Tracks heard", "num"], ["released", "Released"], ["last", "Last played"]],
    genre: [["name", "Genre"], ["plays", "Share", "num"], [null, "Your artists"]],
  };
  async function libraryView(params) {
    const seq = rendering;
    const ov = await api("/api/overview");
    if (ov.empty) return emptyState(seq);
    const kind = KINDS.some(([k]) => k === params.get("kind")) ? params.get("kind") : "artist";
    const period = parseRange(params.get("period") || "all", ov);
    const q = params.get("q") || "";
    const sort = params.get("sort") || "plays";
    const page = Math.max(0, +params.get("page") || 0);
    const artistId = +params.get("artist") || null;
    const size = 50;
    const setParams = (o) => {
      const next = { kind, period: period.key, q, sort, artist: artistId, page: 0, ...o };
      location.hash = "#/library?" + qs({ ...next, page: next.page || null, period: next.period === "all" ? null : next.period,
        sort: next.sort === "plays" ? null : next.sort, kind: next.kind === "artist" ? null : next.kind });
    };
    // text typed while the table was loading must survive the re-render
    const typing = view.querySelector("#q");
    const typed = typing && document.activeElement === typing ? typing.value : null;
    const data = await api("/api/library?" + qs({ kind, start: period.start, end: period.end, q, sort, artist: artistId, limit: size, offset: page * size }));
    const noun = KINDS.find(([k]) => k === kind)[1].toLowerCase();
    const lead = period.key === "all"
      ? html`${Fmt.int(data.total)} ${noun} you've scrobbled since ${Fmt.month(firstDay(ov).slice(0, 7))}${q ? html`, matching “${q}”` : ""}.`
      : html`${Fmt.int(data.total)} ${noun} in ${period.days ? period.label.toLowerCase() : period.label}${q ? html`, matching “${q}”` : ""}.`;
    const artistChip = data.artist ? html`<button type="button" class="chip" id="artist-filter" aria-label="Remove the filter for ${data.artist.name}">
      ${data.artist.name} <span aria-hidden="true">×</span></button>` : "";
    const th = ([key, label, cls]) => {
      if (!key) return html`<th scope="col">${label}</th>`;
      const on = sort === key;
      return html`<th scope="col" class="${cls ?? ""}" aria-sort="${on ? (["name", "artist", "first"].includes(key) ? "ascending" : "descending") : "none"}">
        <button type="button" data-sort="${key}">${label}${on ? (["name", "artist", "first"].includes(key) ? " ↑" : " ↓") : ""}</button></th>`;
    };
    const rank = (i) => html`<td class="num muted">${page * size + i + 1}</td>`;
    const rows = {
      artist: (a, i) => html`<tr>${rank(i)}<td><a class="who" href="#/artist/${a.id}">${initial(a.name)}<span>${a.name}</span></a></td>
        <td class="num strong">${Fmt.int(a.plays)}</td><td class="num">${Fmt.int(a.tracks)}</td><td class="num">${a.years}</td>
        <td class="num">${a.loved ? html`${Fmt.int(a.loved)}${a.loved_rate != null ? html` <span class="secondary" title="${Fmt.pct(a.loved_rate)} of the tracks played">· ${Fmt.pct(a.loved_rate)}<span class="sr-only"> of the tracks played</span></span>` : ""}` : "–"}</td>
        <td>${Fmt.date(a.first_ts)}</td><td>${Fmt.date(a.last_ts)}</td></tr>`,
      track: (t, i) => html`<tr>${rank(i)}<td><a class="who" href="#/track/${t.id}">${initial(t.name)}<span>${t.name}</span></a></td>
        <td>${link.artist(t.artist_id, t.artist)}</td><td class="num strong">${Fmt.int(t.plays)}</td><td>${Fmt.date(t.first_ts)}</td><td>${Fmt.date(t.last_ts)}</td></tr>`,
      album: (a, i) => html`<tr>${rank(i)}<td><a class="who" href="#/album/${a.id}">${initial(a.name, a.image_url, true)}<span>${a.name}</span></a></td>
        <td>${link.artist(a.artist_id, a.artist)}</td><td class="num strong">${Fmt.int(a.plays)}</td><td class="num">${Fmt.int(a.tracks)}</td>
        <td>${a.release_date ? year(a.release_date) : "–"}</td><td>${Fmt.date(a.last_ts)}</td></tr>`,
      genre: (g, i) => html`<tr>${rank(i)}<td><a class="who" href="#/tag/${g.id}">${initial(g.name)}<span>${g.name}</span></a></td>
        <td class="num strong">${Fmt.pct(g.share)}</td><td class="secondary">${g.artists.map((a, k) => html`${k ? ", " : ""}${link.artist(a.id, a.name)}`)}</td></tr>`,
    };
    const from = data.total ? page * size + 1 : 0, to = Math.min(data.total, (page + 1) * size);
    paint(seq, html`
      <div class="page-head"><div><h1>Library</h1><p class="lead">${lead}</p></div></div>
      <div class="toolbar">
        <div class="seg" role="group" aria-label="Show">${KINDS.map(([k, l]) => html`<button type="button" data-kind="${k}" aria-pressed="${String(k === kind)}">${l}</button>`)}</div>
        ${periodControls(period, ov, { compact: true })}
        ${artistChip}
        <label class="filter grow">${icon("filter", 16)}<input id="q" type="search" placeholder="Filter ${noun}" value="${q}" aria-label="Filter ${noun}"></label>
      </div>
      <div class="table-card">
        ${data.items.length ? html`<div class="table-wrap"><table>
          <thead><tr><th class="num" scope="col">#</th>${LIB_COLS[kind].map(th)}</tr></thead>
          <tbody>${data.items.map(rows[kind])}</tbody></table></div>`
          : html`<p class="empty" style="padding:16px 8px">${q ? html`Nothing matches “${q}”.` : "Nothing in this period."}</p>`}
        ${kind === "genre" && data.coverage != null ? html`<p class="secondary small" style="padding:4px 10px">Each artist's plays are split across its top last.fm genre tags; covers ${Fmt.pct(data.coverage)} of plays.</p>` : ""}
        <div class="pager"><span>${Fmt.int(from)}–${Fmt.int(to)} of ${Fmt.int(data.total)}</span>
          <button type="button" class="btn secondary small" id="prev" ${page === 0 ? raw("disabled") : ""}>← Previous</button>
          <button type="button" class="btn primary small" id="next" ${to >= data.total ? raw("disabled") : ""}>Next ${size} →</button></div>
      </div>`);
    view.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => setParams({ kind: b.dataset.kind, sort: "plays", q: "" })));
    view.querySelectorAll("th button[data-sort]").forEach((b) => b.addEventListener("click", () => setParams({ sort: b.dataset.sort })));
    view.querySelector("#prev").addEventListener("click", () => setParams({ page: page - 1 }));
    view.querySelector("#next").addEventListener("click", () => setParams({ page: page + 1 }));
    bindPeriodControls((key) => setParams({ period: key }));
    view.querySelector("#artist-filter")?.addEventListener("click", () => setParams({ artist: null }));
    const qi = view.querySelector("#q");
    let t;
    const schedule = () => { clearTimeout(t); t = setTimeout(() => setParams({ q: qi.value.trim() }), 300); };
    qi.addEventListener("input", schedule);
    if (typed != null && typed.trim() !== q) {
      qi.value = typed;
      qi.focus();
      schedule(); // the newer text wins
    }
  }

  // ---------- artist, album, track, tag ----------
  function menu(id, label, items) {
    return html`<div class="menu"><button type="button" class="btn ghost small" aria-haspopup="true" aria-expanded="false" aria-controls="${id}">${label} ${icon("more", 16)}</button>
      <div class="menu-list" id="${id}" role="menu" hidden>${items}</div></div>`;
  }
  function bindMenus() {
    view.querySelectorAll(".menu").forEach((m) => {
      const b = m.querySelector("button"), list = m.querySelector(".menu-list");
      const close = () => { list.hidden = true; b.setAttribute("aria-expanded", "false"); };
      b.addEventListener("click", () => {
        list.hidden = !list.hidden;
        b.setAttribute("aria-expanded", String(!list.hidden));
        if (!list.hidden) list.querySelector("a, button")?.focus();
      });
      m.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); b.focus(); } });
      document.addEventListener("click", function outside(e) {
        if (!document.body.contains(m)) return document.removeEventListener("click", outside);
        if (!m.contains(e.target)) close();
      });
    });
  }
  const extLink = (href, label) => html`<a class="btn secondary small" href="${href}" target="_blank" rel="noopener noreferrer">${label} ${icon("external", 14)}</a>`;

  // ---------- artist velocity: cumulative plays, compared across artists ----------
  function initVelocity(a, params) {
    const root = document.getElementById("velocity");
    if (!root) return;
    const MAX = 6;
    const colorOf = (k) => (k < 5 ? `var(--series-${k + 1})` : "var(--series-other)");
    const $ = (sel) => root.querySelector(sel);
    let vs = [...new Set((params?.get("vs") ?? "").split(",").map(Number))].filter((n) => Number.isInteger(n) && n > 0 && n !== a.id).slice(0, MAX - 1);
    let mode = params?.get("view") === "relative" ? "relative" : "absolute";
    const names = new Map([[a.id, a.name]]);
    let topList = null, token = 0, found = [];
    const say = (text) => { $("#vel-msg").textContent = text; };
    const sentence = (text) => (/[.!?]$/.test(text) ? text : `${text}.`);
    let refocus = false; // after a chip is removed the keyboard user would otherwise land on the page top
    const span = (days) => (days == null ? "–" : days < 60 ? `${days} d` : days < 730 ? `${Math.round(days / 30.4375)} mo` : `${Fmt.dec(days / 365.25)} y`);
    const selected = () => [a.id, ...vs];

    function suggestions() {
      const chosen = new Set(selected());
      const full = chosen.size >= MAX;
      const chip = (r) => html`<button type="button" class="chip" data-vel-add="${r.id}" data-name="${r.name}" ${chosen.has(r.id) || full ? raw("disabled") : ""}>${r.name}</button>`;
      mount($("#vel-related"), html`${a.related.slice(0, 8).map(chip)}`);
      $("#vel-related-box").hidden = !a.related.length;
      mount($("#vel-top"), topList ? html`${topList.map(chip)}` : html`<span class="secondary small">Loading…</span>`);
      mount($("#vel-results"), html`${found.map(chip)}`);
    }

    async function render() {
      const my = ++token;
      const ids = selected();
      let data;
      try { data = await api(`/api/velocity?ids=${ids.join(",")}`); } catch (err) { $("#vel-note").textContent = `Couldn't load the curves: ${err.message}`; return; }
      if (my !== token || !root.isConnected) return;
      data.series.forEach((s) => names.set(s.id, s.name));
      vs = vs.filter((id) => data.series.some((s) => s.id === id));
      if (location.hash.split("?")[0] === `#/artist/${a.id}`) // not after the reader has moved to another page
        history.replaceState(null, "", `#/artist/${a.id}${(q => (q ? `?${q}` : ""))(qs({ vs: vs.join(","), view: mode === "relative" ? "relative" : null }).replace(/%2C/g, ","))}`);
      root.querySelectorAll("[data-vel-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.velMode === mode)));
      const startMs = Date.parse(`${data.start}T12:00:00Z`);
      const weekMs = 7 * 86400000;
      const weekOf = (iso) => Math.floor((Date.parse(`${iso}T12:00:00Z`) - startMs) / weekMs);
      const series = data.series.map((s, k) => {
        const color = colorOf(k);
        if (mode === "relative") {
          const values = [0, ...s.cum.slice(s.first_week)];
          return { name: s.name, color, values, dashedTo: s.known_until ? weekOf(s.known_until) - s.first_week + 1 : null };
        }
        return { name: s.name, color, values: s.cum.map((c, i) => (i >= s.first_week - 1 ? c : null)), dashedTo: s.known_until ? weekOf(s.known_until) : null };
      });
      const weekIso = (i) => new Date(startMs + i * weekMs).toISOString().slice(0, 10);
      const steps = Math.max(...series.map((s) => s.values.length));
      Charts.lines(document.getElementById("c-velocity"), series, {
        height: 300, label: `Cumulative plays: ${data.series.map((s) => s.name).join(", ")}`,
        xLabel: mode === "relative"
          ? (i) => (i % 52 === 0 ? `${i / 52}y` : steps <= 104 && i % 13 === 0 ? `${(i / 13) * 3} mo` : null)
          : (i) => (i === 0 || weekIso(i).slice(0, 4) !== weekIso(i - 1).slice(0, 4) ? weekIso(i).slice(0, 4) : null),
        title: mode === "relative"
          ? (i) => (i === 0 ? "At the first play" : i < 52 ? `${i} week${i === 1 ? "" : "s"} in` : `${Fmt.dec(i / 52)} years in`)
          : (i) => `Week of ${Fmt.day(weekIso(i))}`,
      });
      mount($("#vel-chips"), html`${data.series.map((s, k) => html`<span class="chip vel-chip"><i class="vdot" style="background:${colorOf(k)}"></i>${k === 0
        ? html`<b>${s.name}</b>` : html`<a href="#/artist/${s.id}">${s.name}</a><button type="button" data-vel-remove="${s.id}" aria-label="Remove ${s.name} from the comparison">×</button>`}</span>`)}`);
      if (refocus) { refocus = false; ($("[data-vel-remove]") ?? $("#vel-add > summary")).focus(); }
      const known = data.series.some((s) => s.known_before);
      $("#vel-note").textContent = [mode === "relative" ? "Each curve starts at that artist's first play." : "",
        known ? "A dashed start means the artist was already in rotation when your history began, so its early pace is not a discovery pace." : ""].filter(Boolean).join(" ");
      mount($("#vel-facts"), html`<table><thead><tr><th>Artist</th><th>Plays</th><th title="Time from the first play to the 100th">To 100</th><th>To 500</th><th>To 1 000</th>
          <th title="The most plays in any 30 days">Fastest 30 days</th><th title="Plays per month over the last year, and over the whole time since the first play">Per month now / overall</th></tr></thead>
        <tbody>${data.series.map((s, k) => {
          const f = s.facts;
          return html`<tr><td><i class="vdot" style="background:${colorOf(k)}"></i> <a href="#/artist/${s.id}">${s.name}</a>
              ${s.known_before ? html` <span class="pill" title="Already in rotation when your history began">known before</span>` : ""}</td>
            <td>${Fmt.int(s.plays)}</td><td>${span(f.days_to["100"])}</td><td>${span(f.days_to["500"])}</td><td>${span(f.days_to["1000"])}</td>
            <td>${Fmt.int(f.fastest.plays)} <span class="dim">from ${Fmt.day(f.fastest.from)}</span></td>
            <td>${f.recent_per_month == null ? "–" : Fmt.dec(f.recent_per_month)} / ${f.lifetime_per_month == null ? "–" : Fmt.dec(f.lifetime_per_month)}</td></tr>`;
        })}</tbody></table>`);
      suggestions();
    }

    root.addEventListener("click", (e) => {
      const add = e.target.closest("[data-vel-add]");
      const remove = e.target.closest("[data-vel-remove]");
      const modeBtn = e.target.closest("[data-vel-mode]");
      if (add && selected().length < MAX && !selected().includes(Number(add.dataset.velAdd))) {
        const id = Number(add.dataset.velAdd);
        names.set(id, add.dataset.name);
        vs.push(id);
        say(`${sentence(`Added ${add.dataset.name}`)}${selected().length >= MAX ? ` That is the most you can compare (${MAX}).` : ""}`);
        render();
      } else if (remove) {
        const id = Number(remove.dataset.velRemove);
        vs = vs.filter((x) => x !== id);
        say(sentence(`Removed ${names.get(id) ?? "the artist"}`));
        refocus = true;
        render();
      } else if (modeBtn && modeBtn.dataset.velMode !== mode) {
        mode = modeBtn.dataset.velMode;
        render();
      }
    });
    $("#vel-add").addEventListener("toggle", async (e) => {
      if (!e.target.open || topList) return;
      try { topList = (await api("/api/top/artist?limit=14")).map((r) => ({ id: r.id, name: r.name })); } catch { topList = []; }
      suggestions();
    });
    let timer = null, searchSeq = 0;
    $("#vel-q").addEventListener("input", (e) => {
      clearTimeout(timer);
      const q = e.target.value.trim();
      if (!q) { found = []; suggestions(); return; }
      timer = setTimeout(async () => {
        const my = ++searchSeq;
        try {
          const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
          if (my === searchSeq) { found = r.artists.slice(0, 8).map((x) => ({ id: x.id, name: x.name })); suggestions(); }
        } catch { /* keep what is shown */ }
      }, 180);
    });
    render();
  }

  async function artistView(id, params) {
    const seq = rendering;
    const [a, ov] = await Promise.all([api(`/api/artists/${id}`), api("/api/overview")]);
    const lastfm = `https://www.last.fm/music/${encodeURIComponent(a.name).replace(/%20/g, "+")}`;
    const mb = `https://musicbrainz.org/search?${qs({ query: a.name, type: "artist" })}`;
    const meta = a.meta?.status === "ok" ? a.meta : null;
    const ledTo = a.led_to.length;
    const story = a.prehistory
      ? html`They were already in rotation when your history begins, on <strong>${Fmt.date(a.first_ts)}</strong>.`
      : html`You found them on <strong>${Fmt.date(a.first_ts)}</strong>${a.gateway_id ? html`, right after <a href="#/artist/${a.gateway_id}">${a.gateway_name}</a>` : ""}.`;
    const peakMonth = a.peak_month.month;
    const maxShared = Math.max(1, ...a.related.map((r) => r.score));
    const hoursPeak = a.hours.indexOf(Math.max(...a.hours));
    paint(seq, html`
      <section class="hero">
        <div class="cover-circle"><div class="disc">${a.image_url ? html`<img src="${a.image_url}" alt="" loading="lazy" referrerpolicy="no-referrer">` : (a.name.trim()[0] ?? "?").toUpperCase()}</div>
          <span class="rank-badge" title="Your #${a.rank} artist of all time">#${Fmt.int(a.rank)}</span></div>
        <div>
          <span class="kicker">Artist · your #${Fmt.int(a.rank)} of all time</span>
          <h1>${a.name}</h1>
          ${tagChips(a.tags)}
          <p class="lead">${story}${ledTo ? html` Since then they've led you to ${Fmt.int(ledTo)} other artist${ledTo === 1 ? "" : "s"}.` : ""}</p>
        </div>
        <div class="hero-links">
          ${extLink(meta?.url || lastfm, "last.fm")}
          ${extLink(meta?.mbid ? `https://musicbrainz.org/artist/${meta.mbid}` : mb, "MusicBrainz")}
          ${menu("artist-more", "More", html`<a role="menuitem" href="#/cleanup?merge=${a.id}">Merge a duplicate spelling…</a>`)}
        </div>
      </section>
      <div class="tiles">
        ${tile("Plays", Fmt.int(a.plays), `${Fmt.pct(a.share)} of everything`, "accent")}
        ${tile("Tracks heard", Fmt.int(a.n_tracks), `on ${Fmt.int(a.n_days)} different days`)}
        ${tile("First heard", Fmt.month((a.first_lday ?? isoDay(a.first_ts)).slice(0, 7)), a.first_track ?? "", "", true)}
        ${tile("Last played", Fmt.ago(a.last_ts, ov.last_ts).replace(/^today$/, "Today"), Fmt.date(a.last_ts), "sage", true)}
        ${tile("Peak month", Fmt.month(peakMonth), `${Fmt.int(a.peak_month.plays)} plays`, "", true)}
      </div>
      ${card("Month by month", html`<div class="chart" id="c-artist"></div>`, { cls: "canvas",
        aside: html`<span class="callout">Peak: <b>${Fmt.month(peakMonth)}</b>, ${Fmt.int(a.peak_month.plays)} plays</span>` })}
      ${card("Velocity", html`<div class="vel-controls"><div class="seg" role="group" aria-label="Time axis">
            <button type="button" data-vel-mode="absolute" aria-pressed="true">Calendar</button>
            <button type="button" data-vel-mode="relative" aria-pressed="false">Since first play</button></div>
          <div class="chips" id="vel-chips"></div></div>
        <div class="chart" id="c-velocity"></div>
        <p class="secondary small" id="vel-note"></p>
        <details class="vel-add" id="vel-add"><summary class="btn secondary small">+ Compare with…</summary>
          <div class="vel-panel"><div><label class="field" for="vel-q">Search for an artist</label>
              <input id="vel-q" type="search" autocomplete="off" spellcheck="false" placeholder="Any artist you've played"></div>
            <div class="chips" id="vel-results"></div>
            <div id="vel-related-box"><p class="secondary small">Played alongside</p><div class="chips" id="vel-related"></div></div>
            <div><p class="secondary small">Your top artists</p><div class="chips" id="vel-top"></div></div></div></details>
        <p class="secondary small" id="vel-msg" role="status"></p>
        <div class="vel-facts table-wrap" id="vel-facts"></div>`, { cls: "canvas", id: "velocity",
        sub: "Cumulative plays: a steeper line is a faster pace. Compare up to six artists." })}
      <div class="grid cols-3">
        ${card("Top tracks", rankList(a.tracks.slice(0, 6), { href: trackHref, name: lovedName }),
          a.n_loved ? { sub: `${Fmt.int(a.n_loved)} loved on last.fm, ${Fmt.pct(a.n_loved / a.n_tracks)} of the tracks you've played` } : {})}
        ${card("Albums", a.albums.length ? rankList(a.albums.slice(0, 6), { href: albumHref, nobar: true, cover: (t) => ({ name: t.name, url: t.image_url }),
          sub: (t) => `${t.release_date ? `${year(t.release_date)} · ` : ""}${t.n_tracks} tracks` }) : html`<p class="empty">No albums.</p>`)}
        ${card("Played alongside", a.related.length ? html`<div class="chips">${a.related.slice(0, 10).map((r) => {
            const w = r.score / maxShared;
            return html`<a class="chip ${w > 0.66 ? "lg" : w > 0.33 ? "md" : ""}" href="#/artist/${r.id}">${r.name} <span class="count">${Fmt.int(r.shared)}</span></a>`;
          })}</div>` : html`<p class="empty">Not enough shared sessions yet.</p>`, { cls: "sage-soft", sub: "Sessions you shared with each" })}
      </div>
      <div class="grid cols-2">
        ${card("Time of day", html`<div class="chart" id="c-hours"></div>`, { sub: `Mostly ${DAYPART(hoursPeak)}.` })}
        ${card("Led you to", ledTo ? html`<ul class="rows">${a.led_to.slice(0, 6).map((r) => html`<li class="link-row"><a href="#/artist/${r.id}">
            <span class="grow"><b>${r.name}</b> <span class="meta">${Fmt.date(r.first_ts)}</span></span><span class="key">${Fmt.int(r.plays)} plays</span></a></li>`)}</ul>`
          : html`<p class="empty box">No discoveries started right after this artist.</p>`, { cls: "accent-soft", sub: "Artists you first heard straight after this one" })}
      </div>`);
    bindMenus();
    Charts.columns(document.getElementById("c-artist"), a.monthly, {
      value: (d) => d.plays, height: 190, label: "Plays per month", xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null),
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
      onClick: (d) => (location.hash = `#/library?${qs({ kind: "track", period: d.month, artist: a.id })}`),
    });
    initVelocity(a, params);
    Charts.columns(document.getElementById("c-hours"), a.hours.map((v, h) => ({ h, v })), {
      value: (d) => d.v, height: 170, label: "Plays by hour", xLabel: (d) => (d.h % 6 === 0 || d.h === 23 ? String(d.h).padStart(2, "0") : null),
      tip: (d) => ({ title: `${String(d.h).padStart(2, "0")}:00–${String(d.h).padStart(2, "0")}:59`, rows: [{ value: Fmt.int(d.v), label: "plays" }] }),
    });
  }

  async function albumView(id) {
    const seq = rendering;
    const al = await api(`/api/albums/${id}`);
    const meta = al.meta?.status === "ok" ? al.meta : null;
    paint(seq, html`
      <section class="hero">
        <div class="cover-square">${meta?.image_url ? html`<img src="${meta.image_url}" alt="" loading="lazy" referrerpolicy="no-referrer">` : (al.name.trim()[0] ?? "?").toUpperCase()}</div>
        <div><span class="kicker">Album · <a href="#/artist/${al.artist_id}">${al.artist}</a></span><h1>${al.name}</h1>${tagChips(al.tags)}</div>
        <div class="hero-links">${meta?.url ? extLink(meta.url, "last.fm") : ""}
          ${meta?.release_group_mbid ? extLink(`https://musicbrainz.org/release-group/${meta.release_group_mbid}`, "MusicBrainz") : ""}</div>
      </section>
      <div class="tiles">
        ${tile("Plays", Fmt.int(al.plays), "", "accent")}
        ${tile("Tracks heard", Fmt.int(al.tracks.length), meta?.n_tracks ? `of ${meta.n_tracks} on the album` : "")}
        ${meta?.release_date ? tile("Released", Fmt.release(meta.release_date), `${meta.release_type ?? ""}${meta.release_date_source === "tag" ? " · from tags" : ""}`, "sage", true) : ""}
      </div>
      ${card("Tracks", rankList(al.tracks, { href: trackHref, name: lovedName, sub: (t) => `last played ${Fmt.date(t.last_ts)}` }))}`);
  }

  async function trackView(id) {
    const seq = rendering;
    const t = await api(`/api/tracks/${id}`);
    paint(seq, html`
      <section class="hero no-cover"><div><span class="kicker">Track · <a href="#/artist/${t.artist_id}">${t.artist}</a></span><h1>${t.name}</h1>
        ${t.loved_at ? html`<div class="chips tags"><span class="chip loved-chip">${icon("heart", 14)} Loved on ${Fmt.date(t.loved_at)}</span></div>
          ${t.love ? html`<p class="lead">${loveTiming(t.love)}</p>` : ""}` : ""}</div></section>
      <div class="tiles">
        ${tile("Plays", Fmt.int(t.plays), `on ${Fmt.int(t.n_days)} days`, "accent")}
        ${tile("First played", Fmt.date(t.first_ts), "", "", true)}
        ${tile("Last played", Fmt.date(t.last_ts), "", "sage", true)}
        ${tile("Most in one day", Fmt.int(t.best_day.plays), Fmt.day(t.best_day.day))}
      </div>
      ${card("Year by year", html`<div class="chart" id="c-track"></div>`, { cls: "canvas" })}`);
    Charts.columns(document.getElementById("c-track"), t.yearly, {
      value: (d) => d.plays, height: 180, xLabel: (d) => d.year, label: "Plays per year",
      tip: (d) => ({ title: d.year, rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
    });
  }

  async function tagView(id) {
    const seq = rendering;
    const t = await api(`/api/tags/${id}`);
    const kind = { genre: "Genre", place: "Place", year: "Year", decade: "Decade", other: "Tag" }[t.kind] ?? "Tag";
    const plays = t.monthly.reduce((s, m) => s + m.plays, 0);
    paint(seq, html`
      <section class="hero no-cover"><div><span class="kicker">${kind}</span><h1>${t.name}</h1>
        ${t.kind === "genre" ? html`<p class="lead">About <strong>${Fmt.int(plays)} plays</strong>, with each artist's plays split across its top genre tags.</p>` : ""}</div>
        <div class="hero-links">${extLink(`https://www.last.fm/tag/${encodeURIComponent(t.name)}`, "last.fm")}</div></section>
      ${t.months ? card("Through the year", html`<div class="chart" id="c-tag-year"></div>`, { cls: "sage-soft", sub: strongestMonths(t.months) }) : ""}
      ${t.kind === "genre" ? card("Month by month", html`<div class="chart" id="c-tag"></div>`, { cls: "canvas" }) : ""}
      <div class="grid cols-2">
        ${card("Your artists", rankList(t.artists.slice(0, 15), { href: artistHref, sub: (r) => `tag weight ${r.weight}` }), { sub: "Artists last.fm tags with this, by your plays" })}
        ${card("Albums", t.albums.length ? rankList(t.albums.slice(0, 15), { href: albumHref, sub: (r) => `${r.artist}${r.release_date ? ` · ${year(r.release_date)}` : ""}` })
          : html`<p class="empty">No albums with this tag yet.</p>`)}
      </div>`);
    if (t.months) {
      Charts.matrix(document.getElementById("c-tag-year"), { cols: t.months.map((m) => m.month), rows: [{ name: t.name, cells: t.months }] },
        { label: `${t.name} through the year` });
    }
    if (t.kind === "genre") {
      Charts.columns(document.getElementById("c-tag"), t.monthly, {
        value: (d) => d.plays, height: 180, xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null), label: `${t.name} plays per month`,
        tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
      });
    }
  }

  // ---------- rhythms ----------
  const SEASONS = { winter: ["Winter", [11, 0, 1]], spring: ["Spring", [2, 3, 4]], summer: ["Summer", [5, 6, 7]], autumn: ["Autumn", [8, 9, 10]] };
  const seasonOf = (month0) => Object.keys(SEASONS).find((k) => SEASONS[k][1].includes(month0));
  const liftText = (x) => `${Fmt.dec(x)}×`;
  const mmdd = (s) => Fmt.dayShort(`2001-${s}`);
  function strongestMonths(cells) {
    const top = cells.filter((c) => c.lift != null && c.lift >= 1.1).sort((a, b) => b.lift - a.lift).slice(0, 3);
    return top.length ? `Strongest in ${top.map((c) => `${c.month} (${liftText(c.lift)})`).join(", ")}, compared with your usual share year by year.`
      : "No month stands out: about your usual share all year.";
  }
  const plural = (season, n) => (season === "winter" ? `winter${n === 1 ? "" : "s"}` : `${season}${n === 1 ? "" : "s"}`);

  async function rhythmsView(params) {
    const seq = rendering;
    const kind = params.get("kind") === "place" ? "place" : "genre";
    const [r, sa] = await Promise.all([api(`/api/rhythms?kind=${kind}`), api("/api/rhythms/artists")]);
    const tabs = html`<div class="seg" role="group" aria-label="Show">${[["genre", "Genres"], ["place", "Places"]].map(([k, l]) =>
      html`<button type="button" data-k="${k}" aria-pressed="${String(k === kind)}">${l}</button>`)}</div>`;
    const head = html`<div class="page-head"><div><h1>Rhythms</h1>
      <p class="lead">How your listening moves through the year, the week and the day.</p>
      <div class="head-chips"><span class="pill sage">Based on ${Fmt.pct(r.coverage)} of your plays</span>
        ${r.coverage < 0.5 ? html`<a class="pill accent" href="#/import">Fetch more tags</a>` : ""}
        <a class="pill" href="#how" id="how-link">How is this measured?</a></div></div>${tabs}</div>`;
    const bindTabs = () => {
      view.querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => {
        location.hash = "#/rhythms" + (b.dataset.k === "place" ? "?kind=place" : "");
      }));
      view.querySelector("#how-link")?.addEventListener("click", (e) => { e.preventDefault(); const d = document.getElementById("how"); d.open = true; scrollToId("how"); });
    };
    if (!r.covered) {
      paint(seq, html`${head}${card("No tags yet", html`<p>Rhythms need ${kind} tags from last.fm. Fetch them on the Import page; the views fill in as tags arrive.</p>
        <p style="margin-top:16px"><a class="btn primary" href="#/import">Go to Import →</a></p>`, { cls: "accent-soft" })}`);
      bindTabs();
      return;
    }
    const now = new Date(sa.reference + "T12:00:00");
    const season = seasonOf(now.getMonth());
    const seasonData = r.seasons.find((s) => s.key === season);
    const seasonGenres = seasonData?.genres.slice(0, 2).map((g) => g.name) ?? [];
    const due = sa.coming_up;
    const seasonCard = (sc) => html`<section class="card season-card ${sc.key}">
      <div class="card-head" style="margin-bottom:12px"><div><h2>${SEASONS[sc.key][0]}</h2><p>${sc.months.join(" · ")}</p></div></div>
      <div class="body">${sc.genres.length ? html`<ul class="glist">${sc.genres.map((g) => html`<li><a href="#/tag/${g.id}">${g.name}</a>
          <span class="num">${liftText(g.lift)}</span><small>in ${g.up} of ${g.years} ${plural(sc.key, g.years)}</small></li>`)}</ul>`
        : html`<p class="empty">No ${kind} stands out: about your usual mix.</p>`}</div>
      ${sc.artists.length ? html`<p class="more-than">More than usual: ${sc.artists.map((a, i) => html`${i ? ", " : ""}<a href="#/artist/${a.id}">${a.name}</a>`)}</p>` : ""}
    </section>`;
    paint(seq, html`${head}
      ${due.length || seasonGenres.length ? html`<section class="card accent-soft season-hero">
        <div class="season-badge" aria-hidden="true">Your<br>season</div>
        <div><p>It's ${season}${seasonGenres.length ? html`, which usually means <strong>more ${seasonGenres.join(" and ")}</strong> for you` : ""}.
          ${due.length ? "These artists return around now every year:" : ""}</p>
          ${due.length ? html`<div class="chips">${due.map((a) => html`<a class="chip" href="#/artist/${a.id}">${a.name}
            <span class="pill ${a.status === "now" ? "sage" : "accent"}">${a.status === "now" ? "In season" : `From ~${mmdd(a.start)}`}</span></a>`)}</div>` : ""}
          ${sa.artists.length ? html`<details style="margin-top:12px"><summary class="more" style="cursor:pointer">All ${Fmt.int(sa.artists.length)} seasonal artists</summary>
            <ul class="rows" style="margin-top:10px">${sa.artists.map((a) => html`<li class="link-row"><a href="#/artist/${a.id}"><span class="grow"><b>${a.name}</b>
              <span class="meta">${mmdd(a.start)} – peak ~${mmdd(a.peak)} · ${a.years_agree} of ${a.years} years</span></span>
              ${a.status ? html`<span class="pill ${a.status === "now" ? "sage" : "accent"}">${a.status === "now" ? "In season" : "Coming up"}</span>` : ""}</a></li>`)}</ul></details>` : ""}
        </div></section>` : ""}
      ${card(kind === "genre" ? "Genres through the year" : "Places through the year", html`<div class="chart" id="c-months"></div>`,
        { cls: "canvas", sub: "Each month's share compared with your usual share that same year." })}
      <div class="grid cols-4">${r.seasons.map(seasonCard)}</div>
      <div class="grid cols-7-5">
        ${card("Time of day", html`<div class="chart" id="c-day"></div>`, { sub: "Night 0–6 · morning 6–12 · afternoon 12–18 · evening 18–24" })}
        ${card("Weekdays vs weekends", html`<div class="chart" id="c-week"></div>`, { sub: "Monday–Friday against Saturday–Sunday" })}
      </div>
      <div class="grid cols-2">
        ${r.drift ? card(kind === "genre" ? "Genre drift" : "Place drift", html`<div class="chart" id="c-drift"></div>`, { sub: `Each year's mix of your top ${kind === "genre" ? "genres" : "places"}` }) : ""}
        ${r.diversity ? card("How varied", html`<div class="chart" id="c-div"></div>`, { cls: "sage-soft",
          sub: html`Effective number of genres in each month.${r.diversity.most ? html` You're most varied in <strong>${r.diversity.most}</strong>, least in <strong>${r.diversity.least}</strong>.` : ""}` }) : ""}
      </div>
      <details class="card" id="how" style="margin-top:var(--gap)"><summary style="cursor:pointer"><h2 style="display:inline">How is this measured?</h2></summary>
        <div class="prose" style="margin-top:12px;display:grid;gap:10px;max-width:760px">
          <p>An artist's plays are split across its top ${kind} tags. Every month, season or part of the day is then compared with <strong>that same year's</strong> mix: 1,5× means half as much again as usual. So a ${kind} you simply listen to more over the years doesn't count as seasonal.</p>
          <p>Small samples are pulled towards “as usual”, and blank cells had too few plays to say. “In 5 of 6 winters” counts the years that were above their own usual share; a season only lists a ${kind} that held in at least half of them.</p>
          <p>Seasonal artists come back around the same dates in at least three years. “From ~date” means their season starts within four weeks of your newest scrobble.</p>
        </div></details>`);
    bindTabs();
    const rows = (m) => ({ cols: m.cols, rows: m.rows.map((row) => ({ ...row, href: `#/tag/${row.id}` })) });
    Charts.matrix(document.getElementById("c-months"), rows(r.months), { label: `${kind === "genre" ? "Genres" : "Places"} through the year`, highlightCols: SEASONS[season][1] });
    Charts.matrix(document.getElementById("c-day"), rows(r.dayparts), { label: "By time of day" });
    Charts.matrix(document.getElementById("c-week"), rows(r.weekparts), { label: "Weekdays and weekends" });
    if (r.drift) {
      Charts.stacked(document.getElementById("c-drift"), r.drift.years, {
        series: r.drift.series, shares: (d) => d.shares, other: (d) => d.other, xLabel: (d) => `'${d.year.slice(2)}`,
        title: (d) => `${d.year} · ${Fmt.int(d.plays)} tagged plays`, label: "Mix per year",
      });
    }
    if (r.diversity) {
      Charts.line(document.getElementById("c-div"), r.diversity.monthly, {
        value: (d) => d.effective, height: 200, endDot: false, label: "Effective number of genres per month",
        yMin: Math.max(0, Math.min(...r.diversity.monthly.map((d) => d.effective)) - 0.5),
        xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null),
        tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.dec(d.effective), label: "effective genres" }, { value: Fmt.int(d.plays), label: "tagged plays" }] }),
      });
    }
  }

  // ---------- decades ----------
  async function decadesView() {
    const seq = rendering;
    const [d, age, lag, rh] = await Promise.all([api("/api/decades"), api("/api/decades/age"), api("/api/decades/lag"), api("/api/decades/rhythms")]);
    // A heatmap where every cell is "as usual" says nothing: state that instead of drawing a blank grid.
    const lifts = (m) => m.rows.flatMap((r) => r.cells).filter((c) => c.lift != null).map((c) => c.lift);
    const isFlat = (m) => { const l = lifts(m); return l.length > 0 && l.every((x) => Charts.liftStep(x) === 0); };
    const liftCard = (title, id, m, o) => isFlat(m)
      ? card(title, html`<p class="empty">Nothing stands out: every decade stays within ${Fmt.pct(Math.ceil(Math.max(...lifts(m).map((l) => Math.abs(l - 1))) * 100 - 1e-9) / 100)} of its usual share.</p>`, o)
      : card(title, html`<div class="chart" id="${id}"></div>`, o);
    const head = (extra = "") => html`<div class="page-head"><div><h1>Decades</h1>
      <p class="lead">Which release decades you listen to, and how that has moved over the years.</p>${extra}</div></div>`;
    if (!d.covered) {
      paint(seq, html`${head()}${card("No release dates yet", html`<p>Decades need release dates for your albums. Fetch them on the Import page; the views fill in as dates arrive.</p>
        <p style="margin-top:16px"><a class="btn primary" href="#/import">Go to Import →</a></p>`, { cls: "accent-soft" })}`);
      return;
    }
    const coverage = html`<div class="head-chips"><span class="pill sage">Based on ${Fmt.pct(d.coverage)} of your plays</span>
      ${d.coverage < 0.5 ? html`<a class="pill accent" href="#/import">Fetch more release dates</a>` : ""}
      <a class="pill" href="#how" id="how-link">How is this measured?</a></div>`;
    const v = d.vogue;
    const lead = v.enough && v.in_vogue.length ? v.in_vogue.map((x) => x.label) : [];
    const peakDecade = d.decades.reduce((a, b) => (b.plays > a.plays ? b : a));
    const dated = age.covered ? age.years.filter((y) => y.median_years != null) : [];
    paint(seq, html`${head(coverage)}
      <section class="card accent-soft"><p>${lead.length
        ? html`Lately you've leaned towards the <strong>${lead.join(" and ")}</strong>: more than your usual share over the past 12 months.`
        : v.enough ? "The past 12 months look like your usual mix of decades."
        : "There aren't enough dated plays in the last 12 months to say what is in vogue yet."}
        Overall, the <strong>${peakDecade.label}</strong> lead with ${Fmt.pct(peakDecade.share)} of dated plays.</p></section>
      ${card("Plays by release year", html`<div class="chart" id="c-years"></div>`, { cls: "canvas", sub: "Every release year of the albums you play; the busiest decade is darkest." })}
      ${card("Decades over the years", html`<div class="chart" id="c-drift"></div>`, { cls: "canvas", sub: "Share of each listening year's dated plays" })}
      ${card("In vogue lately", v.enough ? html`<div class="grid cols-4">${v.decades.map((x) => tile(x.label, liftText(x.lift),
          `${Fmt.pct(x.recent_share)} lately · ${Fmt.pct(x.before_share)} before`))}</div>` : html`<p class="empty">Needs ${Fmt.int(v.min_plays)} dated plays in the last 12 months.</p>`,
        { cls: "sage-soft", sub: "Past 12 months against everything before, most in vogue first" })}
      ${card("Decade by listening year", html`<div class="chart" id="c-matrix"></div>`, { cls: "canvas", sub: "Each decade's share in a year compared with its share of all your plays." })}
      ${age.covered ? html`${card("How old is the music you play?", html`<div class="grid cols-3">
          ${tile("Typical age", `${Fmt.dec(age.median_years)} years`, "Half of your plays are of albums younger than this")}
          ${tile("Fresh releases", Fmt.pct(age.new_share), "Plays of albums less than a year old")}
          ${tile("Back catalogue", Fmt.pct(age.years.reduce((s, y) => s + y.shares[3] * y.plays, 0) / age.covered), "Plays of albums 20 years old or more")}
        </div>`, { cls: "sage-soft", sub: "The album's age on the day you played it" })}
        <div class="grid cols-2">
          ${dated.length ? card("Typical age over the years", html`<div class="chart" id="c-age"></div>`, { sub: "Median age of the album at the time you played it, per listening year" })
            : card("Typical age over the years", html`<p class="empty">Needs ${Fmt.int(age.min_year_plays)} dated plays in a listening year.</p>`)}
          ${card("Age mix per year", html`<div class="chart" id="c-age-mix"></div>`, { sub: "Share of each listening year's dated plays, by how old the album was" })}
        </div>` : ""}
      ${rh.covered ? html`${liftCard("Decades through the year", "c-dec-season", rh.seasons, { cls: "canvas", sub: "Each season's share of a decade compared with your usual share that same year." })}
        <div class="grid cols-7-5">
          ${liftCard("Decades through the day", "c-dec-day", rh.dayparts, { sub: "Night 0–6 · morning 6–12 · afternoon 12–18 · evening 18–24" })}
          ${liftCard("Weekdays vs weekends", "c-dec-week", rh.weekparts, { sub: "Monday–Friday against Saturday–Sunday" })}
        </div>
        ${rh.genres ? html`${card("What each decade sounds like", html`<div class="chart" id="c-dec-genre"></div>`, { cls: "canvas",
            sub: html`Your top genres inside each release decade, against their share of all your plays. Based on ${Fmt.pct(rh.genres.coverage)} of dated plays.` })}
          ${rh.genres.signature.some((x) => x.genres.length) ? html`<div class="chips" style="margin-top:var(--gap)">${rh.genres.signature.filter((x) => x.genres.length).map((x) => html`<span class="chip"><b>${x.label}</b>
            ${x.genres.map((g) => html`<a class="pill sage" href="#/tag/${g.id}">${g.name}</a>`)}</span>`)}</div>` : ""}` : ""}` : ""}
      ${lag.eras.some((e) => e.key !== "tracked") ? card("Older records", html`<div class="grid cols-${Math.max(2, Math.min(3, lag.eras.length))}">
          ${lag.eras.map((e) => tile(e.label, Fmt.pct(e.share), `${Fmt.int(e.albums)} albums · ${Fmt.int(e.plays)} plays`))}
        </div>
        ${lag.dug_up ? html`<p class="secondary small" style="margin:var(--gap) 0 4px">Older albums by the year you first played them</p>
          <div class="chart" id="c-dug"></div>` : ""}`, { cls: "sage-soft",
        sub: html`A wait only means something for albums you could have found on release, so records from before tracking began (${lag.tracking_start.slice(0, 4)}) are shown by share of your plays and by when you dug them up.`,
        foot: html`<form id="birth-form" autocomplete="off"><div class="input-row"><label class="field" for="birth">Born in</label>
          <input id="birth" style="flex:0 0 7em" type="number" inputmode="numeric" min="1900" max="${new Date().getFullYear()}" step="1" value="${lag.birth_year ?? ""}" placeholder="year">
          <button class="btn secondary small" type="submit">Save</button></div>
          <p class="secondary small" id="birth-msg" role="status">${lag.birth_year ? "" : "Optional: tells records from before you were born from your own years. Stays in data/settings.json."}</p></form>` }) : ""}
      ${lag.covered ? html`${card("How long did you take to find it?", html`<div class="grid cols-3">
          ${tile("Typical wait", `${Fmt.dec(lag.median_years)} years`, "From an album's release to your first play of it")}
          ${tile("Found in its first year", Fmt.pct(lag.first_year_share), "Albums you played within a year of release")}
          ${tile(`Found ${lag.late_label} late`, Fmt.int(lag.late_count), "Albums you only met a decade or more after release")}
        </div>
        <p class="secondary small" style="margin:var(--gap) 0 4px">Albums by time from release to first play</p>
        <div class="chart" id="c-lag"></div>`, { cls: "accent-soft", sub: `Based on ${Fmt.int(lag.covered)} albums released since tracking began in ${lag.tracking_start.slice(0, 4)}` })}
        <div class="grid cols-2">
          ${card("Found late", rankList(lag.late, { href: albumHref, sub: (a) => `${a.artist} · ${a.release_date.slice(0, 4)}`, nobar: true,
            value: (a) => a.lag_days, valueText: (a) => `${Fmt.dec(a.lag_years)} yrs`, compact: true }), { sub: `Longest waits, albums with ${lag.min_list_plays}+ plays` })}
          ${card("There on release", rankList(lag.on_release, { href: albumHref, sub: (a) => `${a.artist} · ${a.lag_days ? `+${Fmt.int(a.lag_days)} d` : "day one"}`, nobar: true,
            value: (a) => a.plays, valueText: (a) => `${Fmt.int(a.plays)} plays`, compact: true }), { cls: "sage-soft", sub: `First played within ${lag.on_release_days} days of the release date` })}
        </div>` : ""}
      <details class="card" id="how" style="margin-top:var(--gap)"><summary style="cursor:pointer"><h2 style="display:inline">How is this measured?</h2></summary>
        <div class="prose" style="margin-top:12px;display:grid;gap:10px;max-width:760px">
          <p>Release dates come from MusicBrainz (the original release, not a reissue), or from a year tag on last.fm when MusicBrainz has nothing. Only albums with at least three plays are looked up, and plays without an album can't be dated, so the percentage above tells how much of your listening this page sees.</p>
          <p>Album age is the play date minus the release date. A date with only a year counts as 1 July${age.covered ? html` (${Fmt.pct(age.approximate_share)} of dated plays)` : ""}, and a play dated before its release counts as age 0, so the age figures are a little rough for albums released in the last year.</p>
          ${rh.covered ? html`<p>The decade heatmaps use the same method as Rhythms: each season or part of the day is compared with that same year's mix of decades, so a decade you simply listen to more over the years doesn't look seasonal. The genre map compares a decade's genre mix with your overall mix; an artist's plays are split across its top genre tags.</p>` : ""}
          ${lag.eras.length ? html`<p>The wait is the time from an album's release date to the first day you played it, and only albums released since tracking began count: a record from before then could not have been found on release, so “found 50 years late” would say nothing. Older records are shown by share of plays and by the year you first played them; albums by artists already in your rotation in the first ${lag.prehistory_days} days of tracking are left out of that chart, because their first scrobble is no discovery. “There on release” only uses full release dates, since a bare year is too vague to call a week.</p>` : ""}
          <p>1,5× means half as much again as usual. Small samples are pulled towards “as usual”, and blank cells had too few plays to say. “In vogue” compares the past 12 months with all the listening before them.</p>
        </div></details>`);
    view.querySelector("#how-link")?.addEventListener("click", (e) => { e.preventDefault(); const el = document.getElementById("how"); el.open = true; scrollToId("how"); });
    const decadeOf = (y) => Math.floor(y / 10) * 10;
    Charts.columns(document.getElementById("c-years"), d.years, {
      value: (y) => y.plays, height: 220, axis: true, label: "Plays by release year",
      highlight: (y) => decadeOf(y.year) === peakDecade.decade,
      xLabel: (y) => (y.year % 10 === 0 ? String(y.year) : null),
      tip: (y) => ({ title: String(y.year), rows: [{ value: Fmt.int(y.plays), label: "plays" }] }),
    });
    Charts.stacked(document.getElementById("c-drift"), d.drift.years, {
      series: d.drift.series, shares: (y) => y.shares, other: (y) => y.other, xLabel: (y) => `'${y.year.slice(2)}`,
      title: (y) => `${y.year} · ${Fmt.int(y.plays)} dated plays`, label: "Decade mix per listening year",
    });
    Charts.matrix(document.getElementById("c-matrix"), d.matrix, { label: "Release decades by listening year" });
    if (rh.covered) {
      if (!isFlat(rh.seasons)) Charts.matrix(document.getElementById("c-dec-season"), rh.seasons, { label: "Release decades through the year" });
      if (!isFlat(rh.dayparts)) Charts.matrix(document.getElementById("c-dec-day"), rh.dayparts, { label: "Release decades by time of day" });
      if (!isFlat(rh.weekparts)) Charts.matrix(document.getElementById("c-dec-week"), rh.weekparts, { label: "Release decades on weekdays and weekends" });
      if (rh.genres) Charts.matrix(document.getElementById("c-dec-genre"), rh.genres, { label: "Genres within each release decade" });
    }
    if (lag.dug_up) {
      Charts.columns(document.getElementById("c-dug"), lag.dug_up.years, {
        value: (y) => y.albums, height: 180, label: "Older albums by the year you first played them", xLabel: (y) => `'${String(y.year).slice(2)}`,
        tip: (y) => ({ title: String(y.year), rows: [{ value: Fmt.int(y.albums), label: "older albums first played" }, ...y.by_era.filter((e) => e.albums).map((e) => ({ value: Fmt.int(e.albums), label: e.label }))] }),
      });
    }
    view.querySelector("#birth-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = view.querySelector("#birth-msg");
      const v = view.querySelector("#birth").value.trim();
      try {
        await postJson("/api/settings/birth-year", { year: v ? Number(v) : null }, "PUT", "That isn't a plausible birth year");
        cache.delete("/api/settings");
        cache.delete("/api/decades/lag");
        route();
      } catch (err) {
        msg.textContent = err.message;
      }
    });
    if (lag.covered) {
      Charts.columns(document.getElementById("c-lag"), lag.buckets, {
        value: (b) => b.albums, height: 200, label: "Albums by wait before the first play", xLabel: (b) => b.name,
        tip: (b) => ({ title: b.name, rows: [{ value: Fmt.int(b.albums), label: "albums" }] }),
      });
    }
    if (age.covered) {
      if (dated.length) Charts.line(document.getElementById("c-age"), dated, {
        value: (y) => y.median_years, height: 200, endDot: true, yMin: 0, label: "Median album age per listening year",
        xLabel: (y) => `'${y.year.slice(2)}`,
        tip: (y) => ({ title: y.year, rows: [{ value: `${Fmt.dec(y.median_years)} years`, label: "typical age" }, { value: Fmt.int(y.plays), label: "dated plays" }] }),
      });
      Charts.stacked(document.getElementById("c-age-mix"), age.years, {
        series: age.buckets, shares: (y) => y.shares, other: () => 0, xLabel: (y) => `'${y.year.slice(2)}`,
        colors: ["var(--seq-2)", "var(--seq-3)", "var(--seq-5)", "var(--seq-7)"],
        title: (y) => `${y.year} · ${Fmt.int(y.plays)} dated plays`, label: "Album age mix per listening year",
      });
    }
  }

  // ---------- connections ----------
  async function connectionsView(params) {
    const seq = rendering;
    const n = [60, 120, 200].includes(+params.get("n")) ? +params.get("n") : 120;
    const g = await api(`/api/graph?n=${n}`);
    if (!g.nodes.length) return emptyState(seq);
    const named = (c) => c.members.slice(0, 3).map((m) => m.name).join(" · ");
    const real = g.clusters.filter((c) => c.size > 1);
    const singles = g.clusters.filter((c) => c.size === 1).length;
    paint(seq, html`
      <div class="page-head"><div><h1>Connections</h1>
        <p class="lead">Your top ${g.nodes.length} artists. Two artists are linked when you play them in the same sessions; colours are the taste clusters those links form.</p></div>
        <div class="seg" role="group" aria-label="Show"><span class="label">Show</span>${[60, 120, 200].map((k) =>
          html`<button type="button" data-n="${k}" aria-pressed="${String(k === n)}">${k}</button>`)}</div></div>
      <div class="graph-layout">
        <div class="graph-wrap" id="graph-wrap">
          <div id="graph" style="position:absolute;inset:0"></div>
          <div class="zoom"><button type="button" id="zoom-in" aria-label="Zoom in">+</button><button type="button" id="zoom-out" aria-label="Zoom out">−</button></div>
          <span class="graph-hint">Drag to move · scroll to zoom · click to open</span>
        </div>
        <aside class="clusters" aria-label="Taste clusters"><h2>Taste clusters</h2>
          ${real.map((c, i) => html`<button type="button" class="cluster" data-c="${c.id}" aria-pressed="${String(i === 0)}">
            <span class="head"><span class="dot" style="background:${clusterColor(c.id)}"></span>${c.size} artists<span class="num">${Fmt.int(c.plays)} plays</span></span>
            <p>${named(c)}</p></button>`)}
          ${singles ? html`<p class="secondary small">${singles} artist${singles === 1 ? " has" : "s have"} no strong links.</p>` : ""}
        </aside>
      </div>`);
    const graphEl = document.getElementById("graph");
    graphEl.classList.add("graph-wrap");
    graphEl.style.borderRadius = "0";
    graphEl.style.background = "transparent";
    const graph = TasteGraph.create(graphEl, g, { color: clusterColor, onSelect: (node) => (location.hash = `#/artist/${node.id}`) });
    let pinned = real[0]?.id ?? null;
    graph.highlightCluster(pinned);
    view.querySelectorAll(".cluster").forEach((el) => el.addEventListener("click", () => {
      pinned = pinned === +el.dataset.c ? null : +el.dataset.c;
      view.querySelectorAll(".cluster").forEach((x) => x.setAttribute("aria-pressed", String(+x.dataset.c === pinned)));
      graph.highlightCluster(pinned);
    }));
    view.querySelector("#zoom-in").addEventListener("click", graph.zoomIn);
    view.querySelector("#zoom-out").addEventListener("click", graph.zoomOut);
    view.querySelectorAll("[data-n]").forEach((b) => b.addEventListener("click", () => (location.hash = `#/connections?n=${b.dataset.n}`)));
  }

  // ---------- eras ----------
  async function erasView() {
    const seq = rendering;
    const [eras, timeline] = await Promise.all([api("/api/eras"), api("/api/timeline")]);
    if (!eras.length) return emptyState(seq);
    const years = eras.slice().reverse();
    paint(seq, html`
      <div class="page-head"><div><h1>Eras</h1><p class="lead">How your taste moved, year by year.</p></div></div>
      <nav class="jumpbar" aria-label="Jump to a year">${years.map((e) => html`<button type="button" data-year="${e.year}">${e.year}</button>`)}</nav>
      ${card("New artists each month", html`<div class="chart sage" id="c-new"></div>`, { cls: "canvas", sub: "Not counting your first 30 days of history" })}
      <div class="grid cols-2 eras-grid">${years.map((e) => html`<section class="card era" id="year-${e.year}" tabindex="-1">
        <div class="era-head"><span class="era-year">${e.year}</span>
          <span class="secondary small">${Fmt.int(e.plays)} scrobbles · ${Fmt.int(e.artists)} artists · ${Fmt.int(e.new_artists)} new</span></div>
        <div class="minis">
          <div class="mini"><span class="kicker">Signature</span>${e.signature ? html`<b><a href="#/artist/${e.signature.id}">${e.signature.name}</a></b>
            <span>${liftText(e.signature.lift)} its usual share</span>` : html`<b>–</b><span>no clear signature</span>`}</div>
          <div class="mini"><span class="kicker">Big discovery</span>${e.best_new ? html`<b><a href="#/artist/${e.best_new.id}">${e.best_new.name}</a></b>
            <span>${Fmt.int(e.best_new.plays)} plays</span>` : html`<b>–</b><span>no new artist stuck</span>`}</div>
        </div>
        ${e.genres?.length ? html`<div class="chips">${e.genres.map((g) => html`<a class="chip" href="#/tag/${g.id}">${g.name} <span class="secondary">${Fmt.pct(g.share)}</span></a>`)}</div>` : ""}
        ${rankList(e.top.slice(0, 5), { href: artistHref, compact: true })}
      </section>`)}</div>`);
    view.querySelectorAll("[data-year]").forEach((b) => b.addEventListener("click", () => scrollToId(`year-${b.dataset.year}`)));
    Charts.columns(document.getElementById("c-new"), timeline, {
      value: (d) => d.new_artists, xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null), height: 170, label: "New artists per month",
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.new_artists), label: "new artists" }] }),
      onClick: (d) => (location.hash = `#/library?${qs({ kind: "artist", period: d.month })}`),
    });
  }

  // ---------- insights ----------
  const insightRow = (href, name, meta, key, pos, loved = false) => html`<li class="link-row"><a href="${href}">${pos ? html`<span class="num muted">${pos}</span>` : ""}${loved ? html`<span class="loved row-heart" aria-hidden="true">${icon("heart", 13)}</span>` : ""}
    <span class="grow"><b>${name}</b> <span class="meta">${meta}</span></span><span class="key">${key}</span></a></li>`;
  const NO_LOVED = "No loved tracks yet. Love tracks on last.fm and they show up after the next update.";
  // kind (URL slug, API kind) -> title, subtitle, card tint, payload key, row renderer, empty text by years of history
  const INSIGHTS = [
    { kind: "rising", title: "On the rise", sub: "Last 90 days vs your usual pace for them", cls: "sage-soft", key: "rising",
      row: (r) => [artistHref(r), r.name, `${Fmt.int(r.recent)} plays in 90 d`, `${Fmt.dec(r.growth)}×`],
      empty: (years) => (years < 1.25 ? "Needs at least 15 months of history." : "Nobody is clearly speeding up right now.") },
    { kind: "forgotten", title: "Forgotten favourites", sub: "Big artists you haven't played in over a year", cls: "", key: "forgotten",
      row: (r, ref) => [artistHref(r), r.name, `${Fmt.int(r.plays)} plays`, Fmt.ago(r.last_ts, ref).replace(" ago", "")],
      empty: (years) => (years < 1 ? "Needs a year of history." : "None: you still play all your big artists.") },
    { kind: "obsessions", title: "Obsessions", sub: "Months where one artist took over", cls: "", key: "obsessions",
      row: (r) => [artistHref(r), r.name, Fmt.month(r.month), Fmt.pct(r.share)],
      empty: () => "No month was dominated by one artist." },
    { kind: "staying-power", title: "Staying power", sub: "Played in the most different years", cls: "", key: "staying_power",
      row: (r) => [artistHref(r), r.name, `${Fmt.int(r.plays)} plays`, `${r.n_years} yrs`],
      empty: () => "Needs at least two years of history." },
    { kind: "gateways", title: "Gateways", sub: "Artists you played right before discovering others", cls: "", key: "gateways",
      row: (r) => [artistHref(r), r.name, `led to ${Fmt.int(r.led_to)} · ${Fmt.int(r.downstream_plays)} plays`, Fmt.int(r.led_to)],
      empty: () => "Needs discoveries after your first month." },
    { kind: "binges", title: "Binges", sub: "Most plays of one track in a single day", cls: "sage", key: "binges",
      row: (r) => [trackHref(r), r.name, `${r.artist} · ${Fmt.day(r.day)}`, `${Fmt.int(r.plays)}×`],
      empty: () => "No track was played five times in one day." },
    { kind: "one-track", title: "One-track artists", sub: "One song is almost all you play", cls: "", key: "one_track",
      row: (r) => [artistHref(r), r.name, r.track, Fmt.pct(r.share)],
      empty: () => "No artist is just one song for you." },
    { kind: "loved-left", title: "Loved, then left", sub: "Tracks you loved on last.fm but haven't played in a year", cls: "", key: "loved_left", section: "loved", heart: true,
      row: (r, ref) => [trackHref(r), r.name, `${r.artist} · ${Fmt.int(r.plays)} plays`, Fmt.ago(r.last_ts, ref).replace(" ago", "")],
      empty: (years, ctx) => (!ctx.loved_count ? NO_LOVED : years < 1 ? "Needs a year of history." : "You still play everything you've loved.") },
    { kind: "unloved", title: "Not loved (yet)", sub: "Your most played tracks without a heart on last.fm", cls: "", key: "unloved", section: "loved",
      row: (r) => [trackHref(r), r.name, r.artist, `${Fmt.int(r.plays)} plays`],
      empty: (years, ctx) => (!ctx.loved_count ? NO_LOVED : "You've loved every track you play.") },
    { kind: "instant-love", title: "Love at first listen", sub: "Loved within the first few plays, most played since", cls: "", key: "instant_love", section: "loved", heart: true,
      row: (r) => [trackHref(r), r.name, `${r.artist} · ${r.plays_before ? `loved after ${nPlays(r.plays_before)}`
        : r.loved_at < r.first_ts ? "loved before the first play" : "loved on the first listen"}`, nPlays(r.plays)],
      empty: (years, ctx) => (!ctx.loved_count ? NO_LOVED : "No track was loved within its first few plays.") },
    { kind: "slow-burners", title: "Slow burners", sub: "Loved only after many plays", cls: "", key: "slow_burners", section: "loved", heart: true,
      row: (r) => [trackHref(r), r.name, `${r.artist} · ${r.loved_at - r.first_ts < 86400 ? "loved the same day" : `loved ${span(r.loved_at - r.first_ts)} in`}`, nPlays(r.plays_before)],
      empty: (years, ctx) => (!ctx.loved_count ? NO_LOVED : "No loved track took ten plays or more.") },
    { kind: "deep-dives", title: "Deep dives", sub: "Catalogues you've explored the furthest", cls: "", key: "deep_dives",
      row: (r) => [artistHref(r), r.name, `${Fmt.int(r.plays)} plays`, `${Fmt.int(r.n_tracks)} tracks`],
      empty: () => "Nothing yet." },
  ];
  // " · ♥ 3 loved" after a Rediscover reason when the artist has loved tracks (they weigh in the rank)
  const lovedBadge = (n) => html`<span class="loved count" title="${Fmt.int(n)} loved on last.fm">${icon("heart", 14)} ${Fmt.int(n)} loved<span class="sr-only"> ${n === 1 ? "track" : "tracks"}</span></span>`;
  const lovedCount = (n) => (n > 0 ? html` · ${lovedBadge(n)}` : "");
  const REDISCOVER = { kind: "rediscover", title: "Rediscover", sub: "Artists you used to play alongside your current favourites, untouched for a year.",
    row: (r, ref) => [artistHref(r), r.name, html`because you're into ${r.because.slice(0, 2).map((b, k) => html`${k ? " and " : ""}${b.name}`)}${lovedCount(r.loved)}`, Fmt.ago(r.last_ts, ref)],
    empty: (years) => (years < 1 ? "Needs a year of history." : "Nothing to rediscover: you still play everything you used to pair with your favourites.") };
  const REDISCOVER_SHOWN = 15; // /api/insights returns at most this many (insights.py)
  const seeAll = (kind, label) => html`<a class="more" href="#/insights/${kind}">See all ${label.toLowerCase()} →</a>`;

  // ---------- taste gap: loved tracks vs plays ----------
  // Ratios: one decimal from 0,1× up; below that two significant digits, so 0,018× doesn't read as 0,0×.
  const sig2 = new Intl.NumberFormat("fi-FI", { maximumSignificantDigits: 2 });
  const ratioText = (x) => `${x < 0.1 ? sig2.format(x) : Fmt.dec(x)}×`;
  /* One row: name, "x % of loves · y % of plays", the smoothed ratio, and two bars (loved, played)
     scaled to the largest share in the list. The text carries the numbers; the bars are decoration. */
  function gapRows(items, { href, name, meta }) {
    const top = Math.max(...items.flatMap((x) => [x.loved_share, x.play_share]), 0.0001);
    const w = (v) => `${((v / top) * 100).toFixed(1)}%`;
    const inner = (x) => html`<span class="grow"><b>${name(x)}</b> <span class="meta">${meta(x)}</span></span>
      <span class="key">${ratioText(x.ratio)}</span>
      <span class="gap-bars" aria-hidden="true"><span class="loved"><i style="width:${w(x.loved_share)}"></i></span>
        <span class="played"><i style="width:${w(x.play_share)}"></i></span></span>`;
    return html`<ul class="rows gap-rows">${items.map((x) => (href
      ? html`<li class="link-row"><a href="${href(x)}">${inner(x)}</a></li>` : html`<li>${inner(x)}</li>`))}</ul>`;
  }
  const gapShares = (x) => `${Fmt.pct(x.loved_share)} of loves · ${Fmt.pct(x.play_share)} of plays`;
  const gapLegend = html`<span class="gap-legend" aria-hidden="true"><span class="loved">Loved</span><span class="played">Played</span></span>`;
  const gapEmpty = (text) => html`<p class="empty box">${text}</p>`;
  const tooFew = (n) => gapEmpty(`Needs more loved tracks to compare (now ${Fmt.int(n)}).`);

  // how many taste-gap cards tasteGapCards draws (the jump bar counts them)
  const lovedViews = (g) => (!g?.loved.total ? 0 : !g.loved.matched ? 0 : 5);
  /* The taste-gap cards of the Loved tracks section: genres (loved more / played more), then decades and
     artists. The method behind the numbers is in docs/how-it-works.md, not on the page. */
  function tasteGapCards(g) {
    if (!g?.loved.total) return "";
    const lv = g.loved;
    if (!lv.matched) return gapEmpty("None of your loved tracks match a track you've scrobbled yet.");
    const sub = (title, body, o) => card(title, body, { ...o, h3: true });
    const gn = g.genres;
    const genreCard = (title, subText, side, cls, none) => sub(title, !lv.with_genre
      ? html`<p class="empty box">Needs genre tags: <a class="text-link" href="#/import">fetch them on the Import page</a>.</p>`
      : gn[side].length ? gapRows(gn[side], { href: (x) => `#/tag/${x.id}`, name: (x) => x.name, meta: gapShares })
      : !gn.enough[side] ? tooFew(gn.loved) : gapEmpty(none), { cls, sub: subText });
    const r = g.rules, d = g.decades;
    const decadeBody = !g.plays.with_year ? html`<p class="empty box">Needs release dates: <a class="text-link" href="#/import">fetch them on the Import page</a>.</p>`
      : !d.enough ? gapEmpty(`Needs ${Fmt.int(r.min_dated_loved)} loved tracks with a release year (now ${Fmt.int(d.loved)}).`)
      : gapRows(d.items, { name: (x) => x.label, meta: gapShares });
    const ar = g.artists;
    const artistMeta = (x) => `${Fmt.int(x.loved)} loved · ${Fmt.int(x.plays)} plays`;
    const artistCard = (title, subText, side, cls, none) => sub(title, ar[side].length
      ? gapRows(ar[side], { href: artistHref, name: (x) => x.name, meta: artistMeta })
      : !ar.enough[side] ? tooFew(ar.loved) : gapEmpty(none), { cls, sub: subText });
    return html`<div class="grid cols-2">
        ${genreCard("Loved more than played", "Genres with a bigger share of your loves than of your plays.", "over", "accent-soft",
          "No genre stands out among your loves.")}
        ${genreCard("Played more than loved", "Genres you play a lot but rarely love.", "under", "",
          "You love every big genre about as much as you play it.")}
      </div>
      <div class="grid cols-3">
        ${sub("Decades", decadeBody, { sub: "Release decade of loved tracks vs plays." })}
        ${artistCard("Artists you love more than you play", "A bigger share of your loves than of your plays.", "over", "accent-soft",
          "No artist's loves outrun their plays.")}
        ${artistCard("Big artists, few loves", "Artists whose plays would suggest more loved tracks than they have.", "under", "",
          "Your big artists get their share of loves.")}
      </div>`;
  }

  async function insightsView() {
    const seq = rendering;
    const [i, ov, gap] = await Promise.all([api("/api/insights"), api("/api/overview"), api("/api/loved/gap").catch(() => null)]);
    if (!i.reference_ts) return emptyState(seq);
    const ref = i.reference_ts;
    const years = (ov.last_ts - ov.first_ts) / (365.25 * 86400);
    const shown = 6;
    const listCard = (d) => {
      const items = i[d.key];
      const body = items.length ? html`<ul class="rows">${items.slice(0, shown).map((r) => insightRow(...d.row(r, ref), 0, d.heart))}</ul>`
        : html`<p class="empty box">${d.empty(years, i)}</p>`;
      return card(d.title, body, { id: d.kind, cls: d.cls, sub: d.sub, h3: true, foot: items.length > shown ? seeAll(d.kind, d.title) : "" });
    };
    const patterns = INSIGHTS.filter((d) => d.section !== "loved"), lovedLists = INSIGHTS.filter((d) => d.section === "loved");
    const hasLoved = !!(gap?.loved.total || i.loved_count);
    const tiles = 8;
    paint(seq, html`
      <div class="page-head"><div><h1>Insights</h1><p class="lead">Patterns in your history, up to your latest scrobble on ${Fmt.date(ov.last_ts)}.</p></div>
        <a class="text-link" href="${DOCS}#the-pages-in-detail" target="_blank" rel="noopener noreferrer">How these are measured ${icon("external", 14)}</a></div>
      <nav class="jumpbar" aria-label="Jump to a section">
        <button type="button" data-to="rediscover">Rediscover <span class="n">${Fmt.int(i.rediscover.length)}${i.rediscover.length >= REDISCOVER_SHOWN ? "+" : ""}</span></button>
        <button type="button" data-to="patterns">Your patterns <span class="n">${patterns.length} lists</span></button>
        ${hasLoved ? html`<button type="button" data-to="loved">Loved tracks <span class="n">${lovedViews(gap) + lovedLists.length} views</span></button>` : ""}</nav>
      <section class="card solid feature" id="rediscover" tabindex="-1" aria-labelledby="rediscover-title">
        <div class="feature-head"><h2 id="rediscover-title">Rediscover</h2><p>${REDISCOVER.sub}</p></div>
        ${i.rediscover.length ? html`<div class="tiles-2">${i.rediscover.slice(0, tiles).map((r) => html`<a class="tile-link" href="#/artist/${r.id}">
            <span class="top-line"><b>${r.name}</b><span class="ago">${Fmt.ago(r.last_ts, ref)}</span></span>
            <span class="why">because you're into ${r.because.slice(0, 2).map((b, k) => html`${k ? " and " : ""}<b>${b.name}</b>`)}</span>
            ${r.loved > 0 ? html`<span class="why">${lovedBadge(r.loved)}</span>` : ""}</a>`)}</div>`
          : html`<p>${REDISCOVER.empty(years)}</p>`}
        ${i.rediscover.length > tiles ? html`<p class="card-foot">${seeAll("rediscover", "rediscoveries")}</p>` : ""}
      </section>
      <section class="page-section" id="patterns" tabindex="-1" aria-labelledby="patterns-title">
        <div class="section-head"><h2 id="patterns-title">Your patterns</h2><p class="secondary">How you play: streaks, returns, and where discoveries came from.</p></div>
        <div class="columns-3">${patterns.map(listCard)}</div>
      </section>
      <section class="page-section" id="loved" tabindex="-1" aria-labelledby="loved-title">
        <div class="section-head split"><div><h2 id="loved-title">Loved tracks</h2><p class="secondary">Your hearts on last.fm, set against what you actually play.</p></div>
          ${gap?.loved.total ? html`<div class="head-chips"><span class="pill accent">${Fmt.int(gap.loved.matched)} of ${Fmt.int(gap.loved.total)} loves in your library</span>${gap.loved.matched ? gapLegend : ""}</div>` : ""}</div>
        ${hasLoved ? html`${gap ? tasteGapCards(gap) : gapEmpty("Couldn't load the comparison of loves and plays. Try again later.")}<div class="columns-3">${lovedLists.map(listCard)}</div>`
          : html`<div class="card accent-soft no-loved"><span class="heart-badge" aria-hidden="true">${icon("heart", 24)}</span>
              <div><h3>No loved tracks yet</h3><p>Love tracks on last.fm and this section fills in after the next update: what you love vs what you play, love at first listen, slow burners and more.</p></div></div>`}
      </section>`);
    view.querySelectorAll("[data-to]").forEach((b) => b.addEventListener("click", () => {
      const el = document.getElementById(b.dataset.to);
      el.setAttribute("tabindex", "-1");
      scrollToId(b.dataset.to);
    }));
  }

  // One insight in full; "Show more" grows ?n= so the list stays on one page.
  let insightFocusFrom = null;
  async function insightView(kind, params) {
    const seq = rendering;
    const d = kind === "rediscover" ? REDISCOVER : INSIGHTS.find((x) => x.kind === kind);
    if (!d) {
      paint(seq, html`<div class="page-head"><div><h1>Not found</h1><p class="lead"><a href="#/insights">Back to Insights</a></p></div></div>`);
      return;
    }
    const step = 50, max = 500;
    const n = Math.min(max, Math.max(step, Math.round((+params.get("n") || step) / step) * step));
    const [data, ov] = await Promise.all([api(`/api/insights/${kind}?` + qs({ limit: n })), api("/api/overview")]);
    if (!data.reference_ts) return emptyState(seq);
    const years = (ov.last_ts - ov.first_ts) / (365.25 * 86400);
    const items = data.items;
    paint(seq, html`
      <div class="page-head"><div><a class="kicker" href="#/insights">← Insights</a><h1>${d.title}</h1>
        <p class="lead">${d.sub.replace(/\.$/, "")}. ${!data.has_more ? `All ${Fmt.int(items.length)}` : n < max ? `The top ${Fmt.int(n)}`
          : `The top ${Fmt.int(max)}, where the list stops`}, up to ${Fmt.date(ov.last_ts)}.</p></div></div>
      ${card("", items.length ? html`<ul class="rows">${items.map((r, k) => insightRow(...d.row(r, data.reference_ts), k + 1, d.heart))}</ul>`
        : html`<p class="empty box">${d.empty(years, data)}</p>`, {
        cls: d.cls ?? "",
        foot: data.has_more && n < max ? html`<button type="button" class="btn secondary small" id="more">Show ${step} more</button>` : "",
      })}`);
    // after "Show more", focus the first new row (the button may be gone, and the new rows sit above it)
    if (insightFocusFrom != null) view.querySelectorAll(".rows a")[Math.min(insightFocusFrom, items.length - 1)]?.focus();
    insightFocusFrom = null;
    view.querySelector("#more")?.addEventListener("click", (e) => {
      insightFocusFrom = n;
      e.currentTarget.blur(); // so route() doesn't put focus back on the button
      location.hash = `#/insights/${kind}?` + qs({ n: n + step });
    });
  }

  // ---------- upcoming ----------
  // Releases of the next Fridays (releases.py). The server keeps a cache; a stale one is refreshed
  // when the page opens, which takes a few seconds, so the cached list shows meanwhile.
  const LB_FRESH = "https://listenbrainz.org/explore/fresh-releases/";
  const fridayLabel = (iso, today) => {
    const days = Math.round((new Date(iso + "T12:00:00") - new Date(today + "T12:00:00")) / 864e5);
    return days === 0 ? "Out today" : days < 0 ? "Out this week" : days < 7 ? "This Friday" : days < 14 ? "Next Friday" : `In ${Math.round(days / 7)} weeks`;
  };
  const playsLine = (a) => html`${[nPlays(a.plays), a.recent_plays ? `${Fmt.int(a.recent_plays)} in the last year` : ""].filter(Boolean).join(" · ")}${lovedCount(a.loved)}`;
  // why a release by an artist you don't play is listed: similar artists you play, shared genres
  const whyNew = (r) => [r.like.length ? `Like ${r.like.join(", ")}` : "", r.genres.length ? r.genres.join(", ") : ""].filter(Boolean).join(" · ");
  function releaseRow(r, mine, why = "") {
    const extra = [
      r.source_url ? html`<a class="chip plain" href="${r.source_url}" target="_blank" rel="noopener noreferrer">News ${icon("external", 14)}</a>` : "",
      r.release_group_mbid ? html`<a class="chip plain" href="https://musicbrainz.org/release-group/${r.release_group_mbid}" target="_blank" rel="noopener noreferrer">MusicBrainz ${icon("external", 14)}</a>` : "",
    ];
    const what = [r.release_type, Fmt.day(r.release_date), r.genre, r.label].filter(Boolean).join(" · ");
    const by = mine ? r.artists.map((a, k) => html`${k ? ", " : ""}${link.artist(a.id, a.name)}`) : r.artist;
    const cover = r.cover_url || (mine && r.artists[0].image_url) || null;
    return html`<li>${initial(r.title, cover, true)}
      <div class="grow"><b>${r.title}</b> <span class="meta">by ${by}</span>
        <small>${what}${mine ? html`<br>${r.artists.map((a, k) => html`${k ? " / " : ""}${playsLine(a)}`)}` : ""}${why ? html`<br>${why}` : ""}</small></div>
      <span class="ext">${extra}</span></li>`;
  }
  const PROMPT_TASKS = [["releases", "New releases"], ["discover", "New artists"], ["profile", "Taste only"]];
  const shortDay = (iso) => Fmt.day(iso).replace(/\s\d{4}$/, ""); // "16 Oct"
  let copyTimer = 0;
  let upcomingRefresh = null; // the running refresh, so a re-render doesn't start another
  let upcomingAutoAt = 0;     // when the page last refreshed by itself (at most every 10 minutes, so a failing source can't loop)
  // kept across re-renders (a refresh re-renders the page every few seconds)
  const upcomingOpen = new Set(); // weeks whose "Also out" list is open
  let promptOpen = false;         // the whole prompt shown
  let quietOpen = null;           // the quiet week whose list is shown
  async function upcomingView(params) {
    const seq = rendering;
    const all = params.get("show") === "all";
    const task = PROMPT_TASKS.some(([k]) => k === params.get("prompt")) ? params.get("prompt") : "releases";
    const here = (o) => "#/upcoming?" + qs({ show: all ? "all" : null, prompt: task === "releases" ? null : task, ...o });
    const [u, ov, tp] = await Promise.all([api("/api/upcoming", { fresh: true }), api("/api/overview"),
      api("/api/taste-prompt?" + qs({ task }), { fresh: true }).catch(() => ({ text: "" }))]); // the releases show without it
    const keep = (r) => all || r.release_type !== "Single";
    const src = u.sources, fm = u.lastfm, now = Date.now() / 1000;
    const checked = Math.max(0, ...Object.values(src).map((s) => s.at ?? 0)); // the newest list; a failing source gets a pill
    const busy = u.refreshing || upcomingRefresh;
    const partly = fm.has_key && fm.seeds && fm.seeds_done < fm.seeds ? ` Similar artists looked up for ${Fmt.int(fm.seeds_done)} of your ${Fmt.int(fm.seeds)} most played.` : "";
    const status = busy ? `${u.progress || "Checking ListenBrainz and Wikipedia for new releases"}…`
      : !checked ? "Not checked yet."
      : (now - checked < 86400 ? `Checked at ${new Date(checked * 1000).toTimeString().slice(0, 5)}.` : `Checked ${Fmt.ago(checked, now)}.`) + partly;
    const listName = { listenbrainz: "ListenBrainz", wikipedia: "Wikipedia" };
    const pills = busy ? [] : [
      ...Object.entries(src).filter(([, s]) => s.error).map(([k, s]) => html`<span class="pill accent" title="${s.error}">${listName[k]} didn't answer · ${
        s.at ? `showing the list from ${Fmt.ago(s.at, now).replace(/^today$/, "earlier today")}` : "nothing saved yet"}<span class="sr-only"> (${s.error})</span></span>`),
      fm.has_key && fm.error ? html`<span class="pill accent" title="${fm.error}">${/api key|error (10|26)|suspend/i.test(fm.error)
        ? "last.fm refused the API key" : "last.fm didn't answer"} · new artists may be out of date<span class="sr-only"> (${fm.error})</span></span>` : "",
      fm.has_key ? "" : html`<a class="pill sage" href="#/import">Save your last.fm API key for new artists</a>`,
    ];
    const weeks = u.weeks.map((w) => {
      const fresh = fm.has_key ? w.new.filter(keep) : [], mine = w.mine.filter(keep);
      return { ...w, fresh, mine, also: w.also.filter(keep), count: fresh.length + mine.length };
    });
    const busyWeeks = weeks.filter((w) => w.count), quiet = weeks.filter((w) => !w.count);
    if (!quiet.some((w) => w.friday === quietOpen)) quietOpen = null;
    const lbMore = (n) => html`<a href="${LB_FRESH}" target="_blank" rel="noopener noreferrer">${Fmt.int(n)} more albums and EPs on ListenBrainz ${icon("external", 14)}</a>`;
    const section = (label, rows) => (rows ? html`${fm.has_key ? html`<h3 class="list-h">${label}</h3>` : ""}${rows}` : "");
    paint(seq, html`
      <div class="page-head"><div><span class="kicker">Seven Fridays</span><h1>Upcoming</h1>
        <p class="lead">New albums and EPs for this Friday and the ${["zero", "one", "two", "three", "four", "five", "six", "seven", "eight"][weeks.length - 1] ?? weeks.length - 1} after it${fm.has_key ? ": new artists similar to the ones you play, and your own artists ranked by how much you play them" : ", ranked by how much you play the artist"}.</p></div></div>
      <div class="toolbar upcoming-bar">
        <div class="seg" role="group" aria-label="Show">
          <button type="button" data-show="albums" aria-pressed="${String(!all)}">Albums and EPs</button>
          <button type="button" data-show="all" aria-pressed="${String(all)}">With singles</button></div>
        <div class="status-line" role="status"><span>${status}</span>${pills}</div>
        <button type="button" class="btn secondary small" id="refresh" aria-disabled="${String(!!busy)}">Check again</button></div>
      ${busyWeeks.length ? html`<nav class="jumpbar" aria-label="Jump to a week">${busyWeeks.map((w) => html`<button type="button" data-to="wk-${w.friday}">${shortDay(w.friday)}
        <span class="count">${Fmt.int(w.count)}</span></button>`)}${quiet.length ? html`<span class="quiet-note">${quiet.length === 1 ? "1 quiet week" : `${quiet.length} quiet weeks`} below</span>` : ""}</nav>` : ""}
      ${checked && !busyWeeks.length && !ov.empty ? html`<p class="empty box">None of your artists has a release coming in these weeks${all ? "" : " (singles hidden)"}.
        Release ids come with the metadata fetch on the Import page, which helps matching.</p>` : ""}
      ${busyWeeks.map((w) => card("", html`
        <div class="week-head"><h2>${fridayLabel(w.friday, u.today)}</h2><span class="date">${shortDay(w.friday)}</span>
          <span class="count-text">${fm.has_key ? `${Fmt.int(w.fresh.length)} new to you · ` : ""}${Fmt.int(w.mine.length)} by artists you play</span></div>
        ${section("New to you", w.fresh.length ? html`<ul class="rows releases">${w.fresh.map((r) => releaseRow(r, false, whyNew(r)))}</ul>` : "")}
        ${section("From your artists", w.mine.length ? html`<ul class="rows releases">${w.mine.map((r) => releaseRow(r, true))}</ul>` : "")}
        ${w.also.length ? html`<details class="also" data-week="${w.friday}" ${upcomingOpen.has(w.friday) ? raw("open") : ""}><summary class="more">Also out: ${Fmt.int(w.also.length)} more from Wikipedia's list</summary>
          <ul class="rows releases compact">${w.also.map((r) => releaseRow(r, false))}</ul></details>` : ""}`, {
        id: `wk-${w.friday}`, foot: w.more ? lbMore(w.more) : "",
      }))}
      ${checked && quiet.length ? html`<section class="card quiet" id="quiet">
        <h2>Quiet weeks</h2><p>${!fm.has_key ? "Nothing from your artists." : fm.seeds_done ? "Nothing new to you and nothing from your artists."
          : "Nothing from your artists (new artists aren't looked up yet)."} Other releases are still listed.</p>
        <div class="chips">${quiet.map((w) => (w.also.length || w.more
          ? html`<button type="button" class="chip" data-quiet="${w.friday}" aria-expanded="${String(w.friday === quietOpen)}" aria-controls="quiet-${w.friday}"><b>${shortDay(w.friday)}</b>
              <span class="secondary">${w.also.length ? `${Fmt.int(w.also.length)} also out` : `${Fmt.int(w.more)} on ListenBrainz`}</span></button>`
          : html`<span class="chip plain"><b>${shortDay(w.friday)}</b> <span class="secondary">nothing listed</span></span>`))}</div>
        ${quiet.filter((w) => w.also.length || w.more).map((w) => html`<div id="quiet-${w.friday}" class="quiet-list" role="region"
            aria-label="${fridayLabel(w.friday, u.today)}, ${shortDay(w.friday)}" ${w.friday === quietOpen ? "" : raw("hidden")}>
          <h3 class="list-h">${fridayLabel(w.friday, u.today)}, ${shortDay(w.friday)}</h3>
          ${w.also.length ? html`<ul class="rows releases compact">${w.also.map((r) => releaseRow(r, false))}</ul>` : ""}
          ${w.more ? html`<p class="card-foot">${lbMore(w.more)}</p>` : ""}</div>`)}
      </section>` : ""}
      ${tp.text ? html`<section class="card sage-soft" id="prompt">
        <div class="card-head"><div><h2>Ask an AI to dig further</h2>
          <p>A description of your taste, weighted to what you play now, with a ready task. Paste it into any assistant that can search the web. Nothing is sent from here.</p></div>
          <div class="aside"><div class="seg" role="group" aria-label="Task">${PROMPT_TASKS.map(([k, l]) => html`<button type="button" data-prompt="${k}" aria-pressed="${String(k === task)}">${l}</button>`)}</div></div></div>
        <div class="prompt-box ${promptOpen ? "open" : ""}"><pre class="prompt" id="prompt-text" tabindex="0" role="region" aria-label="The prompt">${tp.text}</pre></div>
        <div class="prompt-actions"><button type="button" class="btn primary small" id="copy-prompt">Copy prompt</button>
          <button type="button" class="btn ghost small" id="toggle-prompt" aria-expanded="${String(promptOpen)}" aria-controls="prompt-text">${promptOpen ? "Show less" : "Show the whole prompt"}</button>
          <span class="muted">${Fmt.int(tp.text.length)} characters</span><span class="sr-only" role="status" id="copy-status"></span></div>
      </section>` : ""}
      <p class="muted source-note">Release lists from ListenBrainz (MusicBrainz data) and Wikipedia${fm.has_key ? "; similar artists and genres from last.fm" : ""}.
        <a class="text-link" href="${DOCS}#upcoming-releases" target="_blank" rel="noopener noreferrer">How this works ${icon("external", 14)}</a></p>`);
    view.querySelectorAll("[data-show]").forEach((b) => b.addEventListener("click", () => {
      location.hash = here({ show: b.dataset.show === "all" ? "all" : null });
    }));
    view.querySelectorAll("[data-prompt]").forEach((b) => b.addEventListener("click", () => {
      location.hash = here({ prompt: b.dataset.prompt === "releases" ? null : b.dataset.prompt });
    }));
    view.querySelector("#toggle-prompt")?.addEventListener("click", (e) => {
      promptOpen = !promptOpen;
      view.querySelector(".prompt-box").classList.toggle("open", promptOpen);
      e.currentTarget.setAttribute("aria-expanded", String(promptOpen));
      e.currentTarget.textContent = promptOpen ? "Show less" : "Show the whole prompt";
    });
    view.querySelectorAll("[data-quiet]").forEach((b) => b.addEventListener("click", () => {
      quietOpen = quietOpen === b.dataset.quiet ? null : b.dataset.quiet;
      view.querySelectorAll("[data-quiet]").forEach((c) => c.setAttribute("aria-expanded", String(c.dataset.quiet === quietOpen)));
      view.querySelectorAll(".quiet-list").forEach((l) => { l.hidden = l.id !== `quiet-${quietOpen}`; });
    }));
    view.querySelector("#copy-prompt")?.addEventListener("click", async (e) => {
      const b = e.currentTarget, pre = view.querySelector("#prompt-text"), status = view.querySelector("#copy-status");
      clearTimeout(copyTimer);
      status.textContent = ""; // so a second copy is announced again
      try {
        await navigator.clipboard.writeText(tp.text);
        b.textContent = "Copied";
      } catch { // no clipboard access: select the text so Cmd/Ctrl+C works
        pre.focus();
        getSelection().selectAllChildren(pre);
        b.textContent = "Selected: press Cmd/Ctrl+C";
      }
      status.textContent = b.textContent;
      copyTimer = setTimeout(() => { b.textContent = "Copy prompt"; status.textContent = ""; }, 2500);
    });
    view.querySelectorAll("[data-to]").forEach((b) => b.addEventListener("click", () => {
      document.getElementById(b.dataset.to).setAttribute("tabindex", "-1");
      scrollToId(b.dataset.to);
    }));
    const run = () => {
      if (upcomingRefresh || busy) return;
      upcomingRefresh = postJson("/api/upcoming/refresh")
        .catch((err) => { if (!/already/.test(err.message)) console.error(err); })
        .finally(() => { upcomingRefresh = null; if (location.hash.startsWith("#/upcoming")) route(); });
      if (location.hash.startsWith("#/upcoming")) route(); // show the "checking" state
    };
    view.querySelector("#refresh").addEventListener("click", run); // stays focusable while busy; run() ignores it then
    view.querySelectorAll("details.also").forEach((d) => d.addEventListener("toggle", () => {
      if (d.open) upcomingOpen.add(d.dataset.week); else upcomingOpen.delete(d.dataset.week);
    }));
    if (u.stale && !busy && Date.now() - upcomingAutoAt > 10 * 60e3) { upcomingAutoAt = Date.now(); run(); }
    // a refresh runs in the background (started here, in another tab or before a reload): look again until it is done
    if (u.refreshing && !upcomingRefresh) setTimeout(() => { if (seq === routeSeq && location.hash.startsWith("#/upcoming")) route(); }, 5000);
  }

  // ---------- cleanup ----------
  function bindPicker(input, onPick) {
    const box = input.parentElement.querySelector(".picker-results");
    let timer, seq = 0;
    const pick = (id, name, plays) => {
      input.value = name; input.dataset.id = id; input.dataset.plays = plays ?? ""; input.classList.add("picked");
      box.hidden = true; input.setAttribute("aria-expanded", "false"); onPick?.(id, name);
    };
    input.addEventListener("input", () => {
      delete input.dataset.id;
      input.classList.remove("picked");
      onPick?.(null);
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { box.hidden = true; return; }
      timer = setTimeout(async () => {
        const my = ++seq;
        const r = await api("/api/artists?" + qs({ q, limit: 8 }));
        if (my !== seq) return;
        mount(box, r.items.length ? html`${r.items.map((a) => html`<button type="button" role="option" data-id="${a.id}" data-name="${a.name}" data-plays="${a.plays}">
          <span>${a.name}</span><span class="secondary num">${Fmt.int(a.plays)}</span></button>`)}` : html`<p class="empty">No artist matches</p>`);
        box.hidden = false;
        input.setAttribute("aria-expanded", "true");
      }, 150);
    });
    box.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) pick(Number(b.dataset.id), b.dataset.name, b.dataset.plays); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") box.hidden = true;
      if (e.key === "ArrowDown") { e.preventDefault(); box.querySelector("button")?.focus(); }
    });
    box.addEventListener("keydown", (e) => {
      const bs = [...box.querySelectorAll("button")], i = bs.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); bs[Math.min(bs.length - 1, i + 1)]?.focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); i <= 0 ? input.focus() : bs[i - 1].focus(); }
      if (e.key === "Escape") { box.hidden = true; input.focus(); }
    });
    input.addEventListener("blur", () => setTimeout(() => { if (!box.contains(document.activeElement)) box.hidden = true; }, 150));
    return pick;
  }
  const picker = (id, placeholder) => html`<span class="picker"><input id="${id}" type="search" autocomplete="off" spellcheck="false" role="combobox"
    aria-expanded="false" aria-controls="${id}-list" placeholder="${placeholder}"><span class="picker-results" id="${id}-list" role="listbox" hidden></span></span>`;
  // Inline confirm for duplicate rows: the first click arms the button; it stays armed until clicked again or blurred.
  function armed(button, confirmText, run) {
    const label = button.textContent;
    const disarm = () => { button.classList.remove("armed", "danger"); button.classList.add("primary"); button.textContent = label; };
    button.addEventListener("click", async () => {
      if (!button.classList.contains("armed")) {
        button.classList.add("armed", "danger");
        button.classList.remove("primary");
        button.textContent = confirmText();
        return;
      }
      button.disabled = true;
      try { await run(); } finally { button.disabled = false; disarm(); }
    });
    button.addEventListener("blur", () => { if (button.classList.contains("armed")) disarm(); });
  }
  // Why two names were grouped, for the reason chip.
  function reasonFor(names, lastfm) {
    const fold = (s) => s.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
    const a = names[0], rest = names.slice(1);
    if (lastfm.some((x) => x && names.some((n) => n !== x && n.toLowerCase() !== x.toLowerCase()))) {
      if (rest.every((b) => fold(a).replace(/[^a-z0-9]/g, "") !== fold(b).replace(/[^a-z0-9]/g, "")) && !rest.some((b) => /0/.test(a + b))) return "last.fm corrects";
    }
    if (rest.some((b) => /0/.test(a + b) && fold(a).replace(/0/g, "o") === fold(b).replace(/0/g, "o"))) return "0 / o";
    if (rest.some((b) => fold(a) === fold(b) && a.toLowerCase() !== b.toLowerCase())) return "accents";
    if (rest.some((b) => /^the\s/i.test(a) !== /^the\s/i.test(b))) return "leading “the”";
    if (rest.some((b) => /&/.test(a + b))) return "& / and";
    return "spelling";
  }

  async function cleanupView(params) {
    const seq = rendering;
    const [groups, rules, loved] = await Promise.all([api("/api/maintenance/duplicates", { fresh: true }),
      api("/api/maintenance/aliases", { fresh: true }), api("/api/loved/unmatched", { fresh: true })]);
    const playsText = (n) => `${Fmt.int(n)} ${n === 1 ? "play" : "plays"}`;
    const lovedHtml = (u, i) => html`<li>
        <div class="lm-main"><b>${u.title}</b> <span class="meta">by ${u.artist_id ? link.artist(u.artist_id, u.artist) : u.artist}</span>
          <span class="reason">${u.reason[0].toUpperCase() + u.reason.slice(1)} · loved ${Fmt.date(u.loved_at)}</span></div>
        <div class="lm-actions">${u.suggestions.length
          ? u.suggestions.map((s) => html`<button type="button" class="btn secondary small loved-link" data-i="${i}" data-track="${s.id}">Link to ${s.title} (${playsText(s.plays)})</button>`)
          : html`<span class="reason">${u.artist_id ? "No similar title among this artist's tracks." : "If it's spelled differently here, merge it or add a name rule above."}</span>`}</div></li>`;
    // the rules list shows even when nothing is loved any more, so leftover links can still be undone
    const lovedBody = html`${!loved.total ? html`<p class="empty box">Nothing loved yet. Loved tracks come from last.fm with each update.</p>`
        : loved.items.length ? html`<ul class="rows loved-unmatched">${loved.items.map(lovedHtml)}</ul>`
        : html`<p class="empty box">All ${Fmt.int(loved.total)} loved tracks match your library.</p>`}
        ${loved.rules.length ? html`<h3 class="lm-head">Linked by you</h3><ul class="rows rules loved-rules">${loved.rules.map((r) => html`<li><b>${r.title}</b><span aria-hidden="true">${icon("arrow", 14)}</span>
            <a class="to" href="#/track/${r.track_id}">${r.track}</a><span class="meta">${r.still_loved ? r.track_artist : `${r.track_artist}, no longer loved`}</span><span class="meta">${Fmt.date(r.created_at)}</span>
            <button type="button" class="btn ghost small loved-undo" data-id="${r.id}" aria-label="Undo the link from ${r.title} to ${r.track}">Undo</button></li>`)}</ul>` : ""}`;
    const preset = params.get("merge") ? await api(`/api/artists/${Number(params.get("merge"))}`).catch(() => null) : null;
    const PAGE = 20;
    let shown = PAGE;
    const groupHtml = (g) => html`<li class="dupe" data-key="${g.key}">
        <div class="dupe-names" role="radiogroup" aria-label="Spelling to keep">${g.artists.map((a) => html`<button type="button" class="keep-opt" role="radio"
          aria-checked="${String(a.id === g.target_id)}" data-id="${a.id}">${a.name} <span class="plays">${Fmt.int(a.plays)}</span><span class="keep-mark">KEEP</span></button>`)}</div>
        <div class="dupe-actions"><span class="reason">${reasonFor(g.artists.map((a) => a.name), g.artists.map((a) => a.lastfm_name))}</span>
          <button type="button" class="btn ghost small dismiss">Not the same</button><button type="button" class="btn primary small merge">Merge</button></div></li>`;
    paint(seq, html`
      <div class="page-head"><div><h1>Cleanup</h1>
        <p class="lead">Combine artists spelled more than one way. A merge moves every scrobble to the name you keep, and future imports of the other spelling land there too.</p></div></div>
      <div class="status-pill" id="cleanup-msg" role="status" aria-live="polite"></div>
      ${card("Merge two artists", html`<form class="merge-form" id="merge-form" autocomplete="off">
          <label class="field" for="m-source">Merge away${picker("m-source", "Search for the misspelling")}</label>
          <span class="arrow" aria-hidden="true">${icon("arrow", 16)}</span>
          <label class="field" for="m-target">Keep${picker("m-target", "Search for the artist to keep")}</label>
          <button class="btn primary" type="submit" id="m-go" disabled>Review merge</button></form>`,
        { sub: "Tracks and albums with the same title are combined. A merge can't be undone." })}
      ${card(html`Possible duplicates ${groups.length ? html`<span class="pill accent" style="vertical-align:4px">${Fmt.int(groups.length)}</span>` : ""}`,
        groups.length ? html`<ul class="dupes" id="dupes">${groups.slice(0, shown).map(groupHtml)}</ul>
            ${groups.length > shown ? html`<p style="margin-top:12px"><button type="button" class="btn secondary small" id="dupes-more">Show all ${Fmt.int(groups.length)}</button></p>` : ""}`
          : html`<p class="empty box">No likely duplicates found.</p>`,
        { cls: "canvas", sub: "Pick the spelling to keep in each group." })}
      ${card("Name rules", html`${rules.length ? html`<ul class="rows rules">${rules.map((r) => html`<li><b>${r.name}</b><span aria-hidden="true">${icon("arrow", 14)}</span>
            <a class="to" href="#/artist/${r.artist_id}">${r.artist}</a><span class="meta">${Fmt.int(r.scrobbles)} scrobbles</span><span class="meta">${Fmt.date(r.created_at)}</span>
            <button type="button" class="btn ghost small rule-remove" data-id="${r.id}" aria-label="Remove the rule for ${r.name}">Remove</button></li>`)}</ul>`
          : html`<p class="empty box">No rules yet. Merging two artists creates one.</p>`}
          <form class="rule-add" id="rule-form" autocomplete="off">
            <input id="r-name" type="text" spellcheck="false" placeholder="A spelling you haven't imported yet" aria-label="Spelling" required maxlength="500">
            <span class="arrow" aria-hidden="true">${icon("arrow", 14)}</span>${picker("r-target", "Artist")}<button type="submit" class="btn secondary" id="r-go" disabled>Add rule</button></form>`,
        { sub: "Applied on every import. Removing one doesn't split artists already merged." })}
      ${card(html`Loved tracks that don't match ${loved.items.length ? html`<span class="pill accent" style="vertical-align:4px">${Fmt.int(loved.items.length)}</span>` : ""}`,
        lovedBody, { id: "loved-unmatched", sub: "Loved on last.fm but not found under the same title here. Linking one keeps working after each update, and Undo takes it back." })}
      <dialog id="merge-dialog" aria-labelledby="merge-title"><div class="dialog-body"></div></dialog>`);

    const say = (tpl, err = false) => {
      const msg = document.getElementById("cleanup-msg");
      msg.classList.toggle("err", err);
      mount(msg, html`${err ? "" : icon("check")}<span>${tpl}</span>`);
    };
    async function merge(sourceIds, targetId) {
      const r = await postJson("/api/maintenance/merge", { source_ids: sourceIds, target_id: targetId });
      cache.clear();
      const moved = r.merged.reduce((n, m) => n + m.scrobbles_moved, 0);
      return html`Merged ${r.merged.map((m) => `“${m.source}”`).join(", ")} into <a href="#/artist/${r.target_id}">${r.merged[0].target}</a>: ${Fmt.int(moved)} scrobbles moved. Future imports go to the same place.`;
    }
    async function refresh(message) {
      if (!location.hash.startsWith("#/cleanup")) return; // the user moved on
      await cleanupView(new URLSearchParams());
      if (message) say(message);
    }

    // manual merge, confirmed in a dialog with the counts
    const src = view.querySelector("#m-source"), dst = view.querySelector("#m-target"), go = view.querySelector("#m-go");
    const ready = () => { go.disabled = !(src.dataset.id && dst.dataset.id && src.dataset.id !== dst.dataset.id); };
    const pickSource = bindPicker(src, ready);
    bindPicker(dst, ready);
    if (preset) { pickSource(preset.id, preset.name, preset.plays); dst.focus(); }
    const dialog = view.querySelector("#merge-dialog");
    view.querySelector("#merge-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { source_ids: [Number(src.dataset.id)], target_id: Number(dst.dataset.id) };
      let p;
      try { p = await postJson("/api/maintenance/merge/preview", body); } catch (err) { return say(err.message, true); }
      mount(dialog.querySelector(".dialog-body"), html`<h2 id="merge-title">Merge “${p.sources[0].name}” into ${p.target}?</h2>
        <p class="secondary">Every scrobble moves to <strong>${p.target}</strong>, and future imports of “${p.sources[0].name}” go there too. This can't be undone.</p>
        <div class="dialog-facts"><div><b>${Fmt.int(p.scrobbles)}</b><span>scrobbles moved</span></div>
          <div><b>${Fmt.int(p.tracks_combined)}</b><span>tracks combined</span></div><div><b>${Fmt.int(p.albums_combined)}</b><span>albums combined</span></div></div>
        ${p.duplicates ? html`<p class="secondary small">${Fmt.int(p.duplicates)} plays were scrobbled under both names at the same minute and are kept once.</p>` : ""}
        <div class="dialog-actions"><button type="button" class="btn secondary" id="d-cancel">Cancel</button><button type="button" class="btn primary" id="d-merge">Merge</button></div>`);
      dialog.showModal();
      dialog.querySelector("#d-cancel").addEventListener("click", () => dialog.close());
      dialog.querySelector("#d-merge").addEventListener("click", async (ev) => {
        ev.target.disabled = true;
        try { const text = await merge(body.source_ids, body.target_id); dialog.close(); await refresh(text); }
        catch (err) { dialog.close(); say(err.message, true); }
      });
    });

    // duplicate suggestions
    function bindGroups() {
      view.querySelectorAll(".dupe:not([data-bound])").forEach((li) => {
        li.dataset.bound = "1";
        const g = groups.find((x) => x.key === li.dataset.key);
        const radios = [...li.querySelectorAll("[role=radio]")];
        const choose = (b) => { radios.forEach((r) => { r.setAttribute("aria-checked", String(r === b)); r.tabIndex = r === b ? 0 : -1; }); b.focus(); };
        radios.forEach((b, k) => {
          b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1;
          b.addEventListener("click", () => choose(b));
          b.addEventListener("keydown", (e) => {
            const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
            if (d) { e.preventDefault(); choose(radios[(k + d + radios.length) % radios.length]); }
          });
        });
        const target = () => Number(li.querySelector("[aria-checked=true]").dataset.id);
        armed(li.querySelector(".merge"), () => `Confirm: keep ${g.artists.find((a) => a.id === target()).name}`, async () => {
          try { await refresh(await merge(g.artists.map((a) => a.id).filter((id) => id !== target()), target())); }
          catch (err) { say(err.message, true); }
        });
        li.querySelector(".dismiss").addEventListener("click", async () => {
          try { await postJson("/api/maintenance/dismiss", { key: g.key }); li.remove(); say(html`Kept apart: ${g.artists.map((a) => a.name).join(" and ")}.`); }
          catch (err) { say(err.message, true); }
        });
      });
    }
    bindGroups();
    view.querySelector("#dupes-more")?.addEventListener("click", (e) => {
      view.querySelector("#dupes").insertAdjacentHTML("beforeend", html`${groups.slice(shown).map(groupHtml)}`.s);
      shown = groups.length;
      e.target.closest("p").remove();
      bindGroups();
    });

    // rules
    view.querySelectorAll(".rule-remove").forEach((b) => b.addEventListener("click", async () => {
      try {
        const res = await fetch(`/api/maintenance/aliases/${b.dataset.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await refresh(html`Rule removed. Merged scrobbles stay merged.`);
      } catch (err) { say(err.message, true); }
    }));
    const rName = view.querySelector("#r-name"), rTarget = view.querySelector("#r-target"), rGo = view.querySelector("#r-go");
    const rReady = () => { rGo.disabled = !(rName.value.trim() && rTarget.dataset.id); };
    rName.addEventListener("input", rReady);
    bindPicker(rTarget, rReady);
    view.querySelector("#rule-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await postJson("/api/maintenance/aliases", { name: rName.value, target_id: Number(rTarget.dataset.id) });
        cache.clear();
        await refresh(r.scrobbles_moved ? html`That spelling was already imported, so it was merged: ${Fmt.int(r.scrobbles_moved)} scrobbles moved.` : html`Rule added.`);
      } catch (err) { say(err.message, true); }
    });

    // loved tracks that don't match: link one to a suggested track, or undo a link
    // After the page redraws, the clicked button is gone: focus the button that now sits where it
    // was (the next row of the same list), else the status line that announces the result.
    const lovedDone = async (message, rowsSelector, index) => {
      cache.clear();
      await refresh(message);
      if (!location.hash.startsWith("#/cleanup")) return;
      const next = [...view.querySelectorAll(rowsSelector)].slice(index).map((li) => li.querySelector("button")).find(Boolean);
      const target = next ?? document.getElementById("cleanup-msg");
      if (!target) return;
      if (!next) target.tabIndex = -1;
      target.focus({ preventScroll: !next });
    };
    view.querySelectorAll(".loved-link").forEach((b) => b.addEventListener("click", async () => {
      const i = Number(b.dataset.i), u = loved.items[i];
      b.disabled = true;
      try {
        const r = await postJson("/api/loved/rules", { artist: u.artist, title: u.title, track_id: Number(b.dataset.track) });
        await lovedDone(html`Linked “${r.title}” to <a href="#/track/${r.track_id}">${r.track}</a>.`, ".loved-unmatched > li", i);
      } catch (err) { b.disabled = false; say(err.message, true); }
    }));
    view.querySelectorAll(".loved-undo").forEach((b, j) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await postJson(`/api/loved/rules/${Number(b.dataset.id)}`, null, "DELETE");
        await lovedDone(html`Link removed. That loved track is unmatched again.`, ".loved-rules > li", j);
      } catch (err) { b.disabled = false; say(err.message, true); }
    }));
  }

  // ---------- import ----------
  async function importView() {
    const seq = rendering;
    const [log, md, job, st] = await Promise.all([api("/api/imports", { fresh: true }), api("/api/metadata/status", { fresh: true }),
      api("/api/metadata/job", { fresh: true }), api("/api/settings", { fresh: true })]);
    const waiting = md.pending_artists + md.pending_albums + md.pending_releases;
    const steps = [
      ["Import scrobbles", log.length > 0],
      ["Connect last.fm", st.has_key],
      ["Fetch tags & covers", st.has_key && !waiting && md.artists_done > 0],
    ];
    const current = steps.findIndex(([, done]) => !done);
    paint(seq, html`
      <div class="page-head"><div><h1>Import</h1><p class="lead">Bring in your scrobbles, then fill in tags, covers and release dates.</p></div></div>
      <ol class="steps" aria-label="Setup steps" style="list-style:none;padding:0">${steps.map(([label, done], i) => html`<li class="step ${done ? "done" : i === current ? "current" : ""}">
        <span class="n">${done ? icon("check", 14) : i + 1}</span>${label}${done ? html`<span class="sr-only"> (done)</span>` : ""}</li>`)}</ol>
      <div class="grid cols-7-5">
        <div><label class="drop" id="drop">
          <input type="file" id="file" accept=".csv,text/csv" hidden>
          <span class="icon-circle">${icon("upload", 36)}</span>
          <strong>Drop your CSV here</strong>
          <span class="secondary">or <span class="choose">choose a file</span>. Re-importing a full export is safe; known scrobbles are skipped.</span></label>
          <div id="result" role="status" aria-live="polite"></div></div>
        ${card("What works", html`<dl class="kv">
          <div><dt>Format</dt><dd>artist, album, track, date without a header, or any CSV whose columns are named artist, track and date (album is optional)</dd></div>
          <div><dt>Encoding</dt><dd>UTF-8, Windows-1252 and Latin-1 are detected automatically</dd></div>
          <div><dt>Time</dt><dd>dates without a time zone are read as UTC (last.fm's are); days and hours use your configured time zone (MTC_TZ)</dd></div>
          <div><dt>From a terminal</dt><dd><code>python -m mtc import export.csv</code></dd></div>
        </dl>`)}
      </div>
      <section class="card sage-soft" id="md" style="margin-top:var(--gap)"></section>
      <div class="grid cols-2">
        ${accountCard(st)}
        ${card("Import history", log.length ? html`<ul class="rows history">${log.map((r) => html`<li><span class="date">${Fmt.date(r.started_at)}</span>
            <span class="grow">${r.label ?? r.source} <span class="meta">${r.min_ts ? html`${Fmt.month(isoDay(r.min_ts).slice(0, 7))} – ${Fmt.month(isoDay(r.max_ts).slice(0, 7))}` : ""}</span></span>
            <span class="key" title="${Fmt.int(r.rows_read)} rows read, ${Fmt.int(r.rows_skipped)} skipped${r.encoding ? `, ${r.encoding}` : ""}">+${Fmt.int(r.rows_added)}
              <span class="meta">· ${Fmt.int(Math.max(0, r.rows_read - r.rows_added))} known</span></span></li>`)}</ul>`
          : html`<p class="empty box">No imports yet.</p>`)}
      </div>`);
    renderMetadata(md, job, st);
    bindAccount(st);
    const drop = view.querySelector("#drop");
    const input = view.querySelector("#file");
    const result = view.querySelector("#result");
    async function upload(file) {
      mount(result, html`<div class="notice">Importing ${file.name} (${Fmt.compact(file.size / 1024)} kB)…</div>`);
      try {
        const res = await fetch("/api/import", { method: "POST", body: file, headers: { "content-type": "text/csv", "x-filename": encodeURIComponent(file.name) } });
        if (!res.ok) throw new Error(await res.text());
        const r = await res.json();
        cache.clear();
        mount(result, html`<div class="notice">Added <strong>${Fmt.int(r.rows_added)}</strong> new scrobbles from ${Fmt.int(r.rows_read)} rows
          (${Fmt.int(r.duplicates)} already known, ${Fmt.int(r.rows_skipped)} skipped, ${r.encoding}).
          ${r.rows_read === 0 && r.rows_skipped ? "Nothing could be read: the file needs columns for artist, track and a date it can read (album is optional)." : ""}</div>`);
        setTimeout(() => route(), 1500);
      } catch (err) {
        mount(result, html`<div class="notice err">Import failed: ${err.message}</div>`);
      }
    }
    input.addEventListener("change", () => input.files[0] && upload(input.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    });
  }

  // ---------- metadata fetch (background job on the server) ----------
  const PHASE_LABEL = { artists: "Artist tags", albums: "Album tags & covers", releases: "Release dates" };
  const duration = (s) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Fmt.dec(s / 3600)} h`);
  const active = (job) => job && (job.state === "running" || job.state === "stopping");
  const jobPercent = (job) => {
    const ph = Object.values(job.phases ?? {}).filter((p) => p.state !== "skipped");
    const total = ph.reduce((n, p) => n + Math.max(p.total, p.done), 0);
    return total ? ph.reduce((n, p) => n + p.done, 0) / total : 0;
  };

  function renderMetadata(md, job, st) {
    const box = document.getElementById("md");
    if (!box) return;
    mount(box, html`<div class="card-head"><div><h2>Tags, covers & release dates</h2>
        <p>Only artist and album names leave this machine, sent to last.fm and MusicBrainz. Your history stays here.</p></div><div class="aside" id="md-action"></div></div>
      <div id="md-job"></div>
      <div class="tiles" style="margin-top:16px">
        ${tile("Artists with tags", Fmt.int(md.artists_tagged), `${Fmt.int(md.artists_done)} of ${Fmt.int(md.artists)} looked up`, "", true)}
        ${tile("Plays with genre info", Fmt.pct(md.plays ? md.plays_covered / md.plays : 0), "", "", true)}
        ${tile("Album covers", Fmt.int(md.albums_with_cover), `${Fmt.int(md.albums_done)} of ${Fmt.int(md.albums_eligible)} albums (3+ plays) looked up`, "", true)}
        ${tile("Release dates", Fmt.int(md.albums_dated), "from MusicBrainz, or year tags", "", true)}
      </div>`);
    renderJob(md, job, st);
  }

  function renderJob(md, job, st) {
    const el = document.getElementById("md-job"), action = document.getElementById("md-action");
    if (!el) return;
    const waiting = md.pending_artists + md.pending_albums + md.pending_releases;
    const estimate = (md.pending_artists + md.pending_albums) * 1.3 + (md.pending_releases + md.pending_albums) * 1.65;
    const hasKey = st?.has_key ?? md.has_key;
    const phases = job?.phases ? html`<div class="phases">${Object.entries(job.phases).filter(([, p]) => p.state !== "skipped").map(([name, p]) => {
      const total = Math.max(p.total, p.done);
      const failed = p.counts.error ?? 0;
      return html`<div class="phase"><div class="phase-head"><b>${PHASE_LABEL[name]}</b>
          <span class="secondary num">${p.state === "queued" ? (total ? `waiting · ≈ ${Fmt.int(total)}` : "waiting") : `${Fmt.int(p.done)} / ${Fmt.int(total)}`}${failed ? ` · ${Fmt.int(failed)} failed` : ""}</span></div>
        <div class="progress" role="progressbar" aria-label="${PHASE_LABEL[name]}" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${p.done}">
          <i style="width:${(total ? (p.done / total) * 100 : p.state === "done" ? 100 : 0).toFixed(1)}%"></i></div></div>`;
    })}</div>` : "";
    if (active(job)) {
      mount(action, html`<button class="btn secondary" id="job-stop" type="button" ${job.state === "stopping" ? raw("disabled") : ""}>${job.state === "stopping" ? "Stopping…" : "Stop"}</button>`);
      mount(el, html`<div class="progress-hero"><span class="pct">${Fmt.pct(jobPercent(job)).replace(/\s?%/, "%")}</span>
          <div class="what"><b>${job.state === "stopping" ? "Stopping after the current item" : `Fetching${job.eta_s ? ` · about ${duration(job.eta_s)} left` : ""}`}</b>
          <span>${job.current ? html`Last: ${job.current}` : "Starting…"}</span></div></div>${phases}`);
    } else {
      const outcome = job?.state === "done" ? html`<div class="notice">Finished ${Fmt.date(job.finished_at)}. Views now include the new tags and covers.</div>`
        : job?.state === "stopped" ? html`<div class="notice">Stopped. Everything fetched so far is saved; fetch again to continue.</div>`
        : job?.state === "failed" ? html`<div class="notice err">Stopped with an error: ${job.error}</div>` : "";
      mount(action, html`<button class="btn primary" id="job-start" type="button" ${hasKey && waiting ? "" : raw("disabled")}>Fetch tags &amp; covers</button>`);
      const datesOnly = waiting && !md.pending_artists && !md.pending_albums; // e.g. albums retried after the title matching improved
      mount(el, html`<div class="progress-hero"><span class="pct">${!waiting ? "✓" : Fmt.int(datesOnly ? md.pending_releases : md.pending_artists + md.pending_albums)}</span>
          <div class="what"><b>${!waiting ? "Everything is up to date" : datesOnly ? `${Fmt.int(md.pending_releases)} release dates to look up` : `${Fmt.int(md.pending_artists)} artists and ${Fmt.int(md.pending_albums)} albums to look up`}</b>
          <span>${!hasKey ? "Add your last.fm API key below to start." : waiting ? `${!datesOnly && md.pending_releases ? `plus ${Fmt.int(md.pending_releases)} release dates · ` : ""}about ${duration(estimate)}, most-played first. Stopping loses nothing.${datesOnly ? " Albums MusicBrainz couldn't match before are tried again when the matching improves." : ""}`
            : "Every artist and album with 3+ plays was looked up in the last 120 days."}</span></div></div>
        ${job?.phases && job.state !== "idle" ? phases : ""}${outcome}`);
    }
    el.parentElement.querySelector("#job-stop")?.addEventListener("click", async () => renderJob(md, await postJson("/api/metadata/job/stop"), st));
    el.parentElement.querySelector("#job-start")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const started = await postJson("/api/metadata/job");
        renderJob(md, started, st);
        watchJob(started);
      } catch (err) {
        mount(el, html`<div class="notice err">Couldn't start: ${err.message}</div>`);
      }
    });
  }

  // Username and API key, stored by the server in data/settings.json (the key is never sent back).
  function accountCard(st) {
    return card("last.fm account", html`<div class="account">
      <form id="user-form" autocomplete="off"><label class="field" for="user">Username</label>
        <div class="input-row"><input id="user" type="text" spellcheck="false" autocapitalize="off" maxlength="15" value="${st.lastfm_username ?? ""}"
          placeholder="Your last.fm username" required><button class="btn ${st.lastfm_username ? "secondary" : "primary"} small" type="submit">Save</button></div>
        <p class="secondary small" id="user-msg" role="status"></p></form>
      <form id="key-form" autocomplete="off"><label class="field" for="key">API key</label>
        <div class="input-row"><input id="key" type="password" spellcheck="false" required
          placeholder="${st.has_key ? "Saved · paste a new key to replace it" : "Paste your last.fm API key"}">
          ${st.key_works ? html`<span class="pill sage works">${icon("check", 14)} works</span>` : st.has_key ? html`<button type="button" class="btn secondary small" id="key-test">Test</button>` : ""}
          <button class="btn ${st.has_key ? "secondary" : "primary"} small" type="submit">Save</button></div>
        <p class="secondary small" id="key-msg" role="status">${st.has_key ? "" : html`Needed to download your history, and for tags and covers. Get a free key at <a class="text-link" href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer">last.fm/api/account/create</a>.`}</p></form>
    </div>`, { sub: "Your username is for fetching new scrobbles, the key for that and for tags. Both stay in data/settings.json." });
  }
  function bindAccount() {
    const form = (id, send) => view.querySelector(id).addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = e.target.querySelector("[role=status]");
      try {
        await send(e.target.querySelector("input").value);
        cache.delete("/api/settings");
        msg.textContent = "Saved ✓";
      } catch (err) {
        msg.textContent = err.message;
      }
    });
    form("#user-form", (username) => postJson("/api/settings/username", { username }, "PUT", "That isn't a valid last.fm username"));
    form("#key-form", async (key) => {
      await postJson("/api/metadata/key", { key }, "PUT", "That doesn't look like a last.fm API key");
      await postJson("/api/metadata/key/verify").catch(() => null);
      route();
    });
    view.querySelector("#key-test")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      const r = await postJson("/api/metadata/key/verify").catch((err) => ({ works: false, error: err.message }));
      if (r.works) return route();
      view.querySelector("#key-msg").textContent = r.error ?? "That key didn't work.";
      e.target.disabled = false;
    });
  }

  // Polls while a fetch runs (whatever page is open): updates the header badge and the Import page.
  let jobTimer = null, polls = 0;
  async function watchJob(known) {
    clearTimeout(jobTimer);
    let job = known;
    try { job = known ?? await api("/api/metadata/job", { fresh: true }); } catch {
      // server restarting: try again while the badge still shows a running fetch
      if (!document.getElementById("job-badge").hidden) jobTimer = setTimeout(() => watchJob(), 5000);
      return;
    }
    const badge = document.getElementById("job-badge");
    badge.hidden = !active(job);
    badge.textContent = active(job) ? Fmt.pct(jobPercent(job)) : "";
    if (active(job)) {
      if (!known && document.getElementById("md-job")) {
        const refresh = ++polls % 4 === 0;
        const md = await api("/api/metadata/status", { fresh: refresh || !cache.has("/api/metadata/status") });
        const st = await api("/api/settings");
        if (refresh) renderMetadata(md, job, st); else renderJob(md, job, st);
      }
      jobTimer = setTimeout(() => watchJob(), 1500);
    } else if (!known && job.state && job.state !== "idle" && job.finished_at && job.finished_at !== watchJob.seen) {
      watchJob.seen = job.finished_at;
      cache.clear(); // tags, covers and genres changed
      if (document.getElementById("md-job")) renderMetadata(await api("/api/metadata/status", { fresh: true }), job, await api("/api/settings", { fresh: true }));
    }
  }

  // ---------- search ----------
  function setupSearch() {
    const input = document.getElementById("search");
    const box = document.getElementById("search-results");
    let timer, seq = 0;
    const close = () => { box.hidden = true; };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) return close();
      timer = setTimeout(async () => {
        const my = ++seq;
        const r = await api("/api/search?" + qs({ q }));
        if (my !== seq) return;
        const group = (title, items, href, sub) => (items.length ? html`<h3>${title}</h3>${items.map((it) => html`
          <a href="${href(it)}"><span>${it.name}${sub ? html` <span class="secondary">${sub(it)}</span>` : ""}</span><span class="secondary num">${Fmt.int(it.plays)}</span></a>`)}` : "");
        const any = r.artists.length + r.tracks.length + r.albums.length;
        mount(box, any ? html`${group("Artists", r.artists, (a) => `#/artist/${a.id}`)}${group("Tracks", r.tracks, (t) => `#/track/${t.id}`, (t) => t.artist)}${group("Albums", r.albums, (a) => `#/album/${a.id}`, (a) => a.artist)}`
          : html`<p class="empty">No matches</p>`);
        box.hidden = false;
      }, 180);
    });
    input.addEventListener("keydown", (e) => {
      const links = [...box.querySelectorAll("a")];
      const cur = links.findIndex((a) => a.classList.contains("sel"));
      if (e.key === "Escape") { close(); input.blur(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = links[(cur + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length];
        links.forEach((a) => a.classList.toggle("sel", a === next));
      }
      if (e.key === "Enter" && links.length) location.hash = (links[cur] ?? links[0]).getAttribute("href");
    });
    box.addEventListener("click", () => { close(); input.value = ""; });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) close(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
    });
  }

  // ---------- theme ----------
  function setupTheme() {
    const root = document.documentElement;
    const btn = document.getElementById("theme");
    const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
    const sync = () => btn.setAttribute("aria-pressed", String(isDark()));
    try { const saved = localStorage.getItem("mtc-theme"); if (saved) root.dataset.theme = saved; } catch { /* storage unavailable */ }
    sync();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", sync);
    btn.addEventListener("click", () => {
      root.dataset.theme = isDark() ? "light" : "dark";
      storage.set("mtc-theme", root.dataset.theme);
      sync();
    });
  }

  // ---------- router ----------
  const routes = [
    [/^\/?$/, overviewView],
    [/^\/library$/, libraryView],
    [/^\/artist\/(\d+)$/, artistView],
    [/^\/album\/(\d+)$/, albumView],
    [/^\/track\/(\d+)$/, trackView],
    [/^\/tag\/(\d+)$/, tagView],
    [/^\/connections$/, connectionsView],
    [/^\/eras$/, erasView],
    [/^\/rhythms$/, rhythmsView],
    [/^\/decades$/, decadesView],
    [/^\/insights$/, insightsView],
    [/^\/insights\/([a-z-]+)$/, insightView],
    [/^\/upcoming$/, upcomingView],
    [/^\/import$/, importView],
    [/^\/cleanup$/, cleanupView],
  ];
  let firstRoute = true, lastPath = null;
  // A selector that finds "the same control" again after a view re-renders.
  function focusKey(el) {
    if (!el || !view.contains(el)) return null;
    if (el.id) return `#${CSS.escape(el.id)}`;
    for (const a of ["data-sort", "data-kind", "data-period", "data-n", "data-k", "data-year", "data-to", "data-show", "data-prompt", "data-quiet"]) {
      if (el.hasAttribute(a)) return `[${a}="${CSS.escape(el.getAttribute(a))}"]`;
    }
    return null;
  }
  async function route() {
    const hash = location.hash.slice(1) || "/";
    const [path, query = ""] = hash.split("?");
    const params = new URLSearchParams(query);
    const my = ++routeSeq;
    const samePage = path === lastPath;
    const restore = samePage ? focusKey(document.activeElement) : null;
    const section = (p) => (p === "/" ? path === "/" : path.startsWith(p));
    document.querySelectorAll("#nav a, .util a").forEach((a) => {
      const target = a.getAttribute("href").slice(1);
      const on = section(target) || (target === "/library" && /^\/(artist|album|track|tag)\//.test(path));
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    view.classList.add("loading");
    try {
      const match = routes.find(([re]) => re.test(path));
      if (match) {
        Charts.cleanup();
        const m = match[0].exec(path);
        rendering = my;
        // has anything changed on the server (an import, a fetch, the updater) since we cached?
        try { noteVersion(await fetch("/api/version")); } catch { /* offline: keep the cache */ }
        if (my !== routeSeq) return;
        await match[1](m[1] ?? params, params);
      } else if (my === routeSeq) {
        mount(view, html`<div class="page-head"><div><h1>Not found</h1><p class="lead"><a href="#/">Back to the overview</a></p></div></div>`);
      }
    } catch (err) {
      if (err instanceof Stale) return; // a newer page took over
      if (my === routeSeq) mount(view, html`<div class="page-head"><div><h1>Something went wrong</h1><p class="lead">Couldn't load this view: ${err.message}</p></div></div>`);
      console.error(err);
    } finally {
      if (my === routeSeq) view.classList.remove("loading");
    }
    if (my !== routeSeq) return;
    if (!samePage) window.scrollTo(0, 0);
    if (samePage) {
      // same page, new state (sort, filter, page, period…): keep focus on the control that was used
      const el = restore && view.querySelector(restore);
      if (el && document.activeElement !== el) {
        el.focus({ preventScroll: true });
        try { // throws on number, date, … inputs, which have no text selection
          if (el.setSelectionRange && typeof el.value === "string") el.setSelectionRange(el.value.length, el.value.length);
        } catch { /* keep the focus, skip the caret */ }
      }
    } else if (!firstRoute) {
      // a new page: move focus to its heading so screen readers announce it
      const h1 = view.querySelector("h1");
      if (h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); }
    }
    firstRoute = false;
    lastPath = path;
  }

  setupTheme();
  setupSearch();
  window.addEventListener("hashchange", route);
  route();
  watchJob();
})();
