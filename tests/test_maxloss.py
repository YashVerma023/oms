"""Max loss: the sheet for accounts that already ran, the rule for the rest.

    python tests/test_maxloss.py
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types
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
from decimal import Decimal  # noqa: E402

from flask import Flask  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Money is written as Decimal, which MySQL takes and SQLite does not.
sqlite3.register_adapter(Decimal, float)

from core import maxloss  # noqa: E402
from database.db import db  # noqa: E402

DAY = dt.date(2026, 8, 21)

# userId, algo, running type, running days, allocation, SubCategory
ACCOUNTS = [
    ("POSDAILY1", "1", "POS", "Daily", 100000, "MSR"),
    ("POSDAILY7", "7", "POS", "Daily", 200000, "MSR"),
    ("POS1DTE1", "1", "POS", "1DTE/0DTE", 150000, "MSR"),
    ("POS1DTE15", "15", "POS", "1DTE/0DTE", 250000, "MSR"),
    ("INT0DTE1", "1", "INT", "0DTE", 300000, "MSR"),
    ("INT0DTE7", "7", "INT", "0DTE", 400000, "MSR"),
    ("NOTRUN", "1", "NOT RUNNING", "NOT RUNNING", 1, "MSR"),
    ("DLRINSHEET", "1", "DLR ACC", "DLR ACC", 500000, "MSR"),
    ("NOTRUNCC", "1", "NOT RUNNING", "NOT RUNNING", 400000, "CC"),
    ("NORULE", "25", "POS", "Daily", 500000, "MSR"),   # no rule anywhere
    ("NOALLOC", "1", "POS", "Daily", 0, "MSR"),        # nothing to work from
    # The four SubCategories that outrank everything else.
    ("CCACC", "1", "POS", "Daily", 125000, "CC"),
    ("CCGACC", "7", "POS", "1DTE/0DTE", 100000, "CCG"),
    ("PGBACC", "15", "INT", "0DTE", 200000, "PGB"),
    ("PVTACC", "1", "POS", "Daily", 300000, "pvt "),   # case and padding
    # In the sheet as well, to prove the override still wins.
    ("CCINSHEET", "1", "POS", "Daily", 400000, "CC"),
    # Algos with a rule of their own.
    ("A8ACC", "8", "POS", "Daily", 200000, "MSR"),
    ("A8INSHEET", "8", "POS", "Daily", 500000, "MSR"),
    ("A19ACC", "19", "POS", "Daily", 100000, "MSR"),
    ("A27ACC", "27", "INT", "0DTE", 300000, "MSR"),
    # A CC account on algo 19: the algo wins, not the SubCategory.
    ("A19CC", "19", "POS", "Daily", 250000, "CC"),
]

# Accounts the sheet covers: they already ran, so their value carries P&L.
SHEET = [("POSDAILY1", 201538.55, 201538.55), ("POSDAILY7", 361487.85, 360000.00),
         ("CCINSHEET", 999999.0, 999999.0), ("A8INSHEET", 444444.0, 555555.0),
         ("A19ACC", 111111.0, 111111.0), ("DLRINSHEET", 777777.0, 777777.0)]

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got!r}   want {want!r}")


def make_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    return app


def seed() -> None:
    db.session.execute(text("""
        CREATE TABLE all_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, `userId` TEXT, `alias` TEXT,
            `algo` TEXT, `server` TEXT, `allocation` REAL, `max_loss` REAL,
            `SubCategory` TEXT, `Operator Name` TEXT,
            `Running Type` TEXT, `Running Days` TEXT,
            `Date` DATE)"""))
    db.session.execute(text("""
        CREATE TABLE maxloss (
            `Date` DATE, `User ID` TEXT, `Stoxxo Max Loss` REAL,
            `MStech Max Loss` REAL, PRIMARY KEY (`Date`, `User ID`))"""))
    db.session.execute(text("""
        CREATE TABLE usersetting (
            `User ID` TEXT, `Max Loss` REAL, `server` TEXT, `User Alias` TEXT,
            PRIMARY KEY (`User ID`, `server`))"""))

    for uid, algo, rtype, rdays, alloc, sub in ACCOUNTS:
        db.session.execute(text("""
            INSERT INTO all_users (`userId`,`alias`,`algo`,`server`,`allocation`,
                `max_loss`,`SubCategory`,`Operator Name`,`Running Type`,
                `Running Days`,`Date`)
            VALUES (:u,:u,:g,'VS1',:a,0,:s,'YASHV',:t,:d2,:d)"""),
            dict(u=uid, g=algo, a=alloc, s=sub, t=rtype, d2=rdays, d=DAY))
        db.session.execute(text(
            "INSERT INTO usersetting VALUES (:u, 0, 'VS1', :u)"), dict(u=uid))

    db.session.execute(text(
        "INSERT INTO usersetting VALUES ('POSDAILY7', 0, 'VS8', 'VS8_FEED')"))

    for uid, stoxxo, mstech in SHEET:
        db.session.execute(text("INSERT INTO maxloss VALUES (:d,:u,:s,:m)"),
                           dict(d=DAY, u=uid, s=stoxxo, m=mstech))
    db.session.commit()


def by_user(result) -> dict:
    return {r["userid"]: r for r in result["rows"]}


def run() -> None:
    app = make_app()
    with app.app_context():
        seed()

        print("-- which accounts count as running today, per mode --")
        check("4DTE takes POS + Daily",
              maxloss.scope_for("4DTE", False),
              {"runningtype": ("pos",), "runningdays": ("daily",)})
        check("1DTE with a previous day takes POS + 1DTE/0DTE",
              maxloss.scope_for("1DTE", True),
              {"runningtype": ("pos",), "runningdays": ("1dte/0dte",)})
        check("1DTE without one takes both",
              maxloss.scope_for("1DTE", False)["runningdays"],
              ("daily", "1dte/0dte"))
        check("0DTE takes INT + 0DTE",
              maxloss.scope_for("0DTE", False),
              {"runningtype": ("int",), "runningdays": ("0dte",)})

        print("\n-- 4DTE: Daily accounts start the cycle --")
        rows = by_user(maxloss.plan(DAY, "4DTE"))
        # In the sheet, so the sheet wins even on 4DTE.
        check("POSDAILY1 takes the sheet", rows["POSDAILY1"]["source"], maxloss.SOURCE_SHEET)
        check("and its MStech value", rows["POSDAILY1"]["mstech"], 201538.55)
        check("the two columns are kept apart",
              (rows["POSDAILY7"]["stoxxo"], rows["POSDAILY7"]["mstech"]),
              (361487.85, 360000.0))
        check("a 1DTE/0DTE account is left alone on 4DTE",
              rows["POS1DTE1"]["source"], maxloss.SOURCE_NONE)
        check("an INT account too", rows["INT0DTE1"]["source"], maxloss.SOURCE_NONE)

        print("\n-- 1DTE with a previous day: 1DTE/0DTE accounts start --")
        rows = by_user(maxloss.plan(DAY, "1DTE", has_previous=True))
        check("POS1DTE1 uses the rule", rows["POS1DTE1"]["source"], maxloss.SOURCE_RULE)
        check("150,000 x 2", rows["POS1DTE1"]["mstech"], 300000.0)
        check("POS1DTE15 at 2", rows["POS1DTE15"]["mstech"], 500000.0)
        check("Daily accounts fall to the sheet",
              rows["POSDAILY1"]["source"], maxloss.SOURCE_SHEET)

        print("\n-- 1DTE without one: both kinds start --")
        rows = by_user(maxloss.plan(DAY, "1DTE", has_previous=False))
        check("POS1DTE1 still uses the rule", rows["POS1DTE1"]["mstech"], 300000.0)
        # POSDAILY7 is in the sheet, so the sheet still wins for it.
        check("a Daily account not in the sheet would use the rule",
              rows["NOALLOC"]["source"], maxloss.SOURCE_NONE)

        print("\n-- 0DTE: INT accounts, at the lower multipliers --")
        rows = by_user(maxloss.plan(DAY, "0DTE"))
        check("INT0DTE1 at 1", rows["INT0DTE1"]["mstech"], 300000.0)
        check("INT0DTE7 at 0.8", rows["INT0DTE7"]["mstech"], 320000.0)
        check("POS accounts are not touched by the rule",
              rows["POS1DTE1"]["source"], maxloss.SOURCE_NONE)

        print("\n-- accounts the rule cannot price --")
        rows = by_user(maxloss.plan(DAY, "4DTE"))
        check("an algo with no multiplier is reported, not zeroed",
              (rows["NORULE"]["source"], rows["NORULE"]["note"]),
              (maxloss.SOURCE_NONE, "No max loss rule for algo 25"))
        check("no allocation means no max loss",
              (rows["NOALLOC"]["source"], rows["NOALLOC"]["note"]),
              (maxloss.SOURCE_NONE, "No allocation to work from"))
        print("\n-- DLR ACC and NOT RUNNING are dropped, not just skipped --")
        result = maxloss.plan(DAY, "1DTE", has_previous=True)
        rows = by_user(result)
        check("a NOT RUNNING account is not in the result",
              "NOTRUN" in rows, False)
        check("nor is a DLR ACC account the sheet lists",
              "DLRINSHEET" in rows, False)
        check("nor a NOT RUNNING account with a CC SubCategory",
              "NOTRUNCC" in rows, False)
        check("they are counted", result["counts"]["inactive"], 3)

        print("\n-- SubCategory outranks both the algo table and the sheet --")
        rows = by_user(maxloss.plan(DAY, "4DTE"))
        for uid, alloc in (("CCACC", 125000), ("CCGACC", 100000),
                           ("PGBACC", 200000), ("PVTACC", 300000)):
            check(f"{uid}: all_users at 30x",
                  (rows[uid]["source"], rows[uid]["mstech"]),
                  (maxloss.SOURCE_SUBCATEGORY, alloc * 30.0))
            check(f"{uid}: usersetting at 0", rows[uid]["stoxxo"], 0.0)
        check("it applies whatever the running days",
              rows["CCGACC"]["source"], maxloss.SOURCE_SUBCATEGORY)
        check("and beats the sheet",
              (rows["CCINSHEET"]["source"], rows["CCINSHEET"]["mstech"]),
              (maxloss.SOURCE_SUBCATEGORY, 12000000.0))
        check("an ordinary SubCategory is unaffected",
              rows["POSDAILY1"]["source"], maxloss.SOURCE_SHEET)

        print("\n-- algos 8, 19 and 27 have their own rule --")
        rows = by_user(maxloss.plan(DAY, "1DTE", has_previous=True))
        check("algo 8 on 1DTE: 1.4x on both sides",
              (rows["A8ACC"]["source"], rows["A8ACC"]["mstech"], rows["A8ACC"]["stoxxo"]),
              (maxloss.SOURCE_ALGO, 280000.0, 280000.0))
        check("algo 19: 30x all_users, 10x usersetting",
              (rows["A19ACC"]["mstech"], rows["A19ACC"]["stoxxo"]), (3000000.0, 1000000.0))
        check("algo 27: 30x all_users, 3x usersetting",
              (rows["A27ACC"]["mstech"], rows["A27ACC"]["stoxxo"]), (9000000.0, 900000.0))
        check("algo 19 beats the sheet",
              (rows["A19ACC"]["source"], rows["A19ACC"]["mstech"]),
              (maxloss.SOURCE_ALGO, 3000000.0))
        check("the SubCategory table does not apply to these algos",
              (rows["A19CC"]["source"], rows["A19CC"]["mstech"], rows["A19CC"]["stoxxo"]),
              (maxloss.SOURCE_ALGO, 7500000.0, 2500000.0))

        print("\n-- algo 8 on 0DTE takes the sheet --")
        rows = by_user(maxloss.plan(DAY, "0DTE"))
        check("from the sheet, both columns",
              (rows["A8INSHEET"]["source"], rows["A8INSHEET"]["stoxxo"],
               rows["A8INSHEET"]["mstech"]),
              (maxloss.SOURCE_SHEET, 444444.0, 555555.0))
        check("an algo 8 account the sheet omits is reported, not guessed",
              (rows["A8ACC"]["source"], "does not list" in rows["A8ACC"]["note"]),
              (maxloss.SOURCE_NONE, True))
        check("algos 19 and 27 keep their fixed rule on 0DTE too",
              (rows["A19ACC"]["mstech"], rows["A27ACC"]["stoxxo"]),
              (3000000.0, 900000.0))

        print("\n-- algo 8 on 4DTE has no rule, so it is left alone --")
        rows = by_user(maxloss.plan(DAY, "4DTE"))
        check("untouched", rows["A8ACC"]["source"], maxloss.SOURCE_NONE)

        print("\n-- the allocation the Setup run is about to write wins --")
        # POS1DTE1 is on algo 1, whose 1DTE multiplier is 2.0 (150000 -> 300000).
        # Setup proposes 400000 for it, so the max loss must follow that, not
        # the 150000 still in the table.
        rows = by_user(maxloss.plan(
            DAY, "1DTE", has_previous=True, proposed={"pos1dte1": 400000},
        ))
        check("uses the proposed allocation", rows["POS1DTE1"]["allocation"], 400000.0)
        check("max loss follows it", rows["POS1DTE1"]["mstech"], 800000.0)
        check("the stored one is still shown",
              rows["POS1DTE1"]["stored_allocation"], 150000.0)
        check("flagged as depending on it",
              rows["POS1DTE1"]["depends_on_allocation"], True)
        check("an account not in the run is not flagged",
              rows["POS1DTE15"]["depends_on_allocation"], False)

        # A proposal identical to what is stored changes nothing and is not
        # flagged: there is no allocation write to wait for.
        same = by_user(maxloss.plan(
            DAY, "1DTE", has_previous=True, proposed={"POS1DTE1": 150000},
        ))
        check("an unchanged proposal is not flagged",
              same["POS1DTE1"]["depends_on_allocation"], False)

        # The SubCategory and per-algo tiers read the same resolved allocation.
        override = by_user(maxloss.plan(
            DAY, "1DTE", has_previous=True,
            proposed={"CCGACC": 200000, "A19CC": 500000},
        ))
        check("SubCategory tier follows the proposal",
              override["CCGACC"]["mstech"], 6000000.0)     # 200000 x 30
        check("algo tier follows the proposal",
              override["A19CC"]["mstech"], 15000000.0)     # 500000 x 30

        print("\n-- applying --")
        plan = maxloss.plan(DAY, "1DTE", has_previous=True)
        writable = [r for r in plan["rows"] if r["mstech"] is not None]
        result = maxloss.apply(DAY, writable)
        check("all_users rows written", result["all_users"], len(writable))
        check("usersetting rows written", result["usersetting"], len(writable))

        stored = dict(db.session.execute(text(
            "SELECT `userId`, `max_loss` FROM all_users WHERE `Date`=:d"),
            {"d": DAY}).all())
        check("all_users took MStech", stored["POSDAILY7"], 360000.0)
        check("the rule value landed too", stored["POS1DTE1"], 300000.0)
        check("an untouched account stays at 0", stored["NOTRUN"], 0.0)

        # Keyed by user id, so one row per user: the feed login sits on its
        # own server and is asserted separately below.
        settings = dict(db.session.execute(text(
            "SELECT `User ID`, `Max Loss` FROM usersetting "
            "WHERE `server`='VS1'")).all())
        check("usersetting took Stoxxo", settings["POSDAILY7"], 361487.85)
        check("not the MStech value", settings["POSDAILY7"] != stored["POSDAILY7"], True)

        print("\n-- feed logins are never written --")
        feed = db.session.execute(text(
            "SELECT `Max Loss` FROM usersetting WHERE `User ID`='POSDAILY7' "
            "AND `server`='VS8'")).scalar()
        check("the FEED row keeps its own value", feed, 0.0)
        check("while the real row was written",
              settings["POSDAILY7"], 361487.85)

        print("\n-- an operator writes only on their own servers --")
        db.session.execute(text(
            "INSERT INTO usersetting VALUES ('POS1DTE1', 0, 'VS9', 'REAL')"))
        db.session.commit()
        maxloss.apply(DAY, [r for r in plan["rows"] if r["userid"] == "POS1DTE1"],
                      servers=["VS1"])
        other = db.session.execute(text(
            "SELECT `Max Loss` FROM usersetting WHERE `User ID`='POS1DTE1' "
            "AND `server`='VS9'")).scalar()
        check("the other server's copy is untouched", other, 0.0)


if __name__ == "__main__":
    run()
    print("\n" + ("All max loss checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
