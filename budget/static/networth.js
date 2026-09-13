/* Net worth page: pick accounts, see where money sits, and how balances moved over time. */
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

  // ---------------------------------------------------------------- data
  const accounts = data.accounts.map((a) => ({ ...a, values: new Map(), months: [] }));
  const byId = new Map(accounts.map((a) => [a.id, a]));
  for (const [id, month, amount] of data.balances) {
    const account = byId.get(id);
    if (account) account.values.set(month, amount);
  }
  for (const a of accounts) a.months = [...a.values.keys()].sort();

  function monthRange(from, to) {
    const out = [];
    let [y, m] = from.split("-").map(Number);
    const [ty, tm] = to.split("-").map(Number);
    while (y < ty || (y === ty && m <= tm)) {
      out.push(`${y}-${String(m).padStart(2, "0")}`);
      m += 1;
      if (m > 12) {
        m = 1;
        y += 1;
      }
    }
    return out;
  }

  const recorded = [...new Set(data.balances.map((b) => b[1]))].sort();
  const cap = data.month < data.today ? data.month : data.today;
  const lastRecorded = recorded[recorded.length - 1];
  const allMonths = recorded.length ? monthRange(recorded[0], lastRecorded > cap ? lastRecorded : cap) : [];

  // A balance carries forward until the next one you enter, and counts only while the account is open.
  function valueAt(a, month) {
    if ((a.opened && month < a.opened) || (a.closed && month > a.closed)) return 0;
    let value = 0;
    for (const m of a.months) {
      if (m > month) break;
      value = a.values.get(m);
    }
    return value;
  }

  function lastEntered(a, month) {
    let found = null;
    for (const m of a.months) {
      if (m > month) break;
      found = m;
    }
    return found;
  }

  // Colors follow the account, not the selection: the biggest asset accounts overall get the slots.
  const bySize = accounts
    .filter((a) => a.side === "asset")
    .map((a) => ({ a, peak: Math.max(0, ...[...a.values.values()].map(Math.abs)) }))
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
    const months = range === "all" ? allMonths : allMonths.slice(-Number(range));
    const focus = allMonths.includes(data.month) ? data.month : allMonths[allMonths.length - 1];

    renderStatement(chosen, focus, totalsAt);
    document.getElementById("nw-charts").hidden = !allMonths.length;
    if (!allMonths.length) {
      document.getElementById("nw-table").innerHTML = "";
      return;
    }
    renderSits(chosen, focus);
    Charts.lines(document.getElementById("nw-lines"), {
      labels: months.map(short),
      titles: months.map(long),
      series: [
        { name: "Own", values: months.map((m) => totalsAt(m).own) },
        { name: "Owe", values: months.map((m) => totalsAt(m).owe) },
        { name: "Net worth", values: months.map((m) => totalsAt(m).net) },
      ],
    });
    renderStack(chosen, months);
    renderTable(months, totalsAt);
  }

  function renderStatement(chosen, focus, totalsAt) {
    const el = document.getElementById("nw-statement");
    el.innerHTML = "";
    const h = document.createElement("h1");
    if (!focus) {
      h.textContent = "Enter this month's balances below to start tracking your net worth.";
      el.appendChild(h);
      return;
    }
    const t = totalsAt(focus);
    const b = document.createElement("b");
    b.textContent = money(t.net);
    const who = excluded.size ? `the ${chosen.length} accounts you picked came to` : "your net worth was";
    h.append(`At the end of ${long(focus)} ${who} `, b, ".");
    const p = document.createElement("p");
    p.className = "compare";
    let text = `You owned ${money(t.own)} and owed ${money(t.owe)}.`;
    const idx = allMonths.indexOf(focus);
    if (idx > 0) {
      const diff = t.net - totalsAt(allMonths[idx - 1]).net;
      text = `${diff >= 0 ? "Up" : "Down"} ${money(Math.abs(diff))} since ${long(allMonths[idx - 1])}. ${text}`;
    }
    p.textContent = text;
    el.append(h, p);
  }

  function renderSits(chosen, focus) {
    const el = document.getElementById("nw-sits");
    document.getElementById("nw-sits-title").textContent = `Where it sits in ${long(focus)}`;
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
        const entered = lastEntered(a, focus);
        track.dataset.tip = `${a.name}: ${money(v)}${entered && entered !== focus ? `, last entered ${long(entered)}` : ""}`;
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

  function renderStack(chosen, months) {
    const el = document.getElementById("nw-stack");
    const assets = chosen.filter((a) => a.side === "asset");
    const named = bySize.filter((a) => slot.has(a.id) && !excluded.has(a.id));
    const rest = assets.filter((a) => !slot.has(a.id));
    const series = [];
    const colors = [];
    for (const a of named) {
      const values = months.map((m) => valueAt(a, m));
      if (values.some((v) => v)) {
        series.push({ name: a.name, values });
        colors.push(colorOf(a));
      }
    }
    if (rest.length) {
      const values = months.map((m) => rest.reduce((s, a) => s + valueAt(a, m), 0));
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
    Charts.columns(el, { labels: months.map(short), titles: months.map(long), stacked: true, series, colors, height: 280 });
  }

  function renderTable(months, totalsAt) {
    const body = document.getElementById("nw-table");
    body.innerHTML = "";
    for (const m of [...months].reverse()) {
      const t = totalsAt(m);
      const idx = allMonths.indexOf(m);
      let change = "—";
      if (idx > 0) {
        const diff = t.net - totalsAt(allMonths[idx - 1]).net;
        change = `${diff >= 0 ? "+" : "−"}${money(Math.abs(diff))}`;
      }
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><a href="?month=${m}">${long(m)}</a></td>` +
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
