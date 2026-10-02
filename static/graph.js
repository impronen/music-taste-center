/* Force-directed taste graph in plain SVG. Layout is computed up front (deterministic
   seed), then nodes can be dragged and the canvas panned / zoomed. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const { Fmt, showTip, moveTip, hideTip } = window.Charts;

  function rng(seed) {
    return () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
  }

  function layout(nodes, edges, width, height) {
    const rand = rng(42);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const nClusters = Math.max(1, ...nodes.map((n) => n.cluster + 1));
    nodes.forEach((n) => {
      const a = (n.cluster / nClusters) * Math.PI * 2;
      const R = Math.min(width, height) * 0.28;
      n.x = width / 2 + Math.cos(a) * R + (rand() - 0.5) * 80;
      n.y = height / 2 + Math.sin(a) * R + (rand() - 0.5) * 80;
      n.vx = n.vy = 0;
    });
    const links = edges.map((e) => ({ s: byId.get(e.source), t: byId.get(e.target), w: e.score })).filter((l) => l.s && l.t);
    const maxW = Math.max(...links.map((l) => l.w), 0.01);
    // repulsion scales with the space each node gets, so small and large graphs both spread out
    const rep = (0.45 * width * height) / Math.max(1, nodes.length);
    const iters = 420;
    for (let it = 0; it < iters; it++) {
      const alpha = 1 - it / iters;
      // repulsion + collision
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy || 0.01;
          const d = Math.sqrt(d2);
          let f = (rep * (0.2 + alpha)) / d2;
          const minD = a.r + b.r + 6;
          if (d < minD) f += (minD - d) * 0.5;
          dx /= d; dy /= d;
          a.vx -= dx * f; a.vy -= dy * f;
          b.vx += dx * f; b.vy += dy * f;
        }
      }
      // springs, stronger and shorter for tighter links
      for (const l of links) {
        const k = 0.02 + 0.08 * (l.w / maxW);
        const len = 40 + l.s.r + l.t.r + 40 * (1 - l.w / maxW);
        const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (d - len) * k * alpha;
        l.s.vx += (dx / d) * f; l.s.vy += (dy / d) * f;
        l.t.vx -= (dx / d) * f; l.t.vy -= (dy / d) * f;
      }
      for (const n of nodes) {
        n.vx += (width / 2 - n.x) * 0.01;
        n.vy += ((height / 2 - n.y) * 0.01 * width) / height;
        n.vx *= 0.6; n.vy *= 0.6;
        n.x += n.vx; n.y += n.vy;
      }
    }
  }

  function create(el, data, opts) {
    el.replaceChildren();
    const width = el.clientWidth, height = el.clientHeight;
    const maxPlays = Math.max(...data.nodes.map((n) => n.plays), 1);
    const nodes = data.nodes.map((n) => ({ ...n, r: 4 + 18 * Math.sqrt(n.plays / maxPlays) }));
    layout(nodes, data.edges, width, height);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const nbrs = new Map(nodes.map((n) => [n.id, []]));
    for (const e of data.edges) {
      nbrs.get(e.source)?.push({ id: e.target, score: e.score, shared: e.shared });
      nbrs.get(e.target)?.push({ id: e.source, score: e.score, shared: e.shared });
    }

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Artist connection graph");
    // fit the laid-out graph into view (room for labels), keeping the container's aspect ratio
    const pad = 40;
    const minX = Math.min(...nodes.map((n) => n.x - n.r)) - pad, maxX = Math.max(...nodes.map((n) => n.x + n.r)) + pad;
    const minY = Math.min(...nodes.map((n) => n.y - n.r)) - pad - 14, maxY = Math.max(...nodes.map((n) => n.y + n.r)) + pad;
    const fit = Math.max((maxX - minX) / width, (maxY - minY) / height);
    let vb = { w: width * fit, h: height * fit };
    vb.x = (minX + maxX) / 2 - vb.w / 2;
    vb.y = (minY + maxY) / 2 - vb.h / 2;
    // labels keep a constant on-screen size whatever the zoom
    const setVB = () => {
      svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
      const k = vb.w / width;
      gLabels.style.fontSize = 11 * k + "px";
      gLabels.style.strokeWidth = 3 * k + "px";
    };
    el.append(svg);
    const gEdges = document.createElementNS(NS, "g");
    const gNodes = document.createElementNS(NS, "g");
    const gLabels = document.createElementNS(NS, "g");
    svg.append(gEdges, gNodes, gLabels);
    setVB();

    const edgeEls = data.edges.map((e) => {
      const l = document.createElementNS(NS, "line");
      l.setAttribute("class", "edge");
      l.setAttribute("stroke-width", String(0.6 + 2.4 * Math.min(1, e.score * 3)));
      l._e = e;
      gEdges.append(l);
      return l;
    });
    const labelRank = [...nodes].sort((a, b) => b.plays - a.plays).slice(0, 28).map((n) => n.id);
    const major = new Set(labelRank);
    for (const n of nodes) {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("class", "node");
      c.setAttribute("r", n.r);
      c.style.fill = opts.color(n.cluster);
      c.setAttribute("tabindex", "0");
      c.setAttribute("aria-label", `${n.name}, ${n.plays} plays`);
      n.el = c;
      gNodes.append(c);
      const t = document.createElementNS(NS, "text");
      t.setAttribute("class", "label" + (major.has(n.id) ? "" : " minor"));
      t.setAttribute("text-anchor", "middle");
      t.textContent = n.name;
      n.label = t;
      gLabels.append(t);

      c.addEventListener("pointerenter", (e) => {
        if (dragging) return;
        focusNodes(new Set([n.id, ...nbrs.get(n.id).map((x) => x.id)]), n.id);
        const top = [...nbrs.get(n.id)].sort((a, b) => b.score - a.score).slice(0, 4);
        showTip(e, [
          { value: Fmt.int(n.plays), label: "plays" },
          ...top.map((x) => ({ value: byId.get(x.id).name, label: `${Fmt.int(x.shared)} shared sessions` })),
        ], n.name);
      });
      c.addEventListener("pointermove", moveTip);
      c.addEventListener("pointerleave", () => {
        if (!dragging) clearFocus();
        hideTip();
      });
      c.addEventListener("focus", () => focusNodes(new Set([n.id, ...nbrs.get(n.id).map((x) => x.id)]), n.id));
      c.addEventListener("blur", clearFocus);
      c.addEventListener("keydown", (e) => e.key === "Enter" && opts.onSelect(n));
      c.addEventListener("pointerdown", (e) => startDrag(e, n));
    }

    function draw() {
      for (const l of edgeEls) {
        const s = byId.get(l._e.source), t = byId.get(l._e.target);
        l.setAttribute("x1", s.x); l.setAttribute("y1", s.y);
        l.setAttribute("x2", t.x); l.setAttribute("y2", t.y);
      }
      for (const n of nodes) {
        n.el.setAttribute("cx", n.x); n.el.setAttribute("cy", n.y);
        n.label.setAttribute("x", n.x); n.label.setAttribute("y", n.y - n.r - 4);
      }
    }
    draw();

    let pinnedCluster = null;
    function focusNodes(ids, center) {
      el.classList.add("focus");
      for (const n of nodes) {
        const on = ids.has(n.id);
        n.el.classList.toggle("on", on);
        n.label.classList.toggle("on", on);
      }
      for (const l of edgeEls) {
        const on = center != null
          ? (l._e.source === center || l._e.target === center)
          : ids.has(l._e.source) && ids.has(l._e.target);
        l.classList.toggle("on", on);
      }
    }
    function clearFocus() {
      if (pinnedCluster != null) return highlightCluster(pinnedCluster);
      el.classList.remove("focus");
    }
    function highlightCluster(id) {
      pinnedCluster = id;
      if (id == null) return el.classList.remove("focus");
      focusNodes(new Set(nodes.filter((n) => n.cluster === id).map((n) => n.id)), null);
    }

    // ---- drag nodes, pan background, wheel zoom ----
    let dragging = null;
    const toSvg = (e) => {
      const b = svg.getBoundingClientRect();
      return { x: vb.x + ((e.clientX - b.left) / b.width) * vb.w, y: vb.y + ((e.clientY - b.top) / b.height) * vb.h };
    };
    function startDrag(e, n) {
      e.stopPropagation();
      const p0 = toSvg(e);
      dragging = { n, moved: false, dx: n.x - p0.x, dy: n.y - p0.y };
      svg.setPointerCapture(e.pointerId);
    }
    svg.addEventListener("pointerdown", (e) => {
      if (dragging) return;
      dragging = { pan: true, cx: e.clientX, cy: e.clientY, vb: { ...vb } };
      svg.classList.add("dragging");
      svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      if (dragging.pan) {
        const b = svg.getBoundingClientRect();
        vb.x = dragging.vb.x - ((e.clientX - dragging.cx) / b.width) * vb.w;
        vb.y = dragging.vb.y - ((e.clientY - dragging.cy) / b.height) * vb.h;
        setVB();
      } else {
        const p = toSvg(e);
        dragging.n.x = p.x + dragging.dx;
        dragging.n.y = p.y + dragging.dy;
        dragging.moved = true;
        hideTip();
        draw();
      }
    });
    svg.addEventListener("pointerup", () => {
      if (dragging && !dragging.pan && !dragging.moved) opts.onSelect(dragging.n);
      dragging = null;
      svg.classList.remove("dragging");
    });
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = toSvg(e);
      const k = Math.exp(e.deltaY * 0.0015);
      const w = Math.min(width * 4, Math.max(width / 6, vb.w * k));
      const s = w / vb.w;
      vb = { x: p.x - (p.x - vb.x) * s, y: p.y - (p.y - vb.y) * s, w, h: vb.h * s };
      setVB();
    }, { passive: false });

    return { highlightCluster };
  }

  window.TasteGraph = { create };
})();
