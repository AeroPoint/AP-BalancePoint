/* Bankroll page: the running-result chart and the Log a session form. */
(function () {
  // ---------------------------------------------------------------- chart
  const dataEl = document.getElementById("bj-data");
  const chartEl = document.getElementById("bj-run");
  if (dataEl && chartEl) {
    const rows = JSON.parse(dataEl.textContent);
    if (rows.length) {
      const label = (d) => new Date(`${d}T12:00`).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
      Charts.lines(chartEl, {
        labels: rows.map((r) => label(r.date)),
        titles: rows.map((r) => `${r.location}, ${new Date(`${r.date}T12:00`).toLocaleDateString(undefined, { dateStyle: "medium" })}`),
        series: [
          { name: "Actual", values: rows.map((r) => r.av) },
          { name: "Expected", values: rows.map((r) => r.ev) },
        ],
      });
    }
  }

  // ---------------------------------------------------------------- form: more tables
  const tables = document.getElementById("bj-tables");
  const addButton = document.getElementById("bj-add-table");
  if (tables && addButton) {
    addButton.addEventListener("click", () => {
      const sets = tables.querySelectorAll(".bj-table");
      const last = sets[sets.length - 1];
      const next = sets.length;
      const copy = last.cloneNode(true);
      copy.dataset.index = next;
      copy.querySelector("legend").textContent = `Table ${next + 1}`;
      copy.querySelectorAll("[name]").forEach((el) => {
        el.name = el.name.replace(/^table\d+-/, `table${next}-`);
        // A second table at the same visit usually keeps the house rules; its time and EV are its own.
        if (/-(hours|ev_hour|ev_total)$/.test(el.name)) el.value = "";
      });
      tables.appendChild(copy);
      copy.querySelector("input").focus();
    });
  }

  // ---------------------------------------------------------------- form: a casino's usual game
  // Picking a casino you've played fills the first table with that casino's last game, if it's empty.
  const lastEl = document.getElementById("bj-last-rules");
  const place = document.querySelector('.bj-form [name="location"]');
  if (lastEl && place && tables) {
    const last = JSON.parse(lastEl.textContent);
    place.addEventListener("change", () => {
      const rules = last[place.value];
      if (!rules) return;
      const first = tables.querySelector(".bj-table");
      const fields = [...first.querySelectorAll("[name]")].filter((el) => !/-(hours|ev_hour|ev_total)$/.test(el.name));
      if (fields.some((el) => el.value)) return;
      for (const el of fields) {
        const key = el.name.replace(/^table\d+-/, "");
        if (rules[key] != null) el.value = rules[key];
      }
      const note = first.querySelector(".bj-rules-edit .muted");
      if (note) note.textContent = `Filled in from your last visit to ${place.value}: check they still hold`;
    });
  }
})();

// A link to an older session (after saving it, say) shows the whole list.
(function () {
  const target = location.hash && document.querySelector(location.hash);
  if (target && target.classList.contains("bj-older")) {
    document.getElementById("sessions").classList.add("all");
    target.scrollIntoView();
  }
})();
