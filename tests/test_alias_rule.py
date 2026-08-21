"""Alias-size allocation for algos 8, 19 and 27.

    python tests/test_alias_rule.py

Covers the arithmetic on its own, then the whole Setup check end to end on a
SQLite database, to prove the rule reaches the rows the Setup tab shows and
leaves every other rule alone.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_USER", "u")
os.environ.setdefault("DB_NAME", "omp")
os.environ.setdefault("SECRET_KEY", "test")

if "mysql.connector" not in sys.modules:
    sys.modules["mysql"] = types.ModuleType("mysql")
    connector = types.ModuleType("mysql.connector")
    connector.Error = Exception
    connector.MySQLConnection = object
    connector.errorcode = types.SimpleNamespace(
        ER_ACCESS_DENIED_ERROR=1045, ER_BAD_DB_ERROR=1049
    )
    sys.modules["mysql.connector"] = connector

import sqlite3  # noqa: E402

from flask import Flask  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Money is written as Decimal, which MySQL takes and SQLite does not. Teaching
# the test database to accept it keeps the production path unchanged.
sqlite3.register_adapter(Decimal, float)

from core import alias_rule  # noqa: E402
from database.db import db  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got}   want {want}")


def check_arithmetic() -> None:
    print("-- algos 19 and 27: the full alias size, every mode --")
    for alias, want in [
        ("MSR_AJAY_AGARWAL_2C", 200_000),
        ("MSR_BANSAL_5C", 500_000),
        ("MSR_BANSAL_1C", 100_000),
        ("MSR_BANSAL_15C", 1_500_000),
        ("MSR_BANSAL_50C", 5_000_000),
    ]:
        got = {
            alias_rule.allocation(alias, algo, mode)
            for algo in ("19", "27")
            for mode in ("0DTE", "1DTE", "4DTE")
        }
        check(alias, got, {Decimal(want)})

    print("\n-- algo 8: the size splits across the two expiries --")
    for alias, one, zero in [
        ("MSR_BANSAL_5C", 200_000, 300_000),
        ("MSR_BANSAL_4C", 200_000, 200_000),
        ("MSR_BANSAL_15C", 700_000, 800_000),
        ("MSR_X_3C", 100_000, 200_000),
    ]:
        check(f"{alias} 1DTE", alias_rule.allocation(alias, "8", "1DTE"), Decimal(one))
        check(f"{alias} 0DTE", alias_rule.allocation(alias, "8", "0DTE"), Decimal(zero))
        check(f"{alias} 4DTE", alias_rule.allocation(alias, "8", "4DTE"), None)

    print("\n-- what the rule refuses to price --")
    check("no suffix", alias_rule.allocation("MSR_ASR4", "19", "0DTE"), None)
    check("C mid-alias", alias_rule.allocation("MSR_2C_EXTRA", "19", "0DTE"), None)
    check("another algo", alias_rule.allocation("MSR_X_2C", "1", "0DTE"), None)
    # floor(1/2) is 0; writing that would disable a live account.
    check("1C on algo 8 1DTE", alias_rule.allocation("MSR_X_1C", "8", "1DTE"), None)
    check("1C on algo 8 0DTE", alias_rule.allocation("MSR_X_1C", "8", "0DTE"), Decimal(100_000))

    print("\n-- shapes the sheet actually contains --")
    check("lowercase", alias_rule.allocation("msr_x_3c", "19", "0DTE"), Decimal(300_000))
    check("trailing space", alias_rule.allocation("MSR_X_3C ", "19", "0DTE"), Decimal(300_000))
    check("algo as int", alias_rule.allocation("MSR_X_2C", 19, "0DTE"), Decimal(200_000))
    check("algo as float", alias_rule.allocation("MSR_X_2C", 8.0, "0DTE"), Decimal(100_000))


# Accounts covering every branch: priced, split, MSJ, no-suffix, other algos.
ACCOUNTS = [
    # userid,      alias,                 algo, subcat, allocation, capital
    ("A19FULL", "MSR_AJAY_AGARWAL_2C", "19", "MSR", 500_000, 10_000_000),
    ("A19OK", "MSR_BANSAL_5C", "19", "MSR", 500_000, 10_000_000),
    ("A19NOSUF", "MSR_ASR4", "19", "MSR", 400_000, 10_000_000),
    ("A19MSJ", "MSS_A19_PS", "19", "MSJ", 600_000, 10_000_000),
    ("A27FULL", "MSR_A27_ZETADMA_10C", "27", "MSR", 100_000, 10_000_000),
    ("A8ODD", "MSR_BANSAL_5C", "8", "MSR", 500_000, 10_000_000),
    ("A8EVEN", "MSR_BANSAL_4C", "8", "MSR", 200_000, 10_000_000),
    ("A8MSJ", "MSR_A8_J_6C", "8", "MSJ", 600_000, 10_000_000),
    ("A1OTHER", "MSR_SOMEONE_9C", "1", "MSR", 100_000, 10_000_000),
]

JAINAM = [("A19MSJ", 6.0), ("A8MSJ", 6.0)]


def build_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    return app


def seed(on_date: dt.date) -> None:
    db.session.execute(text("""
        CREATE TABLE all_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, `userId` TEXT, `alias` TEXT,
            `allocation` REAL, `max_loss` REAL, `server` TEXT, `algo` TEXT,
            `SubCategory` TEXT, `Category` TEXT, `Running Type` TEXT,
            `Running Days` TEXT, `FIX (CR)` REAL, `0SL` REAL, `Broker` TEXT,
            `Operator Name` TEXT, `Date` DATE)"""))
    db.session.execute(text(
        "CREATE TABLE running_users (`userId` TEXT, `capital` REAL, `imported_at` TEXT)"))
    db.session.execute(text("CREATE TABLE jainam (`UserID` TEXT, `ALLOCATION` REAL)"))
    db.session.execute(text(
        "CREATE TABLE usersetting (`User ID` TEXT, `Remarks` TEXT, `server` TEXT)"))

    # The same accounts on the previous day too: 0DTE refuses to run without it.
    for uid, alias, algo, sub, alloc, capital in ACCOUNTS:
        for day in (on_date - dt.timedelta(days=1), on_date):
            db.session.execute(text("""
                INSERT INTO all_users (`userId`,`alias`,`allocation`,`max_loss`,`server`,
                    `algo`,`SubCategory`,`Category`,`Running Type`,`Running Days`,
                    `FIX (CR)`,`0SL`,`Broker`,`Operator Name`,`Date`)
                VALUES (:u,:a,:al,1000,'vs1',:g,:s,'MS','POS','Daily',NULL,NULL,'IIFL','OP',:d)"""),
                dict(u=uid, a=alias, al=alloc, g=algo, s=sub, d=day))
        db.session.execute(text(
            "INSERT INTO running_users VALUES (:u,:c,'2026-08-21 09:00:00')"),
            dict(u=uid, c=capital))
        db.session.execute(text("INSERT INTO usersetting VALUES (:u,'','vs1')"),
                           dict(u=uid))

    for uid, alloc in JAINAM:
        db.session.execute(text("INSERT INTO jainam VALUES (:u,:a)"),
                           dict(u=uid, a=alloc))
    db.session.commit()


def check_end_to_end() -> None:
    from core import setup_check

    on_date = dt.date(2026, 8, 21)
    app = build_app()
    with app.app_context():
        seed(on_date)

        for mode, expectations in {
            # userid -> (rule, status, expected allocation)
            "0DTE": {
                "A19FULL": ("Alias size", "Mismatch", 200_000.0),
                "A19OK": ("Alias size", "Match", 500_000.0),
                "A27FULL": ("Alias size", "Mismatch", 1_000_000.0),
                "A8ODD": ("Alias size", "Mismatch", 300_000.0),
                "A8EVEN": ("Alias size", "Match", 200_000.0),
            },
            "1DTE": {
                "A19FULL": ("Alias size", "Mismatch", 200_000.0),
                "A8ODD": ("Alias size", "Mismatch", 200_000.0),
                "A8EVEN": ("Alias size", "Match", 200_000.0),
            },
        }.items():
            print(f"\n-- the Setup check in {mode} --")
            previous = on_date - dt.timedelta(days=1)
            result = setup_check.run_check(on_date, mode, previous)
            rows = {r["userid"]: r for r in result["rows"]}

            for uid, (rule, status, expected) in expectations.items():
                row = rows[uid]
                check(f"{mode} {uid}", (row["rule"], row["status"], row["expected"]),
                      (rule, status, expected))

            # MSJ stays on the Jainam sheet on every algo, untouched by us.
            for uid in ("A19MSJ", "A8MSJ"):
                check(f"{mode} {uid} still Jainam", rows[uid]["rule"], "Jainam")

            # An alias with no size is flagged, never guessed at.
            no_suffix = rows["A19NOSUF"]
            check(f"{mode} A19NOSUF not priced",
                  (no_suffix["status"], no_suffix["expected"], no_suffix["apply"]),
                  ("Not under check", None, False))
            check(f"{mode} A19NOSUF explains why",
                  no_suffix["remark"], alias_rule.NO_SUFFIX_REMARK)

            # Algo 1 is somebody else's rule.
            check(f"{mode} A1OTHER untouched by us",
                  rows["A1OTHER"]["rule"] != "Alias size", True)

            check(f"{mode} every account still present", len(result["rows"]), len(ACCOUNTS))
            check(f"{mode} reconciled", result["reconciled"], True)

        print("\n-- 4DTE: algo 8 stands down, 19 and 27 carry on --")
        result = setup_check.run_check(on_date, "4DTE",
                                       on_date - dt.timedelta(days=1))
        rows = {r["userid"]: r for r in result["rows"]}
        for uid in ("A8ODD", "A8EVEN"):
            check(f"4DTE {uid} not priced",
                  (rows[uid]["expected"], rows[uid]["apply"]), (None, False))
            check(f"4DTE {uid} says why",
                  rows[uid]["remark"], "Algo 8 does not run on 4DTE")
        check("4DTE A19FULL still priced", rows["A19FULL"]["expected"], 200_000.0)
        check("4DTE A27FULL still priced", rows["A27FULL"]["expected"], 1_000_000.0)

        print("\n-- applying writes allocation and the Remarks value --")
        setup_check.apply_changes(on_date, [
            {"userid": "A19FULL", "expected": 200_000.0},
        ])
        stored = db.session.execute(text(
            "SELECT `allocation` FROM all_users WHERE `userId`='A19FULL' "
            "AND `Date` = :d"), {"d": on_date}).scalar()
        check("allocation written", float(stored), 200_000.0)

        # Remarks carries the allocation value itself, not prose.
        remark = db.session.execute(text(
            "SELECT `Remarks` FROM usersetting WHERE `User ID`='A19FULL'")).scalar()
        check("Remarks holds the allocation", remark, "200000")

        # The previous day must not have moved.
        untouched = db.session.execute(text(
            "SELECT `allocation` FROM all_users WHERE `userId`='A19FULL' "
            "AND `Date` = :d"), {"d": on_date - dt.timedelta(days=1)}).scalar()
        check("previous day untouched", float(untouched), 500_000.0)


if __name__ == "__main__":
    check_arithmetic()
    check_end_to_end()
    print("\n" + ("All alias rule checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
