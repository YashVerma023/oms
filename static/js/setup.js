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

    // Typed column filters, in the same boxes the data tables use, so an
    // expression means the same thing here: >10, <50, 10-20, a bare space for
    // blanks, "/" for anything with a value.
    var typed = Array.prototype.slice.call(
      $(opts.scopeId).querySelectorAll(".col-filter")
    );

    function visible(row) {
      var needle = (self.search.value || "").trim().toLowerCase();
      if (needle) {
        var hay = opts.searchable.map(function (k) { return text(row[k]); })
          .join(" ").toLowerCase();
        if (hay.indexOf(needle) === -1) return false;
      }

      for (var i = 0; i < typed.length; i++) {
        var input = typed[i];
        if (!window.OMPFilter.cell(row[input.dataset.col], input.value,
                                   input.dataset.type === "number")) {
          return false;
        }
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

    typed.forEach(function (input) {
      input.addEventListener("input", function () {
        self.render();
        // The other table's warning column depends on this one's selection.
        if (opts.onToggle) opts.onToggle();
        refreshApply();
      });
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

  // A summary pill: the figure in its own element so a style can give it
  // weight. textContent is unchanged, so anything reading these still sees
  // "381 in scope".
  function pill(id, value, label) {
    var el = $(id);
    el.textContent = "";
    var figure = document.createElement("b");
    figure.textContent = String(value);
    el.appendChild(figure);
    el.appendChild(document.createTextNode(" " + label));
  }

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

  // A run over 800 accounts takes seconds. It writes nothing, so stopping it
  // is always safe - the page is left exactly as it was.
  var inFlight = null;

  function post(url, body) {
    inFlight = new AbortController();
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body),
      signal: inFlight.signal
    }).then(function (r) {
      return r.json().then(function (payload) {
        if (!r.ok) throw new Error(payload.error || ("HTTP " + r.status));
        return payload;
      });
    });
  }

  // ---------------------------------------------------------------------
  // Cycle -> which steps exist, and whether this one carries forward
  // ---------------------------------------------------------------------
  function steps() {
    var picked = $("cycle").options[$("cycle").selectedIndex];
    return picked && picked.dataset.steps ? picked.dataset.steps.split(",") : [];
  }

  // Every step but the first of a cycle needs the day before.
  function needsPrevious() {
    var order = steps();
    var at = order.indexOf($("mode").value);
    return at > 0;
  }

  function syncCycle() {
    var order = steps();
    var mode = $("mode");

    // Only this cycle's steps are offered; 4DTE does not exist in Sensex.
    Array.prototype.forEach.call(mode.options, function (option) {
      option.hidden = order.length > 0 && order.indexOf(option.value) === -1;
    });
    if (order.length && order.indexOf(mode.value) === -1) mode.value = order[0];

    // Kept to one short phrase: a wrapping label drags its input out of line
    // with the rest of the row. The full sentence is the field's tooltip.
    var required = needsPrevious();
    $("prevHint").textContent = required ? "required" : "not needed";
    $("prevDate").required = required;
    $("prevDate").disabled = !required && order.length > 0;
    $("prevDate").title = required
      ? mode.value + " is step " + (order.indexOf(mode.value) + 1) + " of the "
        + $("cycle").value + " cycle, so it carries the day before forward."
      : mode.value + " opens the " + $("cycle").value
        + " cycle, so there is nothing to carry forward.";
    if (!required) $("prevDate").value = "";
  }

  // The sheet carries its own date. Running a different day silently finds
  // nothing, which is exactly the trap that cost a morning once already.
  function checkSheetDate() {
    var note = $("maxlossState");
    if (!note) return;
    var stored = note.dataset.sheetDate;
    var wanted = $("checkDate").value;
    var stale = stored && wanted && stored !== wanted;
    note.classList.toggle("warn", !!stale);
    if (stale && !note.dataset.original) note.dataset.original = note.textContent;
    if (stale) {
      note.textContent = "The Max Loss sheet is dated " + stored +
        " but this run is for " + wanted +
        ". Accounts that already ran will find nothing in it.";
    } else if (note.dataset.original) {
      note.textContent = note.dataset.original;
      delete note.dataset.original;
    }
  }

  $("checkDate").addEventListener("change", checkSheetDate);
  checkSheetDate();

  $("cycle").addEventListener("change", syncCycle);
  $("mode").addEventListener("change", syncCycle);
  syncCycle();

  function run() {
    var date = $("checkDate").value;
    if (!date) { note("Choose the date to check.", "warn"); return; }

    if (needsPrevious() && !$("prevDate").value) {
      note($("mode").value + " is step " +
           (steps().indexOf($("mode").value) + 1) + " of the " + $("cycle").value +
           " cycle, so it needs the previous day's All Users date.", "warn");
      return;
    }

    running(true);
    note("Running the check. Nothing is written until you apply.");

    post(cfg.runUrl, {
      date: date,
      previous: $("prevDate").value,
      mode: $("mode").value,
      cycle: $("cycle").value,
      rounding: $("rounding") ? $("rounding").value : null
    })
      .then(function (data) {
        lastDate = date;
        alloc.load(data.rows || []);

        var ml = data.maxloss || {};
        maxlossPanel.load(ml.rows || []);

        pill("pillScope", data.in_scope, "in scope");
        pill("pillMismatch", data.mismatch, "to change");
        pill("pillMatch", data.match, "already correct");
        $("pillReconcile").textContent = data.reconciled
          ? "reconciled" : "reconciliation failed";
        $("pillReconcile").className = "stat-pill " + (data.reconciled ? "ok" : "warn");

        var counts = ml.counts || {};
        pill("pillMaxloss", counts.changed || 0, "max loss to change");
        pill("pillMaxlossSkipped", counts.skipped || 0, "left alone");

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
        if (err.name === "AbortError") {
          note("Run stopped. Nothing was written.", "");
          return;
        }
        note(err.message, "warn");
        $("summary").hidden = true;
        $("resultPanel").hidden = true;
      })
      .finally(function () { running(false); });
  }

  // The button reads "Run check" or "Stop", and only one of the two is ever
  // true, so the label and the handler cannot disagree.
  function running(active) {
    var button = $("btnRun");
    button.textContent = active ? "Stop" : "Run check";
    button.classList.toggle("danger", active);
    button.dataset.running = active ? "1" : "";
  }

  function stop() {
    if (inFlight) inFlight.abort();
    inFlight = null;
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

    window.OMPConfirm({
      title: "Apply setup changes",
      body: message,
      warning: orphans
        ? orphans + " max loss row(s) rest on an allocation change that is not "
          + "ticked. They will be written anyway."
        : "",
      confirmLabel: "Write changes",
      danger: true
    }).then(function (yes) {
      if (yes) sendApply(allocPicked, maxlossPicked);
    });
  }

  function sendApply(allocPicked, maxlossPicked) {
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
  $("btnRun").addEventListener("click", function () {
    if ($("btnRun").dataset.running) { stop(); return; }
    run();
  });
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

  // Compiled workbooks, admin only - the buttons are not rendered otherwise.
  function wireDownload(id, url) {
    var button = $(id);
    if (!button || !url) return;
    button.addEventListener("click", function () {
      window.OMPDownload(url(), note, button);
    });
  }

  wireDownload("btnUsersettingCompiled", function () {
    return cfg.usersettingCompiledUrl;
  });

  // The strategy tags depend on which step of which cycle is being set up, so
  // both travel with the request rather than being guessed on the server.
  function cycleQuery() {
    return "?date=" + encodeURIComponent($("checkDate").value || "") +
      "&cycle=" + encodeURIComponent($("cycle").value || "") +
      "&dte=" + encodeURIComponent($("mode").value || "");
  }

  wireDownload("btnStrategyTags", function () {
    return cfg.strategyTagsUrl + cycleQuery();
  });

  wireDownload("btnStrategyCompiled", function () {
    return cfg.strategyCompiledUrl + cycleQuery();
  });

  // The date picked for the check is the day exported, so the two always agree.
  wireDownload("btnAllUsersCompiled", function () {
    return cfg.allUsersCompiledUrl +
      "?date=" + encodeURIComponent($("checkDate").value || "");
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
