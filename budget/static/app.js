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

  // ---------------------------------------------------------------- confirmations
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });
})();
