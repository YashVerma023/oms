"""Max loss for a trading day.

Four tiers, first match wins:

  1. **Algo** - algos 8, 19 and 27 have their own multipliers, or take the
     sheet. These ignore the SubCategory table entirely.
  2. **SubCategory** - CC, CCG, PGB and PVT, for every other algo.
  3. **Already ran** - the uploaded sheet.
  4. **Today running** - allocation x the per-algo, per-mode multiplier.

For tiers 3 and 4:

  * **Already ran** - the account carries positions from earlier in the DTE
    cycle. Its max loss comes from the uploaded Max Loss Calculation sheet,
    which already folds in the day's realised and unrealised P&L.
  * **Today running** - the account starts its cycle today, so there is no P&L
    to carry and the max loss is `allocation x multiplier` for its algo.

Which accounts are "today running" depends on the mode, and on whether the
cycle had a previous day:

    4DTE                      POS + Daily
    1DTE, previous day given   POS + 1DTE/0DTE      (Daily already ran)
    1DTE, no previous day      POS + Daily and 1DTE/0DTE
    0DTE                       INT + 0DTE

Two values are written, from separate columns of the sheet:

    all_users.max_loss             <- MStech Max Loss
    usersetting.`Max Loss`         <- Stoxxo Max Loss

Nothing is written by `plan`; `apply` does that, in one transaction.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text

from core import alias_rule, rules_io
from core import rules as business_rules
from database.db import db

logger = logging.getLogger(__name__)

SHEET_TABLE = "maxloss"

# Mode -> the accounts starting their cycle today. Values are compared folded,
# so 'POS', 'pos' and ' Pos ' all match.
TODAY_RUNNING: dict[str, dict[str, tuple[str, ...]]] = {
    "4DTE": {"runningtype": ("pos",), "runningdays": ("daily",)},
    # With a previous day the Daily accounts already ran on it.
    "1DTE+prev": {"runningtype": ("pos",), "runningdays": ("1dte/0dte",)},
    "1DTE": {"runningtype": ("pos",), "runningdays": ("daily", "1dte/0dte")},
    "0DTE": {"runningtype": ("int",), "runningdays": ("0dte",)},
}

SOURCE_SHEET = "Max Loss sheet"
SOURCE_RULE = "Allocation x multiplier"
SOURCE_SUBCATEGORY = "SubCategory rule"
SOURCE_ALGO = "Algo rule"
SOURCE_NONE = ""


class MaxLossError(ValueError):
    """Something the user can fix, safe to show them."""


def _fold(value: Any) -> str:
    return str(value if value is not None else "").strip().lower()


def scope_for(mode: str, has_previous: bool) -> dict[str, tuple[str, ...]]:
    """The today-running definition for a mode.

    Raises:
        MaxLossError: the mode has no definition.
    """
    key = "1DTE+prev" if mode == "1DTE" and has_previous else mode
    scope = TODAY_RUNNING.get(key)
    if scope is None:
        raise MaxLossError(f"No max loss rule is defined for {mode}.")
    return scope


def _sheet(on_date: dt.date) -> dict[str, dict[str, Any]]:
    """That day's uploaded sheet, keyed by user id."""
    rows = db.session.execute(
        text(
            "SELECT `User ID`, `Stoxxo Max Loss`, `MStech Max Loss` "
            f"FROM `{SHEET_TABLE}` WHERE `Date` = :d"
        ),
        {"d": on_date},
    ).mappings().all()

    return {
        str(row["User ID"]).strip().upper(): {
            "stoxxo": row["Stoxxo Max Loss"],
            "mstech": row["MStech Max Loss"],
        }
        for row in rows
    }


def _accounts(on_date: dt.date, servers: list[str] | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT `userId`, `alias`, `algo`, `server`, `allocation`, `max_loss`, "
        "`SubCategory`, `Operator Name`, `Running Type`, `Running Days` "
        "FROM `all_users` WHERE `Date` = :d "
    )
    params: dict[str, Any] = {"d": on_date}
    binds = []

    if servers is not None:
        if not servers:
            return []
        sql += "AND `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))

    sql += "ORDER BY `server`, `userId`"
    statement = text(sql)
    if binds:
        statement = statement.bindparams(*binds)
    return [dict(r) for r in db.session.execute(statement, params).mappings().all()]


def plan(
    on_date: dt.date,
    mode: str,
    has_previous: bool = False,
    servers: list[str] | None = None,
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Work out every account's max loss for `on_date`. Writes nothing.

    Args:
        proposed: user id -> the allocation the allocation check is about to
            write. Used in place of the stored one, so both halves of a Setup
            run agree: a max loss is never derived from an allocation that is
            being replaced in the same breath. Rows built this way carry
            `depends_on_allocation`, so the page can warn if that allocation
            change is not applied alongside.

    Returns:
        {"rows": [...], "mode", "counts": {...}} - one row per in-scope
        account, each carrying where its value came from and whether it
        differs from what is stored.
    """
    scope = scope_for(mode, has_previous)
    sheet = _sheet(on_date)
    multipliers = rules_io.maxloss_rules().get(mode, {})
    overrides = rules_io.subcategory_maxloss()
    proposed = {
        str(k).strip().upper(): v for k, v in (proposed or {}).items()
        if v is not None
    }

    rows: list[dict[str, Any]] = []
    counts = {"sheet": 0, "rule": 0, "subcategory": 0, "algo": 0,
              "skipped": 0, "changed": 0, "inactive": 0}

    for account in _accounts(on_date, servers):
        # DLR ACC and NOT RUNNING are dropped outright, exactly as the
        # allocation check drops them. Not merely skipped: a dealer account
        # listed in the sheet, or one carrying a CC SubCategory, would
        # otherwise be handed a max loss it must never have.
        if business_rules.inactive_state(account) is not None:
            counts["inactive"] += 1
            continue

        user = str(account["userId"]).strip().upper()
        algo = alias_rule.algo_key(account.get("algo"))
        current = account.get("max_loss")

        # The allocation the check is about to write wins over the stored one.
        stored_allocation = account.get("allocation")
        incoming = proposed.get(user)
        allocation = incoming if incoming is not None else stored_allocation
        depends = (
            incoming is not None
            and (stored_allocation is None
                 or Decimal(str(incoming)) != Decimal(str(stored_allocation)))
        )

        row = {
            "userid": account["userId"],
            "alias": account.get("alias"),
            "algo": account.get("algo"),
            "server": account.get("server"),
            "operator_name": account.get("Operator Name"),
            "allocation": _plain(allocation),
            "stored_allocation": _plain(stored_allocation),
            # True when this max loss rests on an allocation change that has
            # not been applied yet.
            "depends_on_allocation": depends,
            "current": _plain(current),
            "subcategory": account.get("SubCategory"),
            "runningtype": account.get("Running Type"),
            "runningdays": account.get("Running Days"),
            "source": SOURCE_NONE,
            "stoxxo": None,
            "mstech": None,
            "note": "",
        }

        subcategory = _fold(account.get("SubCategory")).upper()
        found = sheet.get(user)
        by_algo = rules_io.algo_maxloss_for(algo, mode)

        # An algo with its own rule ignores the SubCategory table entirely -
        # a CC account on algo 19 follows algo 19.
        override = None if by_algo is not None else overrides.get(subcategory)

        if by_algo == rules_io.SHEET:
            if found is None:
                row["note"] = (
                    f"Algo {algo} takes {mode} from the Max Loss sheet, "
                    f"which does not list this account"
                )
                counts["skipped"] += 1
            else:
                row.update(
                    source=SOURCE_SHEET,
                    stoxxo=_plain(found["stoxxo"]),
                    mstech=_plain(found["mstech"]),
                    note=f"Algo {algo} on {mode}",
                )
                counts["sheet"] += 1
        elif isinstance(by_algo, dict):
            if allocation is None or Decimal(str(allocation)) <= 0:
                row["note"] = "No allocation to work from"
                counts["skipped"] += 1
            else:
                base = Decimal(str(allocation))
                row.update(
                    source=SOURCE_ALGO,
                    mstech=_plain(base * Decimal(str(by_algo["mstech"]))),
                    stoxxo=_plain(base * Decimal(str(by_algo["stoxxo"]))),
                    note=(
                        f"Algo {algo}: {allocation:g} x {by_algo['mstech']:g} "
                        f"(Stoxxo x{by_algo['stoxxo']:g})"
                    ),
                )
                counts["algo"] += 1
        elif override is not None:
            # Outranks both the sheet and the per-algo table: these accounts
            # are set by what they are, not by how they traded.
            if allocation is None or Decimal(str(allocation)) <= 0:
                row["note"] = "No allocation to work from"
                counts["skipped"] += 1
            else:
                base = Decimal(str(allocation))
                row.update(
                    source=SOURCE_SUBCATEGORY,
                    mstech=_plain(base * Decimal(str(override["mstech"]))),
                    stoxxo=_plain(base * Decimal(str(override["stoxxo"]))),
                    note=(
                        f"{subcategory}: {allocation:g} x {override['mstech']:g} "
                        f"(Stoxxo x{override['stoxxo']:g})"
                    ),
                )
                counts["subcategory"] += 1
        elif found is not None:
            # Already ran: the sheet is authoritative, P&L included.
            row.update(
                source=SOURCE_SHEET,
                stoxxo=_plain(found["stoxxo"]),
                mstech=_plain(found["mstech"]),
            )
            counts["sheet"] += 1
        elif (
            _fold(account.get("Running Type")) in scope["runningtype"]
            and _fold(account.get("Running Days")) in scope["runningdays"]
        ):
            multiplier = multipliers.get(algo)
            if multiplier is None:
                row["note"] = f"No max loss rule for algo {algo or '(blank)'}"
                counts["skipped"] += 1
            elif allocation is None or Decimal(str(allocation)) <= 0:
                row["note"] = "No allocation to work from"
                counts["skipped"] += 1
            else:
                value = (Decimal(str(allocation)) * Decimal(str(multiplier)))
                row.update(
                    source=SOURCE_RULE,
                    stoxxo=_plain(value),
                    mstech=_plain(value),
                    note=f"{allocation:g} x {multiplier:g}",
                )
                counts["rule"] += 1
        else:
            # Not running today and not in the sheet: left exactly as it is.
            counts["skipped"] += 1
            row["note"] = "Not running today"

        row["changed"] = (
            row["mstech"] is not None and row["mstech"] != row["current"]
        )
        # Same three words the allocation table uses, so one glance reads both.
        row["status"] = (
            "Mismatch" if row["changed"]
            else "Match" if row["mstech"] is not None
            else "Left alone"
        )
        if row["changed"]:
            counts["changed"] += 1
        rows.append(row)

    logger.info(
        "Max loss plan for %s in %s%s: %s by algo, %s by SubCategory, "
        "%s from the sheet, %s from the rule, %s left alone, "
        "%s dropped as DLR ACC / NOT RUNNING, %s would change",
        on_date, mode, " with a previous day" if has_previous else "",
        counts["algo"], counts["subcategory"], counts["sheet"], counts["rule"],
        counts["skipped"], counts["inactive"], counts["changed"],
    )
    counts["needs_allocation"] = sum(
        1 for r in rows if r["depends_on_allocation"] and r["mstech"] is not None
    )
    return {"rows": rows, "mode": mode, "counts": counts,
            "sheet_rows": len(sheet)}


def apply(
    on_date: dt.date,
    updates: list[dict[str, Any]],
    servers: list[str] | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Write the chosen max losses.

    all_users.max_loss takes the MStech value, usersetting takes the Stoxxo
    one. Both writes are in one transaction: a failure leaves neither applied.

    Args:
        commit: pass False to leave the transaction open, so the Setup tab can
            apply allocations and max losses as one unit.
    """
    wanted = [
        {
            "pk": str(u["userid"]).strip(),
            "mstech": Decimal(str(u["mstech"])),
            "stoxxo": Decimal(str(u["stoxxo"])),
        }
        for u in updates
        if u.get("userid") and u.get("mstech") is not None
        and u.get("stoxxo") is not None
    ]
    if not wanted:
        return {"all_users": 0, "usersetting": 0}

    try:
        # An operator may only write on their own servers - enforced on both
        # tables, not just usersetting.
        users_sql = (
            "UPDATE `all_users` SET `max_loss` = :mstech "
            "WHERE `userId` = :pk AND `Date` = :d"
        )
        users_params = [{**row, "d": on_date} for row in wanted]
        if servers is not None:
            users_sql += " AND `server` IN :servers"
            users_statement = text(users_sql).bindparams(
                bindparam("servers", expanding=True)
            )
            users_params = [{**p, "servers": servers} for p in users_params]
        else:
            users_statement = text(users_sql)

        changed = db.session.execute(users_statement, users_params).rowcount

        settings_sql = (
            "UPDATE `usersetting` SET `Max Loss` = :stoxxo WHERE `User ID` = :pk"
            + business_rules.EXCLUDE_FEED_SQL
        )
        params = [{"pk": r["pk"], "stoxxo": r["stoxxo"]} for r in wanted]

        # An operator may only write on their own servers.
        if servers is not None:
            settings_sql += " AND `server` IN :servers"
            statement = text(settings_sql).bindparams(
                bindparam("servers", expanding=True)
            )
            params = [{**p, "servers": servers} for p in params]
        else:
            statement = text(settings_sql)

        settings = db.session.execute(statement, params).rowcount
        if commit:
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Applying max loss for %s failed", on_date)
        raise MaxLossError(f"Could not apply max loss: {exc}") from exc

    logger.info(
        "Max loss applied for %s: %s all_users row(s), %s usersetting row(s)",
        on_date, changed, settings,
    )
    return {"all_users": changed, "usersetting": settings}


def _plain(value: Any) -> Any:
    """Decimals as float so the browser can read them; None stays None."""
    if value is None:
        return None
    return float(value)
