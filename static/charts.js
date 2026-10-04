/* Small SVG chart kit (Organic style): pill columns, area lines, a dot listening clock, lift
   matrices and stacked columns, all with hover tooltips and keyboard access (a chart is one tab
   stop; arrow keys move between marks, Enter opens a mark's link). Labels coming from data go
   through textContent, never innerHTML. */
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
  const timeFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
  const Fmt = {
    int: (n) => intFmt.format(n ?? 0),
    dec: (n) => dec1.format(n ?? 0),
    pct: (n) => pctFmt.format(n ?? 0),
    date: (ts) => (ts ? dateFmt.format(new Date(ts * 1000)) : "–"),
    day: (iso) => (iso ? dateFmt.format(new Date(iso + "T12:00:00")) : "–"),
    dayShort: (iso) => (iso ? dateFmt.format(new Date(iso + "T12:00:00")).replace(/ \d{4}$/, "") : "–"),
    month: (ym) => monthFmt.format(new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7) - 1, 1))),
    time: (ts) => timeFmt.format(new Date(ts * 1000)),
    release: (d) => {
      if (!d) return "–";
      if (/^\d{4}$/.test(d)) return d;
      if (/^\d{4}-\d{2}$/.test(d)) return monthFmt.format(new Date(Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, 1)));
      return dateFmt.format(new Date(d + "T12:00:00"));
    },
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
  function fillTip(rows, title) {
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
    return el;
  }
  function place(el, x, y) {
    const pad = 14;
    const r = el.getBoundingClientRect();
    let px = x + pad, py = y + pad;
    if (px + r.width > window.innerWidth - 8) px = x - r.width - pad;
    if (py + r.height > window.innerHeight - 8) py = y - r.height - pad;
    el.style.left = Math.max(8, px) + "px";
    el.style.top = Math.max(8, py) + "px";
  }
  function showTip(evt, rows, title) { place(fillTip(rows, title), evt.clientX, evt.clientY); }
  function moveTip(evt) { place(tip(), evt.clientX, evt.clientY); }
  function showTipAt(node, rows, title) {
    const b = node.getBoundingClientRect();
    place(fillTip(rows, title), b.left + b.width / 2, b.top);
  }
  function hideTip() { tip().hidden = true; }

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
  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const ticks = [];
    for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }
  // Column with a pill top and a softly rounded foot: border-radius 999px 999px 6px 6px.
  function pillPath(x, y, w, h, bottom = 6) {
    const rt = Math.min(w / 2, h);
    const rb = Math.min(bottom, w / 2, Math.max(0, h - rt));
    return `M${x},${y + h - rb}V${y + rt}A${rt},${rt} 0 0 1 ${x + rt},${y}H${x + w - rt}A${rt},${rt} 0 0 1 ${x + w},${y + rt}`
      + `V${y + h - rb}Q${x + w},${y + h} ${x + w - rb},${y + h}H${x + rb}Q${x},${y + h} ${x},${y + h - rb}Z`;
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
  function median(vals) {
    const v = vals.filter((x) => x > 0).sort((a, b) => a - b);
    return v.length ? v[Math.floor(v.length / 2)] : 0;
  }
  /* Keyboard access for a whole chart: one tab stop, arrows move, Enter/Space activates.
     marks: [{ node, tip: () => ({rows, title}), activate? }] */
  function keyboard(svg, marks, { label, cols } = {}) {
    if (!marks.length) return;
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", `${label ?? "Chart"}. Use the arrow keys to read values${marks.some((m) => m.activate) ? ", Enter to open" : ""}.`);
    let i = -1;
    const show = (k) => {
      marks[i]?.node.classList.remove("hl");
      i = Math.max(0, Math.min(marks.length - 1, k));
      const m = marks[i];
      m.node.classList.add("hl");
      const t = m.tip();
      showTipAt(m.node, t.rows, t.title);
    };
    svg.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols ?? 1, ArrowUp: -(cols ?? 1) }[e.key];
      if (step) { e.preventDefault(); show(i < 0 ? 0 : i + step); }
      else if (e.key === "Home") { e.preventDefault(); show(0); }
      else if (e.key === "End") { e.preventDefault(); show(marks.length - 1); }
      else if ((e.key === "Enter" || e.key === " ") && i >= 0 && marks[i].activate) { e.preventDefault(); marks[i].activate(); }
      else if (e.key === "Escape") hideTip();
    });
    svg.addEventListener("blur", () => { marks[i]?.node.classList.remove("hl"); hideTip(); });
  }
  function hover(node, getTip) {
    node.addEventListener("pointerenter", (e) => { const t = getTip(); showTip(e, t.rows, t.title); });
    node.addEventListener("pointermove", moveTip);
    node.addEventListener("pointerleave", hideTip);
  }
  function xLabels(svg, items, y, minGap = 10) {
    let lastX = -Infinity;
    items.forEach(({ label, cx }) => {
      if (label != null && cx - lastX >= label.length * 7 + minGap) {
        text(svg, cx, y, label, { "text-anchor": "middle" });
        lastX = cx;
      }
    });
  }

  // ---------- column chart ----------
  /* opts: { value(d), xLabel(d,i), tip(d) -> {title, rows}, onClick(d), highlight(d) -> bool, height, label, axis }
     Base bars are pale, bars above the median full accent, the peak bar (or every highlighted bar) darkest. */
  function columns(el, data, opts) {
    const height = opts.height ?? 200;
    responsive(el, (width) => {
      el.replaceChildren();
      const vals = data.map(opts.value);
      const max = Math.max(0, ...vals);
      const med = median(vals);
      const peakIdx = opts.highlight ? -1 : vals.findIndex((v) => v === max && max > 0);
      const top = 8, bottom = 26;
      let left = 0;
      let ticks = null;
      if (opts.axis) {
        ticks = niceTicks(max);
        left = Math.max(...ticks.map((t) => Fmt.compact(t).length)) * 7.2 + 14;
      }
      const yMax = ticks ? ticks[ticks.length - 1] : max || 1;
      const innerW = width - left, innerH = height - top - bottom;
      const y = (v) => top + innerH - (v / yMax) * innerH;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-label": opts.label ?? "" }, el);
      if (ticks) {
        for (const t of ticks.slice(1)) {
          svgEl("line", { x1: left, x2: width, y1: y(t), y2: y(t), class: "gridline" }, svg);
          text(svg, left - 8, y(t) + 4, Fmt.compact(t), { "text-anchor": "end" });
        }
      }
      const band = innerW / Math.max(1, data.length);
      const gap = band > 14 ? Math.min(10, band * 0.28) : 1;
      const bw = Math.max(1, Math.min(34, band - gap));
      const marks = [];
      const labels = [];
      data.forEach((d, i) => {
        const v = vals[i];
        const x0 = left + i * band;
        const bx = x0 + (band - bw) / 2;
        const h = Math.max(v > 0 ? Math.min(bw, 4) : 0, y(0) - y(v));
        const cls = (opts.highlight ? v > 0 && opts.highlight(d) : i === peakIdx) ? "bar peak" : v > med ? "bar up" : "bar";
        const hit = svgEl("rect", { x: x0, y: top, width: band, height: innerH, class: "hit" + (opts.onClick ? " link" : "") }, svg);
        const bar = h > 0 ? svgEl("path", { d: pillPath(bx, y(0) - h, bw, h), class: cls }, svg) : svgEl("rect", { x: bx, y: y(0), width: bw, height: 0, class: cls }, svg);
        const t = () => opts.tip(d);
        hover(hit, t);
        hit.addEventListener("pointerenter", () => bar.classList.add("hl"));
        hit.addEventListener("pointerleave", () => bar.classList.remove("hl"));
        const activate = opts.onClick ? () => opts.onClick(d) : null;
        if (activate) hit.addEventListener("click", activate);
        marks.push({ node: bar, tip: t, activate });
        labels.push({ label: opts.xLabel?.(d, i), cx: x0 + band / 2 });
      });
      svgEl("line", { x1: left, x2: width, y1: y(0) + 1, y2: y(0) + 1, class: "baseline" }, svg);
      xLabels(svg, labels, height - 6);
      keyboard(svg, marks, { label: opts.label });
    });
  }

  // ---------- area line (single series) ----------
  /* opts: { value(d), xLabel(d,i), tip(d), height, yMax, yMin, label, axis, yFormat, endDot } */
  function line(el, data, opts) {
    const height = opts.height ?? 200;
    const fmt = opts.yFormat ?? Fmt.compact;
    responsive(el, (width) => {
      el.replaceChildren();
      const vals = data.map(opts.value);
      const finite = vals.filter((v) => v != null);
      const max = opts.yMax ?? Math.max(0, ...finite);
      const ticks = opts.axis ? niceTicks(max) : null;
      const top = 10, bottom = 26, right = 8;
      const left = ticks ? Math.max(...ticks.map((t) => fmt(t).length)) * 7.2 + 14 : 4;
      const innerW = width - left - right, innerH = height - top - bottom;
      const yMax = ticks ? ticks[ticks.length - 1] : max * 1.08 || 1;
      const yMin = opts.yMin ?? 0;
      const x = (i) => left + (data.length <= 1 ? innerW / 2 : (i / (data.length - 1)) * innerW);
      const y = (v) => top + innerH - ((v - yMin) / (yMax - yMin)) * innerH;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-label": opts.label ?? "" }, el);
      if (ticks) {
        for (const t of ticks.slice(1)) {
          svgEl("line", { x1: left, x2: width - right, y1: y(t), y2: y(t), class: "gridline" }, svg);
          text(svg, left - 8, y(t) + 4, fmt(t), { "text-anchor": "end" });
        }
      }
      // smooth segments (Catmull-Rom to Bézier), broken on null
      let d = "", area = "", seg = [];
      const curve = (pts) => pts.map(([px, py], k) => {
        if (!k) return `M${px},${py}`;
        const [x0, y0] = pts[k - 2] ?? pts[k - 1], [x1, y1] = pts[k - 1], [x3, y3] = pts[k + 1] ?? [px, py];
        return `C${x1 + (px - x0) / 6},${y1 + (py - y0) / 6} ${px - (x3 - x1) / 6},${py - (y3 - y1) / 6} ${px},${py}`;
      }).join("");
      const flush = () => {
        if (!seg.length) return;
        const pts = seg.map(([i, v]) => [x(i), y(v)]);
        const c = curve(pts);
        d += c;
        area += c + `L${pts[pts.length - 1][0]},${y(yMin)}L${pts[0][0]},${y(yMin)}Z`;
        seg = [];
      };
      vals.forEach((v, i) => (v == null ? flush() : seg.push([i, v])));
      flush();
      svgEl("path", { d: area, class: "area" }, svg);
      svgEl("path", { d, class: "line" }, svg);
      const lastI = vals.reduce((a, v, i) => (v == null ? a : i), -1);
      if (opts.endDot !== false && lastI >= 0) svgEl("circle", { cx: x(lastI), cy: y(vals[lastI]), r: 5, class: "marker" }, svg);
      xLabels(svg, data.map((row, i) => ({ label: opts.xLabel?.(row, i), cx: x(i) })), height - 6);

      const cross = svgEl("line", { y1: top, y2: top + innerH, class: "cross", visibility: "hidden" }, svg);
      const marker = svgEl("circle", { r: 5, class: "marker", visibility: "hidden" }, svg);
      const point = (i) => {
        cross.setAttribute("x1", x(i));
        cross.setAttribute("x2", x(i));
        cross.setAttribute("visibility", "visible");
        if (vals[i] != null) {
          marker.setAttribute("cx", x(i));
          marker.setAttribute("cy", y(vals[i]));
          marker.setAttribute("visibility", "visible");
        } else marker.setAttribute("visibility", "hidden");
      };
      const overlay = svgEl("rect", { x: left, y: top, width: innerW, height: innerH, class: "hit" }, svg);
      overlay.addEventListener("pointermove", (e) => {
        const box = svg.getBoundingClientRect();
        const px = ((e.clientX - box.left) / box.width) * width;
        const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - left) / innerW) * (data.length - 1))));
        point(i);
        const t = opts.tip(data[i]);
        showTip(e, t.rows, t.title);
      });
      overlay.addEventListener("pointerleave", () => {
        cross.setAttribute("visibility", "hidden");
        marker.setAttribute("visibility", "hidden");
        hideTip();
      });
      // keyboard: arrows move the crosshair along the series
      const hidePoint = () => { cross.setAttribute("visibility", "hidden"); marker.setAttribute("visibility", "hidden"); };
      keyboard(svg, data.map((row, i) => ({
        node: { classList: { add: () => point(i), remove: hidePoint }, getBoundingClientRect: () => marker.getBoundingClientRect() },
        tip: () => opts.tip(row),
      })), { label: opts.label });
    });
  }

  // ---------- listening clock: 7 × 24 dots, bigger and darker = more plays ----------
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  function clock(el, grid, opts = {}) {
    const max = Math.max(1, ...grid.flat());
    const total = grid.flat().reduce((a, b) => a + b, 0) || 1;
    const bin = (v) => (v === 0 ? 0 : Math.min(7, Math.ceil((v / max) * 7)));
    responsive(el, (width) => {
      el.replaceChildren();
      const left = 40, top = 4, bottom = 24;
      const cw = (width - left) / 24;
      const ch = Math.max(18, Math.min(26, cw));
      const height = top + 7 * ch + bottom;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-label": opts.label ?? "Listening clock" }, el);
      const marks = [];
      grid.forEach((row, wd) => {
        text(svg, 0, top + wd * ch + ch / 2 + 4, DAYS[wd], { class: "tick row-label" });
        row.forEach((v, h) => {
          const b = bin(v);
          const r = ((b ? 6 + 2 * b : 5) / 2) * Math.min(1, cw / 22);
          const dot = svgEl("circle", { cx: left + h * cw + cw / 2, cy: top + wd * ch + ch / 2, r: Math.max(2, r), class: "dot" }, svg);
          dot.style.fill = `var(--seq-${b})`;
          const t = () => ({ title: `${DAYS[wd]} ${String(h).padStart(2, "0")}:00–${String(h).padStart(2, "0")}:59`,
            rows: [{ value: Fmt.int(v), label: `plays · ${Fmt.pct(v / total)} of the period` }] });
          hover(dot, t);
          marks.push({ node: dot, tip: t });
        });
      });
      for (let h = 0; h < 24; h += cw < 22 ? 6 : 3) {
        text(svg, left + h * cw + cw / 2, height - 6, String(h).padStart(2, "0"), { "text-anchor": "middle" });
      }
      keyboard(svg, marks, { label: opts.label ?? "Listening clock", cols: 24 });
    });
  }

  // ---------- lift matrix (rows × columns, around "as usual") ----------
  /* data: { cols: [label], rows: [{ name, href?, cells: [{ lift|null, plays, expected, up, years }] }] }
     opts: { label, yearsLabel, highlightCols: [index] } — sand is "as usual", sage more, terracotta less. */
  const liftFmt = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const liftFmt2 = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function liftStep(lift) {
    const a = Math.abs(Math.log2(lift));
    return a < 0.15 ? 0 : a < 0.38 ? 1 : a < 0.7 ? 2 : 3; // within ±11 % reads as "as usual"; then ±30 %, ±62 %
  }
  function liftFill(lift) {
    const step = liftStep(lift);
    return step === 0 ? "var(--div-mid)" : `var(--div-${lift > 1 ? "pos" : "neg"}-${step})`;
  }
  function matrix(el, data, opts = {}) {
    const { cols, rows } = data;
    const hl = new Set(opts.highlightCols ?? []);
    responsive(el, (width) => {
      el.replaceChildren();
      const longest = Math.max(4, ...rows.map((r) => r.name.length));
      const left = Math.min(150, Math.max(64, longest * 7.4 + 14));
      const maxChars = Math.floor((left - 14) / 7.4);
      const top = 24, gap = 6;
      const cw = (width - left) / cols.length;
      const ch = Math.max(28, Math.min(40, cw * 0.62));
      const height = top + rows.length * ch + 4;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-label": opts.label ?? "" }, el);
      const every = cw < 34 ? 2 : 1;
      cols.forEach((c, ci) => {
        if (ci % every === 0) text(svg, left + ci * cw + cw / 2, 14, c, { "text-anchor": "middle", class: "tick col-label" + (hl.has(ci) ? " hl" : "") });
      });
      const marks = [];
      rows.forEach((row, ri) => {
        const y = top + ri * ch;
        const name = row.name.length > maxChars ? row.name.slice(0, maxChars - 1) + "…" : row.name;
        const holder = row.href ? svgEl("a", { href: row.href }, svg) : svg;
        const t = text(holder, 0, y + ch / 2 + 4, name, { class: "tick row-label" });
        if (name !== row.name) svgEl("title", {}, t).textContent = row.name;
        row.cells.forEach((c, ci) => {
          const w = Math.max(4, cw - gap), h = ch - gap;
          const cx = left + ci * cw + gap / 2, cy = y + gap / 2;
          const cell = svgEl("rect", { x: cx, y: cy, width: w, height: h, rx: Math.min(h / 2, w / 2), class: "cell" }, svg);
          if (c.lift == null) cell.classList.add("nodata");
          else cell.style.fill = liftFill(c.lift);
          if (c.lift != null && liftStep(c.lift) >= 2 && w >= 34) {
            const v = text(svg, cx + w / 2, cy + h / 2 + 4.5, liftFmt.format(c.lift) + "×", { "text-anchor": "middle", class: "cell-v" });
            v.style.fill = "var(--div-ink-strong)";
          }
          const tp = () => ({
            title: `${row.name} · ${cols[ci]}`,
            rows: c.lift == null ? [{ value: "Too few plays", label: "" }] : [
              { value: liftFmt2.format(c.lift) + "×", label: "your usual share" },
              { value: Fmt.int(c.plays), label: `plays · ${Fmt.int(c.expected)} expected` },
              ...(c.years ? [{ value: `${c.up} of ${c.years}`, label: `${opts.yearsLabel ?? "years"} above usual` }] : []),
            ],
          });
          hover(cell, tp);
          marks.push({ node: cell, tip: tp });
        });
      });
      keyboard(svg, marks, { label: opts.label, cols: cols.length });
    });
    const legend = document.createElement("div");
    legend.className = "legend-seq";
    const sw = (v) => { const i = document.createElement("i"); i.style.background = v; legend.append(i); };
    legend.append("less");
    ["var(--div-neg-3)", "var(--div-neg-2)", "var(--div-neg-1)", "var(--div-mid)", "var(--div-pos-1)", "var(--div-pos-2)", "var(--div-pos-3)"].forEach(sw);
    legend.append("more than usual");
    const none = document.createElement("i");
    none.className = "nodata";
    legend.append(none, "too few plays");
    el.after(legend);
  }

  // ---------- 100 % stacked pill columns ----------
  /* opts: { series: [{name}], shares(d) -> [0..1], other(d), xLabel(d), title(d), label, colors? } */
  let clipSeq = 0;
  function stacked(el, data, opts) {
    const height = opts.height ?? 220;
    const colors = opts.colors ?? opts.series.map((_, i) => (i < 5 ? `var(--series-${i + 1})` : "var(--series-other)"));
    responsive(el, (width) => {
      el.replaceChildren();
      const top = 4, bottom = 26;
      const innerH = height - top - bottom;
      const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-label": opts.label ?? "" }, el);
      const defs = svgEl("defs", {}, svg);
      const band = width / Math.max(1, data.length);
      const bw = Math.max(8, Math.min(48, band - 10));
      const marks = [];
      data.forEach((d, di) => {
        const x = di * band + (band - bw) / 2;
        const id = `stk-${++clipSeq}`;
        const clip = svgEl("clipPath", { id }, defs);
        svgEl("rect", { x, y: top, width: bw, height: innerH, rx: bw / 2 }, clip);
        const g = svgEl("g", { "clip-path": `url(#${id})` }, svg);
        const parts = [...opts.shares(d).map((v, i) => ({ v, color: colors[i], name: opts.series[i].name })),
          { v: opts.other(d), color: "var(--series-other)", name: "other" }].filter((p) => p.v > 0);
        const sum = parts.reduce((s, p) => s + p.v, 0) || 1;
        let acc = 0;
        parts.forEach((p, i) => {
          const y1 = top + innerH * (1 - (acc + p.v) / sum), y0 = top + innerH * (1 - acc / sum);
          acc += p.v;
          const h = Math.max(0, y0 - y1 - (i === parts.length - 1 ? 0 : 2)); // 2px canvas gap between segments
          const seg = svgEl("rect", { x, y: y0 - h, width: bw, height: h }, g);
          seg.style.fill = p.color;
        });
        const hit = svgEl("rect", { x: di * band, y: top, width: band, height: innerH, class: "hit" }, svg);
        const t = () => ({ title: opts.title(d), rows: parts.slice().reverse().map((q) => ({ color: q.color, value: Fmt.pct(q.v), label: q.name })) });
        hover(hit, t);
        marks.push({ node: hit, tip: t });
        text(svg, x + bw / 2, height - 6, opts.xLabel(d), { "text-anchor": "middle" });
      });
      keyboard(svg, marks, { label: opts.label });
    });
    const legend = document.createElement("div");
    legend.className = "legend-cat";
    const hasOther = data.some((d) => opts.other(d) > 0);
    [...opts.series.map((s, i) => ({ name: s.name, color: colors[i] })), ...(hasOther ? [{ name: "other", color: "var(--series-other)" }] : [])].forEach((s) => {
      const item = document.createElement("span");
      const i = document.createElement("i");
      i.style.background = s.color;
      item.append(i, s.name);
      legend.append(item);
    });
    el.after(legend);
  }

  window.Charts = { columns, line, clock, matrix, stacked, liftStep, cleanup, showTip, moveTip, hideTip, Fmt };
})();
