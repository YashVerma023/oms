# 01 — Setup

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version`. On Windows use `py -3` if `python` is unavailable. |
| MySQL 8 | Running locally on port 3306 for development. |
| Git | — |

The app creates its own database and tables. You only need a MySQL account
with `CREATE DATABASE` and DDL rights.

---

## Install

```powershell
cd D:\2_projects\git_projects\omp

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks the activation script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

`pip` alone often is not on PATH on Windows — `python -m pip` always is.

---

## Configuration

All configuration comes from environment variables, loaded from `.env` at the
project root by `python-dotenv`. Nothing environment-specific is hardcoded.

`.env` is gitignored. `.env.example` is the committed template — update it
whenever you add a variable.

### Variables

| Variable | Default | Purpose |
|---|---|---|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | *(required)* | MySQL user |
| `DB_PASSWORD` | `""` | Empty is allowed for a default local install |
| `DB_NAME` | `omp` | Validated against `^[A-Za-z0-9_]{1,64}$` |
| `DB_POOL_SIZE` | `5` | SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | `10` | Connections allowed above the pool size |
| `LOG_LEVEL` | `INFO` | Root log level |
| `FLASK_HOST` | `127.0.0.1` | Bind address |
| `FLASK_PORT` | `5000` | Port |
| `FLASK_DEBUG` | `true` | **Set to `false` outside development** |
| `SECRET_KEY` | `dev-only-change-me` | Session signing key — must be replaced in production |
| `MAX_UPLOAD_MB` | `25` | Upload size cap, rejected before the file is read |
| `DEFAULT_ADMIN_ROLE` | `admin` | Bootstrap admin login |
| `DEFAULT_ADMIN_NAME` | `admin` | |
| `DEFAULT_ADMIN_EMAIL` | `admin@gmail.com` | |
| `DEFAULT_ADMIN_PASSWORD` | `admin123` | |
| `DEFAULT_SUPERADMIN_ROLE` | `superadmin` | Bootstrap superadmin login |
| `DEFAULT_SUPERADMIN_NAME` | `superadmin` | |
| `DEFAULT_SUPERADMIN_EMAIL` | `superadmin@gmail.com` | |
| `DEFAULT_SUPERADMIN_PASSWORD` | `sadmin123` | |

Bootstrap logins are inserted only when their email is absent, so a password
changed through the portal is not overwritten on restart.

Generate a production secret:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Run

```powershell
python app.py
```

Startup sequence, all idempotent and logged:

1. `setup_logging()` — file + console handlers.
2. `ensure_database()` — `CREATE DATABASE` if missing (utf8mb4).
3. `ensure_tables()` — creates only tables absent from `information_schema`.
4. `ensure_default_admin()` — inserts bootstrap logins whose email is absent.
5. Blueprints registered, server starts.

Restarting never destroys data: every step checks before it writes.

### Provisioning without starting the server

```powershell
python -m database.db        # database only, with a connectivity self-check
python -m database.schema    # database + tables + default logins
```

Both run assertions afterwards and fail loudly if the result is wrong.

---

## First login

| Role | Email | Password |
|---|---|---|
| admin | `admin@gmail.com` | `admin123` |
| superadmin | `superadmin@gmail.com` | `sadmin123` |

Superadmin additionally sees **Settings → MSUsers** for managing logins.

---

## Loading data

The tabs are empty until sheets are uploaded. Go to **Settings → Uploads** and
load, in any order:

| Card | File | Notes |
|---|---|---|
| All Users | `All User.xlsx` | Reads the `Main` worksheet |
| Jainam | `All User.xlsx` | Reads the `Jainam` worksheet |
| Running Users | `running-users.csv` | Appends a snapshot |
| Usersetting | `USERSETTING.csv` | Accepts several files at once |

Sample files live in `data/`. See [04-data-import.md](04-data-import.md).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `pip is not recognized` | Use `python -m pip`. |
| `Access denied for user` | Wrong `DB_USER`/`DB_PASSWORD`; the error is raised immediately rather than retried. |
| `Can't connect to MySQL` after 3 tries | MySQL is not running, or `DB_HOST`/`DB_PORT` are wrong. Retries are 3 × 2 s. |
| `Table 'x' already exists` | Case mismatch — see [03-database.md](03-database.md#table-name-casing). |
| `Out of range value for column` | A sheet value exceeds the column's `DECIMAL` precision. See [04-data-import.md](04-data-import.md#numeric-range-guard). |
| Styling looks stale | Hard refresh (`Ctrl+F5`); CSS is served statically and cached. |
