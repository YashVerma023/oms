// Data Operation: create a table by hand, or from an uploaded sheet.
//
// The chosen file is kept in memory between "Read the file" and "Create", so
// nothing is stored on the server until the table is actually created.
(function () {
  "use strict";

  var cfg = window.OMP_DATA_OPS;
  if (!cfg) return;

  var $ = function (id) { return document.getElementById(id); };

  var chosenFile = null;      // the File the user picked
  var chosenSheet = "";       // worksheet, for a multi-sheet workbook

  function note(el, message, kind) {
    el.textContent = message || "";
    el.className = "upload-note" + (kind ? " " + kind : "");
  }

  // Not present in every embedding context, and a missing scroll must never
  // look like a failed request.
  function reveal(el) {
    el.hidden = false;
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function typeSelect(value) {
    var select = document.createElement("select");
    Object.keys(cfg.types).forEach(function (key) {
      var option = document.createElement("option");
      option.value = key;
      option.textContent = cfg.types[key].label;
      if (key === value) option.selected = true;
      select.appendChild(option);
    });
    return select;
  }

  function tickbox(checked, role) {
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!checked;
    if (role) box.dataset.role = role;
    return box;
  }

  function cell(row, child) {
    var td = document.createElement("td");
    if (typeof child === "string") td.textContent = child;
    else if (child) td.appendChild(child);
    row.appendChild(td);
    return td;
  }

  // Reads whichever grid is on screen into the list the server expects.
  //
  // The preview grid has two tickboxes per row - include, then key - and
  // carries the column's position in the sheet on the row itself. The manual
  // grid has only the key box and no source position.
  function readGrid(tbody) {
    return [].filter.call(tbody.rows, function (tr) {
      var include = tr.querySelector('input[data-role="include"]');
      return !include || include.checked;
    }).map(function (tr) {
      var boxes = tr.querySelectorAll('input[type="checkbox"]');
      return {
        name: tr.querySelector('input[type="text"]').value.trim(),
        type: tr.querySelector("select").value,
        key: tr.querySelector('input[data-role="key"]').checked,
        index: tr.dataset.index === undefined ? null : Number(tr.dataset.index)
      };
    }).filter(function (c) { return c.name; });
  }

  function post(url, form) {
    return fetch(url, { method: "POST", body: form })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      });
  }

  function showResult(result) {
    $("resultTitle").textContent = "Created " + result.table;
    var parts = [
      result.columns + " column(s)",
      result.keys.length ? "key (" + result.keys.join(", ") + ")" : "no key"
    ];
    if (result.loaded || result.skipped) {
      parts.push(result.loaded + " row(s) loaded");
      if (result.skipped) {
        parts.push(result.skipped + " row(s) skipped - blank or duplicate key");
      }
    }
    $("resultSummary").textContent = parts.join(" · ");
    $("resultDdl").textContent = result.ddl;
    reveal($("resultBlock"));
  }

  // ---- way 1: by hand -------------------------------------------------------

  function addManualRow(name, type) {
    var tr = document.createElement("tr");

    var input = document.createElement("input");
    input.type = "text";
    input.value = name || "";
    input.placeholder = "column_name";
    cell(tr, input);
    cell(tr, typeSelect(type || "text"));

    var keyCell = cell(tr, tickbox(false, "key"));
    keyCell.className = "col-select";

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "row-drop";
    remove.title = "Remove this column";
    remove.textContent = "×";
    remove.addEventListener("click", function () { tr.remove(); });
    cell(tr, remove).className = "col-select";

    $("manualBody").appendChild(tr);
  }

  function createManual() {
    var columns = readGrid($("manualBody"));
    if (!columns.length) {
      note($("manualNote"), "Add at least one column.", "warn");
      return;
    }

    var form = new FormData();
    form.append("table", $("manualTable").value.trim());
    form.append("columns", JSON.stringify(columns));

    $("btnCreateManual").disabled = true;
    note($("manualNote"), "Creating...");

    post(cfg.createUrl, form)
      .then(function (result) {
        note($("manualNote"), "Created " + result.table + ".", "ok");
        $("manualTable").value = "";
        $("manualBody").textContent = "";
        addManualRow();
        showResult(result);
      })
      .catch(function (err) { note($("manualNote"), err.message, "warn"); })
      .finally(function () { $("btnCreateManual").disabled = false; });
  }

  // ---- way 2: from a sheet --------------------------------------------------

  function inspect() {
    if (!chosenFile) {
      note($("sheetNote"), "Choose a CSV or Excel file first.", "warn");
      return;
    }

    var form = new FormData();
    form.append("file", chosenFile);
    if (chosenSheet) form.append("sheet", chosenSheet);

    $("btnInspect").disabled = true;
    note($("sheetNote"), "Reading...");

    post(cfg.inspectUrl, form)
      .then(function (body) {
        // A workbook with several sheets: offer them, and preview the first
        // so something useful is on screen straight away. Nothing is created
        // until Create is pressed, so previewing a sheet is free.
        if (body.sheets) {
          var picker = $("sheetPick");
          picker.textContent = "";
          body.sheets.forEach(function (name) {
            var option = document.createElement("option");
            option.value = option.textContent = name;
            picker.appendChild(option);
          });
          $("sheetPickWrap").hidden = false;

          chosenSheet = body.sheets[0];
          picker.value = chosenSheet;
          note($("sheetNote"),
            "This workbook has " + body.sheets.length +
            " sheets. Choose one and press View to see its columns.");
          return;
        }

        showPreview(body);
        // Keep the reminder of which sheet is on screen; clearing it here
        // would wipe the message the sheet list just set.
        note($("sheetNote"), chosenSheet
          ? "Showing '" + chosenSheet + "' - pick another above to switch."
          : "");
      })
      .catch(function (err) { note($("sheetNote"), err.message, "warn"); })
      .finally(function () { $("btnInspect").disabled = false; });
  }

  function showPreview(body) {
    var tbody = $("previewBody");
    tbody.textContent = "";

    body.columns.forEach(function (column) {
      var tr = document.createElement("tr");
      // The sheet position travels with the row, so unticking a column cannot
      // shift the ones after it when the data is loaded.
      tr.dataset.index = column.index;

      var includeCell = cell(tr, tickbox(true, "include"));
      includeCell.className = "col-select";

      cell(tr, column.source || "(blank)");

      var input = document.createElement("input");
      input.type = "text";
      input.value = column.name;
      cell(tr, input);
      cell(tr, typeSelect(column.type));

      var keyCell = cell(tr, tickbox(false, "key"));
      keyCell.className = "col-select";

      var samples = cell(tr, column.samples.join(" | "));
      samples.className = "muted samples";
      // The full text on hover: the cell itself is clipped so one long value
      // cannot squeeze the Type dropdown down to a sliver.
      samples.title = column.samples.join("\n");
      tbody.appendChild(tr);
    });

    $("includeAll").checked = true;
    $("previewSummary").textContent =
      body.columns.length + " column(s), " + body.rows + " data row(s)" +
      (body.sheet ? " on sheet '" + body.sheet + "'" : "") +
      ". Untick any column you do not want, edit the names and types, tick " +
      "the key column(s), then create.";
    reveal($("previewBlock"));
    updateIncluded();
  }

  // Greys out the row so it is obvious what will not be created, and keeps the
  // count on the button honest.
  function updateIncluded() {
    var rows = [].slice.call($("previewBody").rows);
    var chosen = 0;

    rows.forEach(function (tr) {
      var on = tr.querySelector('input[data-role="include"]').checked;
      tr.classList.toggle("excluded", !on);
      if (on) chosen++;
    });

    $("btnCreateSheet").textContent =
      "Create table with " + chosen + " column(s)";
    $("btnCreateSheet").disabled = chosen === 0;
    $("includeAll").checked = chosen === rows.length && rows.length > 0;
  }

  function createFromSheet() {
    var columns = readGrid($("previewBody"));
    if (!columns.length) {
      note($("previewNote"), "No columns to create.", "warn");
      return;
    }

    var form = new FormData();
    form.append("table", $("sheetTable").value.trim());
    form.append("columns", JSON.stringify(columns));
    form.append("file", chosenFile);
    if (chosenSheet) form.append("sheet", chosenSheet);

    $("btnCreateSheet").disabled = true;
    note($("previewNote"), "Creating the table and loading the rows...");

    post(cfg.createUrl, form)
      .then(function (result) {
        note($("previewNote"), "", "");
        $("previewBlock").hidden = true;
        $("sheetTable").value = "";
        $("sheetFile").value = "";
        $("sheetPickWrap").hidden = true;
        chosenFile = null;
        chosenSheet = "";
        showResult(result);
      })
      .catch(function (err) { note($("previewNote"), err.message, "warn"); })
      .finally(function () { $("btnCreateSheet").disabled = false; });
  }

  // ---- sections -------------------------------------------------------------

  function showSection(name) {
    document.querySelectorAll(".side-item").forEach(function (item) {
      item.classList.toggle("active", item.dataset.section === name);
    });
    document.querySelectorAll(".side-section").forEach(function (section) {
      section.hidden = section.dataset.section !== name;
    });
    if (name === "tables" && !$("tablesBody").rows.length) loadTables();
    if (name === "alter") loadTables();     // the same list, scoped to altering
  }

  function fetchJson(url) {
    return fetch(url, { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) {
        return r.json().then(function (b) {
          if (!r.ok) throw new Error(b.error || ("HTTP " + r.status));
          return b;
        });
      });
  }

  // ---- the list of tables ---------------------------------------------------

  var tables = [];

  function actionButton(label, extra, onClick) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tool" + (extra ? " " + extra : "");
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  function renderTables() {
    var needle = ($("tableSearch").value || "").trim().toLowerCase();
    var shown = tables.filter(function (t) {
      return !needle || t.name.toLowerCase().indexOf(needle) !== -1;
    });

    var body = $("tablesBody");
    body.textContent = "";
    shown.forEach(function (entry) {
      var tr = document.createElement("tr");
      cell(tr, entry.name).className = "table-row-name";

      var actions = document.createElement("td");
      actions.className = "table-row-actions";
      actions.appendChild(actionButton("View", "", function () {
        openTable(entry.name, "rows");
      }));
      actions.appendChild(actionButton("Manage", "manage", function () {
        openTable(entry.name, "structure");
      }));
      tr.appendChild(actions);
      body.appendChild(tr);
    });

    $("tablesEmpty").hidden = shown.length !== 0;
    $("tablesNote").textContent = shown.length === tables.length
      ? tables.length + " table(s) without a tab."
      : shown.length + " of " + tables.length + " table(s).";

    // The Alter list is the same set: only these tables may be altered.
    var alter = $("alterBody");
    alter.textContent = "";
    tables.forEach(function (entry) {
      var tr = document.createElement("tr");
      cell(tr, entry.name).className = "table-row-name";
      var actions = document.createElement("td");
      actions.className = "table-row-actions";
      actions.appendChild(actionButton("Structure", "", function () {
        openTable(entry.name, "structure");
      }));
      tr.appendChild(actions);
      alter.appendChild(tr);
    });
    $("alterEmpty").hidden = tables.length !== 0;
  }

  function loadTables() {
    $("tablesNote").textContent = "Loading...";
    $("tablesNote").className = "table-status";

    fetchJson(cfg.tablesUrl)
      .then(function (data) {
        tables = data.tables || [];
        renderTables();
      })
      .catch(function (err) {
        $("tablesNote").textContent = err.message;
        $("tablesNote").className = "table-status warn";
      });
  }

  // ---- one table, on its own screen ----------------------------------------

  var current = { name: "", columns: [], rows: [], filters: {} };

  function showList() {
    $("tableScreen").hidden = true;
    $("tablesScreen").hidden = false;
  }

  function showPane(which) {
    var rowsOn = which === "rows";
    $("rowsPane").hidden = !rowsOn;
    $("structurePane").hidden = rowsOn;
    $("tabRows").classList.toggle("active", rowsOn);
    $("tabStructure").classList.toggle("active", !rowsOn);
  }

  // Opening replaces the list rather than appending below it: with twenty
  // tables, stacking every one you opened turns the page into a scroll.
  function openTable(name, pane) {
    current.name = name;
    current.filters = {};
    $("rowSearch").value = "";
    $("tablesScreen").hidden = true;
    $("tableScreen").hidden = false;
    $("detailTitle").textContent = name;
    $("detailSummary").textContent = "Loading...";
    showPane(pane);

    Promise.all([
      fetchJson(cfg.rowsUrl.replace("__NAME__", encodeURIComponent(name))),
      fetchJson(cfg.tableUrl.replace("__NAME__", encodeURIComponent(name)))
    ]).then(function (both) {
      var data = both[0], detail = both[1];
      current.columns = data.columns;
      current.rows = data.rows;

      buildHead(data.columns);
      renderRows();

      var structure = $("structureBody");
      structure.textContent = "";
      detail.columns.forEach(function (column) {
        var tr = document.createElement("tr");
        cell(tr, column.name).className = "table-row-name";
        cell(tr, column.type);
        cell(tr, column.nullable ? "Yes" : "No");
        cell(tr, column.key ? "Primary" : "");
        structure.appendChild(tr);
      });

      $("detailSummary").textContent =
        detail.columns.length + " column(s), " + data.total + " row(s)" +
        (data.total > data.rows.length
          ? " - showing the first " + data.limit : "") + ".";
    }).catch(function (err) {
      $("detailSummary").textContent = err.message;
    });
  }

  function buildHead(columns) {
    var head = $("rowsHead");
    var filters = $("rowsFilters");
    head.textContent = "";
    filters.textContent = "";

    columns.forEach(function (column) {
      var th = document.createElement("th");
      th.textContent = column;
      head.appendChild(th);

      var filterCell = document.createElement("th");
      var input = document.createElement("input");
      input.className = "col-filter";
      input.type = "search";
      input.placeholder = "Filter...";
      input.title = "Contains match.\nType a space for blanks, / for non-blanks.";
      input.addEventListener("input", function () {
        current.filters[column] = input.value;
        renderRows();
      });
      filterCell.appendChild(input);
      filters.appendChild(filterCell);
    });
  }

  function visibleRows() {
    var F = window.OMPFilter;
    var needle = ($("rowSearch").value || "").trim().toLowerCase();

    return current.rows.filter(function (row) {
      if (needle && !row.some(function (v) {
        return String(v).toLowerCase().indexOf(needle) !== -1;
      })) return false;

      return current.columns.every(function (column, index) {
        return F.cell(row[index], current.filters[column] || "", false);
      });
    });
  }

  function renderRows() {
    var shown = visibleRows();
    var body = $("rowsBody");
    body.textContent = "";

    var frag = document.createDocumentFragment();
    shown.forEach(function (row) {
      var tr = document.createElement("tr");
      row.forEach(function (value) { cell(tr, value); });
      frag.appendChild(tr);
    });
    body.appendChild(frag);

    $("rowsEmpty").hidden = shown.length !== 0;
    $("rowsNote").textContent = shown.length === current.rows.length
      ? shown.length + " row(s)."
      : shown.length + " of " + current.rows.length + " row(s) after filtering.";
  }

  function exportRows() {
    var esc = function (v) {
      var s = String(v === null || v === undefined ? "" : v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var lines = [current.columns.map(esc).join(",")];
    visibleRows().forEach(function (row) { lines.push(row.map(esc).join(",")); });

    var url = URL.createObjectURL(
      new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" }));
    var a = document.createElement("a");
    a.href = url;
    a.download = current.name + ".csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  // ---- wiring ---------------------------------------------------------------

  document.querySelectorAll(".side-item").forEach(function (item) {
    item.addEventListener("click", function () {
      showList();
      showSection(item.dataset.section);
    });
  });

  $("tableSearch").addEventListener("input", renderTables);
  $("btnTablesReload").addEventListener("click", loadTables);
  $("btnBack").addEventListener("click", showList);
  $("tabRows").addEventListener("click", function () { showPane("rows"); });
  $("tabStructure").addEventListener("click", function () { showPane("structure"); });
  $("rowSearch").addEventListener("input", renderRows);
  $("btnRowsReload").addEventListener("click", function () {
    openTable(current.name, $("rowsPane").hidden ? "structure" : "rows");
  });
  $("btnRowsExport").addEventListener("click", exportRows);

  $("btnAddColumn").addEventListener("click", function () { addManualRow(); });
  $("btnCreateManual").addEventListener("click", createManual);

  $("sheetFile").addEventListener("change", function (e) {
    chosenFile = e.target.files[0] || null;
    chosenSheet = "";
    $("sheetPickWrap").hidden = true;
    $("previewBlock").hidden = true;
    note($("sheetNote"), "");
    // A name is suggested from the file, and stays editable.
    if (chosenFile && !$("sheetTable").value.trim()) {
      $("sheetTable").value = chosenFile.name
        .replace(/\.[^.]+$/, "")
        .replace(/[^A-Za-z0-9_]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .toLowerCase();
    }
    // Read it straight away: a workbook's sheet list is the first thing you
    // need, and having to press a button to discover it reads as a fault.
    if (chosenFile) inspect();
  });

  // Choosing a sheet only arms the View button: the columns on screen belong
  // to the sheet you last viewed, so they are cleared rather than left to
  // look like they describe the new one.
  $("sheetPick").addEventListener("change", function (e) {
    chosenSheet = e.target.value;
    $("previewBlock").hidden = true;
    note($("sheetNote"), "Press View to see the columns in '" + chosenSheet + "'.");
  });

  $("btnViewSchema").addEventListener("click", inspect);

  $("includeAll").addEventListener("change", function (e) {
    [].forEach.call($("previewBody").rows, function (tr) {
      tr.querySelector('input[data-role="include"]').checked = e.target.checked;
    });
    updateIncluded();
  });

  // One listener on the table rather than one per row.
  $("previewBody").addEventListener("change", function (e) {
    if (e.target.dataset.role === "include") updateIncluded();
  });

  $("btnInspect").addEventListener("click", inspect);
  $("btnCreateSheet").addEventListener("click", createFromSheet);
  $("btnCancelPreview").addEventListener("click", function () {
    $("previewBlock").hidden = true;
  });

  addManualRow();     // start with one empty row
})();
