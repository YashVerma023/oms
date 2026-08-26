// Shared interaction behaviour: the confirm dialog, dismissible flashes, Esc
// handling and the "/" search shortcut. Loaded on every signed-in page.
//
// Nothing here touches data. It replaces browser dialogs with in-page ones and
// makes what already exists reachable from a keyboard.
(function () {
  "use strict";

  var doc = document;

  // -----------------------------------------------------------------------
  // OMPConfirm(options) -> Promise<boolean>
  //
  // A drop-in for window.confirm that can be styled, read on a phone and
  // closed with Esc. Falls back to window.confirm where <dialog> is missing,
  // so a decision is never silently skipped.
  // -----------------------------------------------------------------------
  var dialog = doc.getElementById("ompDialog");

  function confirmDialog(options) {
    var opts = typeof options === "string" ? { body: options } : (options || {});
    var text = opts.body || "";

    if (!dialog || typeof dialog.showModal !== "function") {
      return Promise.resolve(window.confirm(text));
    }

    doc.getElementById("ompDialogTitle").textContent = opts.title || "Confirm";
    var body = doc.getElementById("ompDialogBody");
    body.textContent = text;

    if (opts.warning) {
      var warn = doc.createElement("p");
      warn.className = "omp-dialog-warn";
      warn.textContent = opts.warning;
      body.appendChild(warn);
    }

    var ok = doc.getElementById("ompDialogOk");
    var cancel = doc.getElementById("ompDialogCancel");
    ok.textContent = opts.confirmLabel || "Continue";
    cancel.textContent = opts.cancelLabel || "Cancel";
    ok.classList.toggle("danger", !!opts.danger);

    return new Promise(function (resolve) {
      function finish(answer) {
        ok.removeEventListener("click", onOk);
        cancel.removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        if (dialog.open) dialog.close();
        resolve(answer);
      }
      function onOk() { finish(true); }
      function onCancel() { finish(false); }
      // Esc closes the dialog natively; that counts as cancelling.
      function onClose() { finish(false); }

      ok.addEventListener("click", onOk);
      cancel.addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);

      dialog.showModal();
      // The safe choice takes focus, not the one that writes.
      cancel.focus();
    });
  }

  window.OMPConfirm = confirmDialog;

  // -----------------------------------------------------------------------
  // Flashes: dismissible, and gone after a while if they are only good news.
  // -----------------------------------------------------------------------
  doc.querySelectorAll(".flash-close").forEach(function (button) {
    button.addEventListener("click", function () {
      var flash = button.closest(".flash");
      if (flash) flash.remove();
    });
  });

  // -----------------------------------------------------------------------
  // Esc closes whatever is open: the settings menu, or any choice filter.
  // -----------------------------------------------------------------------
  doc.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;

    var menu = doc.getElementById("settingsMenu");
    if (menu && menu.open) {
      menu.open = false;
      var summary = menu.querySelector("summary");
      if (summary) summary.focus();
      return;
    }

    var open = doc.querySelector(".choice-menu:not([hidden])");
    if (open) {
      open.hidden = true;
      var owner = open.closest(".choice");
      var trigger = owner && owner.querySelector(".choice-btn");
      if (trigger) trigger.focus();
    }
  });

  // -----------------------------------------------------------------------
  // "/" focuses the search box on a table page, the way every list UI does.
  // Ignored while typing into something else.
  // -----------------------------------------------------------------------
  doc.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.metaKey || event.ctrlKey) return;
    var active = doc.activeElement;
    var tag = active && active.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;

    var search = doc.querySelector(".search");
    if (search) {
      event.preventDefault();
      search.focus();
      search.select();
    }
  });
})();
