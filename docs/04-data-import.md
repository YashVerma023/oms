# 04 — Data import

`core/importer.py`. Reached from **Settings → Uploads**
(`/admin/uploads`), one card per upload target.

---

## Targets

Declared in `IMPORT_SPECS`:

| Key | Table | File | Sheet | Header row | Mode | Key | Multi-file |
|---|---|---|---|---|---|---|---|
| `all-users` | `all_users` | `.xlsx` | `Main` | 1 | replace | `userId` | no |
| `jainam` | `jainam` | `.xlsx` | `Jainam` | 1 | replace | `Date` + `UserID` | no |
| `server-config` | `server_config` | `.xlsx` | `Servers` | 1 | replace | `Server` | no |
| `running` | `running_users` | `.csv` | — | 1 | append | `userId` | no |
| `usersetting` | `usersetting` | `.csv` | — | **7** | replace_scope (by `server`) | `User ID` | **yes** |

**replace** — the table is emptied and reloaded; the sheet is authoritative.
**append** — rows are added, preserving history.
**replace_scope** — only rows matching the uploaded scope values are replaced.

### Values derived from the file name

`usersetting` files are named per server:

```
VS1 19 AUG 2026 USERSETTING.csv   ->  server = VS1
```

`ImportSpec.filename_columns` extracts it. The server must be the **first
token and separated** from what follows by a space, underscore or hyphen;
matching is case-insensitive and stored uppercase. A run-together name such as
`VS2819AUG2026USERSETTINGS.csv` is **rejected** rather than read as `VS2819`,
as are names where the server is not first. One bad name aborts the whole
upload before anything is written.

Because each file carries its own server, `usersetting` uses
**`mode="replace_scope"`** with `scope_column="server"`: uploading VS1 and VS2
replaces only those two servers' rows and leaves VS3..VS28 untouched. Plain
`replace` would silently delete every other server's settings.

Adding a target is one `ImportSpec`; the Uploads page renders cards from the
registry.

---

## Pipeline

```
upload → read → match columns → coerce values → post-process → dedupe → write
```

1. **Read** — `openpyxl` with `data_only=True` (cached formula *results*, not
   `=VLOOKUP(...)`) or stdlib `csv`. CSV decodes as `utf-8-sig`, falling back
   to `latin-1` with a warning. Headers are stripped.
2. **Match** — for each *table* column, find the sheet header of the same name,
   honouring `ImportSpec.renames`. Sheet columns with no matching table column
   are ignored and listed in the report. Table columns absent from the sheet
   load as NULL and are noted.
3. **Coerce** — each value is converted to its column's MySQL type, read from
   `information_schema` (`DATA_TYPE`, `COLUMN_TYPE`, length, precision, scale).
4. **Post-process** — optional per-row hook. `all-users` uses
   `core.rules.apply`; see [05-business-rules.md](05-business-rules.md).
   Decimals are re-fitted afterwards because a rule can produce a new value.
5. **Dedupe** — see below.
6. **Write** — one transaction.

---

## Type coercion

| Column type | Handling |
|---|---|
| `tinyint(1)` (BOOLEAN) | `true/yes/y/1/t` → True, `false/no/n/0/f` → False, else NULL |
| `decimal` | commas stripped; a trailing `%` divides by 100; rounded to the column scale; rejected if wider than its precision |
| int family | parsed as decimal then truncated |
| `datetime` | ISO 8601 including `2026-08-19T03:05:55.368Z`; converted to **naive UTC** because MySQL DATETIME has no timezone |
| `date` | as datetime, date part kept |
| Excel date serials | a bare number in a date column is read as days since 1899-12-30 (`46265` → `2026-08-31`), bounded to 1900–9999 so ordinary integers in numeric columns are never misread |
| `time` | `HH:MM[:SS]` |
| varchar/text | stripped, truncated to `CHARACTER_MAXIMUM_LENGTH` |

### Null tokens

These become NULL in **any** column type:

```
""   -   --   #N/A   #VALUE!   #REF!   #DIV/0!   #NAME?   #NULL!   nan
```

Deliberately **not** in that list: `Not Running`, `NA`, `As per VU`. These are
real values in text columns such as `server` and `Operator Name` — treating
them as null silently blanked 143 `server` values and 156 `Operator Name`
values during development. Non-numeric text landing in a *numeric* column is
caught by the coercer instead and reported as unparseable.

### Numeric range guard

`_fit_decimal()` rounds to the column's scale and returns NULL if the value is
wider than its precision.

Without it, one oversized cell raises `Out of range value for column` and
aborts the whole upload. With it, that single value becomes NULL, is counted
in the report, and the other 802 rows load.

The case that motivated it: `all_users.ml_pct` = `max_loss / allocation`, and
dealer rows carry an `allocation` placeholder of `1.0`, so user `MONETAAA`
computes to 6,000,000 — beyond `DECIMAL(10,4)`. The column was widened to
`DECIMAL(18,4)` *and* the guard added, because the next such row will not be
predicted either.

---

## Deduplication

**No duplicate key is ever written, for any target**, regardless of load mode.
Within one upload, the **last occurrence wins** — inside a single file and
across multiple files. Every drop is reported with the key and the file it
came from.

Keys are the `pk` tuple of the spec; composite keys join their parts.

Note this is *within an upload*. `running_users` legitimately repeats a
`userId` across imports — that is the history. "Current state" queries filter
on `MAX(imported_at)`.

---

## Writing

```python
if spec.mode == "replace":
    DELETE FROM `table`                              # not TRUNCATE
elif spec.mode == "replace_scope":
    DELETE FROM `table` WHERE `server` IN (uploaded) # other scopes survive
INSERT ... (batched, CHUNK = 500)
COMMIT
```

`DELETE`, not `TRUNCATE`, is deliberate: `TRUNCATE` commits implicitly, so a
failure during the inserts would leave the table empty with the old data gone.
`DELETE` participates in the transaction, so a failed import rolls back to the
previous contents.

---

## Validation and errors

Rejected before parsing, with a flash message:

- unknown target;
- no file selected;
- more than one file for a single-file target;
- wrong extension;
- larger than `MAX_UPLOAD_MB` (Flask rejects it before reading).

Raised during parsing as `ValueError` (whole upload rejected, nothing written):

- sheet name absent from the workbook;
- fewer rows than the configured header row;
- no table column matched any sheet header;
- a key column missing from the sheet;
- no loadable rows.

Handled per row (upload proceeds):

- missing key → row skipped and reported;
- duplicate key → earlier row dropped and reported;
- unparseable value → NULL, counted per column in the report.

---

## The report

Shown after upload and written to `logs/omp.log`:

- rows loaded / skipped / columns matched;
- per-file row counts, when several were uploaded;
- ignored sheet columns;
- per-column count of values stored as NULL;
- up to 50 individual notes (capped so a broken file cannot flood the page).

---

## Verified behaviour

Exercised against the real files in `data/`:

| Target | Rows | Columns matched | Dropped |
|---|---|---|---|
| all-users | 803 | 17 | 12 |
| jainam | 28 | 13 | 17 |
| server-config | 28 | 15 | 1 (`Dte`, derived on read) |
| running | 378 | 26 | 0 |
| usersetting | 21 per file | 37 + `server` from the file name | 0 |

Also confirmed: three usersetting files (VS1, VS2, VS28) load 63 rows tagged
21/21/21 by server; 8 valid file names parse and 9 malformed ones are rejected,
including `VS2819AUG2026USERSETTINGS.csv`; a 383-row file with 5 repeated
`userId`s loads 378 and reports 5; duplicates across two files resolve to the later file's row;
after import, every `all_users` row satisfies the business rules and every
decimal fits its column.
