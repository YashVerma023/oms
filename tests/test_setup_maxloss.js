// Setup tab: allocation and max loss reviewed together, applied together.
//   python tests/render_page.py /admin/setup /tmp/setup.html
//   node   tests/test_setup_maxloss.js /tmp/setup.html
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const HTML = process.argv[2] || "/tmp/setup.html";

const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), { runScripts: "outside-only" });
const { window } = dom;

// One allocation change (A1), one account already correct (A2).
const RUN = {
  in_scope: 2, mismatch: 1, match: 1, reconciled: true, reconcile_message: "",
  rows: [
    { userid: "A1", alias: "A1", server: "VS1", algo: "1", operator_name: "op",
      subcategory: "MSR", rule: "Category", capital: 100000, current: 100000,
      expected: 200000, status: "Mismatch", remark: "", apply: true },
    { userid: "A2", alias: "A2", server: "VS2", algo: "7", operator_name: "op",
      subcategory: "MSR", rule: "Category", capital: 50000, current: 90000,
      expected: 90000, status: "Match", remark: "", apply: false },
  ],
  maxloss: {
    mode: "1DTE",
    counts: { changed: 2, skipped: 1 },
    rows: [
      // Rests on A1's proposed allocation.
      { userid: "A1", alias: "A1", server: "VS1", algo: "1", subcategory: "MSR",
        operator_name: "op", source: "Allocation x multiplier",
        allocation: 200000, stored_allocation: 100000,
        depends_on_allocation: true, current: 200000, mstech: 400000,
        stoxxo: 400000, changed: true, status: "Mismatch", note: "" },
      // Independent of any allocation change.
      { userid: "A2", alias: "A2", server: "VS2", algo: "7", subcategory: "MSR",
        operator_name: "op", source: "Max Loss sheet", allocation: 90000,
        stored_allocation: 90000, depends_on_allocation: false, current: 0,
        mstech: 111, stoxxo: 222, changed: true, status: "Mismatch", note: "" },
      // Nothing to write.
      { userid: "A3", alias: "A3", server: "VS2", algo: "7", subcategory: "MSR",
        operator_name: "op", source: "", allocation: 1, stored_allocation: 1,
        depends_on_allocation: false, current: 0, mstech: null, stoxxo: null,
        changed: false, status: "Left alone", note: "Not running today" },
    ],
  },
};

let posted = [];
window.fetch = (url, opts) => {
  posted.push({ url: url, body: JSON.parse(opts.body) });
  const payload = url.indexOf("apply") !== -1
    ? { allocations: 1, remarks: 1, all_users: 2, usersetting: 2 }
    : RUN;
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
};
window.confirm = (message) => { window.__confirmed = message; return true; };

const source = fs.readFileSync(HTML, "utf8");
const config = source.match(/window\.OMP_SETUP\s*=\s*\{[\s\S]*?\};/);
assert.ok(config, "the page defines window.OMP_SETUP");
window.eval(config[0]);

["static/js/download-files.js", "static/js/choice-filter.js", "static/js/setup.js"]
  .forEach((f) => window.eval(fs.readFileSync(path.join(ROOT, f), "utf8")));

const $ = (id) => window.document.getElementById(id);
const rowsIn = (id) => Array.from($(id).querySelectorAll("tr"));
const cells = (tr) => Array.from(tr.querySelectorAll("td")).map((td) => td.textContent);

let failed = 0;
function check(label, fn) {
  try { fn(); console.log("PASS  " + label); }
  catch (e) { failed++; console.log("FAIL  " + label + "\n      " + e.message); }
}

$("checkDate").value = "2026-08-21";
$("btnRun").click();

setTimeout(() => {
  check("one run fetch fills both tables", () => {
    assert.strictEqual(posted.length, 1);
    assert.strictEqual(rowsIn("setupBody").length, 2);
    assert.strictEqual(rowsIn("maxlossBody").length, 3);
  });

  check("the max loss tab is hidden until picked", () => {
    assert.strictEqual($("tabMaxloss").hidden, true);
    assert.strictEqual($("tabAllocation").hidden, false);
  });

  check("only changeable rows are tickable", () => {
    // A3 has nothing to write, so it gets no checkbox.
    const boxes = $("maxlossBody").querySelectorAll("input[type=checkbox]");
    assert.strictEqual(boxes.length, 2);
    assert.ok(Array.from(boxes).every((b) => b.checked), "changes start ticked");
  });

  check("columns are in the asked-for order, US before AU", () => {
    // select, userId, alias, server, algo, operator, sub category, rule,
    // allocation, max loss US, max loss AU, status, remark
    const a2 = cells(rowsIn("maxlossBody")[1]);
    assert.deepStrictEqual(a2, ["", "A2", "A2", "VS2", "7", "op", "MSR",
      "Max Loss sheet", "90,000", "222", "111", "Mismatch", ""]);
  });

  check("the counts reach the pills and the tabs", () => {
    assert.strictEqual($("pillMaxloss").textContent, "2 max loss to change");
    assert.strictEqual($("tabAllocCount").textContent, "1");
    assert.strictEqual($("tabMaxlossCount").textContent, "2");
  });

  check("a max loss on a ticked allocation is not flagged", () => {
    const a1 = cells(rowsIn("maxlossBody")[0]).join("|");
    assert.ok(a1.indexOf("needs allocation") === -1, a1);
  });

  // Untick A1's allocation change: its max loss now rests on nothing.
  $("setupBody").querySelector("input[type=checkbox]").click();

  check("unticking the allocation flags the max loss that depends on it", () => {
    const a1 = cells(rowsIn("maxlossBody")[0]).join("|");
    assert.ok(a1.indexOf("needs allocation") !== -1, a1);
  });

  check("but it is still applicable - warn, do not block", () => {
    const box = $("maxlossBody").querySelector("input[type=checkbox]");
    assert.strictEqual(box.checked, true);
    assert.ok($("btnApply").textContent.indexOf("0 allocation(s)") !== -1,
      $("btnApply").textContent);
    assert.ok($("btnApply").textContent.indexOf("2 max loss(es)") !== -1,
      $("btnApply").textContent);
  });

  check("switching tab shows the max loss table", () => {
    window.document.querySelector('[data-tab="maxloss"]').click();
    assert.strictEqual($("tabMaxloss").hidden, false);
    assert.strictEqual($("tabAllocation").hidden, true);
  });

  check("each table filters on its own search box", () => {
    $("maxlossSearch").value = "A2";
    $("maxlossSearch").dispatchEvent(new window.Event("input"));
    assert.strictEqual(rowsIn("maxlossBody").length, 1);
    assert.strictEqual(rowsIn("setupBody").length, 2, "allocation is unaffected");
    $("maxlossSearch").value = "";
    $("maxlossSearch").dispatchEvent(new window.Event("input"));
  });

  // Re-tick the allocation and apply.
  $("setupBody").querySelector("input[type=checkbox]").click();
  posted = [];
  $("btnApply").click();

  setTimeout(() => {
    check("apply sends both sets in one request", () => {
      assert.strictEqual(posted.length, 2, "apply, then the re-run");
      const body = posted[0].body;
      assert.deepStrictEqual(body.updates,
        [{ userid: "A1", expected: 200000 }]);
      assert.deepStrictEqual(body.maxloss, [
        { userid: "A1", mstech: 400000, stoxxo: 400000 },
        { userid: "A2", mstech: 111, stoxxo: 222 },
      ]);
      assert.strictEqual(body.date, "2026-08-21");
    });

    check("the confirmation names all four tables", () => {
      const message = window.__confirmed;
      ["all_users.allocation", "usersetting.Remarks", "all_users.max_loss",
       "usersetting.Max Loss"].forEach((name) => {
        assert.ok(message.indexOf(name) !== -1, name + " missing from: " + message);
      });
    });

    console.log(failed ? "\n" + failed + " FAILED"
                       : "\nAll Setup max loss checks passed");
    process.exit(failed ? 1 : 0);
  }, 20);
}, 20);
