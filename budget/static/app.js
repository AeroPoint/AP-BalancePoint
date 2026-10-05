/* Inline editing: categories, merchant names, bulk categorize, net worth helpers. */
(function () {
  const toast = document.getElementById("toast");
  let toastTimer;

  function showToast(message, actions = [], ms = 8000) {
    toast.querySelector(".toast-msg").textContent = message;
    const box = toast.querySelector(".toast-actions");
    box.innerHTML = "";
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `btn small${action.primary ? " primary" : ""}`;
      button.textContent = action.label;
      button.addEventListener("click", () => {
        toast.hidden = true;
        action.run();
      });
      box.appendChild(button);
    }
    toast.hidden = false;
    clearTimeout(toastTimer);
    if (ms) toastTimer = setTimeout(() => (toast.hidden = true), ms);
  }

  async function postJSON(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Couldn't save (error ${res.status}).`);
    return data;
  }

  function flashRow(row) {
    row.classList.remove("saved-flash");
    void row.offsetWidth;
    row.classList.add("saved-flash");
  }

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---------------------------------------------------------------- transactions: category
  document.querySelectorAll("tr[data-id] .cat-select").forEach((select) => {
    select.addEventListener("change", async () => {
      const row = select.closest("tr");
      const { id, name } = row.dataset;
      const categoryId = select.value || null;
      const categoryName = select.selectedOptions[0] ? select.selectedOptions[0].textContent : "";
      try {
        await postJSON(`/api/transactions/${id}`, { category_id: categoryId });
      } catch (err) {
        return showToast(err.message);
      }
      row.dataset.source = categoryId ? "manual" : "none";
      row.querySelector(".guess")?.remove();
      flashRow(row);
      if (!categoryId) return showToast("Category cleared for this transaction.");
      if (row.dataset.handsort || row.dataset.splitPart) {
        // Sorted by hand, or one part of a split charge: this transaction only.
        return showToast(`Saved this ${name} transaction as ${categoryName}.`);
      }
      showToast(`Saved this ${name} transaction as ${categoryName}.`, [
        {
          label: `Always put ${name} in ${categoryName}`,
          primary: true,
          run: async () => {
            try {
              const data = await postJSON(`/api/transactions/${id}`, { category_id: categoryId, remember: true });
              row.dataset.source = "rule";
              document.querySelectorAll("tr[data-id]").forEach((other) => {
                if (other.dataset.name !== name || ["manual", "sheet"].includes(other.dataset.source)) return;
                other.querySelector(".cat-select").value = categoryId;
                other.querySelector(".guess")?.remove();
                other.dataset.source = "rule";
                flashRow(other);
              });
              showToast(`${name} now always goes to ${categoryName}. Updated ${plural(data.changed, "other transaction")}.`);
            } catch (err) {
              showToast(err.message);
            }
          },
        },
      ]);
    });
  });

  // ---------------------------------------------------------------- transactions: rename
  document.querySelectorAll("tr[data-id] .name-edit").forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest("tr");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "name-input";
      input.value = row.dataset.name;
      input.setAttribute("aria-label", "Merchant name");
      button.hidden = true;
      button.after(input);
      input.focus();
      input.select();

      let finished = false;
      const finish = async (save) => {
        if (finished) return;
        finished = true;
        const value = input.value.trim().replace(/\s+/g, " ");
        const old = row.dataset.name;
        input.remove();
        button.hidden = false;
        button.focus();
        if (!save || !value || value === old) return;
        try {
          await postJSON(`/api/transactions/${row.dataset.id}`, { name: value });
        } catch (err) {
          return showToast(err.message);
        }
        button.textContent = value;
        row.dataset.name = value;
        flashRow(row);
        showToast(`Renamed this transaction to ${value}.`, [
          {
            label: `Rename every “${old}” and remember it`,
            primary: true,
            run: async () => {
              try {
                const data = await postJSON(`/api/transactions/${row.dataset.id}`, { name: value, previous_name: old, remember: true });
                document.querySelectorAll("tr[data-id]").forEach((other) => {
                  if (other.dataset.name !== old) return;
                  other.dataset.name = value;
                  other.querySelector(".name-edit").textContent = value;
                  flashRow(other);
                });
                showToast(`Renamed ${plural(data.changed, "more transaction")}. Future uploads will show “${value}”.`);
              } catch (err) {
                showToast(err.message);
              }
            },
          },
        ]);
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") finish(true);
        if (e.key === "Escape") finish(false);
      });
      input.addEventListener("blur", () => finish(true));
    });
  });

  // ---------------------------------------------------------------- transactions: date it counts on
  async function setDate(row, button, value) {
    try {
      const data = await postJSON(`/api/transactions/${row.dataset.id}`, { date: value });
      if (row.dataset.splitPart) return window.location.reload(); // every part of the charge moved
      const t = data.transaction;
      button.textContent = t.effective_date;
      const note = row.querySelector(".date-note");
      note.hidden = t.effective_date === t.date;
      note.textContent = `${t.date_override ? "set by you" : "nearest 1st"}; bank date ${t.date}`;
      flashRow(row);
      if (value) {
        showToast(`This now counts on ${t.effective_date}.`, [
          { label: "Use the bank date", run: () => setDate(row, button, null) },
        ]);
      } else {
        showToast(`Back to counting on ${t.effective_date}.`);
      }
    } catch (err) {
      showToast(err.message);
    }
  }

  document.querySelectorAll("tr[data-id] .date-edit").forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest("tr");
      const input = document.createElement("input");
      input.type = "date";
      input.value = button.textContent.trim();
      input.setAttribute("aria-label", "Date this counts on");
      button.hidden = true;
      button.after(input);
      input.focus();

      let finished = false;
      const finish = (save) => {
        if (finished) return;
        finished = true;
        const value = input.value;
        const old = button.textContent.trim();
        input.remove();
        button.hidden = false;
        button.focus();
        if (save && value && value !== old) setDate(row, button, value);
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") finish(true);
        if (e.key === "Escape") finish(false);
      });
      input.addEventListener("blur", () => finish(true));
    });
  });

  // ---------------------------------------------------------------- splitting a charge across categories
  // The same math as splits.split_amounts, in cents: known amounts, the rest spread over them in
  // proportion (or to the one blank line), the rounding leftover to the largest line.
  function splitAmounts(totalCents, known) {
    if (known.length < 2) throw new Error("Split into at least two parts.");
    const blanks = known.map((k, i) => (k === null ? i : -1)).filter((i) => i >= 0);
    if (blanks.length > 1) throw new Error("Only one line can leave its amount blank (it takes what's left).");
    if (known.some((k) => k !== null && !(k > 0))) throw new Error("Each amount must be more than zero. Leave one blank to give it what's left.");
    const parts = known.map((k) => (k === null ? null : Math.round(k * 100)));
    const claimed = parts.reduce((s, p) => s + (p || 0), 0);
    if (claimed > totalCents) throw new Error(`The amounts add up to ${(claimed / 100).toFixed(2)}, more than the charge (${(totalCents / 100).toFixed(2)}).`);
    const rest = totalCents - claimed;
    if (blanks.length) {
      if (!rest) throw new Error("Nothing is left for the line without an amount.");
      parts[blanks[0]] = rest;
      return parts;
    }
    if (!rest) return parts;
    const spread = parts.map((p) => (p * totalCents) / claimed);
    const out = spread.map((s) => Math.round(s));
    let biggest = 0;
    spread.forEach((s, i) => { if (s > spread[biggest]) biggest = i; });
    out[biggest] += totalCents - out.reduce((s, p) => s + p, 0);
    return out;
  }

  const splitDialog = document.getElementById("split-dialog");
  if (splitDialog) {
    const lines = splitDialog.querySelector(".split-lines");
    const preview = splitDialog.querySelector(".split-preview");
    const lineTemplate = document.getElementById("split-line");
    const money = (cents) => `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    let current = null;

    const parse = (value) => {
      const s = value.replace(/[$,\s]/g, "");
      return s === "" ? null : Number(s);
    };
    const read = () => [...lines.querySelectorAll(".split-line")].map((line) => ({
      line, category_id: line.querySelector(".split-cat").value, amount: line.querySelector(".split-amt").value.trim(),
    }));

    function update() {
      const rows = read();
      rows.forEach((r) => (r.line.querySelector(".split-final").textContent = ""));
      const totalCents = Math.round(Math.abs(current.total) * 100);
      const sign = current.total < 0 ? "−" : "+";
      try {
        if (rows.some((r) => r.amount && Number.isNaN(parse(r.amount)))) throw new Error("Type amounts like 12.50.");
        const known = rows.map((r) => parse(r.amount));
        const parts = splitAmounts(totalCents, known);
        rows.forEach((r, i) => (r.line.querySelector(".split-final").textContent = `→ ${sign}${money(parts[i])}`));
        const claimed = known.reduce((s, k) => s + (k ? Math.round(k * 100) : 0), 0);
        preview.textContent = claimed < totalCents && !known.includes(null)
          ? `${money(totalCents - claimed)} not typed in is spread over the lines in proportion.`
          : "The parts add up to the charge.";
        preview.classList.remove("error");
        return true;
      } catch (err) {
        preview.textContent = err.message;
        preview.classList.add("error");
        return false;
      }
    }

    function addLine(categoryId = "", amount = "") {
      const line = lineTemplate.content.firstElementChild.cloneNode(true);
      line.querySelector(".split-cat").value = categoryId ? String(categoryId) : "";
      line.querySelector(".split-amt").value = amount;
      line.querySelector(".split-remove").addEventListener("click", () => { line.remove(); update(); });
      line.querySelectorAll("select, input").forEach((el) => el.addEventListener("input", update));
      lines.appendChild(line);
      return line;
    }

    document.querySelectorAll(".split-open").forEach((button) => {
      button.addEventListener("click", () => {
        current = JSON.parse(button.dataset.split);
        splitDialog.querySelector(".split-total").textContent = `${current.total < 0 ? "−" : "+"}${money(Math.round(Math.abs(current.total) * 100))}`;
        lines.innerHTML = "";
        if (current.lines.length) current.lines.forEach((l) => addLine(l.category_id, l.amount.toFixed(2)));
        else { addLine(); addLine(); }
        splitDialog.querySelector(".split-undo").hidden = !current.split;
        update();
        splitDialog.showModal();
        lines.querySelector(".split-cat")?.focus();
      });
    });
    splitDialog.querySelector(".split-add").addEventListener("click", () => { addLine().querySelector(".split-cat").focus(); update(); });
    splitDialog.querySelector(".split-cancel").addEventListener("click", () => splitDialog.close());
    splitDialog.querySelector(".split-save").addEventListener("click", async () => {
      if (!update()) return;
      const rows = read();
      if (rows.some((r) => !r.category_id)) {
        preview.textContent = "Pick a category for each line.";
        preview.classList.add("error");
        return;
      }
      try {
        await postJSON(`/api/transactions/${current.id}/split`, { lines: rows.map((r) => ({ category_id: r.category_id, amount: r.amount })) });
        window.location.reload();
      } catch (err) {
        preview.textContent = err.message;
        preview.classList.add("error");
      }
    });
    splitDialog.querySelector(".split-undo").addEventListener("click", async () => {
      if (!window.confirm("Put this charge back together as one transaction?")) return;
      try {
        await postJSON(`/api/transactions/${current.id}/unsplit`, {});
        window.location.reload();
      } catch (err) {
        preview.textContent = err.message;
        preview.classList.add("error");
      }
    });
  }

  // ---------------------------------------------------------------- categorize page
  document.querySelectorAll("tr[data-merchant]").forEach((row) => {
    const apply = row.querySelector(".apply");
    const select = row.querySelector("select");
    apply.addEventListener("click", async () => {
      if (!select.value) {
        select.focus();
        return showToast("Pick a category first.");
      }
      const categoryName = select.selectedOptions[0].textContent;
      apply.disabled = true;
      try {
        const data = await postJSON("/api/merchants/category", {
          name: row.dataset.merchant,
          category_id: select.value,
          remember: row.querySelector("input[type=checkbox]").checked,
        });
        row.classList.add("done");
        row.querySelector(".status").textContent = `${plural(data.changed, "transaction")} moved`;
        showToast(`${row.dataset.merchant}: ${plural(data.changed, "transaction")} set to ${categoryName}.`);
      } catch (err) {
        apply.disabled = false;
        showToast(err.message);
      }
    });
  });

  // ---------------------------------------------------------------- net worth
  document.getElementById("copy-last")?.addEventListener("click", () => {
    let filled = 0;
    document.querySelectorAll("input[data-last]").forEach((input) => {
      if (!input.value) {
        input.value = input.dataset.last;
        filled += 1;
      }
    });
    showToast(filled ? `Filled ${plural(filled, "blank")} with the estimates. Change any that differ from the bank, then save.` : "No blank balances to fill.");
  });

  // ---------------------------------------------------------------- one-off purchases
  document.querySelectorAll("tr[data-id] .oneoff-check").forEach((box) => {
    box.addEventListener("change", async () => {
      const row = box.closest("tr");
      try {
        await postJSON(`/api/transactions/${row.dataset.id}`, { one_off: box.checked });
        flashRow(row);
        showToast(box.checked ? "Marked as a one-off: it no longer counts against fixed or flexible spending." : "Counts as normal spending again.");
      } catch (err) {
        box.checked = !box.checked;
        showToast(err.message);
      }
    });
  });

  // ---------------------------------------------------------------- category checkboxes (Rental property...)
  document.querySelectorAll("tr[data-id] .flag-check").forEach((box) => {
    box.addEventListener("change", async () => {
      const row = box.closest("tr");
      try {
        await postJSON(`/api/transactions/${row.dataset.id}`, { flag: box.checked ? "yes" : null });
        row.querySelector(".flag-dismiss")?.remove();
        flashRow(row);
      } catch (err) {
        box.checked = !box.checked;
        showToast(err.message);
      }
    });
  });
  document.querySelectorAll("tr[data-id] .flag-dismiss").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("tr");
      try {
        await postJSON(`/api/transactions/${row.dataset.id}`, { flag: null });
        button.remove();
        flashRow(row);
      } catch (err) {
        showToast(err.message);
      }
    });
  });

  // The Overview's large-charges list: the month's numbers change, so reload to show them.
  document.querySelectorAll("tr[data-id] .oneoff-toggle").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await postJSON(`/api/transactions/${button.closest("tr").dataset.id}`, { one_off: button.dataset.on === "1" });
        window.location.reload();
      } catch (err) {
        button.disabled = false;
        showToast(err.message);
      }
    });
  });

  // Merchant dictionary: "Sort by hand" / "Can be split" save their entry as soon as they're ticked.
  document.querySelectorAll("input[data-autosave]").forEach((box) => {
    box.addEventListener("change", () => box.form?.requestSubmit());
  });

  // ---------------------------------------------------------------- confirmations
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Slow requests (the bank sync talks to the Bridge): say so and don't send twice.
  document.querySelectorAll("form[data-busy]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
      const button = form.querySelector("button");
      if (!button) return;
      if (button.disabled) return e.preventDefault();
      setTimeout(() => { button.disabled = true; button.textContent = form.dataset.busy; });
    });
  });
})();

// Phone tab bar: the More sheet closes on a tap anywhere else.
document.addEventListener("pointerdown", (e) => {
  const more = document.querySelector(".tab-more[open]");
  if (more && !more.contains(e.target)) more.removeAttribute("open");
});

// Phone page intros are cut to two lines; a tap shows the rest.
document.querySelectorAll(".page-head .sub").forEach((el) => {
  el.addEventListener("click", (e) => {
    if (e.target.closest("a, button, input, select")) return;
    el.classList.toggle("open");
  });
  el.title = "Tap to read more";
});
