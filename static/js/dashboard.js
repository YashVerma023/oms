// Dashboard pivot: algo > server > subcategory > user.
// The whole tree arrives in one request; expanding, filtering, sorting and
// paging are all local.
(function () {
  "use strict";

  var cfg = window.OMP_PIVOT;
  if (!cfg) return;

  var $ = function (id) { return document.getElementById(id); };
  var F = window.OMPFilter;

  var COLUMNS = [
    { name: "name", type: "text" },
    { name: "users", type: "number" },
    { name: "CC", type: "number" },
    { name: "MS", type: "number" }
  ];

  var LABEL = { algo: "Algo - ", server: "Server - ", subcategory: "", user: "" };

  var state = {
    tree: [],
    open: {},          // node path -> true when expanded
    filters: {},       // column -> raw filter text
    global: "",
    sortCol: "",
    sortDir: 1,
    page: 1,
    pageSize: 50,
    top: []            // top-level nodes that survive the filters
  };

  function status(message, kind) {
    var el = $("pivotStatus");
    el.textContent = message || "";
    el.className = "table-status" + (kind ? " " + kind : "");
  }

  function label(node) {
    return (LABEL[node.kind] || "") + node.name;
  }

  // Users are not categorised individually, so their CC/MS cells stay empty
  // rather than reading a misleading 0.
  function value(node, column) {
    if (column === "name") return label(node);
    if (node.kind === "user" && column !== "users") return null;
    return node[column];
  }

  // ---- filtering -----------------------------------------------------------

  function selfMatch(node) {
    if (state.global && !F.text(label(node), state.global)) return false;

    return COLUMNS.every(function (c) {
      return F.cell(value(node, c.name), state.filters[c.name] || "",
                    c.type === "number");
    });
  }

  // A node stays visible if it matches, or if any descendant does - otherwise
  // searching for a user id would hide the algo it sits under.
  function keep(node) {
    if (selfMatch(node)) return true;
    return (node.children || []).some(keep);
  }

  function filtering() {
    return !!state.global || COLUMNS.some(function (c) {
      return (state.filters[c.name] || "") !== "";
    });
  }

  // ---- sorting -------------------------------------------------------------

  function sortTree(nodes) {
    if (!state.sortCol) return;
    var numeric = state.sortCol !== "name";

    nodes.sort(function (a, b) {
      var x = value(a, state.sortCol), y = value(b, state.sortCol);
      if (F.isBlank(x)) return 1;               // blanks always sink
      if (F.isBlank(y)) return -1;
      if (numeric) return (parseFloat(x) - parseFloat(y)) * state.sortDir;
      return String(x).localeCompare(String(y)) * state.sortDir;
    });

    nodes.forEach(function (n) { sortTree(n.children || []); });
  }

  function markSortHeaders() {
    document.querySelectorAll(".th-sort").forEach(function (btn) {
      var on = btn.dataset.col === state.sortCol;
      btn.classList.toggle("sorted", on);
      btn.classList.toggle("desc", on && state.sortDir === -1);
    });
  }

  // ---- rendering -----------------------------------------------------------

  function caret(path, node, deep) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "caret" + (!deep && state.open[path] ? " open" : "");
    btn.title = deep ? "Expand everything below" : "Expand";
    btn.innerHTML = deep
      ? '<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6M6 4l6 6 6-6"/></svg>'
      : '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>';

    btn.addEventListener("click", function () {
      if (deep) {
        var want = !state.open[path];
        setBranch(node, path, want);
      } else {
        state.open[path] = !state.open[path];
      }
      render();
    });
    return btn;
  }

  function setBranch(node, path, want) {
    if (!(node.children || []).length) return;
    state.open[path] = want;
    node.children.forEach(function (child) {
      setBranch(child, path + "/" + child.name, want);
    });
  }

  function row(node, depth, path) {
    var tr = document.createElement("tr");
    tr.className = "pivot-row level-" + depth + " kind-" + node.kind;

    var kids = node.children || [];
    var tdExpand = document.createElement("td");
    tdExpand.className = "col-expand";

    if (kids.length) {
      var tools = document.createElement("div");
      tools.className = "expand-tools";
      tools.appendChild(caret(path, node, false));
      // A second chevron only earns its place when there is more than one
      // level left to open.
      if (kids.some(function (k) { return (k.children || []).length; })) {
        tools.appendChild(caret(path, node, true));
      }
      tdExpand.appendChild(tools);
    }
    tr.appendChild(tdExpand);

    var tdName = document.createElement("td");
    var chip = document.createElement("span");
    chip.className = "chip pivot-name";
    chip.style.marginLeft = (depth * 18) + "px";
    chip.textContent = label(node);
    tdName.appendChild(chip);
    tr.appendChild(tdName);

    ["users", "CC", "MS"].forEach(function (key) {
      var td = document.createElement("td");
      td.className = "num";
      var v = value(node, key);
      var cell = document.createElement("span");
      cell.className = "chip";
      cell.textContent = v === null ? "-" : v;
      td.appendChild(cell);
      tr.appendChild(td);
    });

    return tr;
  }

  function walk(nodes, depth, prefix, out) {
    var open = filtering();     // a search auto-opens the branches that hit

    nodes.forEach(function (node) {
      if (!keep(node)) return;
      var path = prefix + "/" + node.name;
      out.push(row(node, depth, path));

      var kids = node.children || [];
      if (kids.length && (state.open[path] || open)) {
        walk(kids, depth + 1, path, out);
      }
    });
  }

  function pageCount() {
    return Math.max(1, Math.ceil(state.top.length / state.pageSize));
  }

  function render() {
    var body = $("pivotBody");
    body.textContent = "";

    var pages = pageCount();
    if (state.page > pages) state.page = pages;
    var start = (state.page - 1) * state.pageSize;
    var slice = state.top.slice(start, start + state.pageSize);

    var out = [];
    walk(slice, 0, "", out);

    var frag = document.createDocumentFragment();
    out.forEach(function (tr) { frag.appendChild(tr); });
    body.appendChild(frag);

    $("pivotEmpty").hidden = out.length !== 0;
    $("pivotCount").textContent = state.top.length === state.tree.length
      ? state.tree.length + " algo(s)."
      : state.top.length + " of " + state.tree.length + " algo(s).";
    $("pivotPageLabel").textContent = "Page " + state.page + " of " + pages;
    $("pivotFirst").disabled = $("pivotPrev").disabled = state.page === 1;
    $("pivotNext").disabled = $("pivotLast").disabled = state.page === pages;
  }

  function refresh() {
    state.top = state.tree.filter(keep);
    state.page = 1;
    render();
  }

  function setAll(want) {
    state.open = {};
    if (want) {
      state.tree.forEach(function (node) { setBranch(node, "/" + node.name, true); });
    }
    render();
  }

  // ---- export --------------------------------------------------------------

  // Exports what is on screen: filters applied, every level, page ignored.
  function exportCsv() {
    var lines = ["level,name,users,CC,MS"];

    (function walkAll(nodes) {
      nodes.forEach(function (node) {
        if (!keep(node)) return;
        var name = label(node).replace(/"/g, '""');
        var cc = value(node, "CC"), ms = value(node, "MS");
        lines.push([node.kind, '"' + name + '"', node.users,
                    cc === null ? "" : cc, ms === null ? "" : ms].join(","));
        walkAll(node.children || []);
      });
    })(state.top);

    var url = URL.createObjectURL(
      new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" }));
    var a = document.createElement("a");
    a.href = url;
    a.download = "dashboard.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  // Say which DTE mode is in force, and what it hides. Without this the counts
  // look wrong rather than filtered.
  function showMode(data) {
    var el = $("pivotMode");
    if (!el || !data.mode) return;

    var why = data.source === "manual" ? "pinned"
            : data.source === "schedule" ? data.weekday
            : "no schedule";
    el.textContent = "Mode " + data.mode + " (" + why + "): showing "
      + (data.dte || "") + ".";
  }

  // ---- load ----------------------------------------------------------------

  function load() {
    var picker = $("pivotDate");
    var date = picker ? picker.value : cfg.today;
    status("Loading...");

    fetch(cfg.dataUrl + (date ? "?date=" + encodeURIComponent(date) : ""),
          { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (data) {
        state.tree = data.rows || [];
        state.open = {};
        sortTree(state.tree);
        showMode(data);
        $("totUsers").textContent = data.totals.users;
        $("totCC").textContent = data.totals.CC;
        $("totMS").textContent = data.totals.MS;
        refresh();
        status(data.totals.users
          ? "Showing " + data.date
          : "No data for " + data.date, data.totals.users ? "" : "warn");
      })
      .catch(function (err) {
        console.error(err);
        state.tree = [];
        refresh();
        status(err.message, "warn");
      });
  }

  // ---- wiring --------------------------------------------------------------

  $("pivotSearch").addEventListener("input", function (e) {
    state.global = e.target.value.trim();
    refresh();
  });

  document.querySelectorAll(".col-filter").forEach(function (input) {
    input.addEventListener("input", function () {
      state.filters[input.dataset.col] = input.value;
      refresh();
    });
  });

  document.querySelectorAll(".th-sort").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var col = btn.dataset.col;
      state.sortDir = (state.sortCol === col && state.sortDir === 1) ? -1 : 1;
      state.sortCol = col;
      markSortHeaders();
      sortTree(state.tree);
      refresh();
    });
  });

  $("pivotPageSize").addEventListener("change", function (e) {
    state.pageSize = parseInt(e.target.value, 10) || 50;
    state.page = 1;
    render();
  });

  $("pivotFirst").addEventListener("click", function () { state.page = 1; render(); });
  $("pivotPrev").addEventListener("click", function () { state.page--; render(); });
  $("pivotNext").addEventListener("click", function () { state.page++; render(); });
  $("pivotLast").addEventListener("click", function () {
    state.page = pageCount();
    render();
  });

  $("btnExpand").addEventListener("click", function () { setAll(true); });
  $("btnCollapse").addEventListener("click", function () { setAll(false); });
  $("btnPivotExport").addEventListener("click", exportCsv);
  $("btnPivotReload").addEventListener("click", load);

  if ($("pivotDate")) {
    $("pivotDate").addEventListener("change", load);
    var wrap = $("pivotDateWrap");
    if (wrap) {
      wrap.addEventListener("click", function (e) {
        var p = $("pivotDate");
        if (e.target === p) return;
        if (typeof p.showPicker === "function") {
          try { p.showPicker(); } catch (err) { p.focus(); }
        }
      });
    }
  }

  load();
})();
