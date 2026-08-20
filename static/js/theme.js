// Light/dark toggle. Preference persists in localStorage across sessions.
(function () {
  var KEY = "omp-theme";
  var btn = document.getElementById("themeToggle");
  if (!btn) return;

  btn.addEventListener("click", function () {
    var root = document.documentElement;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    localStorage.setItem(KEY, next);
  });
})();

// Close the settings dropdown on outside click or Escape.
(function () {
  var menu = document.getElementById("settingsMenu");
  if (!menu) return;

  document.addEventListener("click", function (e) {
    if (menu.open && !menu.contains(e.target)) menu.open = false;
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") menu.open = false;
  });
})();

