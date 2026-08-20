# OMP — Operations Management Portal

Internal Flask portal for managing algo-trading account configuration. It
replaces a set of shared spreadsheets with a MySQL-backed web app: upload the
sheets, browse them as filterable tables, and edit records under enforced
business rules.

**Status:** in development. Admin and superadmin roles are implemented; the
`data`, `operator` and `crm` roles exist in the login table but have no views
of their own yet.

---

## Stack

| Layer    | Choice                                   |
|----------|------------------------------------------|
| Backend  | Python 3.11+, Flask 3                    |
| Database | MySQL 8, Flask-SQLAlchemy + mysql-connector-python |
| Frontend | Jinja2 templates, vanilla JS, plain CSS (no build step) |
| Parsing  | openpyxl (xlsx), stdlib `csv`            |
| Deploy   | Docker (planned; local MySQL for now)    |

No frontend toolchain, no ORM models, no migration framework. Tables are
created from DDL in `database/schema.py` on startup.

---

## Quick start

```bash
# 1. Virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate           # Linux/macOS

# 2. Dependencies
python -m pip install -r requirements.txt

# 3. Configure — copy .env.example to .env and set DB credentials
copy .env.example .env

# 4. Run. Creates the database, tables and default logins if missing.
python app.py
```

Open <http://127.0.0.1:5000>.

| Role       | Email                    | Password    |
|------------|--------------------------|-------------|
| admin      | admin@gmail.com          | `admin123`  |
| superadmin | superadmin@gmail.com     | `sadmin123` |

Change these in `.env` before anyone else can reach the host.

---

## Layout

```
omp/
├── app.py                  Application factory and entry point
├── auth.py                 Login, logout, @login_required, @roles_required
├── requirements.txt
├── .env                    Local config — never committed
│
├── core/                   Business logic, no HTTP concerns
│   ├── tables.py           Read layer for the data-table pages
│   ├── importer.py         Sheet upload → MySQL loader
│   ├── rules.py            all_users business rules
│   ├── all_users.py        Read/update for the all_users record
│   └── logins.py           Portal user CRUD
│
├── database/
│   ├── db.py               Connection, database provisioning, logging setup
│   └── schema.py           Table DDL and default logins
│
├── roles/                  One package per role — the ~20% that differs
│   ├── admin/              Dashboard, tables, edit, uploads
│   └── superadmin/         MSUsers (everything else inherited from admin)
│
├── templates/
│   ├── base.html           Shared shell — the ~80% every role reuses
│   ├── shared/table.html   Generic data grid, used by all four tabs
│   ├── admin/  superadmin/
│
├── static/css, static/js
├── data/                   Source spreadsheets
├── logs/                   omp.log
└── docs/                   Documentation (start with docs/README.md)
```

---

## Documentation

| Document | Covers |
|---|---|
| [docs/01-setup.md](docs/01-setup.md) | Environment, configuration, running locally |
| [docs/02-architecture.md](docs/02-architecture.md) | Module boundaries, request flow, design decisions |
| [docs/03-database.md](docs/03-database.md) | Schema reference, provisioning, conventions |
| [docs/04-data-import.md](docs/04-data-import.md) | Upload pipeline, type coercion, deduplication |
| [docs/05-business-rules.md](docs/05-business-rules.md) | ml_pct and the NOT RUNNING / DLR ACC linkage |
| [docs/06-auth-and-roles.md](docs/06-auth-and-roles.md) | Sessions, roles, adding a new role |
| [docs/07-frontend.md](docs/07-frontend.md) | Templates, theming, the data grid |
| [docs/08-operations.md](docs/08-operations.md) | Logging, troubleshooting, known issues, roadmap |

---

## Known limitations

Read [docs/08-operations.md](docs/08-operations.md) before deploying. The
short version:

- **Passwords are stored in plain text**, by explicit decision, so superadmin
  can read them. Acceptable only on an isolated internal network.
- `running_users.proxy` contains live broker credentials in the connection
  string. Restrict database read access accordingly.
- No CSRF protection on forms yet.
- No schema migration tool — column changes need a manual `ALTER TABLE`.
