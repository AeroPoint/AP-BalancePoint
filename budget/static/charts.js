/* Small dependency-free SVG charts: columns (grouped or stacked), lines, and a cash-flow sankey. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const seriesColor = (i) => token(`--s${(i % 8) + 1}`);
  const sum = (rows) => rows.reduce((a, r) => a + r.total, 0);

  function money(v, compact) {
    const sign = v < 0 ? "−" : "";
    const a = Math.abs(v);
    if (compact && a >= 1e6) {
      const mil = a / 1e6;
      return `${sign}$${mil >= 10 ? Math.round(mil) : mil.toFixed(1).replace(/\.0$/, "")}M`;
    }
    if (compact && a >= 1000) {
      const k = a / 1000;
      return `${sign}$${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
    }
    return `${sign}$${a.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function svg(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  // ---------------------------------------------------------------- tooltip
  const tipEl = () => document.getElementById("tip");
  let dataTipActive = false;

  function showTip(html, x, y) {
    const tip = tipEl();
    if (!tip) return;
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14;
    let top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }

  function hideTip() {
    const tip = tipEl();
    if (tip) tip.hidden = true;
  }

  function tipRows(title, rows) {
    return (
      `<strong>${escapeHtml(title)}</strong>` +
      rows
        .map(
          (r) =>
            `<div class="row"><span>${r.color ? `<span class="key" style="background:${r.color}"></span>` : ""}${escapeHtml(r.name)}</span><span class="v">${escapeHtml(r.value)}</span></div>`
        )
        .join("")
    );
  }

  // Any element with data-tip gets the same tooltip.
  document.addEventListener("pointermove", (e) => {
    const target = e.target.closest && e.target.closest("[data-tip]");
    if (target) showTip(escapeHtml(target.dataset.tip), e.clientX, e.clientY);
    else if (dataTipActive) hideTip();
    dataTipActive = Boolean(target);
  });

  // ---------------------------------------------------------------- shared pieces
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function roundedTop(x, y, w, h, r) {
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function legend(container, items) {
    const ul = document.createElement("ul");
    ul.className = "legend";
    for (const it of items) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="key${it.line ? " line" : ""}" style="background:${it.color}"></span>${escapeHtml(it.name)}`;
      ul.appendChild(li);
    }
    container.appendChild(ul);
  }

  // Show every Nth x label so labels never run into each other (11px text is ~6.5px per character).
  function labelStep(labels, spacing) {
    const widest = Math.max(1, ...labels.map((l) => String(l).length));
    return Math.max(1, Math.ceil((widest * 6.5 + 12) / Math.max(spacing, 1)));
  }

  function responsive(container, draw) {
    // Charts can be redrawn with new data (the net worth filters do this), so drop the old observer.
    if (container._chartObserver) container._chartObserver.disconnect();
    let lastWidth = 0;
    const render = () => {
      const width = Math.floor(container.clientWidth);
      if (!width || width === lastWidth) return;
      lastWidth = width;
      container.innerHTML = "";
      draw(width);
    };
    container._chartObserver = new ResizeObserver(render);
    container._chartObserver.observe(container);
    render();
  }

  function yAxis(root, { left, right, top, plotH, max, width }) {
    // Quarter steps of 50k are 12.5k; use fifths then so ticks land on round numbers.
    const quarter = max / 4;
    const steps = quarter % 10 ** Math.floor(Math.log10(quarter)) === 0 ? 4 : 5;
    for (let i = 0; i <= steps; i++) {
      const v = (max / steps) * i;
      const y = Math.round(top + plotH - (v / max) * plotH) + 0.5;
      svg("line", { x1: left, x2: width - right, y1: y, y2: y, stroke: token(i === 0 ? "--axis" : "--grid"), "stroke-width": 1 }, root);
      svg("text", { x: left - 8, y: y + 4, "text-anchor": "end" }, root).textContent = money(v, true);
    }
  }

  // ---------------------------------------------------------------- columns
  function columns(container, { labels, series, stacked = false, height = 250, colors, titles }) {
    const palette = colors || series.map((_, i) => seriesColor(i));
    responsive(container, (width) => {
      if (series.length > 1) legend(container, series.map((s, i) => ({ name: s.name, color: palette[i] })));
      const m = { top: 10, right: 8, bottom: 26, left: 50 };
      const plotW = width - m.left - m.right;
      const plotH = height - m.top - m.bottom;
      const totals = labels.map((_, i) =>
        stacked
          ? series.reduce((a, s) => a + Math.max(0, s.values[i] || 0), 0)
          : Math.max(0, ...series.map((s) => s.values[i] || 0))
      );
      const max = niceMax(Math.max(0, ...totals));
      const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": series.map((s) => s.name).join(", ") }, container);
      yAxis(root, { left: m.left, right: m.right, top: m.top, plotH, max, width });
      const band = plotW / labels.length;
      const hover = svg("rect", { y: m.top, width: band, height: plotH, fill: token("--accent-wash"), opacity: 0 }, root);
      const marks = svg("g", {}, root);
      const labelEvery = labelStep(labels, band);

      labels.forEach((label, i) => {
        const bx = m.left + band * i;
        const baseline = m.top + plotH;
        if (stacked) {
          const w = Math.min(24, band * 0.62);
          const segments = series.map((s, si) => ({ v: Math.max(0, s.values[i] || 0), si })).filter((s) => s.v > 0);
          let y = baseline;
          segments.forEach((s, k) => {
            const h = (s.v / max) * plotH;
            y -= h;
            const hh = h - (k > 0 ? 2 : 0); // 2px surface gap between stacked segments
            if (hh <= 0) return;
            const x = bx + (band - w) / 2;
            if (k === segments.length - 1) svg("path", { d: roundedTop(x, y, w, hh, 4), fill: palette[s.si] }, marks);
            else svg("rect", { x, y, width: w, height: hh, fill: palette[s.si] }, marks);
          });
        } else {
          const n = series.length;
          const w = Math.max(2, Math.min(24, (band * 0.72 - 2 * (n - 1)) / n));
          const groupW = n * w + 2 * (n - 1);
          series.forEach((s, si) => {
            const h = (Math.max(0, s.values[i] || 0) / max) * plotH;
            if (h > 0) svg("path", { d: roundedTop(bx + (band - groupW) / 2 + si * (w + 2), baseline - h, w, h, 4), fill: palette[si] }, marks);
          });
        }
        if (i % labelEvery === 0) svg("text", { x: bx + band / 2, y: height - 8, "text-anchor": "middle" }, root).textContent = label;
      });

      const hits = svg("rect", { x: m.left, y: m.top, width: plotW, height: plotH, fill: "transparent" }, root);
      hits.addEventListener("pointermove", (ev) => {
        const rect = root.getBoundingClientRect();
        const i = Math.min(labels.length - 1, Math.max(0, Math.floor((ev.clientX - rect.left - m.left) / band)));
        hover.setAttribute("x", m.left + band * i);
        hover.setAttribute("opacity", 1);
        let rows = series.map((s, si) => ({ name: s.name, color: palette[si], value: money(s.values[i] || 0) }));
        if (stacked) rows = rows.reverse().concat([{ name: "Total", value: money(totals[i]) }]);
        showTip(tipRows(titles ? titles[i] : labels[i], rows), ev.clientX, ev.clientY);
      });
      hits.addEventListener("pointerleave", () => {
        hover.setAttribute("opacity", 0);
        hideTip();
      });
    });
  }

  // ---------------------------------------------------------------- lines
  function lines(container, { labels, series, height = 250, colors, titles }) {
    const palette = colors || series.map((_, i) => seriesColor(i));
    responsive(container, (width) => {
      if (series.length > 1) legend(container, series.map((s, i) => ({ name: s.name, color: palette[i], line: true })));
      const direct = series.length > 1 && series.length <= 4 && width > 480;
      const m = { top: 12, right: direct ? 58 : 16, bottom: 26, left: 56 };
      const plotW = width - m.left - m.right;
      const plotH = height - m.top - m.bottom;
      const values = series.flatMap((s) => s.values.filter((v) => v != null));
      const max = niceMax(Math.max(0, ...values));
      const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": series.map((s) => s.name).join(", ") }, container);
      yAxis(root, { left: m.left, right: m.right, top: m.top, plotH, max, width });
      const step = labels.length > 1 ? plotW / (labels.length - 1) : 0;
      const X = (i) => m.left + step * i;
      const Y = (v) => m.top + plotH - (Math.max(0, v) / max) * plotH;
      const labelEvery = step ? labelStep(labels, step) : 1;
      labels.forEach((label, i) => {
        if (i % labelEvery === 0) svg("text", { x: X(i), y: height - 8, "text-anchor": "middle" }, root).textContent = label;
      });

      const cross = svg("line", { y1: m.top, y2: m.top + plotH, stroke: token("--axis"), "stroke-width": 1, opacity: 0 }, root);
      const surface = token("--panel");
      const ends = [];
      series.forEach((s, si) => {
        let d = "";
        let pen = false;
        let last = null;
        s.values.forEach((v, i) => {
          if (v == null) {
            pen = false;
            return;
          }
          d += `${pen ? "L" : "M"}${X(i)},${Y(v)}`;
          pen = true;
          last = i;
        });
        svg("path", { d, fill: "none", stroke: palette[si], "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, root);
        if (last != null) {
          svg("circle", { cx: X(last), cy: Y(s.values[last]), r: 4, fill: palette[si], stroke: surface, "stroke-width": 2 }, root);
          ends.push({ x: X(last), y: Y(s.values[last]), name: s.name });
        }
      });
      if (direct) {
        // Label line ends; if two ends collide, the legend and tooltip carry the second.
        ends.sort((a, b) => a.y - b.y);
        let prevY = -Infinity;
        for (const e of ends) {
          if (e.y - prevY < 14) continue;
          svg("text", { x: e.x + 9, y: e.y + 4, class: "lbl" }, root).textContent = e.name;
          prevY = e.y;
        }
      }

      const dots = series.map((_, si) => svg("circle", { r: 4, fill: palette[si], stroke: surface, "stroke-width": 2, opacity: 0 }, root));
      const hits = svg("rect", { x: m.left - step / 2, y: m.top, width: plotW + step, height: plotH, fill: "transparent" }, root);
      hits.addEventListener("pointermove", (ev) => {
        const rect = root.getBoundingClientRect();
        const i = step ? Math.min(labels.length - 1, Math.max(0, Math.round((ev.clientX - rect.left - m.left) / step))) : 0;
        cross.setAttribute("x1", X(i));
        cross.setAttribute("x2", X(i));
        cross.setAttribute("opacity", 1);
        const rows = [];
        series.forEach((s, si) => {
          const v = s.values[i];
          dots[si].setAttribute("opacity", v == null ? 0 : 1);
          if (v != null) {
            dots[si].setAttribute("cx", X(i));
            dots[si].setAttribute("cy", Y(v));
            rows.push({ name: s.name, color: series.length > 1 ? palette[si] : null, value: money(v) });
          }
        });
        showTip(tipRows(titles ? titles[i] : labels[i], rows), ev.clientX, ev.clientY);
      });
      hits.addEventListener("pointerleave", () => {
        cross.setAttribute("opacity", 0);
        dots.forEach((d) => d.setAttribute("opacity", 0));
        hideTip();
      });
    });
  }

  // ---------------------------------------------------------------- cash-flow sankey
  function sankey(container, { income, spending, height = 380, link }) {
    responsive(container, (width) => {
      const narrow = width < 600;
      const fold = (rows, limit, otherName) => {
        const total = sum(rows);
        const keep = [];
        const rest = { name: otherName, total: 0, other: true };
        rows.forEach((r, i) => {
          if (i < limit && r.total >= total * 0.02) keep.push({ ...r });
          else rest.total += r.total;
        });
        if (rest.total > 0.5) keep.push(rest);
        return keep;
      };
      const left = fold(income, narrow ? 4 : 6, "Other income");
      const right = fold(spending, narrow ? 7 : 10, "Everything else");
      const inTotal = sum(left);
      const outTotal = sum(right);
      if (outTotal < inTotal) right.push({ name: "Saved", total: inTotal - outTotal, saved: true });
      if (inTotal < outTotal) left.push({ name: "From savings", total: outTotal - inTotal, shortfall: true });
      const total = Math.max(inTotal, outTotal);
      if (!total) {
        container.textContent = "No income or spending in this period.";
        return;
      }

      const labelW = narrow ? 112 : 190;
      const nodeW = 10;
      const gap = 6;
      const nodes = Math.max(left.length, right.length);
      const scale = (height - 16 - gap * (nodes - 1)) / total;
      const xL = labelW;
      const xC = Math.round(width / 2 - nodeW / 2);
      const xR = width - labelW - nodeW;
      const colors = { in: seriesColor(0), out: seriesColor(1), saved: seriesColor(2), other: token("--s-other") };
      const layout = (list) => {
        const h = sum(list) * scale + gap * (list.length - 1);
        let y = (height - h) / 2;
        return list.map((n) => {
          const node = { ...n, y, h: Math.max(1.5, n.total * scale) };
          y += n.total * scale + gap;
          return node;
        });
      };
      const L = layout(left);
      const R = layout(right);
      const centerY = (height - total * scale) / 2;
      const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Cash flow from income to spending" }, container);
      const linkLayer = svg("g", {}, root);
      const nodeLayer = svg("g", {}, root);
      const textLayer = svg("g", {}, root);
      const bandPath = (x0, y0, x1, y1, h) => {
        const mx = (x0 + x1) / 2;
        return `M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}L${x1},${y1 + h}C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h}Z`;
      };
      const pct = (v) => (inTotal ? ` (${Math.round((v / inTotal) * 100)}% of income)` : "");
      const fit = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
      const maxChars = narrow ? 13 : 24;

      const wire = (elements, n, side) => {
        const href = side === "right" && link && !n.other && !n.saved ? `${link}&category=${n.id == null ? "none" : n.id}` : null;
        for (const el of elements) {
          el.addEventListener("pointermove", (ev) => {
            elements[0].setAttribute("fill-opacity", 0.45);
            showTip(tipRows(n.name, [{ name: side === "left" ? "Came in" : n.saved ? "Kept" : "Spent", value: `${money(n.total)}${pct(n.total)}` }]), ev.clientX, ev.clientY);
          });
          el.addEventListener("pointerleave", () => {
            elements[0].setAttribute("fill-opacity", 0.22);
            hideTip();
          });
          if (href) {
            el.classList.add("clickable");
            el.addEventListener("click", () => (window.location.href = href));
          }
        }
      };
      const label = (n, x, anchor, lastY) => {
        const cy = n.y + n.h / 2;
        if (cy - lastY < 15) return lastY; // too crowded: tooltip carries it
        const t = svg("text", { x, y: cy + 4, "text-anchor": anchor, class: "lbl" }, textLayer);
        t.textContent = fit(n.name, maxChars) + " ";
        svg("tspan", { class: "sub" }, t).textContent = money(n.total, narrow);
        return cy;
      };

      let cy = centerY;
      let lastY = -Infinity;
      L.forEach((n) => {
        const color = n.shortfall || n.other ? colors.other : colors.in;
        const h = n.total * scale;
        const band = svg("path", { d: bandPath(xL + nodeW, n.y, xC, cy, h), fill: color, "fill-opacity": 0.22 }, linkLayer);
        const rect = svg("rect", { x: xL, y: n.y, width: nodeW, height: n.h, rx: 2, fill: color }, nodeLayer);
        wire([band, rect], n, "left");
        lastY = label(n, xL - 8, "end", lastY);
        cy += h;
      });
      svg("rect", { x: xC, y: centerY, width: nodeW, height: total * scale, rx: 2, fill: token("--ink-2") }, nodeLayer);

      cy = centerY;
      lastY = -Infinity;
      R.forEach((n) => {
        const color = n.saved ? colors.saved : n.other ? colors.other : colors.out;
        const h = n.total * scale;
        const band = svg("path", { d: bandPath(xC + nodeW, cy, xR, n.y, h), fill: color, "fill-opacity": 0.22 }, linkLayer);
        const rect = svg("rect", { x: xR, y: n.y, width: nodeW, height: n.h, rx: 2, fill: color }, nodeLayer);
        wire([band, rect], n, "right");
        lastY = label(n, xR + nodeW + 8, "start", lastY);
        cy += h;
      });
    });
  }

  window.Charts = { columns, lines, sankey, money, token, seriesColor };
})();
