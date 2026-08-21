// Drives the real dashboard markup + dashboard.js in jsdom.
// Render the page first, then:
//   python tests/render_page.py /admin/ /tmp/dash.html
//   node   tests/test_dashboard_ui.js /tmp/dash.html
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const HTML = process.argv[2] || "/tmp/dash.html";

// algo 1 (VS1: MSR 2 users, CCV 1) and algo 7 (VS2: MSS 1)
const DATA = {
  date: "2026-08-21",
  mode: "4DTE",
  source: "schedule",
  weekday: "Friday",
  dte: "Running Type POS, Running Days Daily",
  totals: { users: 4, CC: 1, MS: 3 },
  rows: [
    { name: "1", kind: "algo", users: 3, CC: 1, MS: 2, children: [
      { name: "VS1", kind: "server", users: 3, CC: 1, MS: 2, children: [
        { name: "MSR", kind: "subcategory", users: 2, CC: 0, MS: 2, children: [
          { name: "AAA11 (alpha)", kind: "user", users: 1, CC: 0, MS: 0, children: [] },
          { name: "BBB22 (beta)", kind: "user", users: 1, CC: 0, MS: 0, children: [] }]},
        { name: "CCV", kind: "subcategory", users: 1, CC: 1, MS: 0, children: [
          { name: "CCC33 (gamma)", kind: "user", users: 1, CC: 0, MS: 0, children: [] }]}]}]},
    { name: "7", kind: "algo", users: 1, CC: 0, MS: 1, children: [
      { name: "VS2", kind: "server", users: 1, CC: 0, MS: 1, children: [
        { name: "MSS", kind: "subcategory", users: 1, CC: 0, MS: 1, children: [
          { name: "DDD44 (delta)", kind: "user", users: 1, CC: 0, MS: 0, children: [] }]}]}]}
  ]
};

const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), { runScripts: "outside-only" });
const { window } = dom;
window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(DATA) });
window.URL.createObjectURL = () => "blob:x";
window.URL.revokeObjectURL = () => {};
window.OMP_PIVOT = { dataUrl: "/admin/api/dashboard", today: "2026-08-21" };

["static/js/filters.js", "static/js/dashboard.js"].forEach(
  (f) => window.eval(fs.readFileSync(path.join(ROOT, f), "utf8")));

const $ = (id) => window.document.getElementById(id);
const names = () => [...window.document.querySelectorAll("#pivotBody .pivot-name")]
  .map((e) => e.textContent);
const type = (el, v) => {
  el.value = v;
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
};

// Every check starts from a clean slate: a failed assertion must not leave a
// filter behind and fail the next three checks for the wrong reason.
function reset() {
  type($("pivotSearch"), "");
  window.document.querySelectorAll(".col-filter").forEach((el) => type(el, ""));
  $("btnCollapse").click();
}

let failed = 0;
function check(label, fn) {
  try { reset(); fn(); console.log("PASS  " + label); }
  catch (e) { failed++; console.log("FAIL  " + label + "\n      " + e.message); }
}

setTimeout(() => {
  check("header and cells share the same columns", () => {
    const heads = [...window.document.querySelectorAll(".head-row .th-sort span")]
      .map((e) => e.textContent);
    assert.deepStrictEqual(heads, ["Name", "Users", "CC", "MS"]);
    const cells = window.document.querySelectorAll("#pivotBody tr:first-child td");
    assert.strictEqual(cells.length, 5, "expand + 4 columns");
    assert.strictEqual(cells[2].className, "num");
    assert.strictEqual(cells[2].querySelector(".chip").textContent, "3");
  });

  check("collapsed by default: only algos show", () => {
    assert.deepStrictEqual(names(), ["Algo - 1", "Algo - 7"]);
  });

  check("the active DTE mode is spelled out", () => {
    assert.strictEqual($("pivotMode").textContent,
      "Mode 4DTE (Friday): showing Running Type POS, Running Days Daily.");
  });

  check("totals reach the stat cards", () => {
    assert.strictEqual($("totUsers").textContent, "4");
    assert.strictEqual($("totCC").textContent, "1");
    assert.strictEqual($("totMS").textContent, "3");
  });

  check("caret opens one level only", () => {
    window.document.querySelector("#pivotBody .caret").click();
    assert.deepStrictEqual(names(), ["Algo - 1", "Server - VS1", "Algo - 7"]);
  });

  check("second caret opens the whole branch", () => {
    const deep = window.document.querySelectorAll("#pivotBody tr:first-child .caret")[1];
    assert.ok(deep, "algo row has a subtree caret");
    deep.click();
    assert.deepStrictEqual(names(), ["Algo - 1", "Server - VS1", "MSR",
      "AAA11 (alpha)", "BBB22 (beta)", "CCV", "CCC33 (gamma)", "Algo - 7"]);
  });

  check("user rows show '-' for CC/MS, not 0", () => {
    $("btnExpand").click();
    const user = [...window.document.querySelectorAll("#pivotBody tr")]
      .find((tr) => tr.className.indexOf("kind-user") !== -1);
    const chips = [...user.querySelectorAll("td.num .chip")].map((c) => c.textContent);
    assert.deepStrictEqual(chips, ["1", "-", "-"]);
  });

  check("searching a user keeps its ancestors visible", () => {
    type($("pivotSearch"), "DDD44");
    assert.deepStrictEqual(names(),
      ["Algo - 7", "Server - VS2", "MSS", "DDD44 (delta)"]);
  });

  check("numeric column filter: Users >2 keeps only the rows that match", () => {
    type(window.document.querySelector('.col-filter[data-col="users"]'), ">2");
    // MSR (2) and CCV (1) are below the threshold and have no matching
    // descendant, so they drop out; their ancestors stay because they match.
    assert.deepStrictEqual(names(), ["Algo - 1", "Server - VS1"]);
  });

  check("range filter: Users 1, 1", () => {
    type(window.document.querySelector('.col-filter[data-col="users"]'), "1, 1");
    assert.ok(names().indexOf("Algo - 7") !== -1);
    assert.strictEqual(names().indexOf("Algo - 1"), 0, "kept: descendants match");
  });

  check("'/' keeps rows that have a value", () => {
    type(window.document.querySelector('.col-filter[data-col="CC"]'), "/");
    // Only user rows have a blank CC, so none of them survive.
    assert.ok(names().length > 0);
    assert.ok(!names().some((n) => n.indexOf("(") !== -1), names().join(" | "));
  });

  check("' ' keeps blanks - user rows, plus the ancestors that lead to them", () => {
    type(window.document.querySelector('.col-filter[data-col="CC"]'), " ");
    const shown = names();
    assert.ok(shown.indexOf("AAA11 (alpha)") !== -1, shown.join(" | "));
    assert.ok(shown.indexOf("Algo - 1") !== -1, "ancestor kept");
  });

  const usersHead = [...window.document.querySelectorAll(".th-sort")]
    .find((b) => b.dataset.col === "users");

  check("sort by Users, ascending then descending", () => {
    usersHead.click();
    assert.deepStrictEqual(names(), ["Algo - 7", "Algo - 1"]);
    usersHead.click();
    assert.deepStrictEqual(names(), ["Algo - 1", "Algo - 7"]);
    assert.ok(usersHead.classList.contains("sorted"));
    assert.ok(usersHead.classList.contains("desc"));
  });

  check("sort reaches nested levels too", () => {
    $("btnExpand").click();               // still sorted descending by Users
    const shown = names();
    assert.ok(shown.indexOf("MSR") < shown.indexOf("CCV"),
      "MSR (2 users) should sit above CCV (1): " + shown.join(" | "));
  });

  check("paging splits the top level", () => {
    const sel = $("pivotPageSize");
    sel.value = "10";
    sel.dispatchEvent(new window.Event("change"));
    assert.strictEqual($("pivotPageLabel").textContent, "Page 1 of 1");
    assert.strictEqual($("pivotFirst").disabled, true);
    assert.strictEqual($("pivotNext").disabled, true);
    assert.strictEqual($("pivotCount").textContent, "2 algo(s).");
  });

  check("empty result shows the empty panel", () => {
    type($("pivotSearch"), "nothing-matches-this");
    assert.strictEqual($("pivotEmpty").hidden, false);
    assert.strictEqual($("pivotCount").textContent, "0 of 2 algo(s).");
    type($("pivotSearch"), "");
    assert.strictEqual($("pivotEmpty").hidden, true);
  });

  check("a second page is reachable", () => {
    const sel = $("pivotPageSize");
    const opt = window.document.createElement("option");
    opt.textContent = "1";                // smallest shipped option is 10
    sel.appendChild(opt);
    sel.value = "1";
    sel.dispatchEvent(new window.Event("change"));
    assert.strictEqual($("pivotPageLabel").textContent, "Page 1 of 2");
    assert.deepStrictEqual(names(), ["Algo - 1"]);
    $("pivotNext").click();
    assert.strictEqual($("pivotPageLabel").textContent, "Page 2 of 2");
    assert.deepStrictEqual(names(), ["Algo - 7"]);
    assert.strictEqual($("pivotNext").disabled, true);
    $("pivotFirst").click();
    sel.value = "50";
    sel.dispatchEvent(new window.Event("change"));
  });

  let captured = "";
  window.Blob = function (parts) { captured = parts[0]; };

  check("CSV export covers every level, users with blank CC/MS", () => {
    $("btnPivotExport").click();
    const lines = captured.split("\n");
    assert.strictEqual(lines[0], "level,name,users,CC,MS");
    assert.strictEqual(lines.length, 12, "header + 2 algos + 2 servers + 3 subs + 4 users");
    assert.ok(captured.indexOf('user,"DDD44 (delta)",1,,') !== -1, captured);
    assert.ok(captured.indexOf('algo,"Algo - 1",3,1,2') !== -1, captured);
  });

  check("CSV export respects the active filter", () => {
    type(window.document.querySelector('.col-filter[data-col="name"]'), "VS2");
    $("btnPivotExport").click();
    assert.ok(captured.indexOf("VS2") !== -1, captured);
    assert.ok(captured.indexOf("VS1") === -1, "filtered-out branch stays out");
  });

  console.log(failed ? "\n" + failed + " FAILED" : "\nAll dashboard UI checks passed");
  process.exit(failed ? 1 : 0);
}, 30);
