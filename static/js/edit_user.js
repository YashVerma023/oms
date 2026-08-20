// Live preview of the server rules on the edit form.
// The server re-applies all of this on save; this is only feedback.
(function () {
  "use strict";

  var cfg = window.OMP_EDIT;
  if (!cfg) return;

  var form = document.querySelector(".edit-form");
  if (!form) return;

  var mlPct = document.getElementById("mlPct");
  var note = document.getElementById("linkNote");
  var linked = cfg.linkedFields.map(function (n) {
    return form.querySelector('[name="' + n + '"]');
  }).filter(Boolean);

  function field(name) { return form.querySelector('[name="' + name + '"]'); }

  function inactiveState() {
    for (var i = 0; i < linked.length; i++) {
      var v = (linked[i].value || "").trim().toLowerCase();
      for (var j = 0; j < cfg.inactiveStates.length; j++) {
        if (v === cfg.inactiveStates[j].toLowerCase()) return cfg.inactiveStates[j];
      }
    }
    return null;
  }

  function refresh() {
    var state = inactiveState();

    if (state) {
      // Mirror the state across all three linked fields and force algo to 0.
      linked.forEach(function (el) {
        if ((el.value || "").toLowerCase() !== state.toLowerCase()) {
          var match = Array.prototype.find.call(el.options || [], function (o) {
            return o.value.toLowerCase() === state.toLowerCase();
          });
          if (match) el.value = match.value;
        }
      });

      var algo = field("algo");
      if (algo) algo.value = "0";

      mlPct.value = "";
      mlPct.placeholder = "not calculated for " + state;
      note.hidden = false;
      note.textContent =
        state + ": server, Running Type and Running Days are kept in sync, " +
        "algo is set to 0, and ml_pct is not calculated.";
      return;
    }

    note.hidden = true;
    mlPct.placeholder = "max_loss / allocation";

    var maxLoss = parseFloat((field("max_loss") || {}).value);
    var allocation = parseFloat((field("allocation") || {}).value);

    if (isNaN(maxLoss) || isNaN(allocation) || allocation === 0) {
      mlPct.value = "";
      return;
    }
    // Trailing zeros trimmed so 1.5000 reads as 1.5.
    mlPct.value = String(parseFloat((maxLoss / allocation).toFixed(4)));
  }

  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  refresh();
})();
