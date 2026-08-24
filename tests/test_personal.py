"""Personal is rebuilt from All Users, not uploaded.

    python tests/test_personal.py
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

from flask import Flask  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core import personal  # noqa: E402
from database.db import db  # noqa: E402

TODAY = dt.date(2026, 8, 24)
YESTERDAY = dt.date(2026, 8, 23)

# userId, alias, SubCategory, server, algo, allocation
ACCOUNTS = [
    ("PGB001", "CC_GOVINDAB2", "PGB", "VS6", "1", 200000),
    ("PVT001", "MSR_PRIVATE1", "PVT", "VS2", "7", 400000),
    ("PPS001", "MSR_PPS1", "PPS", "VS3", "1", 300000),
    ("PRD001", "MSR_RDANGAYACH6", "PRD", "VS9", "19", 200000),
    ("MSR001", "MSR_ORDINARY", "MSR", "VS1", "1", 500000),   # not personal
    ("MSJ001", "MSS_A19_PS", "MSJ", "VS4", "19", 600000),    # not personal
    ("PGB002", "CC_GOVINDAB3", "pgb ", "VS6", "1", 100000),  # case and padding
]

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got!r}   want {want!r}")


def rejects(label: str, fn) -> None:
    try:
        fn()
        failures.append(label)
        print(f"FAIL  {label} -> accepted, should have been refused")
    except personal.PersonalError as exc:
        print(f"PASS  {label} -> refused: {str(exc)[:70]}")


def make_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    # The app reads MySQL's catalogue; SQLite keeps the same facts in PRAGMA.
    personal._columns = lambda table: [
        row[1] for row in db.session.execute(
            text(f"PRAGMA table_info(`{table}`)")).all()
    ]
    return app


def seed(with_date: bool = True) -> None:
    db.session.execute(text("""
        CREATE TABLE all_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, `userId` TEXT, `alias` TEXT,
            `SubCategory` TEXT, `server` TEXT, `algo` TEXT, `allocation` REAL,
            `max_loss` REAL, `Operator Name` TEXT, `Running Days` TEXT,
            `ml_pct` REAL, `Date` DATE)"""))

    date_part = ", `Date` DATE" if with_date else ""
    # Deliberately mixed naming: 'Max Loss' here vs 'max_loss' in all_users,
    # plus one column that has no source at all.
    db.session.execute(text(f"""
        CREATE TABLE personal (
            id INTEGER PRIMARY KEY AUTOINCREMENT, `userId` TEXT, `alias` TEXT,
            `Account_Type` TEXT, `server` TEXT, `Max Loss` REAL,
            `Running_Day` TEXT, `Operator` TEXT, `ml_pct` REAL,
            `Remarks` TEXT{date_part})"""))

    for day in (YESTERDAY, TODAY):
        for uid, alias, sub, server, algo, alloc in ACCOUNTS:
            db.session.execute(text("""
                INSERT INTO all_users (`userId`,`alias`,`SubCategory`,`server`,
                    `algo`,`allocation`,`max_loss`,`Operator Name`,`Running Days`,
                    `ml_pct`,`Date`)
                VALUES (:u,:a,:s,:v,:g,:al,:ml,'VIKASA','Daily',3.0,:d)"""),
                dict(u=uid, a=alias, s=sub, v=server, g=algo, al=alloc,
                     ml=alloc * 0.03, d=day))
    db.session.commit()


def run() -> None:
    app = make_app()

    print("-- with no Date column, it refuses rather than guessing --")
    with app.app_context():
        seed(with_date=False)
        rejects("no Date column", lambda: personal.rebuild(TODAY))

    app = make_app()
    with app.app_context():
        seed()

        print("\n-- the column mapping is worked out from the tables --")
        shape = personal.plan(TODAY)
        check("Account_Type matches across the underscore",
              shape["mapped"].get("Account_Type"), "SubCategory")
        check("'Max Loss' matches 'max_loss' across spacing",
              shape["mapped"].get("Max Loss"), "max_loss")
        check("same-named columns map straight through",
              [shape["mapped"].get(c) for c in ("userId", "alias", "server")],
              ["userId", "alias", "server"])
        check("a column with no source is reported, not guessed",
              shape["unmapped"], ["Remarks"])
        check("the date column is found", shape["date_column"], "Date")
        check("Running_Day maps to the plural Running Days",
              shape["mapped"].get("Running_Day"), "Running Days")
        check("Operator maps to Operator Name",
              shape["mapped"].get("Operator"), "Operator Name")
        check("ml_pct needs no synonym", shape["mapped"].get("ml_pct"), "ml_pct")

        print("\n-- rebuilding a day --")
        result = personal.rebuild(TODAY)
        check("only the four account types are taken", result["written"], 5)
        check("nothing to replace the first time", result["replaced"], 0)

        rows = db.session.execute(text(
            "SELECT `userId`, `Account_Type`, `server`, `Max Loss` "
            "FROM personal ORDER BY `userId`")).all()
        check("the right accounts",
              [r[0] for r in rows],
              ["PGB001", "PGB002", "PPS001", "PRD001", "PVT001"])
        check("Account Type holds the SubCategory",
              sorted({r[1].strip().upper() for r in rows}),
              ["PGB", "PPS", "PRD", "PVT"])
        check("values came across", [r[2] for r in rows][0], "VS6")
        check("max_loss landed in 'Max Loss'", float(rows[0][3]), 6000.0)
        check("the unmapped column is NULL, not junk",
              db.session.execute(text(
                  "SELECT COUNT(*) FROM personal WHERE `Remarks` IS NOT NULL")).scalar(),
              0)

        print("\n-- re-running replaces that day only --")
        db.session.execute(text(
            "UPDATE all_users SET `server`='VS99' WHERE `userId`='PGB001' "
            "AND `Date`=:d"), {"d": TODAY})
        db.session.commit()

        again = personal.rebuild(TODAY)
        check("the day is replaced, not doubled", again["replaced"], 5)
        check("same count after", again["written"], 5)
        check("total rows unchanged",
              db.session.execute(text("SELECT COUNT(*) FROM personal")).scalar(), 5)
        check("the correction came through",
              db.session.execute(text(
                  "SELECT `server` FROM personal WHERE `userId`='PGB001'")).scalar(),
              "VS99")

        print("\n-- a different day is built alongside, not over --")
        personal.rebuild(YESTERDAY)
        check("both days held",
              db.session.execute(text("SELECT COUNT(*) FROM personal")).scalar(), 10)
        check("today still right",
              db.session.execute(text(
                  "SELECT COUNT(*) FROM personal WHERE `Date`=:d"),
                  {"d": TODAY}).scalar(), 5)

        print("\n-- a date with no All Users rows writes nothing --")
        empty = personal.rebuild(dt.date(2026, 1, 1))
        check("nothing written", empty["written"], 0)
        check("existing days untouched",
              db.session.execute(text("SELECT COUNT(*) FROM personal")).scalar(), 10)


if __name__ == "__main__":
    run()
    print("\n" + ("All Personal checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
