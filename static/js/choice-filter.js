// Tick-box column filter, shared by the data grid and the Setup tab.
//
// The caller owns the selection Set and decides what to do when it changes;
// this module owns the menu DOM, positioning and dismissal only.
//
//   OMPChoice.build(holder, {
//     label:    "server",              // shown in the menu header
//     counts:   { VS1: 19, VS2: 4 },   // value -> number of rows
//     selected: Set,                   // mutated in place
//     onChange: function () { ... }
//   });
(function () {
  "use strict";

  function sortValues(counts) {
    // Numeric-looking values sort numerically; everything else naturally.
    return Object.keys(counts).sort(function (a, b) {
      var na = parseFloat(a), nb = parseFloat(b);
      if (!isNaN(na) && !isNaN(nb) && String(na) === a && String(nb) === b) return na - nb;
      return a.localeCompare(b, undefined, { numeric: true });
    });
  }

  function closeAll() {
    document.querySelectorAll(".choice-menu").forEach(function (m) { m.hidden = true; });
  }

  function position(button, menu) {
    var box = button.getBoundingClientRect();
    menu.style.top = (box.bottom + 4) + "px";
    menu.style.left = box.left + "px";

    var menuBox = menu.getBoundingClientRect();
    if (menuBox.right > window.innerWidth - 8) {
      menu.style.left = Math.max(8, window.innerWidth - menuBox.width - 8) + "px";
    }
    if (menuBox.bottom > window.innerHeight - 8) {
      menu.style.maxHeight = (window.innerHeight - box.bottom - 16) + "px";
      menu.style.overflowY = "auto";
    } else {
      menu.style.maxHeight = "";
      menu.style.overflowY = "";
    }
  }

  function syncLabel(holder, selected) {
    var label = holder.querySelector(".choice-label");
    var button = holder.querySelector(".choice-btn");
    if (!selected.size) {
      label.textContent = "Select...";
      button.classList.remove("active");
      return;
    }
    label.textContent = selected.size === 1
      ? Array.from(selected)[0] || "(blank)"
      : selected.size + " selected";
    button.classList.add("active");
  }

  function build(holder, opts) {
    var selected = opts.selected;
    var counts = opts.counts || {};
    var values = sortValues(counts);

    // Drop selections that no longer exist after a reload.
    Array.from(selected).forEach(function (v) {
      if (!(v in counts)) selected.delete(v);
    });

    var old = holder.querySelector(".choice-menu");
    if (old) old.remove();

    var menu = document.createElement("div");
    menu.className = "choice-menu";
    menu.hidden = true;

    var head = document.createElement("div");
    head.className = "choice-head";
    head.textContent = "Filter " + (opts.label || "");
    menu.appendChild(head);

    var clear = document.createElement("button");
    clear.type = "button";
    clear.className = "choice-clear";
    clear.textContent = "Clear all";
    clear.addEventListener("click", function () {
      selected.clear();
      menu.querySelectorAll("input").forEach(function (i) { i.checked = false; });
      syncLabel(holder, selected);
      opts.onChange();
    });
    menu.appendChild(clear);

    var list = document.createElement("div");
    list.className = "choice-list";

    values.forEach(function (value) {
      var row = document.createElement("label");
      row.className = "choice-item";

      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = selected.has(value);
      box.addEventListener("change", function () {
        box.checked ? selected.add(value) : selected.delete(value);
        syncLabel(holder, selected);
        opts.onChange();
      });

      var text = document.createElement("span");
      text.textContent = value === "" ? "(blank)" : value;

      var count = document.createElement("em");
      count.textContent = counts[value];

      row.appendChild(box);
      row.appendChild(text);
      row.appendChild(count);
      list.appendChild(row);
    });

    menu.appendChild(list);

    var done = document.createElement("button");
    done.type = "button";
    done.className = "choice-done";
    done.textContent = "Done";
    done.addEventListener("click", function () { menu.hidden = true; });
    menu.appendChild(done);

    holder.appendChild(menu);
    syncLabel(holder, selected);

    var button = holder.querySelector(".choice-btn");
    if (!button.dataset.bound) {
      button.dataset.bound = "1";
      button.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = holder.querySelector(".choice-menu");
        var wasHidden = open.hidden;
        closeAll();
        if (wasHidden) {
          open.hidden = false;
          position(button, open);
        }
      });
    }
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".choice")) return;
    closeAll();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll();
  });

  // The menu is position:fixed, so it must close when the page scrolls -
  // but scrolling the menu's own list must not dismiss it.
  window.addEventListener("scroll", function (e) {
    var target = e.target;
    if (target && target.closest && target.closest(".choice-menu")) return;
    closeAll();
  }, true);

  window.addEventListener("resize", closeAll);

  window.OMPChoice = { build: build, closeAll: closeAll };
})();
