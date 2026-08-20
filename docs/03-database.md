# 03 — Database

MySQL 8, database `omp`, `utf8mb4` / `utf8mb4_unicode_ci`, InnoDB throughout.

---

## Provisioning

`database/db.py` and `database/schema.py` own this. Both are idempotent and
run on every startup.

| Function | Behaviour |
|---|---|
| `ensure_database()` | `SHOW DATABASES LIKE`, then `CREATE DATABASE` if absent. Logs which happened. |
| `ensure_tables()` | Reads `information_schema.TABLES`, creates only the missing ones, logs each by name. |
| `ensure_default_admin()` | Inserts bootstrap logins whose email is absent. |

Run standalone:

```powershell
python -m database.db        # database + connectivity self-check
python -m database.schema    # tables + default logins + self-check
```

### DDL is not transactional

MySQL commits DDL implicitly. If `ensure_tables()` fails partway, tables
created before the failure **remain**. The error log names exactly which were
created and which were not. There is deliberately no `rollback()` in that path
— it would be a lie.

### Table name casing

MySQL on Windows runs with `lower_case_table_names=1` and folds table names to
lowercase; Linux preserves case. To keep one codebase working on both:

- every table name in `TABLES` is declared **lowercase**;
- `existing_tables()` lowercases what it reads before comparing.

A mixed-case name like `Jainam` would be created as `jainam` on Windows and
then look "missing" on the next startup, which is exactly the bug this
prevents.

---

## Naming conventions

Column names are **verbatim sheet headers**, by project decision, so uploads
map 1:1 with no translation table. Consequences:

- Every identifier is backticked in SQL: `` `Running Type` ``, `` `FIX (CR)` ``.
- `` `0SL` `` starts with a digit and *must* be quoted everywhere.
- Bind parameters cannot contain spaces, so the code derives safe names:
  `_param("Running Type")` → `p_Running_Type`.

Three columns are renamed from their sheet header because the sheet name is
unusable or ambiguous — the mapping lives in `ImportSpec.renames`:

| Table column | Sheet header |
|---|---|
| `ml_pct` | `%` |
| `0SL` | `SL` |
| `Remarks` | `Remarks/Algo8 Previous day realised MTM` |

---

## Tables

### `all_users` — user master

Source: `All User.xlsx`, `Main` sheet. Load mode **replace**. 803 rows.

| Column | Type | Notes |
|---|---|---|
| `userId` | VARCHAR(32) | **PK**, not editable |
| `alias` | VARCHAR(120) | |
| `Broker` | VARCHAR(64) | |
| `max_loss` | DECIMAL(18,2) | |
| `allocation` | DECIMAL(18,2) | |
| `server` | VARCHAR(32) | linked field, indexed |
| `algo` | VARCHAR(8) | forced to `0` when inactive |
| `ml_pct` | DECIMAL(18,4) | **derived** = `max_loss / allocation` |
| `Running Type` | VARCHAR(20) | linked field, fixed option set |
| `Running Days` | VARCHAR(20) | linked field, fixed option set |
| `FIX (CR)` | DECIMAL(12,4) | |
| `0SL` | DECIMAL(18,2) | |
| `Remarks` | VARCHAR(255) | |
| `Operator Name` | VARCHAR(64) | indexed |
| `Category` / `SubCategory` | VARCHAR(16) | composite index |
| `Acc Type` | VARCHAR(32) | |
| `created_at` / `updated_at` | TIMESTAMP | maintained by MySQL |

`ml_pct` is `DECIMAL(18,4)`, not `(10,4)`: dealer rows carry an `allocation`
placeholder of `1.0`, so the ratio can reach millions. One real row
(`MONETAAA`) is 6,000,000 and would overflow a narrower column.

Dropped from the sheet on import: `is_CC`, `R%`, `Check SL`,
`Is positional(A1,7,15)`, `True/False`, `r_capial`, `r_allocation`,
`r_max_loss`, `Allocation check`, `Intraday Allocation%`, `todauy run`, `SL%`.

### `jainam`

Source: `All User.xlsx`, `Jainam` sheet. Load mode **replace**.
**Composite PK `(Date, UserID)`** — one row per user per date.

`Date` DATE · `UserID` VARCHAR(32) · `User Alias` · `Algo` ·
`VT` `GB` `PS` `RD` `RM` DECIMAL(18,4) · `ALLOCATION` · `MAX LOSS` ·
`Type` · `Expiry` · `created_at`.

### `running_users` — snapshot history

Source: `running-users.csv`. Load mode **append**. 26 sheet columns plus:

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT UNSIGNED AUTO_INCREMENT | **PK** |
| `imported_at` | TIMESTAMP | batch marker |

Indexed on `(userId, check_time)`, `imported_at`, `error`.

Because it is append-only, "current state" means the newest batch:

```sql
WHERE `imported_at` = (SELECT MAX(`imported_at`) FROM `running_users`)
```

The Running tab applies exactly that.

> `proxy` holds `http://user:pass@host:port` — **live broker credentials**.
> Restrict who can read this column.

### `usersetting`

Source: `USERSETTING.csv`. Load mode **replace**, multi-file. PK `User ID`.
All 37 sheet columns are kept. Header row is **row 7**; rows 1–6 are comments.

Header `' LIMIT Type'` has a leading space in the file and is stored stripped
as `` `LIMIT Type` ``.

Contains `API Key`, `API Secret`, `Password`, `Pin`, `TwoFA` in plain text —
treat this table as sensitive.

### `server_config`

Source: `Server Configs.xlsx`, first sheet `Servers`. 28 rows. PK `Server`.
Indexed on `Operator` and `Expiry`.

| Column | Type | Notes |
|---|---|---|
| `Server` | VARCHAR(16) | **PK** — VS1, VS2, … |
| `Username` | VARCHAR(32) | |
| `IP` | VARCHAR(45) | sized for IPv6 |
| `Password` | VARCHAR(128) | plain text from the sheet |
| `Stoxxo Id` | VARCHAR(64) | |
| `Stoxxo Password` | VARCHAR(64) | mixed int/text in the sheet |
| `Expiry` | DATE | **converted from an Excel serial** |
| `Subscriptions` / `Logins` / `Active` | INT | |
| `Avlbl` | INT | = Subscriptions − Logins |
| `Aum` | DECIMAL(18,4) | mixed int/float in the sheet |
| `Remarks` | VARCHAR(255) | |
| `Operator` | VARCHAR(64) | indexed |
| `Stoxxo URL` | VARCHAR(128) | licence-key style value |
| `created_at` / `updated_at` | TIMESTAMP | |

Two deliberate deviations from the sheet:

- **`Expiry` is a DATE, not an INT.** The cells use the General format, so
  openpyxl returns the raw Excel serial (`46265`), which is `2026-08-31`.
  Stored as a real date it is sortable and comparable; the import converts it.
- **`Dte` is not a column.** In the sheet it is `=Expiry-TODAY()`, a countdown
  that is wrong the day after upload. It is computed on read from `Expiry`.
  `Avlbl` is also a formula but a stable subtraction, so it is stored.

> Holds server passwords in plain text. Same sensitivity as `usersetting`.

### `login` — portal users

| Column | Type | Notes |
|---|---|---|
| `id` | INT UNSIGNED AUTO_INCREMENT | PK |
| `role` | VARCHAR(32) | one of superadmin, admin, data, operator, crm |
| `name` | VARCHAR(120) | |
| `email` | VARCHAR(190) | **UNIQUE** — 190 so the index fits utf8mb4 |
| `password` | VARCHAR(255) | **plain text**, by decision |
| `created_at` / `updated_at` | TIMESTAMP | |

> Plain-text passwords are an explicit project decision so superadmin can read
> them. This is only defensible on an isolated internal network. The upgrade
> path is a `werkzeug.security` hash plus a reset flow instead of a read path;
> it is marked with a `ponytail:` comment in `schema.py`.

---

## Query conventions

**Always parameterise values.** Identifiers cannot be parameterised in MySQL,
so anything that reaches SQL as an identifier is whitelisted instead:

- `DB_NAME` is regex-validated before `CREATE DATABASE`.
- Table and page names come from `TABLE_PAGES` / `IMPORT_SPECS`, never from
  user input — the URL segment selects a dict entry, it is not interpolated.
- Column names come from `information_schema`, i.e. from MySQL itself.

```python
# correct
db.session.execute(text("SELECT * FROM `all_users` WHERE `userId` = :pk"), {"pk": user_id})

# never
db.session.execute(text(f"SELECT * FROM `all_users` WHERE `userId` = '{user_id}'"))
```

---

## Changing the schema

There is no migration tool. To change a column:

1. Edit the DDL in `database/schema.py` (so fresh installs are correct).
2. Apply an `ALTER TABLE` by hand to existing databases.

```sql
ALTER TABLE omp.all_users MODIFY `ml_pct` DECIMAL(18,4) NULL;
```

`ensure_tables()` only creates missing tables — it never alters existing ones.
Dropping the table and restarting also works while the data is disposable.
