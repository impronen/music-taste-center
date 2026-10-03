/* Small SVG chart kit: column, line and heatmap charts with hover tooltips.
   Every label coming from data goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const observers = new Set();

  // ---------- formatting (Finnish number conventions, English dates) ----------
  const intFmt = new Intl.NumberFormat("fi-FI", { maximumFractionDigits: 0 });
  const dec1 = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const pctFmt = new Intl.NumberFormat("fi-FI", { style: "percent", maximumFractionDigits: 1 });
  const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const monthFmt = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
  const Fmt = {
    int: (n) => intFmt.format(n ?? 0),
    dec: (n) => dec1.format(n ?? 0),
    pct: (n) => pctFmt.format(n ?? 0),
    date: (ts) => (ts ? dateFmt.format(new Date(ts * 1000)) : "–"),
    day: (iso) => (iso ? dateFmt.format(new Date(iso + "T12:00:00")) : "–"),
    month: (ym) => monthFmt.format(new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7) - 1, 1))),
    // release dates come as YYYY, YYYY-MM or YYYY-MM-DD
    release: (d) => (!d ? "–" : d.length === 4 ? d : d.length === 7 ? Fmt.month(d) : Fmt.day(d)),
    // explicit thresholds instead of locale compact notation
    compact: (n) => {
      const a = Math.abs(n);
      if (a >= 1e6) return dec1.format(n / 1e6) + " M";
      if (a >= 10000) return dec1.format(n / 1e3).replace(/,0$/, "") + " k";
      return intFmt.format(n);
    },
    ago: (ts, ref) => {
      const days = Math.round(((ref ?? Date.now() / 1000) - ts) / 86400);
      if (days < 1) return "today";
      if (days < 60) return days + " days ago";
      if (days < 730) return Math.round(days / 30.4) + " months ago";
      return dec1.format(days / 365.25).replace(/,0$/, "") + " years ago";
    },
  };

  // ---------- tooltip ----------
  const tip = () => document.getElementById("tooltip");
  function showTip(evt, rows, title) {
    const el = tip();
    el.replaceChildren();
    if (title) {
      const t = document.createElement("div");
      t.className = "tl";
      t.textContent = title;
      el.append(t);
    }
    for (const r of rows) {
      const row = document.createElement("div");
      if (r.color) {
        const k = document.createElement("span");
        k.className = "key";
        k.style.background = r.color;
        row.append(k);
      }
      const v = document.createElement("span");
      v.className = "tv";
      v.textContent = r.value;
      row.append(v);
      if (r.label) {
        const l = document.createElement("span");
        l.className = "tl";
        l.textContent = " " + r.label;
        row.append(l);
      }
      el.append(row);
    }
    el.hidden = false;
    moveTip(evt);
  }
  function moveTip(evt) {
    const el = tip();
    const pad = 14;
    const { innerWidth: w, innerHeight: h } = window;
    const r = el.getBoundingClientRect();
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;
    if (x + r.width > w - 8) x = evt.clientX - r.width - pad;
    if (y + r.height > h - 8) y = evt.clientY - r.height - pad;
    el.style.left = Math.max(8, x) + "px";
    el.style.top = Math.max(8, y) + "px";
  }
  function hideTip() {
    tip().hidden = true;
  }

  // ---------- helpers ----------
  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.append(e);
    return e;
  }
  function text(parent, x, y, str, attrs = {}) {
    const t = svgEl("text", { x, y, class: "tick", ...attrs }, parent);
    t.textContent = str;
    return t;
  }
  function niceTicks(max, count = 4, minStep = 0) {
    if (!(max > 0)) return [0, 1];
    const raw = Math.max(max / count, minStep);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    if (minStep >= 1) step = Math.ceil(step);
    const ticks = [];
    for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }
  // Bar with a 4px rounded data-end and a square baseline.
  function barPath(x, y, w, h) {
    const r = Math.min(4, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }
  function responsive(el, draw) {
    let last = 0;
    const run = () => {
      const w = el.clientWidth;
      if (w && w !== last) {
        last = w;
        draw(w);
      }
    };
    const ro = new ResizeObserver(() => requestAnimationFrame(run));
    ro.observe(el);
    observers.add(ro);
    run();
  }
  function cleanup() {
    observers.forEach((o) => o.disconnect());
    observers.clear();
    hideTip();
  }
  function yAxis(svg, ticks, y, left, right, fmt) {
    for (const t of ticks) {
      svgEl("line", { x1: left, x2: right, y1: y(t), y2: y(t), class: t === 0 ? "baseline" : "gridline" }, svg);
      text(svg, left - 8, y(t) + 4, fmt(t), { "text-anchor": "end" });
    }
  }
  function leftMargin(ticks, fmt) {
    return Math.max(...ticks.map((t) => fmt(t).length)) * 6.6 + 14;
  }

  // ---------- column chart ----------
  /* data: [{...}], opts: { value(d), xLabel(d,i) -> string|null, tip(d) -> {title, rows}, height, onClick(d) } */
  function columns(el, data, opts) {
    const height = opts.height ?? 220;
    const fmt = opts.yFormat ?? Fmt.compact;
    responsive(el, (width) => {
      el.replaceChildren();
      const max = Math.max(0, ...data.map(opts.value));
      const ticks = niceTicks(max, 4, opts.minStep ?? 1);
      const top = 8, bottom = 24, right = 4;
      const left = leftMargin(ticks, fmt);
      const innerW = width - left - right, innerH = height - top - bottom;
      const yMax = ticks[ticks.length - 1];
      const y = (v) => top + innerH - (v / yMax) * innerH;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.label ?? "" }, el);
      yAxis(svg, ticks, y, left, width - right, fmt);
      const band = innerW / Math.max(1, data.length);
      const bw = Math.max(1, Math.min(24, band - 2));
      let lastLabelX = -Infinity;
      data.forEach((d, i) => {
        const v = opts.value(d);
        const x0 = left + i * band;
        const bx = x0 + (band - bw) / 2;
        const h = Math.max(0, y(0) - y(v));
        const bar = h > 0 ? svgEl("path", { d: barPath(bx, y(v), bw, h), class: "bar" }, svg) : null;
        if (bar && opts.color) bar.style.fill = opts.color(d);
        const hit = svgEl("rect", { x: x0, y: top, width: band, height: innerH, class: "hit" }, svg);
        hit.addEventListener("pointerenter", (e) => {
          bar?.classList.add("hl");
          const t = opts.tip(d);
          showTip(e, t.rows, t.title);
        });
        hit.addEventListener("pointermove", moveTip);
        hit.addEventListener("pointerleave", () => {
          bar?.classList.remove("hl");
          hideTip();
        });
        if (opts.onClick) {
          hit.style.cursor = "pointer";
          hit.addEventListener("click", () => opts.onClick(d));
        }
        const label = opts.xLabel?.(d, i);
        const cx = x0 + band / 2;
        if (label != null && cx - lastLabelX >= label.length * 6.6 + 10) {
          text(svg, cx, height - 6, label, { "text-anchor": "middle" });
          lastLabelX = cx;
        }
      });
    });
  }

  // ---------- line chart (single series, crosshair) ----------
  function line(el, data, opts) {
    const height = opts.height ?? 200;
    const fmt = opts.yFormat ?? Fmt.compact;
    responsive(el, (width) => {
      el.replaceChildren();
      const vals = data.map(opts.value);
      const max = opts.yMax ?? Math.max(0, ...vals.filter((v) => v != null));
      const ticks = niceTicks(max);
      const top = 8, bottom = 24, right = 8;
      const left = leftMargin(ticks, fmt);
      const innerW = width - left - right, innerH = height - top - bottom;
      const yMax = ticks[ticks.length - 1];
      const x = (i) => left + (data.length <= 1 ? innerW / 2 : (i / (data.length - 1)) * innerW);
      const y = (v) => top + innerH - (v / yMax) * innerH;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.label ?? "" }, el);
      yAxis(svg, ticks, y, left, width - right, fmt);

      // segments break on null
      let d = "", area = "", seg = [];
      const flush = () => {
        if (!seg.length) return;
        d += seg.map(([i, v], k) => `${k ? "L" : "M"}${x(i)},${y(v)}`).join("");
        area += `M${x(seg[0][0])},${y(0)}` + seg.map(([i, v]) => `L${x(i)},${y(v)}`).join("") + `L${x(seg[seg.length - 1][0])},${y(0)}Z`;
        seg = [];
      };
      vals.forEach((v, i) => (v == null ? flush() : seg.push([i, v])));
      flush();
      svgEl("path", { d: area, class: "area" }, svg);
      svgEl("path", { d, class: "line" }, svg);

      let lastLabelX = -Infinity;
      data.forEach((row, i) => {
        const label = opts.xLabel?.(row, i);
        if (label != null && x(i) - lastLabelX >= label.length * 6.6 + 10) {
          text(svg, x(i), height - 6, label, { "text-anchor": "middle" });
          lastLabelX = x(i);
        }
      });

      const cross = svgEl("line", { y1: top, y2: top + innerH, class: "cross", visibility: "hidden" }, svg);
      const marker = svgEl("circle", { r: 4, class: "marker", visibility: "hidden" }, svg);
      const overlay = svgEl("rect", { x: left, y: top, width: innerW, height: innerH, class: "hit" }, svg);
      overlay.addEventListener("pointermove", (e) => {
        const box = svg.getBoundingClientRect();
        const px = ((e.clientX - box.left) / box.width) * width;
        const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - left) / innerW) * (data.length - 1))));
        cross.setAttribute("x1", x(i));
        cross.setAttribute("x2", x(i));
        cross.setAttribute("visibility", "visible");
        if (vals[i] != null) {
          marker.setAttribute("cx", x(i));
          marker.setAttribute("cy", y(vals[i]));
          marker.setAttribute("visibility", "visible");
        } else marker.setAttribute("visibility", "hidden");
        const t = opts.tip(data[i]);
        showTip(e, t.rows, t.title);
      });
      overlay.addEventListener("pointerleave", () => {
        cross.setAttribute("visibility", "hidden");
        marker.setAttribute("visibility", "hidden");
        hideTip();
      });
    });
  }

  // ---------- heatmap (7 x 24 listening clock) ----------
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  function heatmap(el, grid, opts = {}) {
    const max = Math.max(1, ...grid.flat());
    const total = grid.flat().reduce((a, b) => a + b, 0) || 1;
    const bin = (v) => (v === 0 ? 0 : Math.min(7, Math.ceil((v / max) * 7)));
    responsive(el, (width) => {
      el.replaceChildren();
      const left = 34, top = 4, gap = 2, bottom = 20;
      const cw = (width - left) / 24;
      const ch = Math.min(26, Math.max(14, cw * 0.8));
      const height = top + 7 * ch + bottom;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.label ?? "Listening clock" }, el);
      grid.forEach((row, wd) => {
        text(svg, 0, top + wd * ch + ch / 2 + 4, DAYS[wd]);
        row.forEach((v, h) => {
          const b = bin(v);
          const cell = svgEl("rect", {
            x: left + h * cw + gap / 2, y: top + wd * ch + gap / 2,
            width: Math.max(1, cw - gap), height: ch - gap, rx: 2, class: "cell",
          }, svg);
          cell.style.fill = b ? `var(--seq-${b})` : "var(--surface-2)";
          cell.addEventListener("pointerenter", (e) =>
            showTip(e, [{ value: Fmt.int(v), label: `plays · ${Fmt.pct(v / total)}` }], `${DAYS[wd]} ${String(h).padStart(2, "0")}:00–${String(h).padStart(2, "0")}:59`));
          cell.addEventListener("pointermove", moveTip);
          cell.addEventListener("pointerleave", hideTip);
        });
      });
      for (let h = 0; h < 24; h += cw < 22 ? 3 : 2) {
        text(svg, left + h * cw + cw / 2, height - 4, String(h).padStart(2, "0"), { "text-anchor": "middle" });
      }
    });
    const legend = document.createElement("div");
    legend.className = "legend-seq";
    legend.append("fewer");
    for (let b = 1; b <= 7; b++) {
      const i = document.createElement("i");
      i.style.background = `var(--seq-${b})`;
      legend.append(i);
    }
    legend.append("more plays");
    el.after(legend);
  }

  // ---------- lift matrix (rows × columns, diverging around "as usual") ----------
  /* data: { cols: [label], rows: [{ name, href?, cells: [{ lift|null, plays, expected, up, years }] }] }
     opts: { label, yearsLabel ("years" / "winters"...) } — lift 1 is gray, teal above, orange below. */
  const liftFmt = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function liftStep(lift) {
    const a = Math.abs(Math.log2(lift));
    const step = a < 0.15 ? 0 : a < 0.38 ? 1 : a < 0.7 ? 2 : 3; // within ±11 % reads as "as usual"; then ±30 %, ±62 %
    return step === 0 ? "var(--div-mid)" : `var(--div-${lift > 1 ? "pos" : "neg"}-${step})`;
  }
  function matrix(el, data, opts = {}) {
    const { cols, rows } = data;
    responsive(el, (width) => {
      el.replaceChildren();
      const longest = Math.max(4, ...rows.map((r) => r.name.length));
      const left = Math.min(150, Math.max(60, longest * 6.8 + 12));
      const maxChars = Math.floor((left - 12) / 6.8);
      const top = 2, bottom = 22, gap = 2;
      const cw = (width - left) / cols.length;
      const ch = Math.min(28, Math.max(18, cw * 0.6));
      const height = top + rows.length * ch + bottom;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.label ?? "" }, el);
      rows.forEach((row, ri) => {
        const y = top + ri * ch;
        const name = row.name.length > maxChars ? row.name.slice(0, maxChars - 1) + "…" : row.name;
        const holder = row.href ? svgEl("a", { href: row.href }, svg) : svg;
        const t = text(holder, left - 10, y + ch / 2 + 4, name, { "text-anchor": "end", class: "tick row-label" });
        if (name !== row.name) svgEl("title", {}, t).textContent = row.name;
        row.cells.forEach((c, ci) => {
          const cell = svgEl("rect", {
            x: left + ci * cw + gap / 2, y: y + gap / 2, width: Math.max(1, cw - gap), height: ch - gap, rx: 3, class: "cell",
          }, svg);
          if (c.lift == null) {
            cell.classList.add("nodata");
          } else {
            cell.style.fill = liftStep(c.lift);
          }
          cell.addEventListener("pointerenter", (e) => {
            const rowsOut = c.lift == null
              ? [{ value: "Not enough plays", label: "" }]
              : [
                  { value: liftFmt.format(c.lift) + "×", label: "your usual share" },
                  { value: Fmt.int(c.plays), label: `plays · ${Fmt.int(c.expected)} expected` },
                  ...(c.years ? [{ value: `${c.up} of ${c.years}`, label: `${opts.yearsLabel ?? "years"} above usual` }] : []),
                ];
            showTip(e, rowsOut, `${row.name} · ${cols[ci]}`);
          });
          cell.addEventListener("pointermove", moveTip);
          cell.addEventListener("pointerleave", hideTip);
        });
      });
      const every = cw < 30 ? 2 : 1;
      cols.forEach((c, ci) => {
        if (ci % every === 0) text(svg, left + ci * cw + cw / 2, height - 6, c, { "text-anchor": "middle" });
      });
    });
    const legend = document.createElement("div");
    legend.className = "legend-seq";
    const sw = (v) => { const i = document.createElement("i"); i.style.background = v; legend.append(i); };
    legend.append("less than usual");
    ["var(--div-neg-3)", "var(--div-neg-2)", "var(--div-neg-1)", "var(--div-mid)", "var(--div-pos-1)", "var(--div-pos-2)", "var(--div-pos-3)"].forEach(sw);
    legend.append("more than usual");
    const none = document.createElement("i");
    none.className = "nodata";
    legend.append(none, "too few plays");
    el.after(legend);
  }

  // ---------- 100 % stacked columns ----------
  /* data: [{...}], opts: { series: [{name}], shares(d) -> [0..1], other(d), xLabel(d), title(d), label }
     Colours follow the series order (--series-1..8), the rest is "Other". */
  function stacked(el, data, opts) {
    const height = opts.height ?? 240;
    const colors = opts.series.map((_, i) => (i < 8 ? `var(--series-${i + 1})` : "var(--series-other)"));
    responsive(el, (width) => {
      el.replaceChildren();
      const top = 8, bottom = 24, right = 4, left = 42;
      const innerW = width - left - right, innerH = height - top - bottom;
      const y = (v) => top + innerH - v * innerH;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": opts.label ?? "" }, el);
      yAxis(svg, [0, 0.25, 0.5, 0.75, 1], y, left, width - right, (v) => Fmt.pct(v));
      const band = innerW / Math.max(1, data.length);
      const bw = Math.max(4, Math.min(56, band - 8));
      data.forEach((d) => {
        const x = left + data.indexOf(d) * band + (band - bw) / 2;
        const parts = [...opts.shares(d).map((v, i) => ({ v, color: colors[i], name: opts.series[i].name })),
          { v: opts.other(d), color: "var(--series-other)", name: "Other" }].filter((p) => p.v > 0);
        let acc = 0;
        parts.forEach((p, i) => {
          const y0 = y(acc), y1 = y(acc + p.v);
          acc += p.v;
          const isTop = i === parts.length - 1;
          const h = Math.max(0, y0 - y1 - (isTop ? 0 : 2)); // 2px surface gap between segments
          const seg = svgEl("path", { d: isTop ? barPath(x, y0 - h, bw, h) : `M${x},${y0}V${y0 - h}H${x + bw}V${y0}Z`, class: "seg" }, svg);
          seg.style.fill = p.color;
          seg.addEventListener("pointerenter", (e) => showTip(e, parts.slice().reverse().map((q) =>
            ({ color: q.color, value: Fmt.pct(q.v), label: q.name })), opts.title(d)));
          seg.addEventListener("pointermove", moveTip);
          seg.addEventListener("pointerleave", hideTip);
        });
        text(svg, x + bw / 2, height - 6, opts.xLabel(d), { "text-anchor": "middle" });
      });
    });
    const legend = document.createElement("div");
    legend.className = "legend-cat";
    [...opts.series.map((s, i) => ({ name: s.name, color: colors[i] })), { name: "Other", color: "var(--series-other)" }].forEach((s) => {
      const item = document.createElement("span");
      const i = document.createElement("i");
      i.style.background = s.color;
      item.append(i, s.name);
      legend.append(item);
    });
    el.after(legend);
  }

  window.Charts = { columns, line, heatmap, matrix, stacked, cleanup, showTip, moveTip, hideTip, Fmt };
})();
