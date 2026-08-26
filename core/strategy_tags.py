"""Strategy tag assignment: which tags an account runs, and at what multiplier.

Pure arithmetic. Nothing here reads the database or writes a file - it turns
one account's allocation into the tags and multipliers that belong to it, so
the rules can be checked against a real StrategyTag CSV before anything is
generated from them.

The three steps of a cycle behave differently:

  **4DTE / 1DTE** - one tag for the whole step, one multiplier per account,
  taken from a band table. The bands do NOT follow the rounding basis; they are
  their own editable numbers.

  **0DTE** - the account's allocation is cut into buckets and the buckets are
  dealt round-robin across the step's five series tags, lowest tag first. The
  bucket size DOES follow the rounding basis: `basis / 100`, so an allocation
  is always a whole number of buckets by construction.

At 0DTE an account gets two sets of series tags:

  * its **algo series** - G for algo 1, B for 7, E for 15 - which every account
    running that day is in, and
  * the **common series** A1..A5, which only accounts whose SubCategory is on
    the configured list join.

Both use the identical arithmetic; they differ only in who is in them.

Carry-forward is not arithmetic: a tag opened earlier in the cycle keeps the
multiplier it was given then, unchanged, on every later step. That belongs to
the generator, not here.

One server runs one algo, so a generated file is per server and therefore per
algo. `algo_of` treats a server carrying two as a data error rather than
picking one.

The band edges deliberately differ between the two steps, confirmed with the
desk:

    4DTE   the bottom of a band counts up      3,00,000 -> 3
    1DTE   the top of a band counts down       2,50,000 -> 2

which makes 1DTE's second band inclusive at both ends and one rupee wider than
the rest. It is written this way on purpose; `test_strategy_tags.py` pins it.
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# A band edge belongs to the higher multiplier ("up") or the lower one ("down").
EDGE_UP = "up"
EDGE_DOWN = "down"

# Per DTE step: the allocation at which the multiplier first reaches 2, how wide
# each band after that is, and which way an exact edge falls. Editable from
# Admin Controls; these are the values in use today.
DEFAULT_BANDS: dict[str, dict[str, Any]] = {
    "4DTE": {"first_step": 200_000, "width": 100_000, "edge": EDGE_UP},
    "1DTE": {"first_step": 150_000, "width": 100_000, "edge": EDGE_DOWN},
}

# The 0DTE series always has this many tags - G1..G5, B1..B5, E1..E5.
SERIES_SIZE = 5

# bucket = rounding basis / this. The Setup tab already says so on the rounding
# field: "expected steps by this / 100".
BUCKET_DIVISOR = 100


class StrategyTagError(ValueError):
    """A rule the user can fix, safe to show them."""


def _fold(value: Any) -> str:
    return str(value if value is not None else "").strip().upper()


def algo_of(accounts: list[dict[str, Any]], server: str) -> str:
    """The single algo a server runs.

    Raises:
        StrategyTagError: the server carries more than one algo, or none. Both
            are data errors: the series a file uses is chosen by the algo, so
            a server with two of them has no correct answer.
    """
    found = {_fold(a.get("algo")) for a in accounts if _fold(a.get("algo"))}
    found.discard("0")            # NOT RUNNING / DLR ACC carry algo 0

    if not found:
        raise StrategyTagError(f"No running accounts on {server}, so no algo to use.")
    if len(found) > 1:
        raise StrategyTagError(
            f"{server} carries algos {', '.join(sorted(found))}. A server runs "
            f"one algo, so which series to use is ambiguous - fix All Users first."
        )
    return found.pop()


def in_common_series(subcategory: Any, allowed: list[str]) -> bool:
    """Whether an account joins A1..A5.

    Membership is by SubCategory, from a list kept in Admin Controls.
    """
    return _fold(subcategory) in {_fold(s) for s in allowed}


def bucket_size(rounding_basis: Any) -> int:
    """The 0DTE bucket for a given rounding basis.

    Basis 25,00,000 gives 25,000; basis 20,00,000 gives 20,000.

    Raises:
        StrategyTagError: the basis is missing or not positive.
    """
    try:
        basis = Decimal(str(rounding_basis))
    except (TypeError, ValueError, ArithmeticError):
        raise StrategyTagError(
            f"Rounding basis {rounding_basis!r} is not a number."
        ) from None

    if basis <= 0:
        raise StrategyTagError("Rounding basis must be above 0.")

    size = basis / BUCKET_DIVISOR
    if size != size.to_integral_value():
        raise StrategyTagError(
            f"Rounding basis {basis:g} does not divide into a whole bucket "
            f"({size} is not an integer)."
        )
    return int(size)


def step_multiplier(allocation: Any, band: dict[str, Any]) -> int:
    """The 4DTE / 1DTE multiplier for one account.

    Args:
        band: one entry of DEFAULT_BANDS - first_step, width, edge.

    Returns:
        1 for anything below the first step, then one per band above it.
    """
    if allocation is None:
        return 1

    value = Decimal(str(allocation))
    first = Decimal(str(band["first_step"]))
    width = Decimal(str(band["width"]))

    if width <= 0:
        raise StrategyTagError("A band width must be above 0.")
    if value < first:
        return 1

    over = (value - first) / width

    if band.get("edge", EDGE_UP) == EDGE_UP:
        # The bottom of a band counts up: at exactly `first + n*width` the
        # multiplier has already stepped.
        return 2 + int(math.floor(over))

    # The top counts down: the multiplier steps only once the edge is passed,
    # so the band that starts at `first` also owns `first + width`.
    return 2 + max(0, int(math.ceil(over)) - 1)


def spread(buckets: int, tags: int = SERIES_SIZE) -> list[int]:
    """Deal `buckets` round-robin across `tags`, lowest tag first.

    6 buckets over 5 tags gives [2, 1, 1, 1, 1] - the remainder goes to the
    front, not the back.
    """
    if buckets < 0:
        raise StrategyTagError("An account cannot have a negative bucket count.")
    base, extra = divmod(buckets, tags)
    return [base + (1 if i < extra else 0) for i in range(tags)]


def series_multipliers(
    allocation: Any, rounding_basis: Any, tags: list[str]
) -> dict[str, int]:
    """The 0DTE series tags an account runs, and at what multiplier.

    A tag that would carry 0 is left out rather than written as a zero - the
    platform reads a listed account as trading.

    Returns:
        tag -> multiplier, in the order the tags were given.
    """
    size = bucket_size(rounding_basis)
    value = Decimal(str(allocation or 0))

    if value < 0:
        raise StrategyTagError("An allocation cannot be negative.")

    buckets = value / size
    if buckets != buckets.to_integral_value():
        # Allocations are rounded to the basis, and the bucket is the basis
        # over 100, so this cannot happen from a clean Setup run. Loud rather
        # than silently rounded: a part bucket means the allocation is wrong.
        raise StrategyTagError(
            f"Allocation {value:g} is not a whole number of {size:,} buckets "
            f"({buckets} buckets). Re-run the allocation check for this day."
        )

    shares = spread(int(buckets), len(tags))
    return {tag: n for tag, n in zip(tags, shares) if n > 0}
