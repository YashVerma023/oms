"""Which DTE mode is in force, and why.

    python tests/test_dte_mode.py

Runs against a throwaway copy of the rules file, so the real
config/allocation_rules.json is never touched.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import rules_io  # noqa: E402

# A known week: Monday 17 Aug 2026 through Sunday 23 Aug 2026.
WEEK = [dt.date(2026, 8, 17) + dt.timedelta(days=i) for i in range(7)]
MON, TUE, WED, THU, FRI, SAT, SUN = WEEK

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got!r}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got!r}   want {want!r}")


def run() -> int:
    print("-- the weekly schedule, with nothing pinned --")
    rules_io.save_today_mode("")
    for day, want in zip(WEEK, ["1DTE", "0DTE", "1DTE", "0DTE", "4DTE", "0DTE", "0DTE"]):
        check(day.strftime("%a %d %b"), rules_io.today_mode(day), want)

    check("weekday source", rules_io.mode_state(MON)["source"], "schedule")
    # Weekends have no scheduled mode; the fallback shows every user rather
    # than silently hiding some.
    check("weekend has no schedule", rules_io.mode_state(SAT)["scheduled"], None)
    check("weekend source", rules_io.mode_state(SAT)["source"], "default")

    print("\n-- a manual pin outranks the schedule --")
    rules_io.save_today_mode("4DTE", WED, by="tester")
    check("pinned day", rules_io.today_mode(WED), "4DTE")
    check("pinned source", rules_io.mode_state(WED)["source"], "manual")
    check("other days untouched", rules_io.today_mode(THU), "0DTE")

    print("\n-- and it expires on its own --")
    check("same weekday next week", rules_io.today_mode(WED + dt.timedelta(days=7)), "1DTE")
    check("the day before", rules_io.today_mode(TUE), "0DTE")

    print("\n-- weekends are manual-only --")
    rules_io.save_today_mode("4DTE", SAT)
    check("pinned Saturday", rules_io.today_mode(SAT), "4DTE")
    check("Sunday unaffected", rules_io.today_mode(SUN), "0DTE")

    print("\n-- clearing hands the day back to the schedule --")
    rules_io.save_today_mode("4DTE", MON)
    rules_io.save_today_mode("")
    check("cleared", rules_io.today_mode(MON), "1DTE")
    check("cleared source", rules_io.mode_state(MON)["source"], "schedule")

    print("\n-- bad input changes nothing --")
    try:
        rules_io.save_today_mode("9DTE")
        check("unknown mode", "accepted", "rejected")
    except ValueError:
        check("unknown mode rejected", "ValueError", "ValueError")
    check("still on schedule", rules_io.today_mode(FRI), "4DTE")

    # Before the schedule existed, the pin was stored as a bare string. It must
    # not be read as a pin for every date from now until the end of time.
    raw = json.loads(rules_io.RULES_PATH.read_text(encoding="utf-8"))
    raw["today_mode"] = "4DTE"
    rules_io.RULES_PATH.write_text(json.dumps(raw), encoding="utf-8")
    check("legacy undated pin ignored", rules_io.manual_mode(MON), None)
    check("legacy falls through to the schedule", rules_io.today_mode(MON), "1DTE")

    print("\n-- what each mode admits --")
    check("4DTE", rules_io.dte_text("4DTE"), "Running Type POS, Running Days Daily")
    check("1DTE", rules_io.dte_text("1DTE"),
          "Running Type POS, Running Days 1DTE/0DTE or Daily")
    check("0DTE", rules_io.dte_text("0DTE"), "every user")

    print("\n" + ("All DTE mode checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    return 1 if failures else 0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp) / "allocation_rules.json"
        shutil.copy(rules_io.RULES_PATH, sandbox)
        rules_io.RULES_PATH = sandbox      # keep the real rules file out of it
        sys.exit(run())
