"""Setup tab - Allocation Check against the OMP database.

The rules themselves live in `core/allocation_check.py`, vendored unchanged
from the Morning Comparison tool, with its parameters in
`config/allocation_rules.json`. Nothing here re-implements a rule: this module
only

  * loads the DataFrames the checker expects out of MySQL,
  * normalises them the way the Streamlit loaders did, and
  * writes the resulting expected allocation back.

Writing is the difference from the original tool, which only reported:
  * `all_users.allocation`  for the chosen date  -> the expected allocation
  * `usersetting.Remarks`                        -> that same allocation value
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

from core import alias_rule
from core import allocation_check as ac
from database.db import db

logger = logging.getLogger(__name__)

RULES_PATH = Path(ac.DEFAULT_RULES_PATH)
MODES = ("0DTE", "1DTE", "4DTE")

# all_users column -> the name the checker expects.
ALL_USERS_COLUMNS = {
    "userId": "userid",
    "alias": "alias",
    "allocation": "allocation",
    "max_loss": "max_loss",
    "server": "server",
    "algo": "algo",
    "SubCategory": "subcategory",
    "Category": "category",
    "Running Type": "runningtype",
    "Running Days": "runningdays",
    "FIX (CR)": "fix_cr",
    "0SL": "sl",
    "Broker": "broker",
    "Operator Name": "operator_name",
}


# ---------------------------------------------------------------------------
# Rules file
# ---------------------------------------------------------------------------

def rules_text() -> str:
    """The rules file as text, for the Admin Controls editor."""
    try:
        return RULES_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Could not read the rules file at %s", RULES_PATH)
        return ""


def save_rules(raw: str) -> None:
    """Validate and write the rules file.

    Validation runs before anything is written, so a bad edit cannot reach
    disk. The previous version is kept as `.bak`.

    Raises:
        ValueError: the text is not valid JSON, or breaks a rule constraint.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    try:
        ac._validate_rules(parsed, RULES_PATH)
    except ac.AllocationRulesError as exc:
        raise ValueError(str(exc)) from exc

    if RULES_PATH.exists():
        shutil.copy2(RULES_PATH, RULES_PATH.with_suffix(".json.bak"))

    tmp = RULES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    tmp.replace(RULES_PATH)          # atomic: a crash cannot half-write it
    logger.info("Allocation rules updated at %s", RULES_PATH)


# ---------------------------------------------------------------------------
# Loading from MySQL
# ---------------------------------------------------------------------------

def _frame(sql: str, params: dict | None = None) -> pd.DataFrame:
    rows = db.session.execute(text(sql), params or {}).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def load_all_users(on_date: dt.date) -> pd.DataFrame:
    """all_users for one date, shaped and normalised for the checker."""
    selected = ", ".join(f"`{c}`" for c in ALL_USERS_COLUMNS)
    df = _frame(
        f"SELECT {selected} FROM `all_users` WHERE `Date` = :d", {"d": on_date}
    )
    if df.empty:
        return df

    df = df.rename(columns=ALL_USERS_COLUMNS)
    return _normalise(df)


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """The same normalisation the Streamlit loaders applied.

    Note allocation is NOT divided by 100 here: that scaling belonged to the
    raw Running file, and all_users values are already in portal units.
    """
    df = df.copy()
    df["userid"] = (
        df["userid"].astype(str).str.strip().str.replace(" ", "", regex=False).str.upper()
    )
    df["alias"] = df["alias"].astype(str).str.strip()
    df["server"] = df["server"].astype(str).str.strip().str.lower()

    for column in ("allocation", "max_loss"):
        df[column] = np.floor(pd.to_numeric(df[column], errors="coerce"))

    for column in ("runningtype", "runningdays"):
        df[column] = (
            df[column].astype(str).str.strip().str.replace(" ", "", regex=False).str.lower()
        )

    df["operator_name"] = df["operator_name"].astype(str).str.strip().replace(
        {"nan": "", "None": "", "NaT": "", "<NA>": ""}
    )
    return df


def load_running() -> pd.DataFrame:
    """The newest running_users snapshot. Only userid and capital are used."""
    df = _frame(
        "SELECT `userId`, `capital` FROM `running_users` "
        "WHERE `imported_at` = (SELECT MAX(`imported_at`) FROM `running_users`)"
    )
    if df.empty:
        return df

    df = df.rename(columns={"userId": "userid"})
    df["userid"] = (
        df["userid"].astype(str).str.strip().str.replace(" ", "", regex=False).str.upper()
    )
    # capital is used raw - the /100 scaling applies to allocation, not capital.
    df["capital"] = pd.to_numeric(df["capital"], errors="coerce")
    return df


def load_jainam() -> pd.DataFrame | None:
    """The jainam table, standing in for the workbook's Jainam sheet.

    Every row is used regardless of `Date`, matching the documented rule -
    `prepare_jainam_sheet` drops the Total row and de-duplicates userids
    itself. The table is loaded in replace mode, so it holds exactly what the
    last uploaded sheet held.

    Returns None when the table is empty: MSJ accounts are then reported as
    mismatches with an explanatory remark rather than silently skipped.
    """
    df = _frame("SELECT `UserID`, `ALLOCATION` FROM `jainam`")
    return None if df.empty else df


def load_rules() -> dict:
    return ac.load_rules(str(RULES_PATH))


def available_dates() -> list[str]:
    rows = db.session.execute(
        text("SELECT DISTINCT `Date` FROM `all_users` ORDER BY `Date` DESC")
    ).scalars().all()
    return [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in rows]


# ---------------------------------------------------------------------------
# Running the check
# ---------------------------------------------------------------------------

def run_check(
    on_date: dt.date,
    mode: str,
    previous_date: dt.date | None = None,
    rounding_basis: Any = None,
    servers: list[str] | None = None,
) -> dict[str, Any]:
    """Compute expected allocations for `on_date`.

    Returns a dict with the consolidated rows and a summary. Nothing is
    written - `apply_changes` does that.

    Raises:
        ValueError: a required input is missing or the rules reject the run.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Choose one of {', '.join(MODES)}.")

    rules = load_rules()

    # The basis is chosen per run on the Setup tab; the saved file keeps the
    # default. Mode and divisor stay as configured.
    if rounding_basis not in (None, ""):
        try:
            basis = int(float(rounding_basis))
        except (TypeError, ValueError):
            raise ValueError(f"Rounding basis '{rounding_basis}' is not a number.") from None
        if basis <= 0:
            raise ValueError("Rounding basis must be above 0.")
        rules = {**rules, "rounding": {**rules.get("rounding", {}), "basis": basis}}

    df_all = load_all_users(on_date)
    if df_all.empty:
        raise ValueError(f"No All Users rows for {on_date}. Upload that date first.")

    # An operator only ever sees - and writes - accounts on their own servers.
    if servers is not None:
        if not servers:
            raise ValueError(
                "No servers are assigned to you in Server Config, so there is "
                "nothing to set up."
            )
        allowed = {str(s).strip().lower() for s in servers}
        df_all = df_all[df_all["server"].isin(allowed)]
        if df_all.empty:
            raise ValueError(
                f"No accounts on your servers ({', '.join(sorted(allowed))}) "
                f"for {on_date}."
            )

    df_run = load_running()
    if df_run.empty:
        raise ValueError("No Running Users data. Upload a running-users file first.")

    df_prev = None
    if previous_date:
        df_prev = load_all_users(previous_date)
        if df_prev.empty:
            raise ValueError(f"No All Users rows for the previous date {previous_date}.")

    required_modes = set(rules.get("previous_day", {}).get("required", []))
    if df_prev is None and mode in required_modes:
        raise ValueError(
            f"{mode} needs a previous-day date - the rules mark it as required."
        )

    try:
        tables = ac.build_allocation_check(
            df_all, df_run, mode, rules, df_prev=df_prev, df_jainam=load_jainam()
        )
        in_scope, _ = ac.apply_dte_scope(df_all, mode, rules)
    except ac.AllocationRulesError as exc:
        raise ValueError(str(exc)) from exc

    consolidated = ac.build_consolidated(tables, in_scope)

    # server / algo / operator_name are not part of the consolidated frame, so
    # they are attached by userid lookup. A merge on a non-unique key would
    # silently multiply rows.
    attributes = _attributes(in_scope)
    ok, message = ac.reconcile(len(in_scope), tables)
    if not ok:
        logger.error("Allocation reconciliation failed: %s", message)

    rows = _rows_for_display(consolidated, attributes, mode)
    return {
        "rows": rows,
        "in_scope": int(len(in_scope)),
        "mismatch": sum(1 for r in rows if r["status"] == ac.STATUS_MISMATCH),
        "match": sum(1 for r in rows if r["status"] == ac.STATUS_MATCH),
        "reconciled": ok,
        "reconcile_message": message,
    }


def _attributes(in_scope: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """userid -> the account attributes shown alongside the check result."""
    wanted = ["server", "algo", "operator_name"]
    present = [c for c in wanted if c in in_scope.columns]
    if "userid" not in in_scope.columns or not present:
        return {}

    frame = in_scope[["userid", *present]].drop_duplicates("userid", keep="first")
    return {
        str(row["userid"]): {c: row[c] for c in present}
        for _, row in frame.iterrows()
    }


def _apply_alias_rule(rows: list[dict[str, Any]], mode: str) -> None:
    """Price algos 8/19/27 off the alias size, in place.

    Those algos sit in `excluded_algos`, so every one of their accounts arrives
    here as 'Not under check' - except MSJ, which is exempted upstream and
    already carries a Jainam result. Only untouched rows are rewritten, so the
    Jainam accounts and the row count both stay exactly as the engine left them.
    """
    priced = skipped = 0

    for row in rows:
        if row.get("status") != ac.STATUS_NOT_CHECKED:
            continue                      # MSJ, or checked by another rule
        if not alias_rule.handles(row.get("algo")):
            continue

        if alias_rule.share(row.get("algo"), mode) == alias_rule.SKIP:
            row["rule"] = alias_rule.RULE_LABEL
            row["remark"] = f"Algo {alias_rule.algo_key(row.get('algo'))} does not run on {mode}"
            skipped += 1
            continue

        expected = alias_rule.allocation(row.get("alias"), row.get("algo"), mode)
        if expected is None:
            row["rule"] = alias_rule.RULE_LABEL
            row["remark"] = (
                alias_rule.ZERO_REMARK if alias_rule.size(row.get("alias")) is not None
                else alias_rule.NO_SUFFIX_REMARK
            )
            skipped += 1
            continue

        current = row.get("current")
        matches = current is not None and Decimal(str(current)) == expected
        row["rule"] = alias_rule.RULE_LABEL
        row["expected"] = float(expected)
        row["status"] = ac.STATUS_MATCH if matches else ac.STATUS_MISMATCH
        row["remark"] = "" if matches else "Allocation differs from the alias size"
        priced += 1

    if priced or skipped:
        logger.info(
            "Alias rule (%s): %d account(s) priced from the alias, %d skipped.",
            mode, priced, skipped,
        )


def _rows_for_display(
    consolidated: pd.DataFrame,
    attributes: dict[str, dict[str, Any]] | None = None,
    mode: str = "",
) -> list[dict[str, Any]]:
    """Consolidated frame -> plain JSON-safe dicts, mismatches first."""
    if consolidated.empty:
        return []

    def clean(value):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        return value

    wanted = {
        "user_id": "userid",
        "user alias": "alias",
        "sub category": "subcategory",
        "rule": "rule",
        "allocation": "current",
        "expected allocation": "expected",
        "capital": "capital",
        "status": "status",
        "remark": "remark",
    }

    attributes = attributes or {}
    rows = []
    for _, row in consolidated.iterrows():
        out = {alias: clean(row.get(source)) for source, alias in wanted.items()}

        extra = attributes.get(str(out["userid"]), {})
        for name in ("server", "algo", "operator_name"):
            out[name] = clean(extra.get(name))

        rows.append(out)

    # Runs before `apply` is set: the alias rule turns 'Not under check' rows
    # into real Match/Mismatch results, and those are what may be written.
    _apply_alias_rule(rows, mode)

    for out in rows:
        out["apply"] = (
            out["status"] == ac.STATUS_MISMATCH and out["expected"] is not None
        )

    order = {ac.STATUS_MISMATCH: 0, ac.STATUS_MATCH: 1}
    rows.sort(key=lambda r: (order.get(r["status"], 2), str(r["userid"])))
    return rows


# ---------------------------------------------------------------------------
# Applying the result
# ---------------------------------------------------------------------------

def apply_changes(
    on_date: dt.date,
    updates: list[dict[str, Any]],
    servers: list[str] | None = None,
) -> dict[str, int]:
    """Write expected allocations back.

    For each entry:
        all_users.allocation  for `on_date`  = expected
        usersetting.Remarks   for that user  = the same expected value

    Both writes happen in one transaction: a failure leaves neither applied.

    Returns:
        Counts of rows updated in each table.
    """
    rows = [
        {"pk": str(u["userid"]).strip(), "value": Decimal(str(u["expected"]))}
        for u in updates
        if u.get("userid") and u.get("expected") is not None
    ]

    # Re-check ownership at write time: the run-time filter is not a guarantee
    # that these particular user ids came from it.
    if servers is not None:
        rows = _owned_by(rows, servers, on_date)

    if not rows:
        return {"allocations": 0, "remarks": 0}

    try:
        allocations = db.session.execute(
            text(
                "UPDATE `all_users` SET `allocation` = :value "
                "WHERE `userId` = :pk AND `Date` = :d"
            ),
            [{**r, "d": on_date} for r in rows],
        ).rowcount

        # Remarks carries the allocation figure, not prose.
        remarks = db.session.execute(
            text("UPDATE `usersetting` SET `Remarks` = :value WHERE `User ID` = :pk"),
            [{"pk": r["pk"], "value": _plain(r["value"])} for r in rows],
        ).rowcount

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Applying allocation changes for %s failed", on_date)
        raise

    logger.info(
        "Allocation check applied for %s: %s all_users row(s), %s usersetting row(s)",
        on_date, allocations, remarks,
    )
    return {"allocations": allocations, "remarks": remarks}


def _owned_by(
    rows: list[dict[str, Any]], servers: list[str], on_date: dt.date
) -> list[dict[str, Any]]:
    """Drop any row whose account is not on one of `servers`."""
    if not servers or not rows:
        return []

    owned = set(
        db.session.execute(
            text(
                "SELECT `userId` FROM `all_users` "
                "WHERE `Date` = :d AND `server` IN :servers"
            ).bindparams(bindparam("servers", expanding=True)),
            {"d": on_date, "servers": servers},
        ).scalars().all()
    )

    kept = [r for r in rows if r["pk"] in owned]
    if len(kept) != len(rows):
        logger.warning(
            "Rejected %s allocation update(s) for accounts outside the "
            "caller's servers", len(rows) - len(kept),
        )
    return kept


def _plain(value: Decimal) -> str:
    """Allocation as a plain integer string when it has no fractional part."""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")
