# 07 — Frontend

Jinja2 templates, one CSS file, three small JS files. No build step, no
framework, no package.json. Edit a file and refresh.

---

## Templates

```
templates/
├── base.html                 Shell: nav, Settings menu, theme toggle, flashes
├── login.html                Standalone (no nav)
├── shared/table.html         Generic data grid — all four tabs use this
├── admin/dashboard.html      Intentionally empty
├── admin/edit_user.html      all_users edit form
├── admin/uploads.html        Upload cards
└── superadmin/msusers.html   Login list + add user
```

`base.html` is the shared ~80%. Role templates extend it and fill `content`;
they should not restyle the shell.

Blocks: `title`, `nav`, `content`, `scripts`.

The nav is generated from `TABLE_PAGES`, injected by a context processor in
`app.py`, so a new tab needs no template edit.

---

## Styling

`static/css/style.css`, organised as: variables → nav → settings menu → page →
toolbar → grid → uploads → edit form → login → flashes → responsive.

### Theming

Two palettes as CSS custom properties on `:root` / `[data-theme="light"]`.
Dark is the default.

```css
--bg  --surface  --surface-2  --chip  --border
--text  --muted  --accent
--pos  --neg  --danger  --danger-hover
--nav-bg  --shadow
```

**Use the variables, never a literal colour** — that is what keeps both themes
correct without a second stylesheet.

The toggle lives top-right: in the navbar on app pages, `position: fixed` on
login. Preference persists in `localStorage` under `omp-theme`, and an inline
script in `<head>` applies it before first paint so the theme does not flash.

### Responsive

| Breakpoint | Behaviour |
|---|---|
| ≤1100px | Nav drops to its own row and scrolls horizontally |
| ≤820px | Nav collapses behind a hamburger; welcome text and Settings label hide |
| ≤560px | Page head and stat cards stack, toolbar wraps, search full width |

The mobile nav is a CSS-only checkbox toggle (`#navToggle`) — no JS.

---

## The data grid

`templates/shared/table.html` + `static/js/table.js`. One implementation
serves All Users, Jainam, Running and Usersetting.

Server renders headers and filter inputs; the browser fetches rows from
`/admin/api/table/<page_key>` and does everything else.

### Features

| Feature | Notes |
|---|---|
| Global search | Matches any column |
| Per-column filter | An input under each header |
| Sort | Click a header, click again to reverse; blanks always sort last |
| Paging | 50 / 100 / 500 / 2000 |
| Row select | Per row and select-all-on-page |
| CSV export | Exports the **filtered** view, not the whole table |
| Reload / clear filters | Toolbar buttons |
| Sticky header | Header and filter row stay put while scrolling |
| Edit link | Only when the page config defines `edit_endpoint` |

### Filter syntax

Text columns do a case-insensitive contains match. Numeric columns accept:

| Input | Meaning |
|---|---|
| `>10` `<50` `>=10` `<=50` `=10` | comparison |
| `10-20` or `10, 20` | inclusive range |
| `>100; <200` | chained with `;`, all must hold |
| any other text | falls back to contains |

`static/js/table.js` exposes the parser as `window.OMP_TABLE_FILTERS` for
console testing. 18 cases are verified: comparators, ranges, chains, nulls,
non-numeric input.

### Why client-side

Rows are fetched once (`MAX_ROWS = 5000`) and filtered in memory. At these
volumes it is faster than a request per keystroke and avoids a server-side
filter DSL entirely. Past ~5000 rows, filtering must move server-side.

---

## Adding a tab

Add an entry to `core/tables.py::TABLE_PAGES`:

```python
"my-tab": {
    "table": "my_table",
    "title": "My Tab",
    "hidden": ("created_at",),
    "visible": ("col_a", "col_b"),   # optional whitelist + display order
    "order_by": "`col_a`",
    "where": None,                   # optional row filter
}
```

That is the whole change. The nav link, route, grid, filters, sorting, paging
and CSV export all follow. Columns come from `information_schema`; a column
listed in `visible` that does not exist is skipped and logged rather than
breaking the page.

### Row filters in use

| Page | `where` | Why |
|---|---|---|
| Running | `imported_at = (SELECT MAX(imported_at) ...)` | Newest snapshot only, since the table is append-only history |
| Jainam | `Date = (SELECT MAX(Date) ... WHERE Date <= CURDATE())` | Today's rows, else the nearest earlier date; future dates never shown |

### Computed columns

`computed` maps a column name to a SQL expression evaluated at read time. The
column need not exist in the table:

```python
"computed": {"Dte": "DATEDIFF(`Expiry`, CURDATE())"}
```

Server Config uses this for `Dte` (days until expiry). It is a countdown, so a
stored copy would be wrong the day after upload; deriving it means it is always
correct. Computed columns filter and sort like any other, and list in `visible`
wherever they should appear.

### Display formats

`formats` maps a column to a rendering style applied in the browser:

```python
"formats": {"Expiry": "date"}     # 2026-02-02 -> 02-FEB-2026
```

The formatted string is precomputed once per load and used for **rendering,
filtering and CSV export**, so typing `FEB` in the filter matches. **Sorting
uses the raw value** — ISO dates sort chronologically as plain strings, whereas
`02-FEB-2026` would sort by day of month and put October before August.

Pages with a `where` should also set `as_of_sql`, which renders a caption above
the toolbar — Jainam shows "Showing today, 2026-08-19" or "No data for today —
showing the most recent date, 2026-08-14", since the Date column is otherwise
constant and the filtering invisible.

---

## JavaScript

| File | Purpose |
|---|---|
| `theme.js` | Theme toggle; closes the Settings dropdown on outside click/Escape |
| `table.js` | The data grid |
| `edit_user.js` | Live preview of the business rules on the edit form |

All three are IIFEs that exit early if their target elements are absent, so
loading them on the wrong page is harmless. Configuration is passed from Jinja
via a `window.OMP_*` object rather than parsed out of the DOM.

The Settings dropdown is a native `<details>` element — click-to-open needs no
JS; the script only handles outside-click and Escape.

**No JavaScript is trusted.** `edit_user.js` previews the rules, and the server
re-applies them on save regardless.
