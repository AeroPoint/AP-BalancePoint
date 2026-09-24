/* Net worth page: pick accounts, see where money sits, and how balances moved over time.
   The server sends each account's balance at the end of every month (today for this month),
   already rolled forward from the last balance entered with the transactions since. */
(function () {
  const app = document.getElementById("nw-app");
  const dataEl = document.getElementById("nw-data");
  if (!app || !dataEl) return;

  const data = JSON.parse(dataEl.textContent);
  const NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const short = (m) => `${NAMES[Number(m.slice(5, 7)) - 1]} ${m.slice(2, 4)}`;
  const long = (m) => `${NAMES[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
  const money = (v) => Charts.money(v);
  const PRESETS = {
    all: () => true,
    liquid: (a) => !["property", "vehicle", "loan"].includes(a.kind),
    cash: (a) => ["checking", "savings", "cash"].includes(a.kind),
    investments: (a) => ["brokerage", "retirement", "hsa", "crypto"].includes(a.kind),
  };
  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (e) {
        /* storage unavailable: the page still works, it just won't remember */
      }
    },
  };

  const months = data.months;
  const monthIndex = new Map(months.map((m, i) => [m, i]));
  const thisMonth = data.today.slice(0, 7);
  const accounts = data.accounts;
  const valueAt = (a, month) => {
    const i = monthIndex.get(month);
    const v = i === undefined ? null : a.values[i];
    return v == null ? 0 : v;
  };

  // Colors follow the account, not the selection: the biggest asset accounts overall get the slots.
  const bySize = accounts
    .filter((a) => a.side === "asset")
    .map((a) => ({ a, peak: Math.max(0, ...a.values.map((v) => Math.abs(v || 0))) }))
    .sort((x, y) => y.peak - x.peak)
    .map((x) => x.a);
  const slot = new Map(bySize.slice(0, 7).map((a, i) => [a.id, i]));
  const colorOf = (a) => (slot.has(a.id) ? Charts.seriesColor(slot.get(a.id)) : Charts.token("--s-other"));

  // ---------------------------------------------------------------- state
  let excluded = new Set(store.get("ledger.nw.excluded", []));
  let showClosed = store.get("ledger.nw.showClosed", false);
  let range = store.get("ledger.nw.range", "24");
  const chipsEl = document.getElementById("nw-chips");
  const closedToggle = document.getElementById("nw-show-closed");
  const rangeEl = document.getElementById("nw-range");
  closedToggle.checked = showClosed;
  rangeEl.value = range;

  function save() {
    store.set("ledger.nw.excluded", [...excluded]);
    store.set("ledger.nw.showClosed", showClosed);
    store.set("ledger.nw.range", range);
  }

  // ---------------------------------------------------------------- filter chips
  function renderChips() {
    chipsEl.innerHTML = "";
    for (const group of ["Cash", "Investments", "Property & other", "Debts"]) {
      const members = accounts.filter((a) => a.group === group && (showClosed || !a.closed));
      if (!members.length) continue;
      const row = document.createElement("div");
      row.className = "chip-group";
      const title = document.createElement("span");
      title.textContent = group;
      row.appendChild(title);
      for (const a of members) {
        const label = document.createElement("label");
        label.className = "chip-check";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = !excluded.has(a.id);
        box.addEventListener("change", () => {
          if (box.checked) excluded.delete(a.id);
          else excluded.add(a.id);
          save();
          render();
        });
        label.appendChild(box);
        if (a.side === "asset") {
          const key = document.createElement("span");
          key.className = "key";
          key.style.background = colorOf(a);
          label.appendChild(key);
        }
        label.appendChild(document.createTextNode(a.name));
        if (a.closed) {
          const note = document.createElement("small");
          note.textContent = `closed ${short(a.closed)}`;
          label.appendChild(note);
        }
        row.appendChild(label);
      }
      chipsEl.appendChild(row);
    }
    document.getElementById("nw-closed-wrap").hidden = !accounts.some((a) => a.closed);
  }

  // ---------------------------------------------------------------- views
  function render() {
    const chosen = accounts.filter((a) => !excluded.has(a.id));
    const cache = new Map();
    const totalsAt = (month) => {
      if (!cache.has(month)) {
        let own = 0;
        let owe = 0;
        for (const a of chosen) {
          const v = valueAt(a, month);
          if (a.side === "asset") own += v;
          else owe += v;
        }
        cache.set(month, { own, owe, net: own - owe });
      }
      return cache.get(month);
    };
    const shown = range === "all" ? months : months.slice(-Number(range));
    const focus = monthIndex.has(data.month) ? data.month : months[months.length - 1];

    renderStatement(chosen, focus, totalsAt);
    document.getElementById("nw-charts").hidden = !months.length;
    if (!months.length) {
      document.getElementById("nw-table").innerHTML = "";
      return;
    }
    renderSits(chosen, focus);
    Charts.lines(document.getElementById("nw-lines"), {
      labels: shown.map(short),
      titles: shown.map((m) => (m === thisMonth ? "Today" : `End of ${long(m)}`)),
      series: [
        { name: "Own", values: shown.map((m) => totalsAt(m).own) },
        { name: "Owe", values: shown.map((m) => totalsAt(m).owe) },
        { name: "Net worth", values: shown.map((m) => totalsAt(m).net) },
      ],
    });
    renderStack(chosen, shown);
    renderTable(shown, totalsAt);
  }

  function renderStatement(chosen, focus, totalsAt) {
    const el = document.getElementById("nw-statement");
    el.innerHTML = "";
    const h = document.createElement("h1");
    if (!focus) {
      h.textContent = "Enter a balance for each account below to start tracking your net worth.";
      el.appendChild(h);
      return;
    }
    const now = focus === thisMonth;
    const t = totalsAt(focus);
    const b = document.createElement("b");
    b.textContent = money(t.net);
    const picked = excluded.size ? `the ${chosen.length} accounts you picked` : null;
    if (now) h.append(picked ? `Today ${picked} come to ` : "Today your net worth is ", b, ".");
    else h.append(`At the end of ${long(focus)} ${picked ? `${picked} came to` : "your net worth was"} `, b, ".");
    const p = document.createElement("p");
    p.className = "compare";
    let text = now ? `You own ${money(t.own)} and owe ${money(t.owe)}.` : `You owned ${money(t.own)} and owed ${money(t.owe)}.`;
    const idx = months.indexOf(focus);
    if (idx > 0) {
      const diff = t.net - totalsAt(months[idx - 1]).net;
      text = `${diff >= 0 ? "Up" : "Down"} ${money(Math.abs(diff))} since the end of ${long(months[idx - 1])}. ${text}`;
    }
    p.textContent = text;
    el.append(h, p);
  }

  function renderSits(chosen, focus) {
    const el = document.getElementById("nw-sits");
    document.getElementById("nw-sits-title").textContent =
      focus === thisMonth ? "Where it sits today" : `Where it sat at the end of ${long(focus)}`;
    el.innerHTML = "";
    for (const side of ["asset", "liability"]) {
      const rows = chosen
        .filter((a) => a.side === side)
        .map((a) => ({ a, v: valueAt(a, focus) }))
        .filter((r) => Math.abs(r.v) >= 0.5)
        .sort((x, y) => y.v - x.v);
      if (!rows.length) continue;
      const heading = document.createElement("h3");
      heading.className = "sits-head";
      heading.textContent = side === "asset" ? "What you own" : "What you owe";
      const list = document.createElement("ul");
      list.className = "catbars";
      const max = Math.max(...rows.map((r) => Math.abs(r.v)));
      const sideTotal = rows.reduce((s, r) => s + Math.abs(r.v), 0);
      for (const { a, v } of rows) {
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.className = "cb-name";
        name.textContent = a.name;
        const track = document.createElement("span");
        track.className = "cb-track";
        track.dataset.tip = `${a.name}: ${money(v)}`;
        const fill = document.createElement("span");
        fill.className = "cb-fill";
        fill.style.width = `${(Math.abs(v) / max) * 100}%`;
        fill.style.background = side === "asset" ? colorOf(a) : Charts.token("--ink-2");
        track.appendChild(fill);
        const amount = document.createElement("span");
        amount.className = "cb-amt";
        amount.textContent = money(v);
        const share = document.createElement("span");
        share.className = "cb-delta";
        share.textContent = `${Math.round((Math.abs(v) / sideTotal) * 100)}%`;
        li.append(name, track, amount, share);
        list.appendChild(li);
      }
      el.append(heading, list);
    }
    if (!el.children.length) el.textContent = "No balances for the accounts you picked in this month.";
  }

  function renderStack(chosen, shown) {
    const el = document.getElementById("nw-stack");
    const assets = chosen.filter((a) => a.side === "asset");
    const named = bySize.filter((a) => slot.has(a.id) && !excluded.has(a.id));
    const rest = assets.filter((a) => !slot.has(a.id));
    const series = [];
    const colors = [];
    for (const a of named) {
      const values = shown.map((m) => valueAt(a, m));
      if (values.some((v) => v)) {
        series.push({ name: a.name, values });
        colors.push(colorOf(a));
      }
    }
    if (rest.length) {
      const values = shown.map((m) => rest.reduce((s, a) => s + valueAt(a, m), 0));
      if (values.some((v) => v)) {
        series.push({ name: "Other accounts", values });
        colors.push(Charts.token("--s-other"));
      }
    }
    if (!series.length) {
      el.innerHTML = "";
      el.textContent = "None of the accounts you picked hold money in this range.";
      return;
    }
    Charts.columns(el, {
      labels: shown.map(short),
      titles: shown.map((m) => (m === thisMonth ? "Today" : `End of ${long(m)}`)),
      stacked: true,
      series,
      colors,
      height: 280,
    });
  }

  function renderTable(shown, totalsAt) {
    const body = document.getElementById("nw-table");
    body.innerHTML = "";
    for (const m of [...shown].reverse()) {
      const t = totalsAt(m);
      const idx = months.indexOf(m);
      let change = "—";
      if (idx > 0) {
        const diff = t.net - totalsAt(months[idx - 1]).net;
        change = `${diff >= 0 ? "+" : "−"}${money(Math.abs(diff))}`;
      }
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><a href="?month=${m}">${long(m)}${m === thisMonth ? " (today)" : ""}</a></td>` +
        `<td class="num">${money(t.own)}</td><td class="num">${money(t.owe)}</td>` +
        `<td class="num strong">${money(t.net)}</td><td class="num">${change}</td>`;
      body.appendChild(tr);
    }
  }

  // ---------------------------------------------------------------- controls
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      const keep = PRESETS[button.dataset.preset];
      excluded = new Set(accounts.filter((a) => !keep(a)).map((a) => a.id));
      save();
      renderChips();
      render();
    });
  });
  closedToggle.addEventListener("change", () => {
    showClosed = closedToggle.checked;
    save();
    renderChips();
  });
  rangeEl.addEventListener("change", () => {
    range = rangeEl.value;
    save();
    render();
  });

  renderChips();
  render();
})();
