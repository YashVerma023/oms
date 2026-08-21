// Setup tab: run the allocation check, review, then apply.
// Nothing is written until Apply is pressed.
(function () {
  "use strict";

  var cfg = window.OMP_SETUP;
  if (!cfg) return;

  var $ = function (id) { return document.getElementById(id); };
  var rows = [];
  var lastDate = "";
  // column -> Set of ticked values. Empty Set means no filter on that column.
  var choices = { server: new Set(), algo: new Set(), operator_name: new Set(),
                  subcategory: new Set(), rule: new Set(), status: new Set(),
                  remark: new Set() };

  function money(value) {
    if (value === null || value === undefined || value === "") return "";
    var n = Number(value);
    if (isNaN(n)) return String(value);
    // Indian grouping: 12,34,56,789
    var s = Math.abs(Math.round(n)).toString();
    var last = s.slice(-3);
    var rest = s.slice(0, -3);
    if (rest) last = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + last;
    return (n < 0 ? "-" : "") + last;
  }

  function note(message, kind) {
    var el = $("setupNote");
    el.textContent = message;
    el.className = "upload-note" + (kind ? " " + kind : "");
  }

  function selected() {
    // Filtered-out rows are never written, so what you see is what you apply.
    return rows.filter(function (r) { return r.apply && r._checked && visible(r); });
  }

  function refreshApply() {
    var n = selected().length;
    $("btnApply").disabled = !n;
    $("btnApply").textContent = n ? "Apply " + n + " change(s)" : "Apply selected";
  }

  function visible(row) {
    var needle = ($("setupSearch").value || "").trim().toLowerCase();
    if (needle) {
      var hay = [row.userid, row.alias, row.server, row.algo, row.operator_name,
                 row.subcategory, row.rule, row.status, row.remark]
        .join(" ").toLowerCase();
      if (hay.indexOf(needle) === -1) return false;
    }

    // OR within a column, AND across columns.
    return Object.keys(choices).every(function (name) {
      var picked = choices[name];
      if (!picked.size) return true;
      return picked.has(row[name] === null || row[name] === undefined
        ? "" : String(row[name]));
    });
  }

  function buildFilters() {
    document.querySelectorAll(".choice").forEach(function (holder) {
      var name = holder.dataset.col;
      var counts = {};
      rows.forEach(function (row) {
        var v = row[name] === null || row[name] === undefined ? "" : String(row[name]);
        counts[v] = (counts[v] || 0) + 1;
      });

      window.OMPChoice.build(holder, {
        label: name,
        counts: counts,
        selected: choices[name],
        onChange: function () { render(); refreshApply(); }
      });
    });
  }

  function render() {
    var body = $("setupBody");
    body.textContent = "";
    var frag = document.createDocumentFragment();

    rows.forEach(function (row) {
      if (!visible(row)) return;

      var tr = document.createElement("tr");

      var tdSel = document.createElement("td");
      tdSel.className = "col-select";
      if (row.apply) {
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!row._checked;
        cb.addEventListener("change", function () {
          row._checked = cb.checked;
          refreshApply();
        });
        tdSel.appendChild(cb);
      }
      tr.appendChild(tdSel);

      [row.userid, row.alias, row.server, row.algo, row.operator_name,
       row.subcategory, row.rule].forEach(function (v) {
        var td = document.createElement("td");
        td.textContent = v === null || v === undefined ? "" : v;
        tr.appendChild(td);
      });

      [row.capital, row.current, row.expected].forEach(function (v) {
        var td = document.createElement("td");
        td.className = "num";
        td.textContent = money(v);
        tr.appendChild(td);
      });

      var tdStatus = document.createElement("td");
      var chip = document.createElement("span");
      chip.className = "chip " + (row.status === "Mismatch" ? "neg"
                                 : row.status === "Match" ? "pos" : "");
      chip.textContent = row.status || "";
      tdStatus.appendChild(chip);
      tr.appendChild(tdStatus);

      var tdRemark = document.createElement("td");
      tdRemark.textContent = row.remark || "";
      tr.appendChild(tdRemark);

      frag.appendChild(tr);
    });

    body.appendChild(frag);
  }

  function run() {
    var date = $("checkDate").value;
    if (!date) { note("Choose the date to check.", "warn"); return; }

    $("btnRun").disabled = true;
    note("Running the check...");

    fetch(cfg.runUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        date: date,
        previous: $("prevDate").value,
        mode: $("mode").value,
        rounding: $("rounding") ? $("rounding").value : null
      })
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (data) {
        rows = data.rows || [];
        lastDate = date;
        // Mismatches start ticked: they are the whole point of the run.
        rows.forEach(function (r) { r._checked = !!r.apply; });

        $("pillScope").textContent = data.in_scope + " in scope";
        $("pillMismatch").textContent = data.mismatch + " to change";
        $("pillMatch").textContent = data.match + " already correct";
        $("pillReconcile").textContent = data.reconciled
          ? "reconciled" : "reconciliation failed";
        $("pillReconcile").className = "stat-pill " + (data.reconciled ? "ok" : "warn");

        $("summary").hidden = false;
        $("resultPanel").hidden = false;
        buildFilters();
        render();
        refreshApply();
        note(data.reconciled
          ? "Nothing written yet. Review the rows, then Apply."
          : "Reconciliation failed: " + data.reconcile_message +
            " - do not apply these numbers.",
          data.reconciled ? "" : "warn");
      })
      .catch(function (err) {
        console.error(err);
        note(err.message, "warn");
        $("summary").hidden = true;
        $("resultPanel").hidden = true;
      })
      .finally(function () { $("btnRun").disabled = false; });
  }

  function apply() {
    var picked = selected();
    if (!picked.length) return;
    if (!window.confirm(
      "Write " + picked.length + " allocation(s) for " + lastDate + "?\n\n" +
      "This updates all_users.allocation and usersetting.Remarks."
    )) return;

    $("btnApply").disabled = true;
    note("Applying " + picked.length + " change(s)...");

    fetch(cfg.applyUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        date: lastDate,
        updates: picked.map(function (r) {
          return { userid: r.userid, expected: r.expected };
        })
      })
    })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
          return body;
        });
      })
      .then(function (result) {
        note("Applied: " + result.allocations + " allocation(s) updated, " +
             result.remarks + " remark(s) updated. Re-run to confirm.", "ok");
        run();
      })
      .catch(function (err) {
        console.error(err);
        note("Apply failed: " + err.message, "warn");
        refreshApply();
      });
  }

  $("btnRun").addEventListener("click", run);
  $("btnApply").addEventListener("click", apply);
  $("setupSearch").addEventListener("input", render);

  // Reads the database, so it hands back whatever has been applied so far -
  // no need to have run the check first.
  $("btnDownloadUsersetting").addEventListener("click", function () {
    window.OMPDownload(cfg.usersettingUrl, note, $("btnDownloadUsersetting"));
  });

  $("btnSelectAll").addEventListener("click", function () {
    // Only what is currently on screen, so a filter narrows the selection too.
    rows.forEach(function (r) { if (r.apply && visible(r)) r._checked = true; });
    render(); refreshApply();
  });

  $("btnSelectNone").addEventListener("click", function () {
    rows.forEach(function (r) { r._checked = false; });
    render(); refreshApply();
  });
})();
