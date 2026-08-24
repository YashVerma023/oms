// Client-side grid: global search, per-column filters, sort, paging, CSV export.
// Rows are fetched once (server caps at MAX_ROWS) and filtered in memory.
(function () {
  "use strict";

  var cfg = window.OMP_TABLE;
  if (!cfg) return;

  var state = {
    rows: [],
    view: [],
    columns: [],
    filters: {},      // column -> raw filter text
    choices: {},      // column -> Set of ticked values (empty Set = no filter)
    global: "",
    sortCol: null,
    sortDir: 1,
    page: 1,
    pageSize: 1000,   // must match the selected <option> in shared/table.html
    date: cfg.selectedDate || "",
    selected: new Set()
  };

  var $ = function (id) { return document.getElementById(id); };
  var body = $("tableBody");
  var empty = $("tableEmpty");

  // ---- display formatting --------------------------------------------------

  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  // "2026-02-02" (or "2026-02-02 00:00:00") -> "02-FEB-2026".
  function formatDate(value) {
    if (value === null || value === "") return "";
    var m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return String(value);
    var month = MONTHS[parseInt(m[2], 10) - 1];
    if (!month) return String(value);
    return m[3] + "-" + month + "-" + m[1];
  }

  // Precomputed once per load, so rendering and filtering never re-parse.
  // Sorting deliberately keeps using the raw value: ISO dates sort
  // chronologically as plain strings, "02-FEB-2026" would not.
  function buildDisplayRow(row) {
    state.columns.forEach(function (c) {
      if (c.format === "date") row["__display__" + c.name] = formatDate(row[c.name]);
    });
  }

  function buildDisplay() {
    if (!state.columns.some(function (c) { return c.format === "date"; })) return;
    state.rows.forEach(buildDisplayRow);
  }

  function display(row, column) {
    var key = "__display__" + column.name;
    return key in row ? row[key] : row[column.name];
  }

  // ---- filtering -----------------------------------------------------------

  // Filter semantics live in filters.js so the pivot behaves identically.
  var numericMatch = window.OMPFilter.numeric;
  var isBlank = window.OMPFilter.isBlank;
  var textMatch = window.OMPFilter.text;

  function applyFilters() {
    var g = state.global.trim().toLowerCase();

    state.view = state.rows.filter(function (row) {
      if (g) {
        // Search what the user can see: "FEB" should match a formatted date.
        var hit = state.columns.some(function (c) {
          return textMatch(display(row, c), g);
        });
        if (!hit) return false;
      }

      return state.columns.every(function (c) {
        var picked = state.choices[c.name];
        if (picked && picked.size) {
          if (!picked.has(row[c.name] === null ? "" : String(row[c.name]))) return false;
        }

        var raw = state.filters[c.name] || "";
        var expr = raw.trim();

        // Two shortcuts, on every column type:
        //   " " (spaces only) -> only blank/NULL cells
        //   "/"               -> only cells that have a value
        if (raw !== "" && expr === "") return isBlank(row[c.name]);
        if (expr === "/") return !isBlank(row[c.name]);

        if (!expr) return true;
        return c.type === "number"
          ? numericMatch(row[c.name], expr)
          : textMatch(display(row, c), expr);
      });
    });

    if (state.sortCol) {
      var col = state.columns.find(function (c) { return c.name === state.sortCol; });
      var numeric = col && col.type === "number";

      state.view.sort(function (a, b) {
        var x = a[state.sortCol], y = b[state.sortCol];
        if (x === null || x === "") return 1;   // blanks always sink
        if (y === null || y === "") return -1;
        if (numeric) return (parseFloat(x) - parseFloat(y)) * state.sortDir;
        return String(x).localeCompare(String(y)) * state.sortDir;
      });
    }

    state.page = 1;
    render();
  }

  // ---- rendering -----------------------------------------------------------

  function pageCount() {
    return Math.max(1, Math.ceil(state.view.length / state.pageSize));
  }

  function cellClass(col, value) {
    if (col.type !== "number" || value === null || value === "") return "";
    var n = parseFloat(value);
    if (isNaN(n)) return "";
    // Only P&L-style columns carry colour; plain counts stay neutral.
    if (/^(mtm|mtm_p|mtm%)$/i.test(col.name)) return n < 0 ? "neg" : "pos";
    return "";
  }

  function render() {
    var start = (state.page - 1) * state.pageSize;
    var slice = state.view.slice(start, start + state.pageSize);

    body.textContent = "";
    var frag = document.createDocumentFragment();

    slice.forEach(function (row, i) {
      var tr = document.createElement("tr");
      var key = start + i;

      var tdSel = document.createElement("td");
      tdSel.className = "col-select";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.selected.has(key);
      cb.addEventListener("change", function () {
        cb.checked ? state.selected.add(key) : state.selected.delete(key);
        updateCounts();
      });
      tdSel.appendChild(cb);
      tr.appendChild(tdSel);

      if (cfg.editUrl) {
        var tdEdit = document.createElement("td");
        tdEdit.className = "col-edit";
        var link = document.createElement("a");
        link.className = "row-edit";
        link.title = "Edit";
        link.href = cfg.editUrl.replace("__KEY__", encodeURIComponent(row[cfg.editKey]));
        link.innerHTML =
          '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z"/></svg>';
        tdEdit.appendChild(link);
        tr.appendChild(tdEdit);
      }

      state.columns.forEach(function (c) {
        var td = document.createElement("td");
        var v = display(row, c);
        var cls = cellClass(c, row[c.name]);
        if (c.type === "number" && !c.format) td.classList.add("num");

        if (cls) {
          var chip = document.createElement("span");
          chip.className = "chip " + cls;
          chip.textContent = v === null ? "" : v;
          td.appendChild(chip);
        } else {
          td.textContent = v === null ? "" : v;
        }

        if (isEditable(c)) {
          td.classList.add("editable");
          td.title = "Double-click to edit";
          td.addEventListener("dblclick", function () {
            beginEdit(td, row, c);
          });
        }
        tr.appendChild(td);
      });

      frag.appendChild(tr);
    });

    body.appendChild(frag);
    empty.hidden = state.view.length !== 0;
    updateCounts();

    $("pageLabel").textContent = "Page " + state.page + " of " + pageCount();
    $("pgFirst").disabled = $("pgPrev").disabled = state.page === 1;
    $("pgNext").disabled = $("pgLast").disabled = state.page === pageCount();
  }

  function updateCounts() {
    $("selectedCount").textContent =
      state.selected.size + " of " + state.view.length + " row(s) selected.";

    // Left enabled with nothing selected: a disabled button gives no feedback,
    // which reads as "delete is broken" rather than "pick some rows first".
    var del = $("btnDelete");
    if (del) del.disabled = !cfg.deleteKey.length;
    // No delete control means the selection is not actionable.
    if (!del) $("selectedCount").textContent = state.view.length + " row(s).";
  }

  function setStatus(message, kind) {
    var el = $("tableStatus");
    if (!el) return;
    el.textContent = message || "";
    el.className = "table-status" + (kind ? " " + kind : "");
    if (message) {
      clearTimeout(setStatus.timer);
      setStatus.timer = setTimeout(function () {
        el.textContent = "";
        el.className = "table-status";
      }, 6000);
    }
  }

  // ---- delete --------------------------------------------------------------

  function deleteSelected() {
    var keys = Array.from(state.selected)
      .map(function (i) { return state.view[i]; })
      .filter(Boolean)
      .map(function (row) {
        return cfg.deleteKey.map(function (c) { return row[c]; });
      });

    if (!keys.length) {
      setStatus("Tick the checkbox on the rows you want to delete first.", "warn");
      return;
    }

    var what = keys.length === 1 ? "1 row" : keys.length + " rows";
    if (!window.confirm("Delete " + what + " permanently? This cannot be undone.")) return;

    var button = $("btnDelete");
    button.disabled = true;
    setStatus("Deleting " + what + "...");

    fetch(cfg.deleteUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ keys: keys })
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (body) {
        state.selected.clear();
        setStatus("Deleted " + body.deleted + " row(s).", "ok");
        return load(true);
      })
      .catch(function (err) {
        console.error("Delete failed:", err);
        setStatus("Delete failed: " + err.message, "warn");
      })
      .finally(function () { button.disabled = false; });
  }

  // ---- choice filters ------------------------------------------------------

  // Tick-box filter for columns with a small set of repeated values. The menu
  // itself lives in static/js/choice-filter.js, shared with the Setup tab.
  function buildChoiceFilters() {
    document.querySelectorAll(".choice").forEach(function (holder) {
      var name = holder.dataset.col;
      var picked = state.choices[name] || (state.choices[name] = new Set());

      var counts = {};
      state.rows.forEach(function (row) {
        var v = row[name] === null ? "" : String(row[name]);
        counts[v] = (counts[v] || 0) + 1;
      });

      window.OMPChoice.build(holder, {
        label: name,
        counts: counts,
        selected: picked,
        onChange: applyFilters
      });
    });
  }

  // ---- inline editing ------------------------------------------------------

  var editing = null;   // guards against opening two editors at once

  function isEditable(column) {
    var meta = cfg.editable;
    if (!meta || !cfg.fieldUrl) return false;
    if (meta.readonly.indexOf(column.name) !== -1) return false;
    if (column.computed) return false;
    return column.name in meta.types;
  }

  function beginEdit(td, row, column) {
    if (editing) return;
    editing = td;

    var meta = cfg.editable;
    var original = row[column.name];
    var options = meta.options[column.name];
    var input;

    if (options && options.length) {
      input = document.createElement("select");
      input.appendChild(new Option("—", ""));
      options.forEach(function (o) {
        var opt = new Option(o, o);
        if (original !== null && String(original).toLowerCase() === o.toLowerCase()) {
          opt.selected = true;
        }
        input.appendChild(opt);
      });
    } else {
      input = document.createElement("input");
      input.type = meta.types[column.name] === "number" ? "number" : "text";
      if (input.type === "number") input.step = "any";
      input.value = original === null ? "" : original;
    }

    input.className = "cell-input";
    td.textContent = "";
    td.appendChild(input);
    input.focus();
    if (input.select) input.select();

    var settled = false;

    function finish(save) {
      if (settled) return;
      settled = true;
      editing = null;

      var value = input.value;
      if (!save || String(value) === String(original === null ? "" : original)) {
        render();                       // discard the editor, keep the data
        return;
      }
      commit(row, column, value, td);
    }

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", function () { finish(true); });
    // A select fires change before blur; commit immediately on choice.
    if (input.tagName === "SELECT") {
      input.addEventListener("change", function () { finish(true); });
    }
  }

  function commit(row, column, value, td) {
    td.classList.add("saving");

    fetch(cfg.fieldUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        key: row[cfg.editable.pk],
        column: column.name,
        value: value
      })
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (body) {
        // Replace the whole row: a rule may have changed sibling columns
        // (linked running state, algo) and derived ones (ml_pct).
        Object.keys(body.row).forEach(function (k) { row[k] = body.row[k]; });
        buildDisplayRow(row);
        applyFilters();
      })
      .catch(function (err) {
        console.error("Could not save:", err);
        window.alert("Could not save: " + err.message);
        render();
      });
  }

  // ---- events --------------------------------------------------------------

  function bind() {
    $("globalSearch").addEventListener("input", function (e) {
      state.global = e.target.value;
      applyFilters();
    });

    document.querySelectorAll(".col-filter").forEach(function (input) {
      input.addEventListener("input", function () {
        state.filters[input.dataset.col] = input.value;
        applyFilters();
      });
    });

    document.querySelectorAll(".th-sort").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var col = btn.dataset.col;
        state.sortDir = state.sortCol === col ? -state.sortDir : 1;
        state.sortCol = col;
        document.querySelectorAll(".th-sort").forEach(function (b) {
          b.classList.toggle("sorted", b === btn);
          b.classList.toggle("desc", b === btn && state.sortDir === -1);
        });
        applyFilters();
      });
    });

    $("selectAll").addEventListener("change", function (e) {
      var start = (state.page - 1) * state.pageSize;
      var end = Math.min(start + state.pageSize, state.view.length);
      for (var i = start; i < end; i++) {
        e.target.checked ? state.selected.add(i) : state.selected.delete(i);
      }
      render();
    });

    $("pageSize").addEventListener("change", function (e) {
      state.pageSize = parseInt(e.target.value, 10);
      state.page = 1;
      render();
    });

    $("pgFirst").addEventListener("click", function () { state.page = 1; render(); });
    $("pgPrev").addEventListener("click", function () { state.page--; render(); });
    $("pgNext").addEventListener("click", function () { state.page++; render(); });
    $("pgLast").addEventListener("click", function () { state.page = pageCount(); render(); });

    if ($("datePick")) {
      // Clicking anywhere on the control opens the native calendar, rather
      // than only the small icon inside the input.
      var wrap = $("datePickWrap");
      if (wrap) {
        wrap.addEventListener("click", function (e) {
          var picker = $("datePick");
          if (e.target === picker) return;
          if (typeof picker.showPicker === "function") {
            try { picker.showPicker(); } catch (err) { picker.focus(); }
          } else {
            picker.focus();
          }
        });
      }

      $("datePick").addEventListener("change", function (e) {
        state.date = e.target.value;
        state.selected.clear();
        setStatus(state.date ? "Loading " + state.date + "..." : "Loading...");
        load(true).then(function () {
          setStatus(state.view.length
            ? "Showing " + state.date
            : "No data for " + state.date, state.view.length ? "" : "warn");
        });
      });
    }

    $("btnReload").addEventListener("click", reconcileThenLoad);
    $("btnExport").addEventListener("click", exportCsv);
    if ($("btnDelete")) $("btnDelete").addEventListener("click", deleteSelected);
    if ($("btnExportFiles")) $("btnExportFiles").addEventListener("click", exportFiles);
  }

  // Per-server files, built by the server. See static/js/download-files.js.
  function exportFiles() {
    window.OMPDownload(cfg.exportUrl, setStatus, $("btnExportFiles"));
  }

  function exportCsv() {
    var names = state.columns.map(function (c) { return c.name; });
    var esc = function (v) {
      var s = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };

    // Export what is on screen, formatted dates included.
    var lines = [names.map(esc).join(",")];
    state.view.forEach(function (row) {
      lines.push(state.columns.map(function (c) {
        return esc(display(row, c));
      }).join(","));
    });

    var url = URL.createObjectURL(
      new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" }));
    var a = document.createElement("a");
    a.href = url;
    a.download = cfg.name + ".csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  // ---- load ----------------------------------------------------------------

  // `manual` marks a click on Refresh: current filters and sort are kept, but
  // the rows are re-fetched. Derived columns (Dte, and anything else in a
  // page's `computed` map) are SQL expressions evaluated per request, so a
  // refetch recalculates them against today's date.
  // On pages that declare one, Refresh first asks the server to re-apply the
  // business rules across the whole table, then refetches. For All Users that
  // realigns server / Running Type / Running Days and forces algo to 0 on any
  // row that drifted - including rows changed outside the portal.
  function reconcileThenLoad() {
    var button = $("btnReload");
    button.disabled = true;
    button.classList.add("spinning");

    var step = cfg.reconcileUrl
      // The date goes with it: on a dated page the endpoint rebuilds the day
      // on screen, not whatever today happens to be.
      ? fetch(cfg.reconcileUrl, {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ date: state.date || "" })
        })
          .then(function (r) {
            return r.json().then(function (body) {
              if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
              return body;
            });
          })
          .then(function (result) {
            if (result.message) setStatus(result.message, "ok");
            else if (result.updated) {
              console.info("Reconciled " + result.updated + " of " +
                           result.checked + " row(s).");
            }
            return result;
          })
          .catch(function (err) {
            // A failed reconcile must not stop the refresh, but it must be
            // visible rather than buried in the console.
            console.error("Reconcile failed:", err);
            setStatus(err.message, "warn");
          })
      : Promise.resolve();

    return step.then(function () { return load(true); });
  }

  function load(manual) {
    var button = $("btnReload");
    if (manual) {
      button.disabled = true;
      button.classList.add("spinning");
    }

    // no-store: a cached 200 would silently return yesterday's derived values.
    var url = cfg.dataUrl + (state.date ? "?date=" + encodeURIComponent(state.date) : "");

    fetch(url, { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        state.columns = data.columns;
        state.rows = data.rows;
        if (data.date) {
          state.date = data.date;
          var picker = $("datePick");
          if (picker && picker.value !== data.date) picker.value = data.date;
        }
        // An empty day is ambiguous: nothing uploaded, or uploaded under the
        // date the sheet carries. Say which.
        empty.textContent = (!data.rows.length && data.latest && data.latest !== state.date)
          ? "Nothing for " + state.date + ". The most recent data is dated "
            + data.latest + " - choose that date above."
          : "No data to display.";

        state.selected.clear();
        buildDisplay();
        buildChoiceFilters();
        applyFilters();
      })
      .catch(function (err) {
        console.error("Failed to load table data:", err);
        empty.textContent = "Could not load data. See server logs.";
        empty.hidden = false;
      })
      .finally(function () {
        button.disabled = false;
        button.classList.remove("spinning");
      });
  }

  // Exposed for tests/console; the filter parser is the only tricky logic here.
  window.OMP_TABLE_FILTERS = {
    numericMatch: numericMatch,
    textMatch: textMatch,
    formatDate: formatDate,
    isBlank: isBlank
  };

  bind();
  load();
})();
