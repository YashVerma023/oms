"""Reading the allocation rules file.

Deliberately stdlib-only. `core.setup_check` needs pandas/numpy to *run* the
rules, but simply reading them - to populate the mode dropdown or show the
rules editor - must keep working even when that stack is unavailable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "allocation_rules.json"

# Used when the file is unreadable, so the UI still offers the known modes.
FALLBACK_MODES = ("0DTE", "1DTE", "4DTE")


def rules_text() -> str:
    """The rules file verbatim, or an empty string if it cannot be read."""
    try:
        return RULES_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Could not read the rules file at %s", RULES_PATH)
        return ""


def rules_dict() -> dict:
    """Parsed rules, or an empty dict if the file is missing or malformed."""
    try:
        return json.loads(rules_text() or "{}")
    except json.JSONDecodeError:
        logger.exception("The rules file at %s is not valid JSON", RULES_PATH)
        return {}


def modes() -> tuple[str, ...]:
    """DTE modes the rules define, in file order."""
    found = tuple(rules_dict().get("dte_filters", {}))
    return found or FALLBACK_MODES


def previous_day_required(mode: str) -> bool:
    """Whether `mode` refuses to run without a previous-day date."""
    return mode in set(rules_dict().get("previous_day", {}).get("required", []))


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

# Method labels shown in the UI, mapped to the `action` stored in the file.
METHOD_CAPITAL = "Capital %"
METHOD_JAINAM = "Jainam sheet"
METHODS = (METHOD_CAPITAL, METHOD_JAINAM)

BROKER_PCT = "% of capital"
BROKER_FIX = "From FIX (CR)"
BROKER_METHODS = (BROKER_PCT, BROKER_FIX)

_BROKER_METHOD_TO_KEY = {BROKER_PCT: "capital_pct", BROKER_FIX: "fix_allocation"}
_BROKER_KEY_TO_METHOD = {v: k for k, v in _BROKER_METHOD_TO_KEY.items()}


def subcategory_rows() -> list[dict]:
    """SubCategory rules as table rows."""
    rows = []
    for name, cfg in rules_dict().get("subcategories", {}).items():
        action = cfg.get("action", "check")
        rows.append(
            {
                "name": name,
                # 'exclude' is stored with pct 0; the table shows 0 % and the
                # two are converted back on save.
                "pct": 0 if action == "exclude" else cfg.get("pct", 0),
                "method": METHOD_JAINAM if action == "jexception" else METHOD_CAPITAL,
                "note": cfg.get("note", ""),
            }
        )
    return rows


def broker_rows() -> list[dict]:
    rows = []
    for name, cfg in rules_dict().get("broker_rules", {}).items():
        method = _BROKER_KEY_TO_METHOD.get(cfg.get("method"), BROKER_PCT)
        value = cfg.get("pct") if method == BROKER_PCT else cfg.get("multiplier")
        rows.append({"name": name, "method": method, "value": value})
    return rows


def rounding() -> dict:
    block = rules_dict().get("rounding", {})
    return {
        "basis": block.get("basis", 2_500_000),
        "mode": block.get("mode", "half_up"),
        "divisor": block.get("divisor", 100),
    }


def _write(rules: dict) -> None:
    """Atomically replace the rules file, keeping a .bak of the old one."""
    if RULES_PATH.exists():
        RULES_PATH.with_suffix(".json.bak").write_bytes(RULES_PATH.read_bytes())

    tmp = RULES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    tmp.replace(RULES_PATH)
    logger.info("Allocation rules written to %s", RULES_PATH)


def _percent(raw, label: str) -> float:
    """A whole percent. 0.6 is rejected: it almost certainly means 60%."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: '{raw}' is not a number.") from None
    if value < 0 or value > 100:
        raise ValueError(f"{label}: {value:g} must be between 0 and 100.")
    if 0 < value < 1:
        raise ValueError(
            f"{label}: {value:g} looks like a fraction. Write whole percents - "
            f"60 for 60%, not 0.6."
        )
    return value


def save_subcategories(rows: list[dict]) -> None:
    """Replace the SubCategory table.

    Raises:
        ValueError: a blank or duplicate name, or a percentage out of range.
    """
    out: dict[str, dict] = {}
    for row in rows:
        name = (row.get("name") or "").strip().upper()
        if not name:
            continue
        if name in out:
            raise ValueError(f"SubCategory '{name}' appears more than once.")

        method = row.get("method") or METHOD_CAPITAL
        if method == METHOD_JAINAM:
            out[name] = {"action": "jexception"}
        else:
            pct = _percent(row.get("pct", 0), f"SubCategory '{name}'")
            out[name] = (
                {"action": "exclude", "pct": 0}
                if pct == 0
                else {"action": "check", "pct": pct}
            )

        note = (row.get("note") or "").strip()
        if note:
            out[name]["note"] = note

    if not out:
        raise ValueError("At least one SubCategory is required.")

    rules = rules_dict()
    rules["subcategories"] = out
    _write(rules)


def save_brokers(rows: list[dict]) -> None:
    """Replace the broker overrides."""
    out: dict[str, dict] = {}
    for row in rows:
        name = (row.get("name") or "").strip().upper()
        if not name:
            continue
        if name in out:
            raise ValueError(f"Broker '{name}' appears more than once.")

        method = row.get("method") or BROKER_PCT
        if method == BROKER_PCT:
            out[name] = {
                "method": "capital_pct",
                "pct": _percent(row.get("value", 0), f"Broker '{name}'"),
            }
        else:
            try:
                multiplier = float(row.get("value") or 0)
            except (TypeError, ValueError):
                raise ValueError(f"Broker '{name}': multiplier must be a number.") from None
            if multiplier <= 0:
                raise ValueError(f"Broker '{name}': multiplier must be above 0.")
            out[name] = {"method": "fix_allocation", "multiplier": multiplier}

    rules = rules_dict()
    rules["broker_rules"] = out
    _write(rules)


def save_rounding(basis) -> None:
    """Set the rounding basis. Mode and divisor are left as they are."""
    try:
        value = int(float(basis))
    except (TypeError, ValueError):
        raise ValueError(f"Rounding basis '{basis}' is not a number.") from None
    if value <= 0:
        raise ValueError("Rounding basis must be above 0.")

    rules = rules_dict()
    block = rules.setdefault("rounding", {"mode": "half_up", "divisor": 100})
    block["basis"] = value
    _write(rules)
