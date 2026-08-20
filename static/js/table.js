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
    global: "",
    sortCol: null,
    sortDir: 1,
    page: 1,
    pageSize: 100,
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
  function buildDisplay() {
    var formatted = state.columns.filter(function (c) { return c.format === "date"; });
    if (!formatted.length) return;

    state.rows.forEach(function (row) {
      formatted.forEach(function (c) {
        row["__display__" + c.name] = formatDate(row[c.name]);
      });
    });
  }

  function display(row, column) {
    var key = "__display__" + column.name;
    return key in row ? row[key] : row[column.name];
  }

  // ---- filtering -----------------------------------------------------------

  // Numeric filters accept: >10  <50  >=10  <=50  10-20  10,20  or a plain number.
  function numericMatch(value, expr) {
    if (value === null || value === "") return false;
    var num = parseFloat(value);
    if (isNaN(num)) return false;

    return expr.split(";").every(function (part) {
      part = part.trim();
      if (!part) return true;

      var m = part.match(/^(>=|<=|>|<|=)\s*(-?\d*\.?\d+)$/);
      if (m) {
        var n = parseFloat(m[2]);
        switch (m[1]) {
          case ">":  return num > n;
          case "<":  return num < n;
          case ">=": return num >= n;
          case "<=": return num <= n;
          default:   return num === n;
        }
      }

      var range = part.match(/^(-?\d*\.?\d+)\s*[-,]\s*(-?\d*\.?\d+)$/);
      if (range) {
        var lo = parseFloat(range[1]), hi = parseFloat(range[2]);
        return num >= Math.min(lo, hi) && num <= Math.max(lo, hi);
      }

      return String(value).toLowerCase().indexOf(part.toLowerCase()) !== -1;
    });
  }

  function textMatch(value, needle) {
    return String(value === null ? "" : value).toLowerCase()
      .indexOf(needle.toLowerCase()) !== -1;
  }

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
        var expr = (state.filters[c.name] || "").trim();
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

    $("btnReload").addEventListener("click", reconcileThenLoad);
    $("btnExport").addEventListener("click", exportCsv);
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
      ? fetch(cfg.reconcileUrl, { method: "POST", headers: { "Accept": "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function (result) {
            if (result.updated) {
              console.info("Reconciled " + result.updated + " of " +
                           result.checked + " row(s).");
            }
            return result;
          })
          .catch(function (err) {
            // A failed reconcile must not stop the refresh.
            console.error("Reconcile failed:", err);
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
    fetch(cfg.dataUrl, { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        state.columns = data.columns;
        state.rows = data.rows;
        state.selected.clear();
        buildDisplay();
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
    formatDate: formatDate
  };

  bind();
  load();
})();
