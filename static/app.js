/* Taste Center UI: hash router + views. Data-derived strings are escaped by the html`` tag. */
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

  // ---------- data ----------
  const cache = new Map();
  async function api(path, { fresh = false } = {}) {
    if (!fresh && cache.has(path)) return cache.get(path);
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const data = await res.json();
    cache.set(path, data);
    return data;
  }
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== "")).toString();

  // ---------- shared bits ----------
  const link = {
    artist: (id, name) => html`<a href="#/artist/${id}">${name}</a>`,
    track: (id, name) => html`<a href="#/track/${id}">${name}</a>`,
    album: (id, name) => html`<a href="#/album/${id}">${name}</a>`,
  };
  const clusterColor = (i) => (i < 8 ? `var(--series-${i + 1})` : "var(--series-other)");
  const year = (d) => (d ? String(d).slice(0, 4) : "");
  // Genre and place tags link to their tag page; years and decades are shown plain.
  const tagChips = (tags) => (tags?.length ? html`<div class="chips tags">${tags.map((t) =>
    t.kind === "year" || t.kind === "decade"
      ? html`<span class="chip muted">${t.name}</span>`
      : html`<a class="chip ${t.kind === "place" ? "place" : ""}" href="#/tag/${t.id}">${t.name}</a>`)}</div>` : "");

  const thumb = (url) => (url ? html`<img class="thumb" src="${url}" alt="" loading="lazy" referrerpolicy="no-referrer">` : html`<span class="thumb"></span>`);
  function rankList(items, { nameOf, max, extra, thumbs = false } = {}) {
    if (!items.length) return html`<p class="empty">Nothing here yet.</p>`;
    const top = max ?? Math.max(...items.map((i) => i.plays));
    return html`<ol class="rank ${thumbs ? "thumbs" : ""}">${items.map((it, i) => html`
      <li><span class="pos">${i + 1}</span>${thumbs ? thumb(it.image_url) : ""}
        <span class="name">${nameOf(it)}</span>
        <span class="num secondary">${extra ? extra(it) : Fmt.int(it.plays)}</span>
        <span class="bar"><i style="width:${((it.plays / top) * 100).toFixed(1)}%"></i></span></li>`)}</ol>`;
  }
  const artistName = (it) => link.artist(it.id, it.name);
  const trackName = (it) => html`${link.track(it.id, it.name)}<small>${it.artist}</small>`;
  const albumName = (it) => html`${link.album(it.id, it.name)}<small>${it.artist}</small>`;

  function card(title, body, sub = "", cls = "") {
    return html`<section class="card ${cls}"><div class="card-head"><div><h2>${title}</h2>${sub ? html`<p>${sub}</p>` : ""}</div></div>${body}</section>`;
  }

  // Period presets relative to the newest scrobble, so an old export still makes sense.
  function periods(ov) {
    if (!ov || ov.empty) return [{ key: "all", label: "All time" }];
    const end = new Date(ov.last_ts * 1000);
    const iso = (d) => d.toISOString().slice(0, 10);
    const back = (days) => iso(new Date(end - days * 864e5));
    const out = [
      { key: "7d", label: "Last 7 days", start: back(6), end: iso(end) },
      { key: "30d", label: "Last 30 days", start: back(29), end: iso(end) },
      { key: "90d", label: "Last 90 days", start: back(89), end: iso(end) },
      { key: "365d", label: "Last 12 months", start: back(364), end: iso(end) },
    ];
    const y0 = new Date(ov.first_ts * 1000).getUTCFullYear();
    for (let y = end.getUTCFullYear(); y >= y0; y--) out.push({ key: String(y), label: String(y), start: `${y}-01-01`, end: `${y}-12-31` });
    out.push({ key: "all", label: "All time" });
    return out;
  }
  function parseRange(key, ov) {
    const c = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/.exec(key || "");
    if (c) {
      const [start, end] = c[1] <= c[2] ? [c[1], c[2]] : [c[2], c[1]];
      return { key: `${start}..${end}`, label: start === end ? Fmt.day(start) : `${Fmt.day(start)} – ${Fmt.day(end)}`, start, end };
    }
    const m = /^(\d{4}-\d{2})$/.exec(key || "");
    if (m) {
      const [y, mo] = m[1].split("-").map(Number);
      const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
      return { key, label: Fmt.month(m[1]), start: `${m[1]}-01`, end: `${m[1]}-${last}` };
    }
    return periods(ov).find((p) => p.key === key) ?? periods(ov).at(-1);
  }

  // Period bar: quick presets, a year/month picker and a custom date range. onChange(key).
  const QUICK = [["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["365d", "1 year"], ["all", "All time"]];
  const isoDay = (ts) => new Date(ts * 1000).toISOString().slice(0, 10);
  function periodBar(period, ov) {
    const years = periods(ov).filter((p) => /^\d{4}$/.test(p.key));
    const other = !QUICK.some(([k]) => k === period.key) && !years.some((y) => y.key === period.key);
    const [lo, hi] = [isoDay(ov.first_ts), isoDay(ov.last_ts)];
    return html`<div class="toolbar period-bar">
      <div class="seg" role="group" aria-label="Period">${QUICK.map(([k, l]) => html`<button type="button" data-period="${k}" class="${k === period.key ? "on" : ""}">${l}</button>`)}</div>
      <select id="period-year" aria-label="Year or month">
        <option value="" ${years.some((y) => y.key === period.key) || other ? "" : raw("selected")}>Year…</option>
        ${years.map((y) => html`<option value="${y.key}" ${y.key === period.key ? raw("selected") : ""}>${y.label}</option>`)}
        ${other ? html`<option value="${period.key}" selected>${period.label}</option>` : ""}</select>
      <span class="date-range"><input type="date" id="period-from" min="${lo}" max="${hi}" value="${period.start ?? lo}" aria-label="From">
        <span class="muted">–</span><input type="date" id="period-to" min="${lo}" max="${hi}" value="${period.end ?? hi}" aria-label="To">
        <button type="button" id="period-apply">Show</button></span>
    </div>`;
  }
  function bindPeriodBar(onChange) {
    view.querySelectorAll("[data-period]").forEach((b) => b.addEventListener("click", () => onChange(b.dataset.period)));
    view.querySelector("#period-year").addEventListener("change", (e) => e.target.value && onChange(e.target.value));
    const from = view.querySelector("#period-from"), to = view.querySelector("#period-to");
    const apply = () => from.value && to.value && onChange(`${from.value}..${to.value}`);
    view.querySelector("#period-apply").addEventListener("click", apply);
    [from, to].forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); }));
  }
  const storage = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };

  // ---------- views ----------
  async function overviewView(params) {
    const ov = await api("/api/overview");
    if (ov.empty) return emptyState();
    const period = parseRange(params.get("period") || storage.get("mtc-period") || "30d", ov);
    const range = { start: period.start, end: period.end };
    const [sum, act, timeline, clock, ta, tt, tal, recent] = await Promise.all([
      api("/api/summary?" + qs(range)), api("/api/activity?" + qs(range)), api("/api/timeline"), api("/api/clock?" + qs(range)),
      api("/api/top/artist?" + qs({ ...range, limit: 10 })),
      api("/api/top/track?" + qs({ ...range, limit: 10 })),
      api("/api/top/album?" + qs({ ...range, limit: 10 })),
      api("/api/recent?limit=8"),
    ]);
    const prev = sum.previous;
    // Change against the previous period of the same length; text stays in text colours, the arrow carries direction.
    const delta = (key) => {
      if (!prev || !prev[key]) return "";
      const d = sum[key] / prev[key] - 1;
      if (Math.abs(d) < 0.005) return html`<span class="delta">±0 %</span>`;
      return html`<span class="delta" title="vs the previous period"><i class="${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"}</i> ${Fmt.pct(Math.abs(d))}</span>`;
    };
    const range_ = (a, b) => (a === b ? Fmt.day(a) : `${Fmt.day(a)} – ${Fmt.day(b)}`);
    const unitDay = act.unit === "day";
    mount(view, html`
      <div class="page-head"><div><h1>Your listening</h1>
        <p>${period.key === "all" ? html`${range_(sum.start, sum.end)} · ${Fmt.int(sum.calendar_days)} days of history`
          : html`${period.key.includes("..") ? "" : `${period.label} · `}${range_(sum.start, sum.end)}${prev ? html` · <span class="muted">changes vs ${range_(prev.start, prev.end)}${prev.partial ? " (partly before your history)" : ""}</span>` : ""}`}</p></div></div>
      ${periodBar(period, ov)}
      <div class="tiles">
        <div class="tile big"><div class="label">Scrobbles</div><div class="value">${Fmt.int(sum.plays)}</div>
          <div class="sub">${delta("plays")} ${Fmt.dec(sum.per_day)} per day</div></div>
        <div class="tile"><div class="label">Artists</div><div class="value">${Fmt.int(sum.artists)}</div>
          <div class="sub">${delta("artists")} top 10 = ${Fmt.pct(sum.top10_share)} of plays</div></div>
        <div class="tile"><div class="label">New artists</div><div class="value">${Fmt.int(sum.new_artists)}</div>
          <div class="sub">${delta("new_artists")} first heard in this period</div></div>
        <div class="tile"><div class="label">Tracks</div><div class="value">${Fmt.int(sum.tracks)}</div>
          <div class="sub">${delta("tracks")} ${Fmt.int(sum.albums)} albums</div></div>
        <div class="tile"><div class="label">Listening days</div><div class="value">${Fmt.int(sum.listening_days)}</div>
          <div class="sub">${Fmt.pct(sum.calendar_days ? sum.listening_days / sum.calendar_days : 0)} of ${Fmt.int(sum.calendar_days)} days · longest run ${Fmt.int(sum.longest_streak)}&nbsp;d</div></div>
      </div>
      <div class="grid">
        ${card(unitDay ? "Scrobbles per day" : "Scrobbles per month", html`<div class="chart" id="c-timeline"></div>`,
          `${period.label} · hover for the top artist · click to open ${unitDay ? "that day" : "that month"} in the library`)}
        <div class="grid cols-3">
          ${card("Top artists", rankList(ta, { nameOf: artistName }), period.label)}
          ${card("Top tracks", rankList(tt, { nameOf: trackName }), period.label)}
          ${card("Top albums", rankList(tal, { nameOf: albumName, thumbs: true }), period.label)}
        </div>
        <div class="grid cols-2">
          ${card("Listening clock", html`<div class="chart" id="c-clock"></div>`, `${period.label} · plays by weekday and local hour`)}
          ${card("Novelty", html`<div class="chart" id="c-novelty"></div>`, "All time · share of plays going to artists you first heard within the previous 12 months")}
        </div>
        <div class="grid cols-2">
          ${card("New in this period", sum.discoveries.length ? rankList(sum.discoveries.slice(0, 8), {
              nameOf: (r) => html`${link.artist(r.id, r.name)}<small>${Fmt.date(r.first_ts)}${r.gateway_name ? html` · after ${r.gateway_name}` : ""}</small>` })
            : html`<p class="empty">No new artists in this period.</p>`, "Artists first heard in this period, by plays in it")}
          ${card("Recently played", html`<div class="table-wrap"><table><tbody>${recent.map((r) => html`
            <tr><td class="ellipsis">${link.track(r.track_id, r.track)} <span class="muted small">${r.artist}</span></td>
            <td class="num muted">${Fmt.date(r.ts)}</td></tr>`)}</tbody></table></div>`, "Your newest scrobbles")}
        </div>
      </div>`);
    bindPeriodBar((key) => {
      storage.set("mtc-period", key);
      location.hash = "#/?" + qs({ period: key });
    });
    const items = act.items;
    const dayLabel = (d, i) => {
      const day = +d.key.slice(8);
      if (items.length <= 14) return `${day}`;
      if (items.length <= 45) return i % 7 === 0 ? Fmt.day(d.key).replace(/ \d{4}$/, "") : null;
      return day === 1 ? Fmt.month(d.key.slice(0, 7)).replace(/ \d{4}$/, "") : null;
    };
    const monthLabel = (d) => (items.length <= 24 ? (d.key.endsWith("-01") ? d.key.slice(0, 4) : Fmt.month(d.key).slice(0, 3))
      : d.key.endsWith("-01") ? d.key.slice(0, 4) : null);
    Charts.columns(document.getElementById("c-timeline"), items, {
      value: (d) => d.plays, xLabel: unitDay ? dayLabel : monthLabel, label: unitDay ? "Scrobbles per day" : "Scrobbles per month",
      tip: (d) => ({
        title: unitDay ? Fmt.day(d.key) : Fmt.month(d.key),
        rows: [
          { value: Fmt.int(d.plays), label: "scrobbles" },
          ...(d.top ? [{ value: d.top.name, label: `top artist · ${Fmt.int(d.top.plays)}` }] : []),
          { value: Fmt.int(d.new_artists), label: "new artists" },
        ],
      }),
      onClick: (d) => (location.hash = `#/library?${qs({ kind: "artist", period: unitDay ? `${d.key}..${d.key}` : d.key })}`),
    });
    const yearLabel = (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null);
    Charts.line(document.getElementById("c-novelty"), timeline, {
      value: (d) => d.novelty, yMax: 1, yFormat: (v) => Fmt.pct(v), xLabel: yearLabel, label: "Novelty share",
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: d.novelty == null ? "–" : Fmt.pct(d.novelty), label: "from recent discoveries" }] }),
    });
    Charts.heatmap(document.getElementById("c-clock"), clock);
  }

  function emptyState() {
    mount(view, html`<div class="page-head"><div><h1>No scrobbles yet</h1>
      <p>Export your history with lastfm-to-csv, then import the file.</p></div></div>
      <p><a class="cta" href="#/import">Go to import →</a></p>`);
  }

  async function libraryView(params) {
    const ov = await api("/api/overview");
    if (ov.empty) return emptyState();
    const kind = ["artist", "track", "album", "genre"].includes(params.get("kind")) ? params.get("kind") : "artist";
    const period = parseRange(params.get("period") || "all", ov);
    const q = params.get("q") || "";
    const sort = params.get("sort") || "plays";
    const page = Math.max(0, +params.get("page") || 0);
    const setParams = (o) => {
      const next = { kind, period: period.key, q, sort, page: 0, ...o };
      location.hash = "#/library?" + qs({ ...next, page: next.page || null, period: next.period === "all" ? null : next.period, sort: next.sort === "plays" ? null : next.sort });
    };
    const toolbar = html`${periodBar(period, ov)}<div class="toolbar">
      <div class="seg" id="kind">${["artist", "track", "album", "genre"].map((k) => html`<button data-k="${k}" class="${k === kind ? "on" : ""}">${k[0].toUpperCase() + k.slice(1)}s</button>`)}</div>
      ${kind === "artist" && period.key === "all" ? html`<input id="q" type="search" placeholder="Filter artists" value="${q}">` : ""}
    </div>`;

    let body;
    if (kind === "artist" && period.key === "all") {
      const size = 50;
      const data = await api("/api/artists?" + qs({ q, sort, limit: size, offset: page * size }));
      const th = (key, label, cls = "") => html`<th class="sortable ${cls} ${sort === key ? "sorted" : ""}" data-sort="${key}">${label}${sort === key ? " ↓" : ""}</th>`;
      body = html`<div class="card"><div class="table-wrap"><table>
        <thead><tr><th class="num">#</th>${th("name", "Artist")}${th("plays", "Plays", "num")}${th("tracks", "Tracks", "num")}${th("years", "Years", "num")}${th("oldest", "First heard")}${th("recent", "Last played")}</tr></thead>
        <tbody>${data.items.map((a, i) => html`<tr><td class="num muted">${page * size + i + 1}</td><td>${link.artist(a.id, a.name)}</td>
          <td class="num">${Fmt.int(a.plays)}</td><td class="num">${Fmt.int(a.n_tracks)}</td><td class="num">${a.n_years}</td>
          <td class="muted">${Fmt.date(a.first_ts)}</td><td class="muted">${Fmt.date(a.last_ts)}</td></tr>`)}</tbody></table></div>
        <div class="pager">${Fmt.int(data.total)} artists · page ${page + 1} / ${Math.max(1, Math.ceil(data.total / size))}
          <button id="prev" ${page === 0 ? raw("disabled") : ""}>←</button><button id="next" ${(page + 1) * size >= data.total ? raw("disabled") : ""}>→</button></div></div>`;
      mount(view, html`<div class="page-head"><div><h1>Library</h1><p>Every artist you've scrobbled</p></div></div>${toolbar}${body}`);
      view.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => setParams({ sort: th.dataset.sort })));
      view.querySelector("#prev")?.addEventListener("click", () => setParams({ page: page - 1 }));
      view.querySelector("#next")?.addEventListener("click", () => setParams({ page: page + 1 }));
      const qi = view.querySelector("#q");
      let t;
      qi?.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => setParams({ q: qi.value.trim() }), 300); });
      if (q && qi) { qi.focus(); qi.setSelectionRange(q.length, q.length); }
    } else if (kind === "genre") {
      const g = await api("/api/genres?" + qs({ start: period.start, end: period.end, limit: 60 }));
      body = g.items.length
        ? card("Top genres", rankList(g.items, {
            nameOf: (r) => html`<a href="#/tag/${r.id}">${r.name}</a><small>${r.artists.map((a) => a.name).join(", ")}</small>`,
            extra: (r) => Fmt.pct(r.share),
          }), `${period.label} · each artist's plays split across its top last.fm genre tags · covers ${Fmt.pct(g.coverage)} of plays`)
        : card("Top genres", html`<p class="empty">No genre data yet. Fetch tags from last.fm on the <a href="#/import">Import</a> page.</p>`);
      mount(view, html`<div class="page-head"><div><h1>Library</h1><p>${period.label}</p></div></div>${toolbar}${body}`);
    } else {
      const items = await api(`/api/top/${kind}?` + qs({ start: period.start, end: period.end, limit: 200 }));
      const nameOf = { artist: artistName, track: trackName, album: albumName }[kind];
      body = card(`Top ${kind}s`, rankList(items, { nameOf, thumbs: kind === "album" }), period.label);
      mount(view, html`<div class="page-head"><div><h1>Library</h1><p>${period.label}</p></div></div>${toolbar}${body}`);
    }
    view.querySelectorAll("#kind button").forEach((b) => b.addEventListener("click", () => setParams({ kind: b.dataset.k })));
    bindPeriodBar((key) => setParams({ period: key }));
  }

  async function artistView(id) {
    const [a, ov] = await Promise.all([api(`/api/artists/${id}`), api("/api/overview")]);
    const lastfm = `https://www.last.fm/music/${encodeURIComponent(a.name).replace(/%20/g, "+")}`;
    const mb = `https://musicbrainz.org/search?${qs({ query: a.name, type: "artist" })}`;
    const discovered = a.prehistory
      ? html`already in rotation when your history begins`
      : a.gateway_id
        ? html`right after ${link.artist(a.gateway_id, a.gateway_name)}`
        : html`as the first thing in a session`;
    const meta = a.meta?.status === "ok" ? a.meta : null;
    mount(view, html`
      <div class="hero ${a.image_url ? "album-hero" : ""}">
        ${a.image_url ? html`<img class="cover" src="${a.image_url}" alt="" loading="lazy" referrerpolicy="no-referrer"
          title="${a.meta?.image_url ? "" : "Cover of your most-played album"}">` : ""}
        <div><div class="kicker">Artist · #${Fmt.int(a.rank)} all time</div><h1>${a.name}</h1>${tagChips(a.tags)}</div>
        <div class="links">${meta?.listeners ? html`<span class="muted">${Fmt.compact(meta.listeners)} last.fm listeners</span>` : ""}
          <a href="#/cleanup?merge=${a.id}" title="Merge a duplicate spelling of this artist">Merge…</a>
          <a href="${meta?.url || lastfm}" target="_blank" rel="noopener noreferrer">last.fm ↗</a><a href="${meta?.mbid ? `https://musicbrainz.org/artist/${meta.mbid}` : mb}" target="_blank" rel="noopener noreferrer">MusicBrainz ↗</a></div></div>
      <div class="tiles">
        <div class="tile"><div class="label">Plays</div><div class="value">${Fmt.int(a.plays)}</div><div class="sub">${Fmt.pct(a.share)} of everything</div></div>
        <div class="tile"><div class="label">Tracks heard</div><div class="value">${Fmt.int(a.n_tracks)}</div><div class="sub">on ${Fmt.int(a.n_days)} different days</div></div>
        <div class="tile"><div class="label">First heard</div><div class="value">${Fmt.date(a.first_ts)}</div><div class="sub">${a.first_track ?? ""}</div></div>
        <div class="tile"><div class="label">Last played</div><div class="value">${Fmt.date(a.last_ts)}</div><div class="sub">${Fmt.ago(a.last_ts, ov.last_ts)}</div></div>
        <div class="tile"><div class="label">Peak month</div><div class="value">${Fmt.month(a.peak_month.month)}</div><div class="sub">${Fmt.int(a.peak_month.plays)} plays</div></div>
      </div>
      <div class="grid">
        ${card("Plays per month", html`<div class="chart" id="c-artist"></div>`, html`Discovered ${discovered}`)}
        <div class="grid cols-3">
          ${card("Top tracks", rankList(a.tracks.slice(0, 10), { nameOf: (t) => link.track(t.id, t.name) }))}
          ${card("Albums", rankList(a.albums.slice(0, 10), { thumbs: true, nameOf: (t) => html`${link.album(t.id, t.name)}<small>${t.release_date ? `${year(t.release_date)} · ` : ""}${t.n_tracks} tracks</small>` }))}
          ${card("Listened alongside", a.related.length ? html`<ol class="rank">${a.related.slice(0, 10).map((r, i) => html`
              <li><span class="pos">${i + 1}</span><span class="name">${link.artist(r.id, r.name)}</span>
              <span class="num secondary" title="shared sessions">${Fmt.int(r.shared)}</span>
              <span class="bar"><i style="width:${(Math.min(1, r.score / a.related[0].score) * 100).toFixed(1)}%"></i></span></li>`)}</ol>`
            : html`<p class="empty">Not enough shared sessions yet.</p>`, "Artists that share your listening sessions; number = sessions together")}
        </div>
        <div class="grid cols-2">
          ${card("Time of day", html`<div class="chart" id="c-hours"></div>`, "Plays by local hour")}
          ${card("Led you to", a.led_to.length ? rankList(a.led_to, { nameOf: (r) => html`${link.artist(r.id, r.name)}<small>${Fmt.date(r.first_ts)}</small>` })
            : html`<p class="empty">No discoveries started right after this artist.</p>`, "Artists you first heard right after playing this one")}
        </div>
      </div>`);
    Charts.columns(document.getElementById("c-artist"), a.monthly, {
      value: (d) => d.plays, xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null), label: "Plays per month",
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
    });
    Charts.columns(document.getElementById("c-hours"), a.hours.map((v, h) => ({ h, v })), {
      value: (d) => d.v, height: 160, xLabel: (d) => (d.h % 3 === 0 ? String(d.h).padStart(2, "0") : null), label: "Plays by hour",
      tip: (d) => ({ title: `${String(d.h).padStart(2, "0")}:00–${String(d.h).padStart(2, "0")}:59`, rows: [{ value: Fmt.int(d.v), label: "plays" }] }),
    });
  }

  async function albumView(id) {
    const al = await api(`/api/albums/${id}`);
    const meta = al.meta?.status === "ok" ? al.meta : null;
    const released = meta?.release_date
      ? html`<div class="tile"><div class="label">Released</div><div class="value">${Fmt.release(meta.release_date)}</div>
          <div class="sub">${meta.release_type ?? ""}${meta.release_date_source === "tag" ? " · from tags" : ""}</div></div>` : "";
    mount(view, html`<div class="hero album-hero">
        ${meta?.image_url ? html`<img class="cover" src="${meta.image_url}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}
        <div><div class="kicker">Album · ${link.artist(al.artist_id, al.artist)}</div><h1>${al.name}</h1>${tagChips(al.tags)}</div>
        ${meta?.url ? html`<div class="links"><a href="${meta.url}" target="_blank" rel="noopener noreferrer">last.fm ↗</a>
          ${meta.release_group_mbid ? html`<a href="https://musicbrainz.org/release-group/${meta.release_group_mbid}" target="_blank" rel="noopener noreferrer">MusicBrainz ↗</a>` : ""}</div>` : ""}</div>
      <div class="tiles"><div class="tile"><div class="label">Plays</div><div class="value">${Fmt.int(al.plays)}</div></div>
      <div class="tile"><div class="label">Tracks heard</div><div class="value">${Fmt.int(al.tracks.length)}</div>
        <div class="sub">${meta?.n_tracks ? `of ${meta.n_tracks} on the album` : ""}</div></div>${released}</div>
      ${card("Tracks", rankList(al.tracks, { nameOf: (t) => html`${link.track(t.id, t.name)}<small>last ${Fmt.date(t.last_ts)}</small>` }))}`);
  }

  async function trackView(id) {
    const t = await api(`/api/tracks/${id}`);
    mount(view, html`<div class="hero"><div><div class="kicker">Track · ${link.artist(t.artist_id, t.artist)}</div><h1>${t.name}</h1></div></div>
      <div class="tiles">
        <div class="tile"><div class="label">Plays</div><div class="value">${Fmt.int(t.plays)}</div><div class="sub">on ${Fmt.int(t.n_days)} days</div></div>
        <div class="tile"><div class="label">First played</div><div class="value">${Fmt.date(t.first_ts)}</div></div>
        <div class="tile"><div class="label">Last played</div><div class="value">${Fmt.date(t.last_ts)}</div></div>
        <div class="tile"><div class="label">Most in one day</div><div class="value">${Fmt.int(t.best_day.plays)}</div><div class="sub">${Fmt.day(t.best_day.day)}</div></div>
      </div>
      ${card("Plays per year", html`<div class="chart" id="c-track"></div>`)}`);
    Charts.columns(document.getElementById("c-track"), t.yearly, {
      value: (d) => d.plays, height: 180, xLabel: (d) => d.year, label: "Plays per year",
      tip: (d) => ({ title: d.year, rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
    });
  }

  async function tagView(id) {
    const t = await api(`/api/tags/${id}`);
    const kind = { genre: "Genre", place: "Place", year: "Year", decade: "Decade", other: "Tag" }[t.kind] ?? "Tag";
    const plays = t.monthly.reduce((s, m) => s + m.plays, 0);
    mount(view, html`
      <div class="hero"><div><div class="kicker">${kind}</div><h1>${t.name}</h1></div>
        <div class="links"><a href="https://www.last.fm/tag/${encodeURIComponent(t.name)}" target="_blank" rel="noopener noreferrer">last.fm ↗</a></div></div>
      <div class="grid">
        ${t.kind === "genre" ? card("Plays per month", html`<div class="chart" id="c-tag"></div>`,
          `About ${Fmt.int(plays)} plays: each artist's plays are split across its top genre tags`) : ""}
        <div class="grid cols-2">
          ${card("Your artists", rankList(t.artists, { nameOf: (r) => html`${link.artist(r.id, r.name)}<small>tag weight ${r.weight}</small>` }),
            "Artists last.fm tags with this, by your plays")}
          ${card("Albums", t.albums.length ? rankList(t.albums, { nameOf: (r) => html`${link.album(r.id, r.name)}<small>${r.artist}${r.release_date ? ` · ${year(r.release_date)}` : ""}</small>` })
            : html`<p class="empty">No albums with this tag yet.</p>`)}
        </div>
      </div>`);
    if (t.kind === "genre") {
      Charts.columns(document.getElementById("c-tag"), t.monthly, {
        value: (d) => d.plays, xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null), label: `${t.name} plays per month`,
        tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.plays), label: "plays" }] }),
      });
    }
  }

  async function connectionsView(params) {
    const n = [60, 120, 200].includes(+params.get("n")) ? +params.get("n") : 120;
    const g = await api(`/api/graph?n=${n}`);
    if (!g.nodes.length) return emptyState();
    const named = (c) => c.members.slice(0, 3).map((m) => m.name).join(" · ");
    mount(view, html`
      <div class="page-head"><div><h1>Connections</h1>
        <p>Your top ${g.nodes.length} artists, linked when you play them in the same listening sessions. Colours are taste clusters found from those links.</p></div>
        <div class="seg" id="size">${[60, 120, 200].map((k) => html`<button data-n="${k}" class="${k === n ? "on" : ""}">${k}</button>`)}</div></div>
      <div class="grid" style="grid-template-columns:minmax(0,3fr) minmax(220px,1fr)">
        <div class="graph-wrap" id="graph"></div>
        <div class="clusters" id="clusters">${g.clusters.filter((c) => c.size > 1).map((c) => html`
          <div class="cluster" data-c="${c.id}" tabindex="0"><div class="head"><span class="dot" style="background:${clusterColor(c.id)}"></span>
            ${c.size} artists <span class="badge">${Fmt.int(c.plays)} plays</span></div><p>${named(c)}</p></div>`)}
          ${g.clusters.some((c) => c.size === 1) ? html`<p class="muted">${g.clusters.filter((c) => c.size === 1).length} artists stand alone (no strong links).</p>` : ""}
        </div>
      </div>
      <p class="muted">Hover a node for its strongest links, drag to rearrange, scroll to zoom, click to open. Click a cluster to highlight it.</p>`);
    const graph = TasteGraph.create(document.getElementById("graph"), g, {
      color: clusterColor, onSelect: (node) => (location.hash = `#/artist/${node.id}`),
    });
    let pinned = null;
    view.querySelectorAll(".cluster").forEach((el) => {
      const toggle = () => {
        pinned = pinned === +el.dataset.c ? null : +el.dataset.c;
        view.querySelectorAll(".cluster").forEach((x) => x.classList.toggle("on", +x.dataset.c === pinned));
        graph.highlightCluster(pinned);
      };
      el.addEventListener("click", toggle);
      el.addEventListener("keydown", (e) => e.key === "Enter" && toggle());
    });
    view.querySelectorAll("#size button").forEach((b) => b.addEventListener("click", () => (location.hash = `#/connections?n=${b.dataset.n}`)));
  }

  async function erasView() {
    const [eras, timeline] = await Promise.all([api("/api/eras"), api("/api/timeline")]);
    if (!eras.length) return emptyState();
    mount(view, html`
      <div class="page-head"><div><h1>Eras</h1><p>How your taste moved, year by year</p></div></div>
      <div class="grid">
        ${card("New artists per month", html`<div class="chart" id="c-new"></div>`, "Artists heard for the first time (not counting your first 30 days of history)")}
        <div class="grid cols-2">${eras.slice().reverse().map((e) => card(e.year, html`
          <dl class="kv" style="margin-bottom:12px">
            <dt>Scrobbles</dt><dd>${Fmt.int(e.plays)} · ${Fmt.int(e.artists)} artists · ${Fmt.int(e.new_artists)} new</dd>
            ${e.signature ? html`<dt>Signature</dt><dd>${link.artist(e.signature.id, e.signature.name)} <span class="badge">${Fmt.dec(e.signature.lift)}× its usual share</span></dd>` : ""}
            ${e.best_new ? html`<dt>Big discovery</dt><dd>${link.artist(e.best_new.id, e.best_new.name)} <span class="muted">${Fmt.int(e.best_new.plays)} plays</span></dd>` : ""}
            ${e.genres?.length ? html`<dt>Genres</dt><dd class="chips">${e.genres.map((g) => html`<a class="chip" href="#/tag/${g.id}">${g.name} <span class="muted">${Fmt.pct(g.share)}</span></a>`)}</dd>` : ""}
          </dl>${rankList(e.top, { nameOf: artistName })}`, "", "era"))}</div>
      </div>`);
    Charts.columns(document.getElementById("c-new"), timeline, {
      value: (d) => d.new_artists, xLabel: (d) => (d.month.endsWith("-01") ? d.month.slice(0, 4) : null), height: 180, label: "New artists per month",
      tip: (d) => ({ title: Fmt.month(d.month), rows: [{ value: Fmt.int(d.new_artists), label: "new artists" }] }),
    });
  }

  async function insightsView() {
    const [i, ov] = await Promise.all([api("/api/insights"), api("/api/overview")]);
    if (!i.reference_ts) return emptyState();
    const ref = i.reference_ts;
    const list = (items, render) => (items.length ? html`<ul class="insight-list">${items.map(render)}</ul>` : html`<p class="empty">Nothing stands out yet.</p>`);
    mount(view, html`
      <div class="page-head"><div><h1>Insights</h1><p>Patterns in your history, measured up to your latest scrobble (${Fmt.date(ov.last_ts)})</p></div></div>
      <div class="masonry">
        ${card("Rediscover", list(i.rediscover, (r) => html`<li><div>${link.artist(r.id, r.name)}
            <div class="why">because you're into ${r.because.map((b, k) => html`${k ? ", " : ""}${link.artist(b.id, b.name)}`)}</div></div>
            <span class="muted num">${Fmt.int(r.plays)} plays · ${Fmt.ago(r.last_ts, ref)}</span></li>`),
          "Artists you used to play alongside your current favourites, untouched for a year")}
        ${card("On the rise", list(i.rising, (r) => html`<li>${link.artist(r.id, r.name)}
            <span class="num"><span>${Fmt.dec(r.growth)}×</span> <span class="muted">${Fmt.int(r.recent)} plays in 90 d</span></span></li>`),
          "Last 90 days vs. your usual pace for them over the year before")}
        ${card("Forgotten favourites", list(i.forgotten, (r) => html`<li>${link.artist(r.id, r.name)}
            <span class="muted num">${Fmt.int(r.plays)} plays · last ${Fmt.ago(r.last_ts, ref)}</span></li>`),
          "Big artists you haven't played in over a year")}
        ${card("Obsessions", list(i.obsessions, (r) => html`<li><div>${link.artist(r.id, r.name)} <span class="muted">${Fmt.month(r.month)}</span></div>
            <span class="num">${Fmt.pct(r.share)} <span class="muted">of the month</span></span></li>`),
          "Months where one artist took over")}
        ${card("Staying power", list(i.staying_power, (r) => html`<li>${link.artist(r.id, r.name)}
            <span class="muted num">${r.n_years} years · ${Fmt.int(r.plays)} plays</span></li>`),
          "Played in the most different years")}
        ${card("Gateways", list(i.gateways, (r) => html`<li>${link.artist(r.id, r.name)}
            <span class="muted num">led to ${Fmt.int(r.led_to)} artists · ${Fmt.int(r.downstream_plays)} plays</span></li>`),
          "Artists you were playing right before discovering others, weighted by how much those discoveries stuck")}
        ${card("Binges", list(i.binges, (r) => html`<li><div>${link.track(r.id, r.name)} <span class="muted">${r.artist}</span></div>
            <span class="muted num">${Fmt.int(r.plays)}× on ${Fmt.day(r.day)}</span></li>`),
          "Most plays of one track in a single day")}
        ${card("One-track artists", list(i.one_track, (r) => html`<li><div>${link.artist(r.id, r.name)} <span class="muted">${r.track}</span></div>
            <span class="muted num">${Fmt.pct(r.share)} of ${Fmt.int(r.plays)} plays</span></li>`),
          "Artists where one song is almost all you play")}
        ${card("Deep dives", list(i.deep_dives, (r) => html`<li>${link.artist(r.id, r.name)}
            <span class="muted num">${Fmt.int(r.n_tracks)} tracks · ${Fmt.int(r.plays)} plays</span></li>`),
          "Artists whose catalogue you've explored the furthest")}
      </div>`);
  }

  async function importView() {
    const [log, md, job, st] = await Promise.all([api("/api/imports", { fresh: true }), api("/api/metadata/status", { fresh: true }),
      api("/api/metadata/job", { fresh: true }), api("/api/settings", { fresh: true })]);
    mount(view, html`
      <div class="page-head"><div><h1>Import</h1>
        <p>Drop a CSV from lastfm-to-csv. Re-importing a full export is safe: scrobbles already in the database are skipped.</p></div></div>
      <div class="grid cols-2">
        <section class="card"><label class="drop" id="drop">
          <input type="file" id="file" accept=".csv,text/csv" hidden>
          <strong>Drop a CSV here</strong><br><span class="muted">or click to choose a file</span></label>
          <div id="result"></div></section>
        ${card("How it works", html`<dl class="kv">
          <dt>Format</dt><dd><code>artist, album, track, date</code> without a header (lastfm-to-csv), or any CSV with a header containing artist / track / date or uts</dd>
          <dt>Encoding</dt><dd>UTF-8 (with or without BOM), Windows-1252 or Latin-1 are detected automatically</dd>
          <dt>Time</dt><dd>last.fm dates are UTC; local hours and days use your configured zone (MTC_TZ)</dd>
          <dt>CLI</dt><dd><code>.venv/bin/python -m mtc import export.csv</code></dd>
        </dl>`)}
      </div>
      <div class="grid" style="margin-top:16px">${accountCard(st)}${card("Tags, covers & release dates", html`<div id="md"></div>`,
          "Lookups send artist and album names to last.fm and MusicBrainz, and covers load from last.fm; your listening history itself stays on this machine")}
      ${card("Import history", log.length ? html`<div class="table-wrap"><table>
        <thead><tr><th>When</th><th>Source</th><th>File</th><th class="num">Read</th><th class="num">Added</th><th class="num">Skipped</th><th>Covers</th></tr></thead>
        <tbody>${log.map((r) => html`<tr><td>${Fmt.date(r.started_at)}</td><td>${r.source}</td><td>${r.label ?? ""} <span class="muted">${r.encoding ?? ""}</span></td>
          <td class="num">${Fmt.int(r.rows_read)}</td><td class="num">${Fmt.int(r.rows_added)}</td><td class="num">${Fmt.int(r.rows_skipped)}</td>
          <td class="muted">${r.min_ts ? html`${Fmt.date(r.min_ts)} – ${Fmt.date(r.max_ts)}` : "–"}</td></tr>`)}</tbody></table></div>`
        : html`<p class="empty">No imports yet.</p>`)}</div>`);
    bindAccount();
    renderMetadata(md, job);
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
          (${Fmt.int(r.duplicates)} already known, ${Fmt.int(r.rows_skipped)} skipped, ${r.encoding}).</div>`);
        setTimeout(() => route(), 1200);
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

  // ---------- cleanup: duplicate artists and name rules ----------
  // Artist picker: a search box that resolves to an artist id (stored in data-id).
  function bindPicker(input, onPick) {
    const box = input.parentElement.querySelector(".picker-results");
    let timer, seq = 0;
    const pick = (id, name) => { input.value = name; input.dataset.id = id; box.hidden = true; onPick?.(id, name); };
    input.addEventListener("input", () => {
      delete input.dataset.id;
      onPick?.(null);
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { box.hidden = true; return; }
      timer = setTimeout(async () => {
        const my = ++seq;
        const r = await api("/api/artists?" + qs({ q, limit: 8 }));
        if (my !== seq) return;
        mount(box, r.items.length ? html`${r.items.map((a) => html`<button type="button" data-id="${a.id}" data-name="${a.name}">
          <span>${a.name}</span><span class="muted num">${Fmt.int(a.plays)}</span></button>`)}` : html`<p class="empty">No artist matches</p>`);
        box.hidden = false;
      }, 150);
    });
    box.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) pick(Number(b.dataset.id), b.dataset.name); });
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") box.hidden = true; });
    input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 150));
    return pick;
  }
  const picker = (id, placeholder) => html`<span class="picker"><input id="${id}" type="search" autocomplete="off" spellcheck="false"
    placeholder="${placeholder}" aria-label="${placeholder}"><span class="picker-results" hidden></span></span>`;

  // Two-step button for irreversible actions: the first click arms it, the second runs.
  function armed(button, confirmText, run) {
    const label = button.textContent;
    let timer;
    button.addEventListener("click", async () => {
      if (!button.classList.contains("armed")) {
        button.classList.add("armed");
        button.textContent = confirmText();
        timer = setTimeout(() => { button.classList.remove("armed"); button.textContent = label; }, 6000);
        return;
      }
      clearTimeout(timer);
      button.disabled = true;
      try { await run(); } finally { button.disabled = false; button.classList.remove("armed"); button.textContent = label; }
    });
  }

  async function cleanupView(params) {
    const [groups, rules] = await Promise.all([api("/api/maintenance/duplicates", { fresh: true }), api("/api/maintenance/aliases", { fresh: true })]);
    const preset = params.get("merge") ? await api(`/api/artists/${Number(params.get("merge"))}`).catch(() => null) : null;
    const PAGE = 25;
    let shown = PAGE;
    const groupHtml = (g) => html`<li class="dupe" data-key="${g.key}">
        <div class="dupe-names">${g.artists.map((a) => html`<label class="dupe-name">
          <input type="radio" name="t-${g.target_id}" value="${a.id}" ${a.id === g.target_id ? "checked" : ""}>
          ${link.artist(a.id, a.name)} <span class="muted num">${Fmt.int(a.plays)}</span>
          ${a.lastfm_name && a.lastfm_name !== a.name ? html`<span class="muted" title="last.fm's corrected name">→ ${a.lastfm_name}</span>` : ""}</label>`)}</div>
        <div class="dupe-actions"><button type="button" class="primary merge">Merge</button><button type="button" class="ghost dismiss">Not the same</button></div></li>`;
    mount(view, html`
      <div class="page-head"><div><h1>Cleanup</h1>
        <p>Combine artists that are spelled in more than one way. A merge moves every scrobble to the artist you keep and adds a name rule, so future imports of the other spelling land in the right place.</p></div></div>
      <div id="cleanup-msg"></div>
      <div class="grid">
        ${card("Merge artists", html`<form class="merge-form" id="merge-form" autocomplete="off">
            ${picker("m-source", "Artist to merge away (e.g. the misspelling)")}<span class="muted">into</span>${picker("m-target", "Artist to keep")}
            <button class="primary" type="submit" id="m-go" disabled>Merge</button></form>`,
          "Tracks and albums with the same title are combined. Merging can't be undone, but the name rule can be removed.")}
        ${card(`Possible duplicates${groups.length ? ` · ${Fmt.int(groups.length)}` : ""}`, groups.length
          ? html`<ol class="dupes" id="dupes">${groups.slice(0, shown).map(groupHtml)}</ol>
              ${groups.length > shown ? html`<button type="button" id="dupes-more">Show all ${Fmt.int(groups.length)}</button>` : ""}`
          : html`<p class="empty">No likely duplicates found.</p>`,
          "Names that match when accents, punctuation, 0/o, & and a leading “the” are ignored, or that last.fm corrects to the same artist. The selected one is kept.")}
        ${card("Name rules", html`${rules.length ? html`<div class="table-wrap"><table>
            <thead><tr><th>Spelling</th><th>Becomes</th><th class="num">Scrobbles merged</th><th>Added</th><th></th></tr></thead>
            <tbody>${rules.map((r) => html`<tr><td>${r.name}</td><td>${link.artist(r.artist_id, r.artist)}</td><td class="num">${Fmt.int(r.scrobbles)}</td>
              <td class="muted">${Fmt.date(r.created_at)}</td><td class="num"><button type="button" class="ghost rule-remove" data-id="${r.id}">Remove</button></td></tr>`)}</tbody></table></div>`
            : html`<p class="empty">No rules yet. Merging two artists creates one.</p>`}
            <form class="merge-form" id="rule-form" autocomplete="off">
              <input id="r-name" type="text" spellcheck="false" placeholder="A spelling not imported yet" aria-label="Spelling" required maxlength="500">
              <span class="muted">→</span>${picker("r-target", "Artist")}<button type="submit" id="r-go" disabled>Add rule</button></form>`,
          "Applied whenever scrobbles are imported. Removing a rule doesn't split artists that were already merged.")}
      </div>`);

    const say = (tpl, err = false) => { // looked up each time: actions re-render the page first
      const msg = document.getElementById("cleanup-msg");
      mount(msg, html`<div class="notice ${err ? "err" : ""}">${tpl}</div>`);
      msg.scrollIntoView({ block: "nearest" });
    };
    async function merge(sourceIds, targetId) {
      const r = await postJson("/api/maintenance/merge", { source_ids: sourceIds, target_id: targetId });
      cache.clear();
      const moved = r.merged.reduce((n, m) => n + m.scrobbles_moved, 0);
      return { r, moved, text: html`Merged ${r.merged.map((m) => m.source).join(", ")} into ${link.artist(r.target_id, r.merged[0].target)}:
        ${Fmt.int(moved)} scrobbles moved${r.merged.some((m) => m.duplicates_dropped) ? `, ${Fmt.int(r.merged.reduce((n, m) => n + m.duplicates_dropped, 0))} double scrobbles dropped` : ""}.
        Future imports of ${r.merged.length > 1 ? "those spellings" : "that spelling"} go to the same place.` };
    }

    // manual merge
    const src = view.querySelector("#m-source"), dst = view.querySelector("#m-target"), go = view.querySelector("#m-go");
    const ready = () => { go.disabled = !(src.dataset.id && dst.dataset.id && src.dataset.id !== dst.dataset.id); };
    const pickSource = bindPicker(src, ready);
    bindPicker(dst, ready);
    if (preset) { pickSource(preset.id, preset.name); dst.focus(); }
    view.querySelector("#merge-form").addEventListener("submit", (e) => e.preventDefault());
    armed(go, () => `Confirm: keep ${dst.value}`, async () => {
      try {
        const { text } = await merge([Number(src.dataset.id)], Number(dst.dataset.id));
        await cleanupView(new URLSearchParams());
        say(text);
      } catch (err) { say(err.message, true); }
    });

    // suggestions
    function bindGroups() {
      view.querySelectorAll(".dupe:not([data-bound])").forEach((li) => {
        li.dataset.bound = "1";
        const g = groups.find((x) => x.key === li.dataset.key);
        const target = () => Number(li.querySelector("input:checked").value);
        armed(li.querySelector(".merge"), () => `Keep ${g.artists.find((a) => a.id === target()).name}?`, async () => {
          try {
            const { text } = await merge(g.artists.map((a) => a.id).filter((id) => id !== target()), target());
            await cleanupView(new URLSearchParams()); // refreshes the counts and the rules list
            say(text);
          } catch (err) { say(err.message, true); }
        });
        li.querySelector(".dismiss").addEventListener("click", async () => {
          try { await postJson("/api/maintenance/dismiss", { key: g.key }); li.remove(); } catch (err) { say(err.message, true); }
        });
      });
    }
    bindGroups();
    view.querySelector("#dupes-more")?.addEventListener("click", (e) => {
      view.querySelector("#dupes").insertAdjacentHTML("beforeend", html`${groups.slice(shown).map(groupHtml)}`.s);
      shown = groups.length;
      e.target.remove();
      bindGroups();
    });

    // rules
    view.querySelectorAll(".rule-remove").forEach((b) => armed(b, () => "Remove?", async () => {
      try {
        const res = await fetch(`/api/maintenance/aliases/${b.dataset.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await cleanupView(new URLSearchParams());
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
        await cleanupView(new URLSearchParams());
        say(r.scrobbles_moved ? html`That spelling was already imported, so it was merged: ${Fmt.int(r.scrobbles_moved)} scrobbles moved.` : html`Rule added.`);
      } catch (err) { say(err.message, true); }
    });
  }

  // ---------- metadata fetch (background job on the server) ----------
  const PHASE_LABEL = { artists: "Artist tags", albums: "Album tags & covers", releases: "Release dates (MusicBrainz)" };
  const duration = (s) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Fmt.dec(s / 3600, 1)} h`);
  const active = (job) => job && (job.state === "running" || job.state === "stopping");
  const jobPercent = (job) => {
    const ph = Object.values(job.phases ?? {}).filter((p) => p.state !== "skipped");
    const total = ph.reduce((n, p) => n + Math.max(p.total, p.done), 0);
    return total ? ph.reduce((n, p) => n + p.done, 0) / total : 0;
  };
  async function postJson(path, body, method = "POST", invalid = "Invalid input") {
    const res = await fetch(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.status === 422 ? invalid : `HTTP ${res.status}`);
    return data;
  }

  function renderMetadata(md, job) {
    const box = document.getElementById("md");
    if (!box) return;
    const tiles = html`<div class="tiles" id="md-tiles">
        <div class="tile"><div class="label">Artists with tags</div><div class="value">${Fmt.int(md.artists_tagged)}</div>
          <div class="sub">${Fmt.int(md.artists_done)} of ${Fmt.int(md.artists)} looked up</div></div>
        <div class="tile"><div class="label">Plays with genre info</div><div class="value">${Fmt.pct(md.plays ? md.plays_covered / md.plays : 0)}</div></div>
        <div class="tile"><div class="label">Album covers</div><div class="value">${Fmt.int(md.albums_with_cover)}</div>
          <div class="sub">${Fmt.int(md.albums_done)} of ${Fmt.int(md.albums_eligible)} albums (3+ plays) looked up</div></div>
        <div class="tile"><div class="label">Albums with release date</div><div class="value">${Fmt.int(md.albums_dated)}</div></div>
      </div>`;
    mount(box, html`${tiles}
      <div id="md-job"></div>
      <dl class="kv">
        <dt>Sources</dt><dd>Tags, listener counts and album covers from last.fm; release dates from MusicBrainz (year tags as a fallback). Artist pages use the cover of your most-played album, because last.fm no longer serves artist photos.</dd>
        <dt>Pace</dt><dd>Most-played first, about 1 s per artist or album and 1,5 s per release date. Stopping or closing the app loses nothing; the next fetch continues where it left off and also picks up new imports and anything older than 120 days.</dd>
        ${md.last_fetch ? html`<dt>Last fetch</dt><dd>${Fmt.date(md.last_fetch)}</dd>` : ""}
        <dt>CLI</dt><dd><code>.venv/bin/python -m mtc enrich</code> does the same from a terminal</dd>
      </dl>`);
    renderJob(md, job);
  }

  // Username and API key, stored by the server in data/settings.json (the key is never sent back).
  function accountCard(st) {
    return card("last.fm account", html`<dl class="kv account">
      <dt>Username</dt><dd><form class="key-form" id="user-form" autocomplete="off">
        <input id="user" type="text" spellcheck="false" autocapitalize="off" maxlength="15" value="${st.lastfm_username ?? ""}"
          placeholder="Your last.fm username" aria-label="last.fm username" required>
        <button class="${st.lastfm_username ? "" : "primary"}" type="submit">Save</button><span class="muted" id="user-msg"></span></form></dd>
      <dt>API key</dt><dd><form class="key-form" id="key-form" autocomplete="off">
        <input id="key" type="password" spellcheck="false" aria-label="last.fm API key" required
          placeholder="${st.has_key ? "Saved · paste a new key to replace it" : "Paste your last.fm API key"}">
        <button class="${st.has_key ? "" : "primary"}" type="submit">Save key</button><span class="muted" id="key-msg"></span></form>
        <p class="muted hint">${st.has_key ? "" : "Needed for tags and covers. "}Get a free key at
          <a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer">last.fm/api/account/create ↗</a> (any app name works).</p></dd>
    </dl>`, "The username is for fetching your newest scrobbles, the API key for that and for tags. Both are stored only in data/settings.json on this machine.");
  }
  function bindAccount() {
    const form = (id, send) => view.querySelector(id).addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = e.target.querySelector(".muted");
      try {
        await send(e.target.querySelector("input").value);
        cache.delete("/api/settings");
        msg.textContent = "Saved ✓";
        e.target.querySelector("button").classList.remove("primary");
      } catch (err) {
        msg.textContent = err.message;
      }
    });
    form("#user-form", (username) => postJson("/api/settings/username", { username }, "PUT", "That isn't a valid last.fm username"));
    form("#key-form", async (key) => {
      await postJson("/api/metadata/key", { key }, "PUT", "That doesn't look like a last.fm API key");
      route(); // enables the fetch button
    });
  }

  function renderJob(md, job) {
    const el = document.getElementById("md-job");
    if (!el) return;
    const waiting = md.pending_artists + md.pending_albums + md.pending_releases;
    const estimate = (md.pending_artists + md.pending_albums) * 1.3 + (md.pending_releases + md.pending_albums) * 1.65;
    const phases = job?.phases ? html`<div class="phases">${Object.entries(job.phases).filter(([, p]) => p.state !== "skipped" && (active(job) || p.state !== "queued")).map(([name, p]) => {
      const total = Math.max(p.total, p.done);
      const failed = (p.counts.error ?? 0);
      return html`<div class="phase ${p.state}">
        <div class="phase-head"><span>${PHASE_LABEL[name]}</span>
          <span class="muted num">${p.state === "queued" ? (total ? `≈ ${Fmt.int(total)} waiting` : "waiting") : `${Fmt.int(p.done)} / ${Fmt.int(total)}`}${p.counts.not_found ? ` · ${Fmt.int(p.counts.not_found)} not found` : ""}${failed ? ` · ${Fmt.int(failed)} failed` : ""}</span></div>
        <div class="progress"><i style="width:${(total ? (p.done / total) * 100 : p.state === "done" ? 100 : 0).toFixed(1)}%"></i></div></div>`;
    })}</div>` : "";
    let head;
    if (active(job)) {
      head = html`<div class="job-bar"><div><strong>${job.state === "stopping" ? "Stopping after the current item…" : `Fetching · ${Fmt.pct(jobPercent(job))}`}</strong>
          <div class="muted">${job.current ? html`Last: ${job.current}` : "Starting…"}${job.eta_s ? ` · about ${duration(job.eta_s)} left` : ""}</div></div>
        <button id="job-stop" type="button" ${job.state === "stopping" ? "disabled" : ""}>Stop</button></div>`;
    } else {
      const outcome = job?.state === "done" ? html`<div class="notice">Finished ${Fmt.date(job.finished_at)}. Views now include the new tags and covers.</div>`
        : job?.state === "stopped" ? html`<div class="notice">Stopped. Everything fetched so far is saved; fetch again to continue.</div>`
        : job?.state === "failed" ? html`<div class="notice err">Stopped with an error: ${job.error}</div>` : "";
      head = html`${outcome}<div class="job-bar"><div>
          <strong>${waiting ? `${Fmt.int(md.pending_artists)} artists and ${Fmt.int(md.pending_albums)} albums to look up` : "Everything is up to date"}</strong>
          <div class="muted">${waiting && !md.has_key ? "Add your last.fm API key above to start" : waiting ? `${md.pending_releases ? `plus ${Fmt.int(md.pending_releases)} release dates · ` : ""}about ${duration(estimate)}, most-played first`
            : "All artists and albums with 3+ plays were looked up in the last 120 days"}</div></div>
        <button class="primary" id="job-start" type="button" ${md.has_key && waiting ? "" : "disabled"}>Fetch tags &amp; covers</button></div>`;
    }
    mount(el, html`${head}${phases}${active(job) && job.log.length ? html`<ol class="job-log">${job.log.slice().reverse().map((l) =>
      html`<li><span class="status ${l.status}">${l.status === "ok" ? "✓" : l.status === "not_found" || l.status === "merged" ? "–" : "!"}</span>${l.item}</li>`)}</ol>` : ""}`);
    el.querySelector("#job-stop")?.addEventListener("click", async () => { renderJob(md, await postJson("/api/metadata/job/stop")); });
    el.querySelector("#job-start")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const started = await postJson("/api/metadata/job");
        renderJob(md, started);
        watchJob(started);
      } catch (err) {
        mount(el, html`<div class="notice err">Couldn't start: ${err.message}</div>`);
      }
    });
  }

  // Polls while a fetch runs (whatever page is open): updates the nav badge and the Import page.
  let jobTimer = null, polls = 0;
  async function watchJob(known) {
    clearTimeout(jobTimer);
    let job = known;
    try { job = known ?? await api("/api/metadata/job", { fresh: true }); } catch { return; }
    const badge = document.getElementById("job-badge");
    badge.hidden = !active(job);
    badge.textContent = active(job) ? Fmt.pct(jobPercent(job)) : "";
    badge.title = active(job) ? "Fetching tags and covers" : "";
    if (active(job)) {
      if (!known && document.getElementById("md-job")) {
        const refreshTiles = ++polls % 4 === 0;
        const md = await api("/api/metadata/status", { fresh: refreshTiles || !cache.has("/api/metadata/status") });
        if (refreshTiles) renderMetadata(md, job); else renderJob(md, job);
      }
      jobTimer = setTimeout(() => watchJob(), 1500);
    } else if (!known && job.state && job.state !== "idle" && job.finished_at && job.finished_at !== watchJob.seen) {
      watchJob.seen = job.finished_at;
      cache.clear(); // tags, covers and genres changed
      if (document.getElementById("md-job")) renderMetadata(await api("/api/metadata/status", { fresh: true }), job);
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
          <a href="${href(it)}"><span>${it.name}${sub ? html` <span class="muted">${sub(it)}</span>` : ""}</span><span class="muted num">${Fmt.int(it.plays)}</span></a>`)}` : "");
        const any = r.artists.length + r.tracks.length + r.albums.length;
        mount(box, any ? html`${group("Artists", r.artists, (a) => `#/artist/${a.id}`)}${group("Tracks", r.tracks, (t) => `#/track/${t.id}`, (t) => t.artist)}${group("Albums", r.albums, (a) => `#/album/${a.id}`, (a) => a.artist)}`
          : html`<p class="empty" style="padding:8px">No matches</p>`);
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
      if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); input.focus(); }
    });
  }

  // ---------- theme ----------
  function setupTheme() {
    const root = document.documentElement;
    try { const saved = localStorage.getItem("mtc-theme"); if (saved) root.dataset.theme = saved; } catch { /* storage unavailable */ }
    document.getElementById("theme").addEventListener("click", () => {
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("mtc-theme", root.dataset.theme); } catch { /* ignore */ }
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
    [/^\/insights$/, insightsView],
    [/^\/import$/, importView],
    [/^\/cleanup$/, cleanupView],
  ];
  let routeSeq = 0;
  async function route() {
    const hash = location.hash.slice(1) || "/";
    const [path, query = ""] = hash.split("?");
    const params = new URLSearchParams(query);
    const my = ++routeSeq;
    document.querySelectorAll("#nav a").forEach((a) => {
      const target = a.getAttribute("href").slice(1);
      a.classList.toggle("active", target === "/" ? path === "/" : path.startsWith(target));
    });
    view.classList.add("loading");
    try {
      for (const [re, fn] of routes) {
        const m = re.exec(path);
        if (m) {
          Charts.cleanup();
          await fn(m[1] ?? params, params);
          break;
        }
      }
      if (my === routeSeq && !routes.some(([re]) => re.test(path))) mount(view, html`<h1>Not found</h1>`);
    } catch (err) {
      if (my === routeSeq) mount(view, html`<div class="notice err">Couldn't load this view: ${err.message}</div>`);
      console.error(err);
    } finally {
      if (my === routeSeq) view.classList.remove("loading");
    }
    if (!/^\/library/.test(path)) window.scrollTo(0, 0);
  }

  setupTheme();
  setupSearch();
  watchJob();
  window.addEventListener("hashchange", route);
  route();
})();
