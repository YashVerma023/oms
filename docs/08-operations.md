# 08 — Operations

## Logging

Configured once by `setup_logging()` in `database/db.py`, called from
`create_app()`. Output goes to `logs/omp.log` **and** the console.

```
2026-08-19 14:15:58,289 | INFO | database.schema | Created table 'all_users'
```

Level from `LOG_LEVEL` (default `INFO`). `mysql.connector` is pinned to
WARNING — it logs authentication-plugin details at INFO on every connect.

Use the `logging` module, never `print`. Every module has
`logger = logging.getLogger(__name__)`, so the log names the source module.

### What is logged

| Event | Level |
|---|---|
| Database / table / default login created | INFO |
| Row counts loaded per page | INFO |
| Import summary — target, files, loaded, skipped, columns | INFO |
| User update, with resulting server / running type / algo / ml_pct | INFO |
| Login success, logout | INFO |
| Failed login, with email and remote address | WARNING |
| Access denied by `@roles_required` | WARNING |
| Connection retry | WARNING |
| Column in `visible` missing from the table | ERROR |
| Any failed write, with traceback | ERROR |

There is **no log rotation**. `logs/*.log` is gitignored. Add rotation before
this runs unattended for long.

---

## Failure modes

| Failure | Behaviour |
|---|---|
| MySQL unreachable at startup | 3 retries, 2 s apart, then `ConnectionError`; the app does not start |
| Bad credentials | Raised immediately, not retried — retrying cannot help |
| `ensure_tables` fails partway | DDL auto-commits, so earlier tables persist; the log names created vs remaining |
| Import fails | Transaction rolls back; existing data intact |
| Import has bad rows | Good rows load; bad ones are skipped and reported |
| Oversized decimal | That value becomes NULL and is reported; the upload continues |
| Upload too large | Rejected by Flask before the body is read |
| MySQL closes an idle connection | `pool_pre_ping` discards it; `pool_recycle=280` refreshes before MySQL's default 300 s |

---

## Health check

`GET /health` executes `SELECT 1`.

```json
{"status": "ok", "database": "omp"}
```

Returns 503 with a `detail` field if the database is unreachable. Suitable for
a container health check or load-balancer probe. It is not authenticated.

---

## Deployment checklist

Nothing here is done yet — this is the list for when it is.

- [ ] `FLASK_DEBUG=false` — the debugger is a remote code execution hole
- [ ] `SECRET_KEY` set to a real random value
- [ ] Real WSGI server (`gunicorn`/`waitress`), not `app.run()`
- [ ] MySQL user scoped to the `omp` database, not root
- [ ] Log rotation configured
- [ ] `SESSION_COOKIE_SECURE = True` behind HTTPS
- [ ] CSRF protection added
- [ ] Password hashing decided (see below)
- [ ] Backups for `omp`
- [ ] `.env` present on the host and **not** in the image

`app` is module-level in `app.py`, so `gunicorn app:app` works as-is.

---

## Known issues and risks

### Security

1. **Plain-text passwords** in `login`, by explicit decision, so superadmin can
   read them. Defensible only on an isolated network. Upgrade path: store a
   `werkzeug.security` hash and replace the read path with a reset flow. Marked
   with a `ponytail:` comment in `database/schema.py`.
2. **No CSRF protection.** Every form is vulnerable to cross-site submission,
   including Add User and Upload. Flask-WTF or a token in the session is the
   fix.
3. **Live broker credentials in the database.** `running_users.proxy` holds
   `http://user:pass@host:port`; `usersetting` holds API keys, secrets,
   passwords, PINs and 2FA seeds — all plain text from the source sheets.
   Restrict database read access and be careful with CSV export.
4. **No rate limiting** on login.

### Functional

5. **No schema migrations.** Column changes need a manual `ALTER TABLE`; only
   missing *tables* are auto-created.
6. **Client-side filtering caps out** around `MAX_ROWS = 5000`. `running_users`
   is append-only, so the table grows even though the view filters to the
   newest batch.
7. **`data`, `operator` and `crm` roles have no views.** They can log in and
   land on the admin dashboard, which they should not see once real
   restrictions matter.
8. **The dashboard is empty**, pending a decision on what it should show.
9. **No automated test suite.** Logic has been verified with one-off scripts
   against the real files in `data/`; those checks are not committed.

### Data quality

10. **Dealer rows carry placeholder values** — `max_loss` and `allocation` of
    `1.0` — which make `ml_pct` meaningless. Rule 1 nulls it for
    `DLR ACC` / `NOT RUNNING` rows, but placeholder rows that are *not* marked
    inactive still produce inflated ratios.
11. **`Acc Type` is `#N/A` for 777 of 803 rows** in the source sheet, stored as
    NULL.
12. **Excel formula columns** are read as cached values. A workbook saved by a
    tool that does not compute formulas will yield NULLs.

---

## Roadmap

Rough order of value:

1. Decide and build the dashboard.
2. CSRF protection.
3. Views and restrictions for `data`, `operator`, `crm`.
4. Validation/reconciliation checks — the comparisons the spreadsheet did with
   `r_allocation`, `r_max_loss` and the positional calculations.
5. Scheduled import of `running-users.csv` instead of manual upload.
6. Edit and delete in MSUsers.
7. A committed test suite.
8. Docker Compose for deployment.
