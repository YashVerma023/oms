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
from typing import Any

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


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------
# A cycle is one expiry's run of DTE steps. Two of them:
#
#     Nifty    4DTE -> 1DTE -> 0DTE      (Tuesday expiry)
#     Sensex           1DTE -> 0DTE      (Thursday expiry)
#
# This is what makes the previous-day file optional: 1DTE opens the Sensex
# cycle with nothing behind it, but sits mid-run in the Nifty cycle and must
# carry Friday's 4DTE forward. The rule is simply that every step except the
# first of its cycle needs the day before.
NIFTY = "Nifty"
SENSEX = "Sensex"

DEFAULT_CYCLES: dict[str, tuple[str, ...]] = {
    NIFTY: ("4DTE", "1DTE", "0DTE"),
    SENSEX: ("1DTE", "0DTE"),
}

# Which cycle each weekday belongs to. Matches `DEFAULT_WEEKDAY_MODES`:
# Fri 4DTE, Mon 1DTE, Tue 0DTE is one Nifty run; Wed 1DTE, Thu 0DTE is one
# Sensex run. Weekends are absent, like the mode schedule.
DEFAULT_WEEKDAY_CYCLES = {
    "mon": NIFTY, "tue": NIFTY, "wed": SENSEX, "thu": SENSEX, "fri": NIFTY,
}


def cycles() -> dict[str, list[str]]:
    """Cycle name -> its DTE steps in order."""
    configured = rules_dict().get("cycles")
    if isinstance(configured, dict) and configured:
        return {str(name): [str(s) for s in steps]
                for name, steps in configured.items()}
    return {name: list(steps) for name, steps in DEFAULT_CYCLES.items()}


def cycle_names() -> tuple[str, ...]:
    return tuple(cycles())


def cycle_steps(cycle: str) -> list[str]:
    """The steps of `cycle`, or [] if it is not a known cycle."""
    return cycles().get(cycle, [])


def scheduled_cycle(on_date: dt.date) -> str | None:
    """The cycle `on_date` falls in, or None at the weekend."""
    weekday = WEEKDAYS[on_date.weekday()]
    schedule = rules_dict().get("weekday_cycles") or DEFAULT_WEEKDAY_CYCLES
    name = str(schedule.get(weekday) or "").strip()
    return name if name in cycles() else None


def needs_previous(cycle: str | None, mode: str) -> bool:
    """Whether this step of this cycle needs the previous day's All Users.

    Every step but the first carries forward. With no cycle given - a manual
    run outside the schedule - this falls back to the `previous_day.required`
    list in the rules file.
    """
    steps = cycle_steps(cycle) if cycle else []
    if mode not in steps:
        return previous_day_required(mode)
    return steps.index(mode) > 0


def cycle_state(on_date: dt.date | None = None) -> dict:
    """Cycle, step and previous-day requirement for a date, for the UI."""
    on_date = on_date or dt.date.today()
    cycle = scheduled_cycle(on_date)
    mode = today_mode(on_date)
    steps = cycle_steps(cycle) if cycle else []
    return {
        "cycle": cycle,
        "steps": steps,
        "mode": mode,
        "position": steps.index(mode) + 1 if mode in steps else None,
        "needs_previous": needs_previous(cycle, mode),
    }


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


# ---------------------------------------------------------------------------
# Max loss
# ---------------------------------------------------------------------------

# Max loss = allocation x multiplier. Not a percentage despite the desk's
# wording: an allocation of 10,000 at 2 gives a max loss of 20,000.
DEFAULT_MAXLOSS_RULES = {
    "4DTE": {"1": 2, "15": 2, "7": 1.8},
    "1DTE": {"1": 2, "15": 2, "7": 1.8},
    "0DTE": {"1": 1, "15": 1, "7": 0.8},
}


# Algos with their own max loss, which outrank everything else including the
# SubCategory table: a CC account on algo 19 follows algo 19, not CC.
#
# A mode may name SHEET instead of multipliers, meaning "take that day's
# uploaded Max Loss sheet". '*' covers every mode.
SHEET = "sheet"

DEFAULT_ALGO_MAXLOSS = {
    "8": {
        "1DTE": {"mstech": 1.4, "stoxxo": 1.4},
        "0DTE": SHEET,
    },
    "19": {"*": {"mstech": 30, "stoxxo": 10}},
    "27": {"*": {"mstech": 30, "stoxxo": 3}},
}


def algo_maxloss() -> dict[str, dict[str, Any]]:
    """Algo -> mode -> {'mstech', 'stoxxo'} or SHEET."""
    stored = rules_dict().get("maxloss_algo")
    if not isinstance(stored, dict) or not stored:
        return {k: dict(v) for k, v in DEFAULT_ALGO_MAXLOSS.items()}

    out: dict[str, dict[str, Any]] = {}
    for algo, block in stored.items():
        key = str(algo).strip()
        if not key or not isinstance(block, dict):
            continue
        modes_out: dict[str, Any] = {}
        for mode, value in block.items():
            if isinstance(value, str) and value.strip().lower() == SHEET:
                modes_out[str(mode)] = SHEET
            elif isinstance(value, dict):
                modes_out[str(mode)] = {
                    "mstech": float(value.get("mstech", 0)),
                    "stoxxo": float(value.get("stoxxo", 0)),
                }
        if modes_out:
            out[key] = modes_out
    return out


def algo_maxloss_for(algo: Any, mode: str) -> Any:
    """The rule for one algo in one mode: a dict, SHEET, or None."""
    from core import alias_rule

    block = algo_maxloss().get(alias_rule.algo_key(algo))
    if not block:
        return None
    return block.get(str(mode).strip()) or block.get("*")


def algo_maxloss_rows() -> list[dict]:
    """The algo table as rows, one column per mode and side, for the editor."""
    rules = algo_maxloss()
    rows = []
    for algo in sorted(rules, key=lambda a: (float(a) if a.isdigit() else 1e9, a)):
        row: dict[str, Any] = {"algo": algo}
        for mode in modes():
            value = rules[algo].get(mode) or rules[algo].get("*")
            if value == SHEET:
                row[f"{mode}_mstech"] = SHEET
                row[f"{mode}_stoxxo"] = SHEET
            elif isinstance(value, dict):
                row[f"{mode}_mstech"] = value["mstech"]
                row[f"{mode}_stoxxo"] = value["stoxxo"]
            else:
                row[f"{mode}_mstech"] = ""
                row[f"{mode}_stoxxo"] = ""
        rows.append(row)
    return rows


def save_algo_maxloss(rows: list[dict]) -> None:
    """Replace the per-algo max loss overrides.

    A cell holding the word 'sheet' means that mode takes the uploaded Max
    Loss sheet. Both cells of a mode must agree on that. An empty pair means
    the algo has no override for that mode.

    Raises:
        ValueError: a duplicate algo, a non-numeric value, or one side set to
            'sheet' while the other is a number.
    """
    known = list(modes())
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    for row in rows:
        algo = str(row.get("algo") or "").strip()
        if not algo:
            continue
        if algo in seen:
            raise ValueError(f"Algo '{algo}' appears more than once.")
        seen.add(algo)

        block: dict[str, Any] = {}
        for mode in known:
            raw = {
                side: str(row.get(f"{mode}_{side}", "") or "").strip()
                for side in ("mstech", "stoxxo")
            }
            wants_sheet = {k: v.lower() == SHEET for k, v in raw.items()}

            if all(wants_sheet.values()):
                block[mode] = SHEET
                continue
            if any(wants_sheet.values()):
                raise ValueError(
                    f"Algo {algo}, {mode}: set both sides to 'sheet', or neither."
                )
            if not any(raw.values()):
                continue

            values: dict[str, float] = {}
            for side, text_value in raw.items():
                try:
                    values[side] = float(text_value or 0)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Algo {algo}, {mode}: '{text_value}' is not a number."
                    ) from None
                if values[side] < 0:
                    raise ValueError(
                        f"Algo {algo}, {mode}: a multiplier cannot be negative."
                    )
            block[mode] = values

        if block:
            out[algo] = block

    rules = rules_dict()
    rules["maxloss_algo"] = out
    _write(rules)


# SubCategory overrides, which outrank the per-algo table. `mstech` is written
# to all_users.max_loss, `stoxxo` to usersetting - both as multipliers of the
# allocation, so 0 means a max loss of nothing, which is what these accounts
# want in Stoxxo.
DEFAULT_SUBCATEGORY_MAXLOSS = {
    "CC": {"mstech": 30, "stoxxo": 0},
    "CCG": {"mstech": 30, "stoxxo": 0},
    "PGB": {"mstech": 30, "stoxxo": 0},
    "PVT": {"mstech": 30, "stoxxo": 0},
}


def subcategory_maxloss() -> dict[str, dict[str, float]]:
    """SubCategory -> {'mstech': multiplier, 'stoxxo': multiplier}."""
    stored = rules_dict().get("maxloss_subcategory")
    if not isinstance(stored, dict) or not stored:
        return {k: dict(v) for k, v in DEFAULT_SUBCATEGORY_MAXLOSS.items()}

    out: dict[str, dict[str, float]] = {}
    for name, block in stored.items():
        key = str(name).strip().upper()
        if not key or not isinstance(block, dict):
            continue
        out[key] = {
            "mstech": float(block.get("mstech", 0)),
            "stoxxo": float(block.get("stoxxo", 0)),
        }
    return out


def subcategory_maxloss_rows() -> list[dict]:
    """The override table as rows, for the Admin Controls editor."""
    return [
        {"name": name, "mstech": block["mstech"], "stoxxo": block["stoxxo"]}
        for name, block in sorted(subcategory_maxloss().items())
    ]


def save_subcategory_maxloss(rows: list[dict]) -> None:
    """Replace the SubCategory overrides.

    Unlike the per-algo table, 0 is allowed and meaningful here: it is how
    these accounts are told to carry no Stoxxo limit. A blank cell is read as
    0 for the same reason.

    Raises:
        ValueError: a duplicate SubCategory, or a value that is not a number.
    """
    out: dict[str, dict[str, float]] = {}

    for row in rows:
        name = str(row.get("name") or "").strip().upper()
        if not name:
            continue
        if name in out:
            raise ValueError(f"SubCategory '{name}' appears more than once.")

        values: dict[str, float] = {}
        for side in ("mstech", "stoxxo"):
            raw = row.get(side, "")
            if raw is None or str(raw).strip() == "":
                values[side] = 0.0
                continue
            try:
                values[side] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"SubCategory '{name}': '{raw}' is not a number."
                ) from None
            if values[side] < 0:
                raise ValueError(
                    f"SubCategory '{name}': a multiplier cannot be negative."
                )
        out[name] = values

    rules = rules_dict()
    rules["maxloss_subcategory"] = out
    _write(rules)


def maxloss_rules() -> dict[str, dict[str, float]]:
    """DTE mode -> algo -> multiplier, as stored."""
    stored = rules_dict().get("maxloss_rules")
    if not isinstance(stored, dict) or not stored:
        return {m: dict(a) for m, a in DEFAULT_MAXLOSS_RULES.items()}

    out: dict[str, dict[str, float]] = {}
    for mode in modes():
        block = stored.get(mode) or {}
        out[mode] = {
            str(algo).strip(): float(value)
            for algo, value in block.items()
            if str(algo).strip()
        }
    return out


def maxloss_multiplier(mode: str, algo: Any) -> float | None:
    """The multiplier for one algo in one mode, or None if it has no rule."""
    from core import alias_rule            # algo_key: '7', 7 and 7.0 agree

    return maxloss_rules().get(mode, {}).get(alias_rule.algo_key(algo))


def maxloss_algos() -> list[str]:
    """Every algo any mode has a rule for, numerically sorted."""
    seen = {a for block in maxloss_rules().values() for a in block}

    def key(algo: str):
        try:
            return (0, float(algo), "")
        except ValueError:
            return (1, 0.0, algo)

    return sorted(seen, key=key)


def save_maxloss_rules(rows: list[dict]) -> None:
    """Replace the max-loss table from Admin Controls.

    Each row is {"algo": "7", "4DTE": 1.8, "1DTE": 1.8, "0DTE": 0.8}. An empty
    cell means that algo has no rule in that mode, and is stored as absent
    rather than as zero - zero would silently set a max loss of nothing.

    Raises:
        ValueError: a blank or duplicate algo, or a multiplier that is not a
            positive number.
    """
    known = list(modes())
    out: dict[str, dict[str, float]] = {mode: {} for mode in known}
    seen: set[str] = set()

    for row in rows:
        algo = str(row.get("algo") or "").strip()
        if not algo:
            continue
        if algo in seen:
            raise ValueError(f"Algo '{algo}' appears more than once.")
        seen.add(algo)

        for mode in known:
            raw = row.get(mode, "")
            if raw is None or str(raw).strip() == "":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Algo {algo}, {mode}: '{raw}' is not a number."
                ) from None
            if value <= 0:
                raise ValueError(
                    f"Algo {algo}, {mode}: the multiplier must be above 0. "
                    f"Leave it empty for 'no rule'."
                )
            out[mode][algo] = value

    if not seen:
        raise ValueError("At least one algo is required.")

    rules = rules_dict()
    rules["maxloss_rules"] = out
    _write(rules)


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


# ---------------------------------------------------------------------------
# Strategy tags
# ---------------------------------------------------------------------------
# Which tags an algo runs on a cycle step, how the 4DTE / 1DTE multiplier bands
# work, and who joins the common A series. The arithmetic lives in
# `core.strategy_tags`; this only reads and writes the numbers.

DEFAULT_COMMON_TAGS = ("A1", "A2", "A3", "A4", "A5")

EDGES = ("up", "down")


def strategy_tag_map() -> list[dict]:
    """Tag map rows: algo, cycle, dte, tags."""
    rows = rules_dict().get("strategy_tags", {}).get("map") or []
    return [
        {
            "algo": str(r.get("algo", "")).strip(),
            "cycle": str(r.get("cycle", "")).strip(),
            "dte": str(r.get("dte", "")).strip(),
            "tags": [str(t).strip() for t in (r.get("tags") or []) if str(t).strip()],
        }
        for r in rows
    ]


def tags_for(algo: str, cycle: str, dte: str) -> list[str]:
    """The tags one algo runs on one step, or [] if the map has no entry."""
    algo, cycle, dte = str(algo).strip(), str(cycle).strip(), str(dte).strip()
    for row in strategy_tag_map():
        if (row["algo"] == algo and row["cycle"].lower() == cycle.lower()
                and row["dte"].upper() == dte.upper()):
            return list(row["tags"])
    return []


def strategy_bands() -> dict[str, dict]:
    """Per-step band settings, falling back to the values in use today."""
    from core import strategy_tags          # local: avoids a cycle at import

    stored = rules_dict().get("strategy_tags", {}).get("bands") or {}
    out = {}
    for step, default in strategy_tags.DEFAULT_BANDS.items():
        band = stored.get(step) or {}
        out[step] = {
            "first_step": band.get("first_step", default["first_step"]),
            "width": band.get("width", default["width"]),
            "edge": band.get("edge", default["edge"]),
        }
    return out


def common_series() -> dict:
    """The A-series tag names and the SubCategories that join them.

    An empty SubCategory list means no account joins, so the tags are simply
    not written - which is the safe state before the desk has configured it.
    """
    block = rules_dict().get("strategy_tags", {}).get("common") or {}
    return {
        "tags": [str(t).strip() for t in (block.get("tags") or DEFAULT_COMMON_TAGS)
                 if str(t).strip()],
        "subcategories": [str(s).strip().upper()
                          for s in (block.get("subcategories") or [])
                          if str(s).strip()],
    }


def strategy_map_rows() -> list[dict]:
    """The tag map as editable table rows - tags joined for one text box."""
    return [
        {"algo": r["algo"], "cycle": r["cycle"], "dte": r["dte"],
         "tags": ", ".join(r["tags"])}
        for r in strategy_tag_map()
    ]


def band_rows() -> list[dict]:
    """The band settings as editable table rows."""
    bands = strategy_bands()
    return [
        {"step": step, "first_step": b["first_step"], "width": b["width"],
         "edge": b["edge"]}
        for step, b in bands.items()
    ]


def save_strategy_map(rows: list[dict]) -> None:
    """Replace the tag map.

    Raises:
        ValueError: a row is incomplete, or the same algo/cycle/step appears
            twice - the lookup would then depend on file order.
    """
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        algo = (row.get("algo") or "").strip()
        cycle = (row.get("cycle") or "").strip()
        dte = (row.get("dte") or "").strip().upper()
        tags = [t.strip() for t in (row.get("tags") or "").split(",") if t.strip()]

        if not any((algo, cycle, dte, tags)):
            continue                       # a blank row the user added and left
        if not (algo and cycle and dte):
            raise ValueError(
                f"Every rule needs an algo, a cycle and a step - "
                f"got algo {algo!r}, cycle {cycle!r}, step {dte!r}."
            )
        if not tags:
            raise ValueError(f"Algo {algo} {cycle} {dte} lists no tags.")

        key = (algo, cycle.lower(), dte)
        if key in seen:
            raise ValueError(
                f"Algo {algo} {cycle} {dte} appears twice. One row per "
                f"algo, cycle and step."
            )
        seen.add(key)
        out.append({"algo": algo, "cycle": cycle, "dte": dte, "tags": tags})

    rules = rules_dict()
    block = rules.setdefault("strategy_tags", {})
    block["map"] = out
    _write(rules)


def save_strategy_bands(rows: list[dict]) -> None:
    """Replace the 4DTE / 1DTE band settings.

    Raises:
        ValueError: a number is missing, not positive, or the edge is not one
            of the two directions.
    """
    out: dict[str, dict] = {}
    for row in rows:
        step = (row.get("step") or "").strip().upper()
        if not step:
            continue

        try:
            first = int(float(row.get("first_step") or 0))
            width = int(float(row.get("width") or 0))
        except (TypeError, ValueError):
            raise ValueError(f"{step}: the band numbers must be whole numbers.") from None

        if first <= 0 or width <= 0:
            raise ValueError(f"{step}: the first step and the width must be above 0.")

        edge = (row.get("edge") or "up").strip().lower()
        if edge not in EDGES:
            raise ValueError(
                f"{step}: the edge must be '{EDGES[0]}' or '{EDGES[1]}', not {edge!r}."
            )

        out[step] = {"first_step": first, "width": width, "edge": edge}

    rules = rules_dict()
    block = rules.setdefault("strategy_tags", {})
    block["bands"] = out
    _write(rules)


def save_common_series(tags: str, subcategories: Any) -> None:
    """Replace the A-series tag names and the SubCategories that join them.

    Args:
        subcategories: the ticked list from the checklist, or a comma-separated
            string. The list is stored as given and stays put until it is
            changed again - it is not rebuilt per day.

    Raises:
        ValueError: no tags were given. An empty SubCategory list is allowed
            and simply means nobody joins yet.
    """
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    if not tag_list:
        raise ValueError("The common series needs at least one tag name.")

    if isinstance(subcategories, str):
        subcategories = subcategories.split(",")
    subs = sorted({str(s).strip().upper() for s in (subcategories or [])
                   if str(s).strip()})

    rules = rules_dict()
    block = rules.setdefault("strategy_tags", {})
    block["common"] = {"tags": tag_list, "subcategories": subs}
    _write(rules)
