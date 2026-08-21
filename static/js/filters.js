// Column-filter semantics, shared by the data tables and the dashboard pivot.
// One copy so " " (blanks) and "/" (non-blanks) mean the same thing everywhere.
(function () {
  "use strict";

  function isBlank(value) {
    return value === null || value === undefined || String(value).trim() === "";
  }

  function text(value, needle) {
    return String(value === null || value === undefined ? "" : value)
      .toLowerCase().indexOf(needle.toLowerCase()) !== -1;
  }

  // Numeric filters accept: >10  <50  >=10  <=50  10-20  10,20  or a plain
  // number. Terms are separated by ';' and all must hold.
  function numeric(value, expr) {
    if (isBlank(value)) return false;
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

      return text(value, part);
    });
  }

  // A cell passes when the raw filter box is empty, blank-only (" "), "/" or
  // an expression. Returns true when nothing is being filtered.
  function cell(value, raw, isNumber) {
    var expr = (raw || "").trim();
    if (raw !== "" && expr === "") return isBlank(value);
    if (expr === "/") return !isBlank(value);
    if (!expr) return true;
    return isNumber ? numeric(value, expr) : text(value, expr);
  }

  window.OMPFilter = { isBlank: isBlank, text: text, numeric: numeric, cell: cell };
})();
