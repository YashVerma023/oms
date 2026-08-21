"""Reading the allocation rules file.

Deliberately stdlib-only. `core.setup_check` needs pandas/numpy to *run* the
rules, but simply reading them - to populate the mode dropdown or show the
rules editor - must keep working even when that stack is unavailable.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "allocation_rules.json"

# Used when the file is unreadable, so the UI still offers the known modes.
FALLBACK_MODES = ("0DTE", "1DTE", "4DTE")

# DTE filter key -> the all_users column it constrains.
DTE_COLUMNS = {"runningtype": "Running Type", "runningdays": "Running Days"}

# date.weekday() -> the key used in the rules file.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Used when the rules file has no schedule. Saturday and Sunday are absent by
# design: they are manual-only.
DEFAULT_WEEKDAY_MODES = {
    "mon": "1DTE", "tue": "0DTE", "wed": "1DTE", "thu": "0DTE", "fri": "4DTE",
}

# Filters nothing. Used when no mode applies, so a gap never hides users.
FALLBACK_MODE = "0DTE"


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


def scheduled_mode(on_date: dt.date) -> str | None:
    """The mode the weekly schedule gives `on_date`, or None if it has none.

    Saturday and Sunday deliberately have no entry: weekends are set by hand.
    """
    weekday = WEEKDAYS[on_date.weekday()]
    schedule = rules_dict().get("weekday_modes") or DEFAULT_WEEKDAY_MODES
    mode = str(schedule.get(weekday) or "").strip()
    return mode if mode in modes() else None


def manual_mode(on_date: dt.date) -> str | None:
    """The hand-picked mode for `on_date`, or None if nobody set one.

    The pick is stored against a date so it expires on its own. A permanent
    override would mean the schedule never ran again after a single manual
    change.
    """
    block = rules_dict().get("today_mode")
    if not isinstance(block, dict):
        return None                       # older undated shape: treat as unset

    if str(block.get("date") or "") != on_date.isoformat():
        return None

    mode = str(block.get("mode") or "").strip()
    return mode if mode in modes() else None


def today_mode(on_date: dt.date | None = None) -> str:
    """The DTE mode in force for `on_date` (today by default).

    Manual pick first, then the weekly schedule. Falls back to 0DTE - which
    filters nothing - on weekends and whenever the rules file is unreadable,
    because a broken setting must never silently hide users.
    """
    on_date = on_date or dt.date.today()
    return manual_mode(on_date) or scheduled_mode(on_date) or FALLBACK_MODE


def mode_state(on_date: dt.date | None = None) -> dict:
    """Everything the UI needs to explain which mode is in force, and why."""
    on_date = on_date or dt.date.today()
    manual = manual_mode(on_date)
    scheduled = scheduled_mode(on_date)
    return {
        "date": on_date.isoformat(),
        "weekday": on_date.strftime("%A"),
        "mode": manual or scheduled or FALLBACK_MODE,
        "manual": manual,
        "scheduled": scheduled,
        "source": "manual" if manual else ("schedule" if scheduled else "default"),
    }


def dte_text(mode: str) -> str:
    """What `mode` admits, in words: 'Running Type POS, Running Days Daily'."""
    from core import rules  # local import: keeps this module's imports stdlib

    options = {
        "runningtype": rules.RUNNING_TYPE_OPTIONS,
        "runningdays": rules.RUNNING_DAYS_OPTIONS,
    }

    block = dte_filter(mode)
    if not block:
        return "every user"

    # Stored lowercase; shown in the same casing as the edit dropdowns.
    return ", ".join(
        "{} {}".format(
            DTE_COLUMNS[key],
            " or ".join(rules.canonical(v, options[key]) for v in values),
        )
        for key, values in block.items()
    )


def dte_summary() -> dict[str, str]:
    """One readable line per mode, for the Admin Controls card."""
    return {mode: dte_text(mode) for mode in modes()}


def schedule_rows(today: dt.date | None = None) -> list[dict]:
    """The weekly plan, for display. Days with no entry are manual-only."""
    today = today or dt.date.today()
    schedule = rules_dict().get("weekday_modes") or DEFAULT_WEEKDAY_MODES
    return [
        {
            "day": calendar.day_name[index],
            "mode": schedule.get(key) or "",
            "today": index == today.weekday(),
        }
        for index, key in enumerate(WEEKDAYS)
    ]


def dte_filter(mode: str) -> dict[str, list[str]]:
    """Running Type / Running Days a mode admits, lowercased.

    An empty dict means 'no restriction'. The lists come straight from
    `dte_filters` in the rules file, so the dashboard and the allocation check
    can never disagree about what 4DTE means.
    """
    block = rules_dict().get("dte_filters", {}).get(mode) or {}
    out: dict[str, list[str]] = {}
    for key in ("runningtype", "runningdays"):
        values = block.get(key)
        if values:
            out[key] = [str(v).strip().lower() for v in values]
    return out


def save_today_mode(mode: str, on_date: dt.date | None = None, by: str = "") -> None:
    """Pin the DTE mode for `on_date`, or clear the pin when `mode` is blank.

    Clearing hands the day back to the weekly schedule. The pick is dated, so
    it lapses by itself once the day is over.

    Raises:
        ValueError: the mode is not one the rules file defines.
    """
    on_date = on_date or dt.date.today()
    token = (mode or "").strip()
    rules = rules_dict()

    if not token:
        rules.pop("today_mode", None)
        _write(rules)
        logger.info("DTE mode for %s handed back to the schedule by %s", on_date, by)
        return

    if token not in modes():
        raise ValueError(f"'{mode}' is not one of {', '.join(modes())}.")

    rules["today_mode"] = {"mode": token, "date": on_date.isoformat(), "by": by}
    _write(rules)
    logger.info("DTE mode for %s pinned to %s by %s", on_date, token, by)


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
