// Setup tab: run the check, review allocations and max losses, then apply.
// Nothing is written until Apply is pressed, and both tables are written in
// one request so the day cannot end up half set up.
(function () {
  "use strict";

  var cfg = window.OMP_SETUP;
  if (!cfg) return;

  var $ = function (id) { return document.getElementById(id); };

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

  function text(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  // ---------------------------------------------------------------------
  // One reviewable table: filters, tick-boxes, select all/none.
  // Two of them on this page, identical in behaviour and different only in
  // which columns they show and which rows may be applied.
  // ---------------------------------------------------------------------
  function Panel(opts) {
    var self = {
      rows: [],
      choices: {},
      search: $(opts.searchId),
      body: $(opts.bodyId)
    };

    opts.filters.forEach(function (name) { self.choices[name] = new Set(); });

    function visible(row) {
      var needle = (self.search.value || "").trim().toLowerCase();
      if (needle) {
        var hay = opts.searchable.map(function (k) { return text(row[k]); })
          .join(" ").toLowerCase();
        if (hay.indexOf(needle) === -1) return false;
      }
      // OR within a column, AND across columns.
      return opts.filters.every(function (name) {
        var picked = self.choices[name];
        return !picked.size || picked.has(text(row[name]));
      });
    }

    function buildFilters() {
      // Scoped to this table: both tables filter on `server`, and they must
      // not share a menu.
      var scope = $(opts.scopeId);
      opts.filters.forEach(function (name) {
        var holder = scope.querySelector('.choice[data-col="' + name + '"]');
        if (!holder) return;

        var counts = {};
        self.rows.forEach(function (row) {
          var v = text(row[name]);
          counts[v] = (counts[v] || 0) + 1;
        });

        window.OMPChoice.build(holder, {
          label: name,
          counts: counts,
          selected: self.choices[name],
          onChange: function () { self.render(); refreshApply(); }
        });
      });
    }

    self.visible = visible;

    self.selected = function () {
      // Filtered-out rows are never written: what you see is what you apply.
      return self.rows.filter(function (r) {
        return opts.canApply(r) && r._checked && visible(r);
      });
    };

    self.load = function (rows) {
      self.rows = rows || [];
      self.rows.forEach(function (r) { r._checked = opts.canApply(r); });
      buildFilters();
      self.render();
    };

    self.render = function () {
      self.body.textContent = "";
      var frag = document.createDocumentFragment();

      self.rows.forEach(function (row) {
        if (!visible(row)) return;

        var tr = document.createElement("tr");
        var tdSel = document.createElement("td");
        tdSel.className = "col-select";
        if (opts.canApply(row)) {
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = !!row._checked;
          cb.addEventListener("change", function () {
            row._checked = cb.checked;
            if (opts.onToggle) opts.onToggle();
            refreshApply();
          });
          tdSel.appendChild(cb);
        }
        tr.appendChild(tdSel);

        opts.cells(row).forEach(function (cell) {
          var td = document.createElement("td");
          if (cell.cls) td.className = cell.cls;
          if (cell.chip) {
            var chip = document.createElement("span");
            chip.className = "chip " + (cell.chip === true ? "" : cell.chip);
            chip.textContent = cell.value;
            td.appendChild(chip);
          } else {
            td.textContent = cell.value;
          }
          if (cell.title) td.title = cell.title;
          tr.appendChild(td);
        });

        frag.appendChild(tr);
      });

      self.body.appendChild(frag);
    };

    self.search.addEventListener("input", function () {
      self.render();
      refreshApply();
    });

    return self;
  }

  // ---------------------------------------------------------------------
  // The two tables
  // ---------------------------------------------------------------------
  var alloc = Panel({
    bodyId: "setupBody",
    searchId: "setupSearch",
    scopeId: "tabAllocation",
    filters: ["server", "algo", "operator_name", "subcategory", "rule",
              "status", "remark"],
    searchable: ["userid", "alias", "server", "algo", "operator_name",
                 "subcategory", "rule", "status", "remark"],
    canApply: function (row) { return !!row.apply; },
    onToggle: function () { maxlossPanel.render(); },
    cells: function (row) {
      var out = ["userid", "alias", "server", "algo", "operator_name",
                 "subcategory", "rule"].map(function (k) {
        return { value: text(row[k]) };
      });
      ["capital", "current", "expected"].forEach(function (k) {
        out.push({ value: money(row[k]), cls: "num" });
      });
      out.push({
        value: text(row.status),
        chip: row.status === "Mismatch" ? "neg" : row.status === "Match" ? "pos" : true
      });
      out.push({ value: text(row.remark) });
      return out;
    }
  });

  // A max loss row is applicable when there is a value to write and it differs
  // from what is stored.
  function maxlossApplicable(row) {
    return row.mstech !== null && row.mstech !== undefined && !!row.changed;
  }

  // True when the max loss was worked out from an allocation the run has not
  // been told to write. Applying it alone stores a limit for an allocation
  // that is not there - allowed, but flagged.
  function orphan(row) {
    if (!row.depends_on_allocation) return false;
    var owner = alloc.rows.find(function (r) {
      return String(r.userid) === String(row.userid);
    });
    return !(owner && owner._checked && alloc.visible(owner));
  }

  var maxlossPanel = Panel({
    bodyId: "maxlossBody",
    searchId: "maxlossSearch",
    scopeId: "tabMaxloss",
    filters: ["server", "algo", "operator_name", "subcategory", "source",
              "status"],
    searchable: ["userid", "alias", "server", "algo", "operator_name",
                 "subcategory", "source", "status", "note"],
    canApply: maxlossApplicable,
    cells: function (row) {
      var out = ["userid", "alias", "server", "algo", "operator_name",
                 "subcategory"]
        .map(function (k) { return { value: text(row[k]) }; });
      // `source` is the rule that produced the numbers.
      out.push({ value: text(row.source) });
      out.push({
        value: money(row.allocation),
        cls: "num",
        title: row.depends_on_allocation
          ? "From the allocation this run proposes (stored: " +
            money(row.stored_allocation) + ")"
          : ""
      });
      // US goes to usersetting (Stoxxo), AU to all_users (MStech).
      out.push({ value: money(row.stoxxo), cls: "num" });
      out.push({ value: money(row.mstech), cls: "num" });
      out.push({
        value: text(row.status),
        chip: row.status === "Mismatch" ? "neg" : row.status === "Match" ? "pos" : true
      });

      var warn = orphan(row);
      out.push({
        value: warn ? "needs allocation" : text(row.note),
        chip: warn ? "neg" : false,
        title: warn
          ? "Based on a proposed allocation that is not ticked. " + text(row.note)
          : ""
      });
      return out;
    }
  });

  function refreshApply() {
    var a = alloc.selected().length;
    var m = maxlossPanel.selected().length;
    var total = a + m;
    $("btnApply").disabled = !total;
    $("btnApply").textContent = total
      ? "Apply " + a + " allocation(s) + " + m + " max loss(es)"
      : "Apply selected";
    $("tabAllocCount").textContent = a;
    $("tabMaxlossCount").textContent = m;
  }

  // ---------------------------------------------------------------------
  // Run and apply
  // ---------------------------------------------------------------------
  var lastDate = "";

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (payload) {
        if (!r.ok) throw new Error(payload.error || ("HTTP " + r.status));
        return payload;
      });
    });
  }

  function run() {
    var date = $("checkDate").value;
    if (!date) { note("Choose the date to check.", "warn"); return; }

    $("btnRun").disabled = true;
    note("Running the check...");

    post(cfg.runUrl, {
      date: date,
      previous: $("prevDate").value,
      mode: $("mode").value,
      rounding: $("rounding") ? $("rounding").value : null
    })
      .then(function (data) {
        lastDate = date;
        alloc.load(data.rows || []);

        var ml = data.maxloss || {};
        maxlossPanel.load(ml.rows || []);

        $("pillScope").textContent = data.in_scope + " in scope";
        $("pillMismatch").textContent = data.mismatch + " to change";
        $("pillMatch").textContent = data.match + " already correct";
        $("pillReconcile").textContent = data.reconciled
          ? "reconciled" : "reconciliation failed";
        $("pillReconcile").className = "stat-pill " + (data.reconciled ? "ok" : "warn");

        var counts = ml.counts || {};
        $("pillMaxloss").textContent = (counts.changed || 0) + " max loss to change";
        $("pillMaxlossSkipped").textContent = (counts.skipped || 0) + " left alone";

        $("summary").hidden = false;
        $("resultPanel").hidden = false;
        refreshApply();

        note(
          !data.reconciled
            ? "Reconciliation failed: " + data.reconcile_message +
              " - do not apply these numbers."
            : ml.error
              ? "Allocations are ready. Max loss: " + ml.error
              : "Nothing written yet. Review both tables, then Apply.",
          data.reconciled && !ml.error ? "" : "warn"
        );
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
    var allocPicked = alloc.selected();
    var maxlossPicked = maxlossPanel.selected();
    if (!allocPicked.length && !maxlossPicked.length) return;

    var orphans = maxlossPicked.filter(orphan).length;
    var message =
      "Write " + allocPicked.length + " allocation(s) and " +
      maxlossPicked.length + " max loss(es) for " + lastDate + "?\n\n" +
      "This updates all_users.allocation, usersetting.Remarks, " +
      "all_users.max_loss and usersetting.Max Loss.";
    if (orphans) {
      message += "\n\n" + orphans + " max loss row(s) are based on an " +
        "allocation change that is not ticked. They will be written anyway.";
    }
    if (!window.confirm(message)) return;

    $("btnApply").disabled = true;
    note("Applying...");

    post(cfg.applyUrl, {
      date: lastDate,
      updates: allocPicked.map(function (r) {
        return { userid: r.userid, expected: r.expected };
      }),
      maxloss: maxlossPicked.map(function (r) {
        return { userid: r.userid, mstech: r.mstech, stoxxo: r.stoxxo };
      })
    })
      .then(function (result) {
        note("Applied: " + result.allocations + " allocation(s), " +
             result.remarks + " remark(s), " + result.all_users +
             " max loss(es), " + result.usersetting +
             " usersetting max loss(es). Re-run to confirm.", "ok");
        run();
      })
      .catch(function (err) {
        console.error(err);
        note("Apply failed: " + err.message, "warn");
        refreshApply();
      });
  }

  // ---------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------
  $("btnRun").addEventListener("click", run);
  $("btnApply").addEventListener("click", apply);

  document.querySelectorAll("[data-tab]").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll("[data-tab]").forEach(function (other) {
        other.classList.toggle("active", other === button);
      });
      document.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
        panel.hidden = panel.dataset.tabPanel !== button.dataset.tab;
      });
    });
  });

  // Reads the database, so it hands back whatever has been applied so far -
  // no need to have run the check first.
  $("btnDownloadUsersetting").addEventListener("click", function () {
    window.OMPDownload(cfg.usersettingUrl, note, $("btnDownloadUsersetting"));
  });

  function bulk(panel, value) {
    panel.rows.forEach(function (r) {
      if (value && !panel.visible(r)) return;
      r._checked = value && (panel === alloc ? !!r.apply : maxlossApplicable(r));
    });
    alloc.render();
    maxlossPanel.render();
    refreshApply();
  }

  $("btnSelectAll").addEventListener("click", function () { bulk(alloc, true); });
  $("btnSelectNone").addEventListener("click", function () { bulk(alloc, false); });
  $("btnMaxlossSelectAll").addEventListener("click", function () {
    bulk(maxlossPanel, true);
  });
  $("btnMaxlossSelectNone").addEventListener("click", function () {
    bulk(maxlossPanel, false);
  });
})();
