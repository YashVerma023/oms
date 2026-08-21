// The Usersetting CSVs button on the Setup tab, driven in jsdom against the
// real rendered markup.
//   python tests/render_setup.py /tmp/setup.html
//   node   tests/test_setup_download.js /tmp/setup.html
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const HTML = process.argv[2] || "/tmp/setup.html";

const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), { runScripts: "outside-only" });
const { window } = dom;

let requested = null;
let saved = null;
let response = {
  ok: true,
  headers: new Map([
    ["Content-Disposition", 'attachment; filename="USERSETTINGS 21 AUG 26.zip"'],
    ["X-OMP-Files", "3"],
    ["X-OMP-Skipped", ""],
  ]),
  blob: () => Promise.resolve("BLOB"),
};
response.headers.get = Map.prototype.get.bind(response.headers);

window.fetch = (url) => {
  requested = url;
  return Promise.resolve(response);
};
window.URL.createObjectURL = () => "blob:x";
window.URL.revokeObjectURL = () => {};
// Catch the save without letting jsdom try to navigate.
window.HTMLAnchorElement.prototype.click = function () {
  saved = this.download;
};

// Inline scripts do not run in this mode, so the page's own config block is
// evaluated by hand - the test then uses the URLs the template really emits.
const config = fs.readFileSync(HTML, "utf8")
  .match(/window\.OMP_SETUP\s*=\s*\{[\s\S]*?\};/);
assert.ok(config, "the page defines window.OMP_SETUP");
window.eval(config[0]);
assert.ok(window.OMP_SETUP.usersettingUrl, "the template passes usersettingUrl");

["static/js/download-files.js", "static/js/choice-filter.js", "static/js/setup.js"]
  .forEach((f) => window.eval(fs.readFileSync(path.join(ROOT, f), "utf8")));

const $ = (id) => window.document.getElementById(id);

let failed = 0;
function check(label, fn) {
  try { fn(); console.log("PASS  " + label); }
  catch (e) { failed++; console.log("FAIL  " + label + "\n      " + e.message); }
}

const button = $("btnDownloadUsersetting");

check("the button exists next to Run and Apply", () => {
  assert.ok(button, "btnDownloadUsersetting is on the page");
  assert.ok($("btnRun") && $("btnApply"));
  assert.strictEqual(button.disabled, false, "usable before the check is run");
});

button.click();

setTimeout(() => {
  check("it calls the usersetting download endpoint", () => {
    assert.strictEqual(requested, "/admin/usersetting/download");
  });

  check("it saves under the name the server chose", () => {
    assert.strictEqual(saved, "USERSETTINGS 21 AUG 26.zip");
  });

  check("it reports how many files came back", () => {
    assert.strictEqual($("setupNote").textContent,
      "3 file(s) downloaded: USERSETTINGS 21 AUG 26.zip.");
    assert.strictEqual($("setupNote").className, "upload-note ok");
  });

  check("the button is usable again", () => {
    assert.strictEqual(button.disabled, false);
  });

  // Now a response that leaves accounts out.
  response.headers.set("X-OMP-Skipped", "NOSERVER1,NOSERVER2");
  response.headers.set("X-OMP-Files", "2");
  button.click();

  setTimeout(() => {
    check("accounts with no server are named, not silently dropped", () => {
      const text = $("setupNote").textContent;
      assert.ok(text.indexOf("2 account(s) left out") !== -1, text);
      assert.ok(text.indexOf("NOSERVER1, NOSERVER2") !== -1, text);
      assert.strictEqual($("setupNote").className, "upload-note warn");
    });

    // And a failure.
    response.ok = false;
    response.status = 404;
    response.json = () => Promise.resolve({ error: "No usersetting rows to export." });
    button.click();

    setTimeout(() => {
      check("an error is shown, not swallowed", () => {
        assert.strictEqual($("setupNote").textContent,
          "No usersetting rows to export.");
        assert.strictEqual($("setupNote").className, "upload-note warn");
        assert.strictEqual(button.disabled, false, "still usable after a failure");
      });

      console.log(failed ? "\n" + failed + " FAILED"
                         : "\nAll Setup download checks passed");
      process.exit(failed ? 1 : 0);
    }, 20);
  }, 20);
}, 20);
