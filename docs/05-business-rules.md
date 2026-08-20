# 05 — Business rules

`core/rules.py`. Pure functions, no database or Flask imports, so they are
directly testable.

Applied in **both** write paths:

- `core/all_users.py::update_user` — the edit form;
- `core/importer.py` — the All Users upload, via `ImportSpec.post_process`.

The edit form also previews them in JavaScript
(`static/js/edit_user.js`). That is feedback only — the server re-applies
`rules.apply()` on every save, so bypassing the JS changes nothing.

---

## Rule 1 — linked running state

`server`, `Running Type` and `Running Days` are linked.

If **any one** of them is set to `NOT RUNNING` or `DLR ACC`:

1. the other two are set to the same value;
2. `algo` is forced to `"0"`;
3. `ml_pct` is set to NULL.

Matching is case-insensitive, so `not running`, `Not Running` and
`NOT RUNNING` are all recognised. The stored value is always the canonical
uppercase form.

## Rule 2 — derived `ml_pct`

```
ml_pct = max_loss / allocation
```

Never entered by hand — the field is disabled on the form and excluded from
the update statement's input.

Returns NULL when:

- the row is inactive per Rule 1;
- `max_loss` or `allocation` is NULL;
- `allocation` is 0 (no division by zero);
- the result is wider than `DECIMAL(18,4)` (see
  [04-data-import.md](04-data-import.md#numeric-range-guard)).

## Rule 3 — canonical option values

Legacy sheet values are snapped onto the option lists, case-insensitively:
`Not Running` → `NOT RUNNING`, `DAILY` → `Daily`, `int` → `INT`. Unrecognised
values pass through stripped but unchanged, so nothing is silently discarded.

---

## Option sets

**Running Type**

```
DLR ACC · NOT RUNNING · POS · INT
```

**Running Days**

```
DLR ACC · NOT RUNNING · Daily · 1DTE/0DTE · 0DTE
```

**server** — a dropdown built at render time from the distinct `server` values
already in `all_users`, plus `DLR ACC` and `NOT RUNNING` pinned first. It is a
dropdown rather than free text so a typo cannot break the linkage.

---

## Editing

`/admin/all-users/<userId>/edit`, reachable from the pencil icon on each row of
the All Users tab.

Every field is editable **except**:

| Field | Why |
|---|---|
| `userId` | Primary key |
| `ml_pct` | Derived by Rule 2 |
| `created_at`, `updated_at` | Maintained by MySQL |

The set is `READONLY_COLUMNS` in `core/all_users.py`; everything else is
generated from `information_schema`, so a new column appears on the form
automatically.

Empty fields are stored as NULL. Numeric fields are parsed with the importer's
coercion helpers rather than a second parser.

After saving, the flash message states what the rules changed — either the
recalculated `ml_pct`, or that the row was marked inactive and which fields
were aligned.

---

## Reference

```python
NOT_RUNNING     = "NOT RUNNING"
DLR_ACC         = "DLR ACC"
INACTIVE_STATES = (DLR_ACC, NOT_RUNNING)
LINKED_FIELDS   = ("server", "Running Type", "Running Days")
INACTIVE_ALGO   = "0"

canonical(value, options)      # case-insensitive snap onto an option list
inactive_state(record)         # -> "DLR ACC" | "NOT RUNNING" | None
compute_ml_pct(max_loss, allocation)
apply(record)                  # normalises in place, returns it
```

`apply()` takes a dict keyed by **real column names** (`"Running Type"`, not a
slug), so rules read the way they are described here.

---

## Verified behaviour

- All 3 linked fields × 4 casings of each inactive state → all three fields
  aligned, `algo = "0"`, `ml_pct` NULL.
- Active row: `max_loss = -150000`, `allocation = 7500000` → `ml_pct = -0.02`.
- `allocation = 0` → NULL. `max_loss = NULL` → NULL.
- Full `All User.xlsx` import: 156 inactive rows with zero linkage, algo or
  `ml_pct` violations; 647 active rows with `ml_pct` matching
  `max_loss / allocation` exactly.

---

## Open question

For inactive rows `ml_pct` currently stores **NULL** (rendered blank). Storing
`0` instead is a one-line change in `rules.apply` if that reads better on the
floor.
