"""Business rules for the all_users record.

Applied in both places a row can change: the edit form and a sheet upload.

Rules
-----
1. `server`, `Running Type` and `Running Days` are linked. If any one of them
   is set to NOT RUNNING or DLR ACC, the other two are set to the same value
   and `algo` becomes '0'.
2. `ml_pct` = max_loss / allocation, computed - never entered by hand. It is
   not calculated for NOT RUNNING or DLR ACC rows, which store NULL.
"""

from __future__ import annotations

import logging
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

NOT_RUNNING = "NOT RUNNING"
DLR_ACC = "DLR ACC"

# Setting any linked field to one of these marks the user inactive.
INACTIVE_STATES = (DLR_ACC, NOT_RUNNING)

# Changing one of these changes all three.
LINKED_FIELDS = ("server", "Running Type", "Running Days")

RUNNING_TYPE_OPTIONS = (DLR_ACC, NOT_RUNNING, "POS", "INT")
RUNNING_DAYS_OPTIONS = (DLR_ACC, NOT_RUNNING, "Daily", "1DTE/0DTE", "0DTE")

# Algo is forced to this for inactive users.
INACTIVE_ALGO = "0"

# Feed and dealer logins sit in usersetting under the same User ID as a real
# account, on their own server row, with FEED in the alias. They place no
# orders, so an allocation or a max loss must never be written onto them.
# Appended to the WHERE clause of every usersetting write.
EXCLUDE_FEED_SQL = " AND (`User Alias` IS NULL OR `User Alias` NOT LIKE '%FEED%')"


def canonical(value: Any, options: tuple[str, ...]) -> Any:
    """Snap a value onto one of `options`, ignoring case and padding.

    Existing rows hold variants like 'Not Running' and 'DAILY'; this maps them
    onto the option list so the dropdowns select correctly and stored values
    stay consistent. Anything unrecognised is returned stripped, unchanged.
    """
    if value is None:
        return None
    token = str(value).strip()
    for option in options:
        if token.casefold() == option.casefold():
            return option
    return token


def inactive_state(record: dict[str, Any]) -> str | None:
    """Return DLR ACC / NOT RUNNING if any linked field says so, else None."""
    for name in LINKED_FIELDS:
        value = record.get(name)
        if value is None:
            continue
        token = str(value).strip()
        for state in INACTIVE_STATES:
            if token.casefold() == state.casefold():
                return state
    return None


def compute_ml_pct(max_loss: Any, allocation: Any) -> Decimal | None:
    """max_loss / allocation, or None when it cannot be computed."""
    if max_loss is None or allocation is None:
        return None
    try:
        numerator = Decimal(str(max_loss))
        denominator = Decimal(str(allocation))
        if denominator == 0:
            return None
        return numerator / denominator
    except (InvalidOperation, DivisionByZero, ValueError):
        return None


def apply(record: dict[str, Any]) -> dict[str, Any]:
    """Normalise one all_users record in place and return it.

    `record` is keyed by real column name ('Running Type', not a slug).
    """
    record["Running Type"] = canonical(record.get("Running Type"), RUNNING_TYPE_OPTIONS)
    record["Running Days"] = canonical(record.get("Running Days"), RUNNING_DAYS_OPTIONS)
    record["server"] = canonical(record.get("server"), INACTIVE_STATES)

    state = inactive_state(record)
    if state:
        # One inactive field makes the whole row inactive.
        for name in LINKED_FIELDS:
            record[name] = state
        record["algo"] = INACTIVE_ALGO
        record["ml_pct"] = None
        return record

    record["ml_pct"] = compute_ml_pct(record.get("max_loss"), record.get("allocation"))
    return record
