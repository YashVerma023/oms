// Downloading a server-built file, shared by the Usersetting and Setup tabs.
//
// Fetched rather than followed as a plain link: the response carries headers
// saying how many files came back and which accounts were left out, and the
// page needs to show that.
(function () {
  "use strict";

  function filenameFrom(response, fallback) {
    var disposition = response.headers.get("Content-Disposition") || "";
    var match = disposition.match(/filename="([^"]+)"/);
    return match ? match[1] : fallback;
  }

  function save(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  // url:    where to fetch from
  // report: function(message, kind) - "ok" or "warn"
  // button: disabled while the request is in flight (optional)
  function download(url, report, button) {
    if (button) button.disabled = true;
    report("Building the files...");

    return fetch(url, { headers: { "Accept": "*/*" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok) {
          return r.json()
            .catch(function () { return { error: "HTTP " + r.status }; })
            .then(function (body) { throw new Error(body.error || ("HTTP " + r.status)); });
        }

        var name = filenameFrom(r, "usersettings.zip");
        var count = r.headers.get("X-OMP-Files") || "";
        var skipped = (r.headers.get("X-OMP-Skipped") || "").split(",")
          .filter(function (s) { return s; });

        return r.blob().then(function (blob) {
          save(blob, name);

          // A compiled workbook is one file, so its row count is the useful
          // number; the per-server export reports the file count instead.
          var rows = r.headers.get("X-OMP-Rows");
          var message = rows
            ? "Downloaded " + name + " - " + rows + " row(s)."
            : count + " file(s) downloaded: " + name + ".";
          if (skipped.length) {
            message += " " + skipped.length + " account(s) left out - no server: "
              + skipped.slice(0, 5).join(", ")
              + (skipped.length > 5 ? "..." : "");
          }
          report(message, skipped.length ? "warn" : "ok");
        });
      })
      .catch(function (err) {
        console.error(err);
        report(err.message, "warn");
      })
      .finally(function () { if (button) button.disabled = false; });
  }

  window.OMPDownload = download;
})();
