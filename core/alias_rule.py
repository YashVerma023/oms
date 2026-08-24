"""Allocation from the size suffix on the alias, for algos 8, 19 and 27.

These algos are listed in `excluded_algos`, so the capital and previous-day
rules deliberately skip them. Their allocation comes from the alias instead:
the trailing `_<n>C` is the account size, and one unit of `n` is 100,000.

    MSR_AJAY_AGARWAL_2C   ->    200,000
    MSR_BANSAL_15C        ->  1,500,000

Algos 19 and 27 take the full size on every DTE mode. Algo 8 takes half of it
on 1DTE, rounded down, and the whole of it on 0DTE, and does not run at all on
4DTE:

    5C  -> 1DTE 200,000   0DTE   500,000
    7C  -> 1DTE 300,000   0DTE   700,000
    8C  -> 1DTE 400,000   0DTE   800,000
    15C -> 1DTE 700,000   0DTE 1,500,000

MSJ accounts are *not* handled here. They are exempt from the algo exclusion
upstream and are already priced off the Jainam sheet, on every algo.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# One unit of the alias suffix, in rupees. '2C' means 2 units, not 2 crore.
UNIT = Decimal("100000")

# Trailing size on the alias: MSR_BANSAL_15C -> 15. Case-insensitive, and
# tolerant of the trailing spaces that turn up in the sheet.
SUFFIX = re.compile(r"_(\d+(?:\.\d+)?)C\s*$", re.IGNORECASE)

FULL = "full"        # the whole size
LOWER_HALF = "lower" # floor(n / 2) - algo 8 on 1DTE
SKIP = "skip"        # the algo does not trade in this mode

# algo -> DTE mode -> share of the alias size. '*' applies to every mode.
ALGO_SHARES: dict[str, dict[str, str]] = {
    "19": {"*": FULL},
    "27": {"*": FULL},
    "8": {"1DTE": LOWER_HALF, "0DTE": FULL, "4DTE": SKIP},
}

RULE_LABEL = "Alias size"

NO_SUFFIX_REMARK = (
    "Alias has no _<n>C size suffix - allocation cannot be derived from it"
)

ZERO_REMARK = (
    "Alias size is too small to split for this mode - the rule gives 0, "
    "which would disable the account"
)


def algo_key(value: Any) -> str:
    """'8', 8, 8.0 and ' 8 ' all name the same algo."""
    token = str(value if value is not None else "").strip()
    if not token:
        return ""
    try:
        number = Decimal(token)
    except InvalidOperation:
        return token.upper()
    return str(number.to_integral_value()) if number == number.to_integral_value() else token


def handles(algo: Any) -> bool:
    """Whether this rule owns the algo at all."""
    return algo_key(algo) in ALGO_SHARES


def size(alias: Any) -> Decimal | None:
    """The `n` in a trailing `_<n>C`, or None when the alias carries no size."""
    match = SUFFIX.search(str(alias or ""))
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:          # pragma: no cover - the regex forbids it
        return None


def share(algo: Any, mode: str) -> str | None:
    """How much of the alias size applies, or None if the algo is not ours."""
    modes = ALGO_SHARES.get(algo_key(algo))
    if modes is None:
        return None
    return modes.get(str(mode).strip().upper(), modes.get("*"))


def allocation(alias: Any, algo: Any, mode: str) -> Decimal | None:
    """Expected allocation, or None when the rule cannot produce one.

    None means 'leave this account alone': the algo is not ours, it does not
    trade in this mode, or the alias carries no size.
    """
    which = share(algo, mode)
    if which is None or which == SKIP:
        return None

    units = size(alias)
    if units is None:
        return None

    if which == FULL:
        value = units * UNIT
    else:
        # Half, rounded down: 7C gives 3 on 1DTE, not 3.5 or 4.
        whole = (units / 2).to_integral_value(rounding="ROUND_FLOOR")
        value = whole * UNIT

    # 1C on algo 8's 1DTE leg floors to nothing. Writing 0 would disable a live
    # account, so the rule declines instead and the account gets flagged.
    if value <= 0:
        logger.warning(
            "Alias rule: %s on algo %s / %s computes to 0 - skipped.",
            alias, algo_key(algo), mode,
        )
        return None
    return value
