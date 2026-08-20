# 02 — Architecture

## Principles

1. **`core/` holds business logic and knows nothing about HTTP.** No `request`,
   no `session`, no `flash`. It is callable from a route, a script or a test.
2. **`roles/<role>/` holds only what differs between roles.** The shared ~80%
   lives in `core/` and `templates/base.html`. Roles are Flask blueprints.
3. **The database is the source of truth for structure.** Columns are read
   from `information_schema` at request time rather than duplicated in Python,
   so a schema change propagates without edits elsewhere.
4. **Configuration comes from the environment.** No credentials, paths or
   hostnames in code.
5. **One write path per entity.** Every `all_users` write goes through
   `core.all_users.update_user`, so rules cannot be bypassed by a new caller.

---

## Module map

```
app.py                 Application factory; wires blueprints and config
  │
  ├── auth.py          Session auth + @login_required / @roles_required
  │
  ├── roles/admin      Dashboard, table pages, edit form, uploads
  ├── roles/superadmin MSUsers
  │
  ├── core/
  │   ├── tables.py    Reads rows for the table pages (page registry)
  │   ├── importer.py  Parses uploads and writes them
  │   ├── rules.py     all_users derivation rules (pure functions)
  │   ├── all_users.py Single-record read/update
  │   └── logins.py    Portal user CRUD
  │
  └── database/
      ├── db.py        Engine, provisioning, logging setup
      └── schema.py    DDL + bootstrap logins
```

Dependency direction is strictly downward: `roles → core → database`.
`core.rules` depends on nothing but the standard library, which is why it is
trivially testable.

---

## Startup

`create_app()` in `app.py`:

```
setup_logging()
  → Flask(__name__), SECRET_KEY, MAX_CONTENT_LENGTH
  → init_app(app)              database/db.py
        ensure_database()      CREATE DATABASE IF absent
        ensure_tables()        CREATE TABLE for absent tables only
        ensure_default_admin() INSERT bootstrap logins if absent
        db.init_app(app)       bind Flask-SQLAlchemy
  → register_blueprint(auth, admin, superadmin)
  → context_processor: table_pages   (drives the nav)
```

`app` is created at import time so a WSGI server can load `app:app`; the
`__main__` block is only for the development server.

`database/db.py` imports `database/schema.py` *inside* `init_app` rather than
at module top, because schema imports db — a deliberate local import to break
the cycle.

---

## Request flow

### A table page

```
GET /admin/table/running
  → @roles_required("admin", "superadmin")
  → TABLE_PAGES["running"]              page registry in core/tables.py
  → get_columns()                       information_schema, filtered/ordered
  → renders templates/shared/table.html with column headers only
  → browser runs static/js/table.js
       → GET /admin/api/table/running   JSON: {columns, rows}
       → renders, filters, sorts, pages entirely client-side
```

Rows are fetched once and filtered in the browser. At these volumes (hundreds
to low thousands) that is faster than a round trip per keystroke and removes a
whole class of server-side filter-parsing code. `MAX_ROWS = 5000` caps the
payload; past that, filtering must move server-side.

### An edit

```
GET  /admin/all-users/<userId>/edit   → form, rules previewed live in JS
POST same
  → core.all_users.update_user()
       parse form values by column type
       core.rules.apply()              linkage + ml_pct, authoritative
       UPDATE ... WHERE userId = :pk   parameterised
  → redirect to the table with a flash describing what the rules changed
```

The JavaScript preview is a convenience. The server re-applies `rules.apply()`
on every save, so disabling JS or posting by hand changes nothing.

### An upload

See [04-data-import.md](04-data-import.md).

---

## The page registry

`core/tables.py::TABLE_PAGES` is a dict of page key → config. It drives the
nav, the routes and the queries at once:

```python
"jainam": {
    "table": "jainam",
    "title": "Jainam",
    "hidden": ("created_at",),         # never displayed
    "visible": (...),                  # optional whitelist + display order
    "order_by": "`UserID`",
    "where": "`Date` = (SELECT MAX(...))",   # optional row filter
    "as_of_sql": "...",                # optional caption
    "edit_endpoint": "admin.edit_user",# optional per-row edit link
    "edit_key": "userId",
}
```

Adding a tab is one entry — the nav, the route, the grid, filtering, sorting,
paging and CSV export all follow. See [07-frontend.md](07-frontend.md).

---

## Design decisions and their trade-offs

| Decision | Why | Cost |
|---|---|---|
| Columns read from `information_schema` | Schema stays the single source of truth | One extra metadata query per request |
| Client-side filtering | No filter DSL on the server; instant UX | Breaks down past ~5000 rows |
| Sheet header names kept verbatim as columns | Uploads map 1:1; no mental translation | Every query needs backticks; `` `0SL` `` starts with a digit |
| Raw SQL via SQLAlchemy Core, no ORM models | Column names with spaces are painful to model; queries stay obvious | No lazy relationships or unit-of-work |
| Plain Flask session, not Flask-Login | ~30 lines, no dependency | Remember-me and user loaders are hand-rolled if ever needed |
| DDL in `schema.py`, no Alembic | Zero setup for a greenfield internal app | Column changes require manual `ALTER TABLE` |
| No frontend build step | Anyone can edit a file and refresh | No bundling, minification or JSX |

---

## Adding functionality

**A new table tab** → add DDL to `database/schema.py`, add an entry to
`TABLE_PAGES`. Nothing else.

**A new uploadable sheet** → add an `ImportSpec` to `IMPORT_SPECS` in
`core/importer.py`. The Uploads page renders a card per spec.

**A new role** → create `roles/<role>/__init__.py` with a blueprint, register
it in `app.py`, add its landing endpoint to `ROLE_HOME` in `auth.py`. See
[06-auth-and-roles.md](06-auth-and-roles.md).

**A new business rule on all_users** → put it in `core/rules.py::apply`. Both
the edit form and the importer call it, so it applies everywhere at once.
